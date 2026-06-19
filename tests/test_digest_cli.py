from babbla.digest.__main__ import _utcnow, run_once
from babbla.digest.scheduler import ActionScheduler


class RecordingAction:
    """Matches the real Action contract: a `.project` (None for non-project-scoped
    actions like shared/personal digests) and a `maybe_run` coroutine."""

    def __init__(self, project=None, label="action"):
        self.project = project
        self.label = label
        self.ran = False

    async def maybe_run(self, now):
        self.ran = True


def _sched(*actions):
    return ActionScheduler(actions=tuple(actions), now_fn=_utcnow)


async def test_run_once_ticks_all_actions():
    a, b = RecordingAction("MyTV"), RecordingAction("Other")
    rc = await run_once(_sched(a, b))
    assert rc == 0
    assert a.ran and b.ran


async def test_run_once_single_project_runs_only_that_action():
    a, b = RecordingAction("MyTV"), RecordingAction("Other")
    rc = await run_once(_sched(a, b), project="MyTV")
    assert rc == 0
    assert a.ran and not b.ran


async def test_run_once_single_project_with_multiword_name():
    a = RecordingAction("Agentic Engineering Kit")
    rc = await run_once(_sched(a), project="Agentic Engineering Kit")
    assert rc == 0
    assert a.ran


async def test_run_once_unknown_project_errors_and_runs_nothing():
    a = RecordingAction("MyTV")
    rc = await run_once(_sched(a), project="Nope")
    assert rc == 2
    assert not a.ran
