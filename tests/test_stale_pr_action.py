from datetime import datetime, timedelta, timezone

from babbla.config import ProjectBinding, StalePRConfig
from babbla.digest.actions import StalePRAction

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          stale_prs=StalePRConfig("weekly", "UTC", 14, False))


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); return "TS1"


def _pr(number, days_ago, *, draft=False, login="alice", title=None):
    updated = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"number": number, "title": title or f"PR {number}",
            "html_url": f"https://github.com/Wkkkkk/MyTV/pull/{number}",
            "draft": draft, "updated_at": updated, "user": {"login": login}}


def _reader(prs):
    return lambda path: prs


def _action(last, prs, *, threshold_days=14, include_drafts=False):
    timer, poster = FakeTimer(last), FakePoster()
    action = StalePRAction(_binding(), timer, _reader(prs), poster,
                           "weekly", "UTC", threshold_days, include_drafts)
    return action, timer, poster


async def test_not_due_does_nothing():
    action, timer, poster = _action(NOW.timestamp(), [_pr(1, 30)])
    await action.maybe_run(NOW)
    assert poster.posts == [] and timer.advanced == []


async def test_stale_present_posts_list_and_advances():
    action, timer, poster = _action(None, [_pr(42, 30, login="bob", title="fix thing")])
    await action.maybe_run(NOW)
    assert len(poster.posts) == 1
    channel, text = poster.posts[0]
    assert channel == "C0XXXXXXXXX"
    assert text.startswith("🧹 *MyTV — 1 open PRs idle ≥ 14d*")   # exact header glyphs + count + threshold
    assert "<https://github.com/Wkkkkk/MyTV/pull/42|#42>" in text
    assert "*fix thing*" in text and "30d" in text and "@bob" in text
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_none_stale_no_post_but_advances():
    action, timer, poster = _action(None, [_pr(1, 3)])   # fresh PR, below threshold
    await action.maybe_run(NOW)
    assert poster.posts == []
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_drafts_excluded_by_default():
    action, timer, poster = _action(None, [_pr(1, 30, draft=True)])
    await action.maybe_run(NOW)
    assert poster.posts == []                 # only stale PR was a draft
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_list_capped_with_and_more_tail():
    prs = [_pr(n, 30 + n) for n in range(1, 26)]   # 25 stale PRs
    action, timer, poster = _action(None, prs)
    await action.maybe_run(NOW)
    text = poster.posts[0][1]
    assert text.count("• ") == 20                  # capped at 20 bullets
    assert "…and 5 more" in text


async def test_none_returned_treated_as_no_prs():
    timer, poster = FakeTimer(None), FakePoster()
    action = StalePRAction(_binding(), timer, lambda p: None, poster, "weekly", "UTC", 14, False)
    await action.maybe_run(NOW)
    assert poster.posts == []
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_same_bucket_second_tick_not_due():
    action, timer, poster = _action((NOW - timedelta(hours=1)).timestamp(), [_pr(1, 30)])
    await action.maybe_run(NOW)
    assert poster.posts == []
