from datetime import datetime, timezone

from babbla.config import ProjectBinding
from babbla.digest.actions import PersonalDigestAction
from babbla.session_store import PersonalSubStore, PersonalDigestStateStore

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)

MYTV = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", False)
SECRET = ProjectBinding("Secret", "o", "secret", "private", "C2", False)
BY_NAME = {"MyTV": MYTV, "Secret": SECRET}


class FakeRunner:
    def __init__(self, text="digest text"):
        self.text = text
        self.last_topics = None

    async def summarize_shared(self, binding, per_project_changes, slugs=None, topics_by_project=None):
        self.last_topics = topics_by_project
        return self.text


class FakePoster:
    def __init__(self, fail_open=False):
        self.posts = []
        self.opened = []
        self.fail_open = fail_open

    async def open_dm(self, user_id):
        self.opened.append(user_id)
        if self.fail_open:
            raise RuntimeError("cannot open dm")
        return f"D-{user_id}"

    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text))
        self.blocks = blocks
        return "ts-1"


def _get_json_with_commits(head_sha, commits):
    # head_for("branch") calls: /repos/{owner}/{repo}/commits?per_page=1  → list
    #   → expects commits[0]["sha"]
    # changes_since calls: /repos/{owner}/{repo}/commits?since=...&per_page=100 → list
    #   → each item passed to _change(c) which reads c["sha"] and c.get("commit",{})
    def get_json(path):
        if "commits?" in path or "/commits?" in path:
            return commits
        # changes_between calls /compare/base...head → {"commits": [...]}
        if "/compare/" in path:
            return {"commits": commits}
        return None

    return get_json


async def _store_pair(tmp_path):
    subs = PersonalSubStore(str(tmp_path / "p.db"))
    state = PersonalDigestStateStore(str(tmp_path / "p.db"))
    return subs, state


async def test_no_subscribers_is_noop(tmp_path):
    subs, state = await _store_pair(tmp_path)
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]), FakeRunner(), poster,
                                  "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []
    subs.close()
    state.close()


async def test_paused_user_skipped(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.set_cadence("U1", "off")
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []
    subs.close()
    state.close()


async def test_private_project_filtered_at_send_time(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "Secret")        # private — must never be summarized to a DM
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []              # no changes gathered → no DM
    subs.close()
    state.close()


async def test_personal_digest_dm_carries_delete_button_owned_by_user(tmp_path):
    from babbla.blocks import DELETE_ACTION_ID
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")           # open-tier → a DM digest is sent
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == [("D-U1", "digest text")]   # delivered to U1's DM
    ids = [e["action_id"] for b in (poster.blocks or []) if b.get("type") == "actions"
           for e in b["elements"]]
    assert DELETE_ACTION_ID in ids
    value = next(b["elements"][0]["value"] for b in poster.blocks if b.get("type") == "actions")
    assert value == "U1"                   # only the recipient may delete their digest
    subs.close()
    state.close()


async def test_one_user_failure_does_not_abort_others(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.add("U2", "MyTV")
    poster = FakePoster(fail_open=True)    # open_dm raises for everyone
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)            # must not raise
    assert sorted(poster.opened) == ["U1", "U2"]   # both attempted
    subs.close()
    state.close()


async def test_personal_digest_passes_user_topics_to_runner(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.add_topic("U1", "MyTV", "security", "auth, CVEs")
    runner = FakeRunner()
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  runner, poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert runner.last_topics == {"MyTV": (("security", "auth, CVEs"),)}
    subs.close(); state.close()


async def test_personal_digest_empty_summary_skips_post_but_advances(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.add_topic("U1", "MyTV", "i18n", "translations")   # nothing matches → runner returns ""
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(text=""), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []                                    # no blank DM
    assert (await state.get("U1")).watermarks.get("MyTV") == "sha1"   # watermark advanced
    subs.close(); state.close()


async def test_topics_scoped_to_changed_projects_only(tmp_path):
    """Topics for projects absent from per_project_changes must not reach summarize_shared.

    Scenario: user follows MyTV (has changes) + Babbla (no changes this cycle).
    User has a topic only on Babbla.  Before the fix, the full topic dict was
    passed → has_topics=True → NOTHING_RELEVANT preamble → digest suppressed.
    After the fix, topics_by_project passed to the runner excludes Babbla, so
    runner.last_topics has no orphan keys (only keys present in per_project_changes).
    """
    BABBLA = ProjectBinding("Babbla", "Eyevinn", "babbla", "public", "C3", False)
    by_name = {**BY_NAME, "Babbla": BABBLA}

    def get_json_mytv_only(path):
        # MyTV has commits; Babbla returns an empty commit list (no changes).
        if ("o/MyTV" in path or "o/mytv" in path.lower()) and ("commits?" in path or "/compare/" in path):
            if "/compare/" in path:
                return {"commits": [{"sha": "sha1"}]}
            return [{"sha": "sha1"}]
        if ("Eyevinn/babbla" in path) and ("commits?" in path or "/compare/" in path):
            if "/compare/" in path:
                return {"commits": []}
            return []
        # head_for: /repos/{owner}/{repo}/commits?per_page=1
        if "MyTV" in path and "commits?" in path:
            return [{"sha": "sha1"}]
        if "babbla" in path and "commits?" in path:
            return [{"sha": "sha2"}]
        return None

    subs, state = await _store_pair(tmp_path)
    # Subscribe to both; give user a topic ONLY on Babbla
    await subs.add("U1", "MyTV")
    await subs.add("U1", "Babbla")
    await subs.add_topic("U1", "Babbla", "security", "auth")

    runner = FakeRunner()
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, by_name, get_json_mytv_only,
                                  runner, poster, "weekly", "UTC")
    await action.maybe_run(NOW)

    # Digest must be posted (MyTV has changes and no topic filter suppresses it)
    assert poster.posts != [], "digest was suppressed — orphan topic leaked into topics_by_project"

    # runner.last_topics must only contain keys that had actual changes this cycle
    if runner.last_topics is not None:
        assert "Babbla" not in runner.last_topics, (
            "Babbla (no changes) must not appear in topics_by_project passed to runner"
        )

    subs.close(); state.close()
