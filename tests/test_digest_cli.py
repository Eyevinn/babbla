import pytest
from datetime import datetime, timezone
from babbla.config import Config, DigestConfig, ProjectBinding
from babbla.digest.__main__ import run_once


def _binding(name, channel):
    return ProjectBinding(name, "o", "r", "public", channel, False, DigestConfig("weekly", "UTC", "branch"))


class RecordingScheduler:
    def __init__(self, config):
        self._config = config
        self.ticked = []
    async def tick(self, now):
        self.ticked.append([b.name for b in self._config.digest_bindings()])


async def test_run_once_ticks_all_projects():
    cfg = Config(bindings=(_binding("MyTV", "C0AAA"), _binding("Other", "C0BBB")))
    sched = RecordingScheduler(cfg)
    rc = await run_once(sched)
    assert rc == 0
    assert sched.ticked == [["MyTV", "Other"]]


async def test_run_once_unknown_project_errors():
    cfg = Config(bindings=(_binding("MyTV", "C0AAA"),))
    sched = RecordingScheduler(cfg)
    rc = await run_once(sched, project="Nope")
    assert rc == 2
