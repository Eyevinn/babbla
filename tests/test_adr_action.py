from datetime import datetime, timedelta, timezone

from babbla.config import AdrConfig, ProjectBinding
from babbla.digest.actions import AdrOfWeekAction

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          adr=AdrConfig("weekly", "UTC", "docs/adr"))


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakeCursor:
    def __init__(self, value=None): self._v = value; self.sets = []
    async def get(self, key): return self._v
    async def set(self, key, value): self.sets.append((key, value)); self._v = value


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); return "TS1"


class FakeAdrRunner:
    def __init__(self, text="TEASER", fail=False):
        self._text = text; self._fail = fail; self.calls = []
    async def teaser(self, binding, adr_path):
        self.calls.append(adr_path)
        if self._fail:
            raise RuntimeError("agent boom")
        return self._text


def _entries(*names):
    # GitHub contents API returns a list of entries with a `name` each.
    items = [{"name": n} for n in names]
    return lambda path: items


def _action(last, cursor_value, reader, runner):
    timer, cursor, poster = FakeTimer(last), FakeCursor(cursor_value), FakePoster()
    action = AdrOfWeekAction(_binding(), timer, cursor, reader, runner, poster,
                             "weekly", "UTC", "docs/adr")
    return action, timer, cursor, poster


async def test_not_due_does_nothing():
    runner = FakeAdrRunner()
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(NOW.timestamp(), None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and timer.advanced == [] and cursor.sets == []


async def test_first_run_posts_first_adr_sets_cursor_advances():
    runner = FakeAdrRunner("TEASER A")
    reader = _entries("0002-b.md", "0001-a.md", "README.md")  # unsorted + non-NNNN excluded
    action, timer, cursor, poster = _action(None, None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0001-a.md"]      # sorted-first, README ignored
    assert poster.posts == [("C0XXXXXXXXX", "TEASER A")]
    assert cursor.sets == [("adr:MyTV", "0001-a.md")]
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_subsequent_run_picks_next_adr():
    runner = FakeAdrRunner("TEASER B")
    reader = _entries("0001-a.md", "0002-b.md", "0003-c.md")
    action, timer, cursor, poster = _action(None, "0001-a.md", reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0002-b.md"]
    assert cursor.sets == [("adr:MyTV", "0002-b.md")]


async def test_wraps_around_at_end():
    runner = FakeAdrRunner()
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(None, "0002-b.md", reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0001-a.md"]      # wrapped to first


async def test_cursor_names_missing_file_wraps_to_first():
    runner = FakeAdrRunner()
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(None, "9999-gone.md", reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0001-a.md"]


async def test_no_adrs_quiet_but_advances():
    runner = FakeAdrRunner()
    reader = _entries("README.md", "index.md")          # nothing matches NNNN-*.md
    action, timer, cursor, poster = _action(None, None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert cursor.sets == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_none_contents_quiet_but_advances():
    runner = FakeAdrRunner()
    action, timer, cursor, poster = _action(None, None, lambda p: None, runner)
    await action.maybe_run(NOW)
    assert poster.posts == [] and timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_teaser_failure_does_not_advance_cursor_or_timer():
    runner = FakeAdrRunner(fail=True)
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(None, None, reader, runner)
    try:
        await action.maybe_run(NOW)
    except RuntimeError:
        pass
    assert poster.posts == [] and cursor.sets == [] and timer.advanced == []
