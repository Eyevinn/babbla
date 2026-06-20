from __future__ import annotations

import fnmatch
from dataclasses import replace

from babbla.config import Topic
from babbla.digest.anchors import Change


def _pr_labels(owner: str, repo: str, n: int, get_json) -> tuple[str, ...]:
    data = get_json(f"/repos/{owner}/{repo}/pulls/{n}")
    if not data:
        return ()
    return tuple(lbl["name"] for lbl in data.get("labels", []))


def _pr_files(owner: str, repo: str, n: int, get_json) -> tuple[str, ...]:
    data = get_json(f"/repos/{owner}/{repo}/pulls/{n}/files?per_page=100")
    if not data:
        return ()
    return tuple(f["filename"] for f in data)


def _path_match(path: str, glob: str) -> bool:
    # Case-sensitive, cross-platform deterministic. `*`/`**` cross `/`, so
    # `src/babbla/**` matches nested files; over-matching is low-stakes since
    # signals only guarantee inclusion, never gate.
    return fnmatch.fnmatchcase(path, glob)


def enrich_changes(owner: str, repo: str, changes: list[Change], topic: Topic, *, get_json) -> list[Change]:
    """Populate labels/paths on PR-backed changes, only as the topic needs them.

    No-op (returns `changes`) when the topic has no signals. Each PR is fetched
    at most once per call. Missing PR data (404/None) -> empty tuples, never raises.
    """
    if not topic.has_signals:
        return changes
    label_cache: dict[int, tuple[str, ...]] = {}
    file_cache: dict[int, tuple[str, ...]] = {}
    out: list[Change] = []
    for c in changes:
        if c.pr_number is None:
            out.append(c)
            continue
        labels = c.labels
        paths = c.paths
        if topic.labels:
            if c.pr_number not in label_cache:
                label_cache[c.pr_number] = _pr_labels(owner, repo, c.pr_number, get_json)
            labels = label_cache[c.pr_number]
        if topic.paths:
            if c.pr_number not in file_cache:
                file_cache[c.pr_number] = _pr_files(owner, repo, c.pr_number, get_json)
            paths = file_cache[c.pr_number]
        out.append(replace(c, labels=labels, paths=paths))
    return out


def matches_topic(change: Change, topic: Topic) -> bool:
    if topic.labels and set(change.labels) & set(topic.labels):
        return True
    if topic.paths and any(_path_match(p, g) for p in change.paths for g in topic.paths):
        return True
    return False
