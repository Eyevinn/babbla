from babbla.config import ProjectBinding
from babbla.lobby import CatalogEntry
from babbla import subscriptions

A = CatalogEntry(ProjectBinding("MyTV", "o", "MyTV", "public", "C1", False), None)
B = CatalogEntry(ProjectBinding("Stream", "o", "stream", "internal", "C2", False), None)
CATALOG = (A, B)


def test_entries_for_filters_and_orders_by_names():
    assert subscriptions.entries_for(CATALOG, ["Stream", "MyTV"]) == (B, A)


def test_entries_for_skips_unknown_names():
    assert subscriptions.entries_for(CATALOG, ["MyTV", "Ghost"]) == (A,)


def test_entries_for_empty_names_is_empty():
    assert subscriptions.entries_for(CATALOG, []) == ()


def test_subscription_clarify_lists_multiple():
    msg = subscriptions.subscription_clarify((A, B))
    assert "MyTV" in msg and "Stream" in msg


def test_subscription_clarify_single():
    msg = subscriptions.subscription_clarify((A,))
    assert "MyTV" in msg
