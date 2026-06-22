import pytest

from babbla.access import Surface, authorize_ask, authorize_personal, is_open_tier
from babbla.config import ProjectBinding


def _binding(visibility="public", channel_id="C123"):
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", visibility, channel_id, True)


def _b(visibility):
    return ProjectBinding("P", "o", "r", visibility, "C1", False)


def test_is_open_tier_public_and_internal_true():
    assert is_open_tier(_b("public")) is True
    assert is_open_tier(_b("internal")) is True


def test_is_open_tier_private_false():
    assert is_open_tier(_b("private")) is False


@pytest.mark.parametrize("visibility", ["public", "internal", "private"])
def test_channel_surface_always_allows(visibility):
    d = authorize_ask(_binding(visibility), Surface.CHANNEL)
    assert d.allowed is True
    assert d.pointer is None


@pytest.mark.parametrize("visibility", ["public", "internal"])
def test_dm_allows_public_and_internal(visibility):
    assert authorize_ask(_binding(visibility), Surface.DM).allowed is True


def test_dm_denies_private_and_points_to_channel():
    d = authorize_ask(_binding("private", "C123"), Surface.DM)
    assert d.allowed is False
    assert d.reason is not None
    assert "<#C123>" in d.pointer
    assert "MyTV" in d.pointer


def test_dm_denies_private_without_channel_gracefully():
    d = authorize_ask(_binding("private", None), Surface.DM)
    assert d.allowed is False
    assert "<#" not in d.pointer  # no broken channel link
    assert "MyTV" in d.pointer


def test_public_and_internal_decisions_are_identical():
    # Single-workspace: every DM-er is a workspace member, so the tiers must
    # not diverge. Guards the intentional-redundancy comment in access.py.
    pub = authorize_ask(_binding("public"), Surface.DM)
    intern = authorize_ask(_binding("internal"), Surface.DM)
    assert pub == intern


def test_surface_value_roundtrip():
    assert Surface("dm") is Surface.DM
    assert Surface.CHANNEL.value == "channel"


@pytest.mark.parametrize("visibility", ["public", "internal"])
def test_lobby_allows_public_and_internal(visibility):
    assert authorize_ask(_binding(visibility), Surface.LOBBY).allowed is True


def test_lobby_denies_private_and_points():
    d = authorize_ask(_binding("private", "C123"), Surface.LOBBY)
    assert d.allowed is False
    assert "<#C123>" in d.pointer


def test_authorize_personal_open_tier_allows_ignoring_membership():
    d = authorize_personal(_b("public"), is_member=False)
    assert d.allowed is True
    assert d.pointer is None


def test_authorize_personal_private_member_allows():
    d = authorize_personal(_binding("private", "C123"), is_member=True)
    assert d.allowed is True


def test_authorize_personal_private_non_member_denies_with_pointer():
    d = authorize_personal(_binding("private", "C123"), is_member=False)
    assert d.allowed is False
    assert d.reason is not None
    assert "<#C123>" in d.pointer


def test_authorize_personal_private_no_channel_denies_even_if_member():
    d = authorize_personal(_binding("private", None), is_member=True)
    assert d.allowed is False
    assert d.pointer is not None
