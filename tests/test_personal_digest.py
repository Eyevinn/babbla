from datetime import datetime, timezone

from babbla.config import ProjectBinding
from babbla.digest.actions import PersonalDigestAction
from babbla.session_store import PersonalSubStore, PersonalDigestStateStore

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)

MYTV = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", False)
SECRET = ProjectBinding("Secret", "o", "secret", "private", "C2", False)
BY_NAME = {"MyTV": MYTV, "Secret": SECRET}


class FakeRunner:
    async def summarize_shared(self, binding, per_project_changes):
        return "digest text"


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

    async def post(self, channel_id, text, thread_ts=None):
        self.posts.append((channel_id, text))
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
                                  _get_json_with_commits("sha1", []), FakeRunner(), poster,
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
