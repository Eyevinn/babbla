from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

logger = logging.getLogger(__name__)

_CADENCES = {"daily", "weekly"}
_ANCHORS = {"branch", "deploy"}


@dataclass(frozen=True)
class DigestConfig:
    cadence: str
    tz: str
    anchor: str
    deploy_workflow: str | None = None


@dataclass(frozen=True)
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool
    digest: DigestConfig | None = None


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

    def digest_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.digest is not None and b.channel_id)


def _parse_digest(name: str, raw: dict | None) -> DigestConfig | None:
    if not raw:
        return None
    raw_cadence = raw.get("cadence", "off")
    # PyYAML coerces bare `off`/`on`/`yes`/`no` to booleans; treat off/False as disabled.
    if raw_cadence is False or str(raw_cadence).strip().lower() == "off":
        return None
    cadence = str(raw_cadence)
    if cadence not in _CADENCES:
        raise ValueError(f"{name}: digest.cadence must be one of off|daily|weekly, got {cadence!r}")
    anchor = str(raw.get("anchor", ""))
    if anchor not in _ANCHORS:
        raise ValueError(f"{name}: digest.anchor must be one of branch|deploy, got {anchor!r}")
    tz = str(raw.get("tz", "UTC"))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{name}: digest.tz is not a valid time zone: {tz!r}") from exc
    deploy_workflow = None
    if anchor == "deploy":
        deploy_workflow = (raw.get("deploy") or {}).get("workflow")
        if not deploy_workflow:
            raise ValueError(f"{name}: digest.anchor=deploy requires digest.deploy.workflow")
    return DigestConfig(cadence=cadence, tz=tz, anchor=anchor, deploy_workflow=deploy_workflow)


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
            digest=_parse_digest(p["name"], p.get("digest")),
        )
        for p in raw.get("projects", [])
    )
    for b in bindings:
        if b.visibility == "private" and b.dm:
            logger.warning(
                "channels.yaml: project %r is private with dm: true — its DM surface "
                "will always deny and point to the channel (a dead DM surface).",
                b.name,
            )
    if sum(1 for b in bindings if b.dm) > 1:
        raise ValueError("channels.yaml: exactly one project may set dm: true in the pilot")
    return Config(bindings=bindings)
