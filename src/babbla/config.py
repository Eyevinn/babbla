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
class Topic:
    name: str
    description: str


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
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool
    digest: DigestConfig | None = None
    quiz: QuizConfig | None = None


@dataclass(frozen=True)
class SubscriptionDigest:
    cadence: str
    tz: str
    topic: "Topic | None" = None


@dataclass(frozen=True)
class PersonalDigestConfig:
    default_cadence: str
    tz: str


@dataclass(frozen=True)
class Subscription:
    channel_id: str
    project_names: tuple[str, ...]
    digest: SubscriptionDigest | None = None


@dataclass(frozen=True)
class Config:
    bindings: tuple[ProjectBinding, ...]
    lobby_channel_id: str | None = None
    subscriptions: tuple[Subscription, ...] = ()
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

    def subscription_for(self, channel_id: str) -> Subscription | None:
        for s in self.subscriptions:
            if s.channel_id == channel_id:
                return s
        return None

    def digest_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.digest is not None and b.channel_id)

    def digest_subscriptions(self) -> tuple[Subscription, ...]:
        return tuple(s for s in self.subscriptions if s.digest is not None)

    def quiz_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.quiz is not None and b.channel_id)


def _parse_cadence_tz(label: str, raw: dict | None, kind: str):
    """Shared cadence+tz parse for subscription digest / quiz. Returns (cadence, tz) or None."""
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


def _parse_subscriptions(raw_subs, known_names: set[str]) -> tuple[Subscription, ...]:
    subscriptions: list[Subscription] = []
    seen_channels: set[str] = set()
    for raw_sub in raw_subs or []:
        channel_id = raw_sub.get("channel_id")
        if not channel_id:
            raise ValueError("channels.yaml: each subscription requires a channel_id")
        names = tuple(raw_sub.get("projects") or ())
        if not names:
            raise ValueError(
                f"channels.yaml: subscription for {channel_id} must list at least one project"
            )
        for n in names:
            if n not in known_names:
                raise ValueError(
                    f"channels.yaml: subscription for {channel_id} references unknown project {n!r}"
                )
        if channel_id in seen_channels:
            raise ValueError(
                f"channels.yaml: channel_id {channel_id} appears in more than one subscription"
            )
        seen_channels.add(channel_id)
        raw_digest = raw_sub.get("digest")
        ct = _parse_cadence_tz(f"subscription {channel_id}", raw_digest, "digest")
        if ct:
            topic = _parse_topic(f"subscription {channel_id}", (raw_digest or {}).get("topic"))
            digest = SubscriptionDigest(cadence=ct[0], tz=ct[1], topic=topic)
        else:
            digest = None
        subscriptions.append(
            Subscription(channel_id=channel_id, project_names=names, digest=digest)
        )
    return tuple(subscriptions)


def _parse_topic(label: str, raw: dict | None) -> "Topic | None":
    if not raw:
        return None
    name = str(raw.get("name", "")).strip()
    description = str(raw.get("description", "")).strip()
    if not name or not description:
        raise ValueError(f"{label}: topic requires both name and description")
    return Topic(name=name, description=description)


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
    subscriptions = _parse_subscriptions(raw.get("subscriptions"), {b.name for b in bindings})
    for sub in subscriptions:
        if lobby_channel_id is not None and sub.channel_id == lobby_channel_id:
            logger.warning(
                "channels.yaml: channel_id %r is both the lobby channel and a subscription; "
                "the lobby dispatch wins, so the subscription is shadowed.",
                sub.channel_id,
            )
    personal_digest = _parse_personal_digest(raw.get("personal_digest"))
    return Config(
        bindings=bindings,
        lobby_channel_id=lobby_channel_id,
        subscriptions=subscriptions,
        personal_digest=personal_digest,
    )
