from datetime import datetime, timedelta, timezone

from babbla.config import AdrConfig, ProjectBinding
from babbla.digest.actions import AdrDigestAction

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          adr=AdrConfig("weekly", "UTC", "docs/adr"))


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); return "TS1"


class FakeAdrRunner:
    def __init__(self, text="DIGEST", fail=False):
        self._text = text; self._fail = fail; self.calls = []
    async def digest(self, binding, adr_paths):
        self.calls.append(list(adr_paths))
        if self._fail:
            raise RuntimeError("agent boom")
        return self._text


def _contents(*names):
    return [{"name": n} for n in names]


def _commit(date_iso):
    return [{"commit": {"committer": {"date": date_iso}}}]


def _action(last, reader, runner):
    timer, poster = FakeTimer(last), FakePoster()
    action = AdrDigestAction(_binding(), timer, reader, runner, poster, "weekly", "UTC", "docs/adr")
    return action, timer, poster


# Deterministic branded lead-in the action prepends (cadence "weekly", slug Wkkkkk/MyTV).
LEAD = "Here's a weekly architecture decision record on *Wkkkkk/MyTV*"


async def test_not_due_does_nothing():
    runner = FakeAdrRunner()
    action, timer, poster = _action(NOW.timestamp(), lambda p: _contents("0001-a.md"), runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and timer.advanced == []


async def test_first_run_backfills_all_and_advances():
    runner = FakeAdrRunner("DIGEST A")
    reader = lambda p: _contents("0002-b.md", "0001-a.md", "README.md")   # since=None -> no commit calls
    action, timer, poster = _action(None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [["docs/adr/0001-a.md", "docs/adr/0002-b.md"]]   # all, sorted, README excluded
    assert poster.posts == [("C0XXXXXXXXX", f"{LEAD}\n\nDIGEST A")]          # branded lead-in + agent body
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_subsequent_run_posts_only_changed():
    runner = FakeAdrRunner("DIGEST B")
    last = (NOW - timedelta(days=7)).timestamp()        # previous weekly bucket -> due; since = 7d ago
    def reader(path):
        if "/contents/" in path:
            return _contents("0001-a.md", "0002-b.md")
        if "0001-a.md" in path:
            return _commit("2026-06-01T00:00:00Z")      # before since -> excluded
        if "0002-b.md" in path:
            return _commit("2026-06-18T00:00:00Z")      # within window -> included
        raise AssertionError(path)
    action, timer, poster = _action(last, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [["docs/adr/0002-b.md"]]
    assert poster.posts == [("C0XXXXXXXXX", f"{LEAD}\n\nDIGEST B")]
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_nothing_changed_quiet_but_advances():
    runner = FakeAdrRunner()
    last = (NOW - timedelta(days=7)).timestamp()
    def reader(path):
        if "/contents/" in path:
            return _contents("0001-a.md")
        return _commit("2026-06-01T00:00:00Z")          # before since -> nothing changed
    action, timer, poster = _action(last, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_no_adrs_quiet_but_advances():
    runner = FakeAdrRunner()
    action, timer, poster = _action(None, lambda p: _contents("README.md"), runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_none_contents_quiet_but_advances():
    runner = FakeAdrRunner()
    action, timer, poster = _action(None, lambda p: None, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_digest_failure_does_not_advance():
    runner = FakeAdrRunner(fail=True)
    action, timer, poster = _action(None, lambda p: _contents("0001-a.md"), runner)
    try:
        await action.maybe_run(NOW)
    except RuntimeError:
        pass
    assert poster.posts == [] and timer.advanced == []
