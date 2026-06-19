from datetime import datetime, timezone
from babbla.digest.scheduler import ActionScheduler

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


class RecordingAction:
    def __init__(self, label): self.label = label; self.ran = []
    async def maybe_run(self, now): self.ran.append(now)


class BoomAction:
    label = "boom"
    async def maybe_run(self, now): raise RuntimeError("kaboom")


async def test_tick_runs_each_action():
    a, b = RecordingAction("a"), RecordingAction("b")
    await ActionScheduler(actions=(a, b), now_fn=lambda: NOW).tick(NOW)
    assert a.ran == [NOW] and b.ran == [NOW]


async def test_tick_isolates_failures():
    good = RecordingAction("good")
    await ActionScheduler(actions=(BoomAction(), good), now_fn=lambda: NOW).tick(NOW)
    assert good.ran == [NOW]          # a raising action does not stop the others


async def test_tick_empty_is_harmless():
    await ActionScheduler(actions=(), now_fn=lambda: NOW).tick(NOW)
