from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool


@dataclass(frozen=True)
class Config:
    bindings: tuple[ProjectBinding, ...]

    def for_channel(self, channel_id: str) -> ProjectBinding | None:
        for b in self.bindings:
            if b.channel_id is not None and b.channel_id == channel_id:
                return b
        return None

    def for_dm(self) -> ProjectBinding | None:
        for b in self.bindings:
            if b.dm:
                return b
        return None


def load_config(path: str | os.PathLike) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    bindings = tuple(
        ProjectBinding(
            name=p["name"],
            owner=p["owner"],
            repo=p["repo"],
            visibility=p["visibility"],
            channel_id=p.get("channel_id"),
            dm=bool(p.get("dm", False)),
        )
        for p in raw.get("projects", [])
    )
    if sum(1 for b in bindings if b.dm) > 1:
        raise ValueError("channels.yaml: exactly one project may set dm: true in the pilot")
    return Config(bindings=bindings)
