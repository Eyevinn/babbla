from babbla import personal
from babbla.personal import Command


def test_parse_empty_is_list():
    assert personal.parse_command("") == Command("list")
    assert personal.parse_command("   ") == Command("list")


def test_parse_subscribe_and_unsubscribe():
    assert personal.parse_command("subscribe MyTV") == Command("subscribe", "MyTV")
    assert personal.parse_command("unsubscribe MyTV") == Command("unsubscribe", "MyTV")


def test_parse_subscribe_without_arg_is_help():
    assert personal.parse_command("subscribe") == Command("help")


def test_parse_list_aliases():
    assert personal.parse_command("list") == Command("list")
    assert personal.parse_command("subscriptions") == Command("list")


def test_parse_digest_valid_and_invalid():
    assert personal.parse_command("digest daily") == Command("digest", "daily")
    assert personal.parse_command("digest off") == Command("digest", "off")
    assert personal.parse_command("digest hourly") == Command("help")
    assert personal.parse_command("digest") == Command("help")


def test_parse_unknown_is_help():
    assert personal.parse_command("frobnicate") == Command("help")


def test_parse_is_case_insensitive_on_verb():
    assert personal.parse_command("SUBSCRIBE MyTV") == Command("subscribe", "MyTV")


def test_render_list_with_and_without_subs():
    assert "MyTV" in personal.render_list(["MyTV"], "weekly")
    assert "weekly" in personal.render_list(["MyTV"], "weekly")
    assert "paused" in personal.render_list(["MyTV"], "off")
    assert "subscribe" in personal.render_list([], "weekly").lower()


def test_render_private_and_unknown():
    assert "private" in personal.render_private_refused("Secret").lower()
    assert "MyTV" in personal.render_unknown_project(["MyTV", "Stream"])
