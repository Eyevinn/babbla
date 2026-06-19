from __future__ import annotations

from typing import Sequence

from babbla.lobby import CatalogEntry


def entries_for(
    catalog: Sequence[CatalogEntry], names: Sequence[str]
) -> tuple[CatalogEntry, ...]:
    """Catalog entries for the given project names, in the order given.

    A name with no matching catalog entry is silently skipped — config
    validation guarantees the name exists as a binding, so this only guards a
    partially-built catalog.
    """
    by_name = {e.binding.name: e for e in catalog}
    return tuple(by_name[n] for n in names if n in by_name)


def subscription_clarify(entries: Sequence[CatalogEntry]) -> str:
    """The 'which project?' reply listing a channel's subscribed projects."""
    listing = ", ".join(f"*{e.binding.name}*" for e in entries)
    return (
        "🤔 I'm not sure which project you mean. This channel follows: "
        + listing
        + ".\nMention the project name and I'll dig in."
    )
