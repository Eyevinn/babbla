from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

logger = logging.getLogger(__name__)

_CADENCES = {"daily", "weekly"}
_ANCHORS = {"branch", "deploy"}


@dataclass(frozen=True)
class Topic:
    name: str
    description: str
    labels: tuple[str, ...] = ()   # PR labels that mark a change as in-topic
    paths: tuple[str, ...] = ()    # glob patterns over changed file paths

    @property
    def has_signals(self) -> bool:
        return bool(self.labels or self.paths)


@dataclass(frozen=True)
class DigestConfig:
    cadence: str
    tz: str
    anchor: str
    deploy_workflow: str | None = None
    topic: "Topic | None" = None


@dataclass(frozen=True)
class QuizConfig:
    cadence: str
    tz: str
    count: int = 3


@dataclass(frozen=True)
class StalePRConfig:
    cadence: str          # daily | weekly
    tz: str
    threshold_days: int = 14
    include_drafts: bool = False


@dataclass(frozen=True)
class AdrConfig:
    cadence: str          # daily | weekly
    tz: str
    dir: str = "docs/adr"


@dataclass(frozen=True)
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool
    digest: DigestConfig | None = None
    quiz: QuizConfig | None = None
    stale_prs: "StalePRConfig | None" = None
    adr: "AdrConfig | None" = None
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonalDigestConfig:
    default_cadence: str
    tz: str


@dataclass(frozen=True)
class Config:
    bindings: tuple[ProjectBinding, ...]
    lobby_channel_id: str | None = None
    personal_digest: "PersonalDigestConfig | None" = None

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

    def quiz_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.quiz is not None and b.channel_id)

    def stale_pr_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.stale_prs is not None and b.channel_id)

    def adr_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.adr is not None and b.channel_id)


def _parse_cadence_tz(label: str, raw: dict | None, kind: str):
    """Shared cadence+tz parse for the quiz block. Returns (cadence, tz) or None."""
    if not raw:
        return None
    raw_cadence = raw.get("cadence", "off")
    if raw_cadence is False or str(raw_cadence).strip().lower() == "off":
        return None
    cadence = str(raw_cadence)
    if cadence not in _CADENCES:
        raise ValueError(f"{label}: {kind}.cadence must be one of off|daily|weekly, got {cadence!r}")
    tz = str(raw.get("tz", "UTC"))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{label}: {kind}.tz is not a valid time zone: {tz!r}") from exc
    return cadence, tz


def _parse_quiz(name: str, raw: dict | None) -> QuizConfig | None:
    ct = _parse_cadence_tz(name, raw, "quiz")
    if ct is None:
        return None
    count = raw.get("count", 3)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"{name}: quiz.count must be a positive integer, got {count!r}")
    return QuizConfig(cadence=ct[0], tz=ct[1], count=count)


def _parse_stale_prs(name: str, raw: dict | None) -> "StalePRConfig | None":
    ct = _parse_cadence_tz(name, raw, "stale_prs")
    if ct is None:
        return None
    threshold = raw.get("threshold_days", 14)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise ValueError(
            f"{name}: stale_prs.threshold_days must be a positive integer, got {threshold!r}"
        )
    include_drafts = bool(raw.get("include_drafts", False))
    return StalePRConfig(cadence=ct[0], tz=ct[1], threshold_days=threshold,
                         include_drafts=include_drafts)


def _parse_adr(name: str, raw: dict | None) -> "AdrConfig | None":
    ct = _parse_cadence_tz(name, raw, "adr")
    if ct is None:
        return None
    return AdrConfig(cadence=ct[0], tz=ct[1], dir=str(raw.get("dir", "docs/adr")))


def _parse_str_list(label: str, field: str, raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{label}: {field} must be a list of strings")
    return tuple(str(x) for x in raw)


def _parse_skills(name: str, raw: object, pool: Path) -> tuple[str, ...]:
    skills = _parse_str_list(name, "skills", raw)
    for s in skills:
        if not (pool / s / "SKILL.md").is_file():
            raise ValueError(f"{name}: unknown skill {s!r} (no {pool}/{s}/SKILL.md)")
    return skills


def _parse_topic(label: str, raw: dict | None) -> "Topic | None":
    if not raw:
        return None
    name = str(raw.get("name", "")).strip()
    description = str(raw.get("description", "")).strip()
    if not name or not description:
        raise ValueError(f"{label}: topic requires both name and description")
    labels = _parse_str_list(label, "topic.labels", raw.get("labels"))
    paths = _parse_str_list(label, "topic.paths", raw.get("paths"))
    return Topic(name=name, description=description, labels=labels, paths=paths)


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
    topic = _parse_topic(name, raw.get("topic"))
    return DigestConfig(cadence=cadence, tz=tz, anchor=anchor, deploy_workflow=deploy_workflow, topic=topic)


def _parse_personal_digest(raw: dict | None) -> "PersonalDigestConfig | None":
    if not raw:
        return None
    cadence = str(raw.get("default_cadence", "weekly"))
    if cadence not in _CADENCES:
        raise ValueError(
            f"personal_digest.default_cadence must be one of daily|weekly, got {cadence!r}"
        )
    tz = str(raw.get("tz", "UTC"))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"personal_digest.tz is not a valid time zone: {tz!r}") from exc
    return PersonalDigestConfig(default_cadence=cadence, tz=tz)


def load_config(path: str | os.PathLike) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    pool = Path(path).parent / "skills"
    bindings = tuple(
        ProjectBinding(
            name=p["name"],
            owner=p["owner"],
            repo=p["repo"],
            visibility=p["visibility"],
            channel_id=p.get("channel_id"),
            dm=bool(p.get("dm", False)),
            digest=_parse_digest(p["name"], p.get("digest")),
            quiz=_parse_quiz(p["name"], p.get("quiz")),
            stale_prs=_parse_stale_prs(p["name"], p.get("stale_prs")),
            adr=_parse_adr(p["name"], p.get("adr")),
            skills=_parse_skills(p["name"], p.get("skills"), pool),
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
    lobby_channel_id = raw.get("lobby_channel_id")
    personal_digest = _parse_personal_digest(raw.get("personal_digest"))
    return Config(
        bindings=bindings,
        lobby_channel_id=lobby_channel_id,
        personal_digest=personal_digest,
    )
