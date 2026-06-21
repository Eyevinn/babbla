import asyncio

from babbla import personal
from babbla.personal import (
    Command,
    render_topic_added,
    render_topic_removed,
    render_topic_list,
    render_topic_needs_follow,
    classify_intent,
)


def test_parse_empty_is_list():
    assert personal.parse_command("") == Command("list")
    assert personal.parse_command("   ") == Command("list")


def test_parse_subscribe_and_unsubscribe():
    assert personal.parse_command("subscribe MyTV") == Command("subscribe", "MyTV")
    assert personal.parse_command("unsubscribe MyTV") == Command("unsubscribe", "MyTV")


def test_parse_subscribe_keeps_multiword_project_name():
    assert personal.parse_command("subscribe Agentic Engineering Kit") == Command(
        "subscribe", "Agentic Engineering Kit"
    )
    assert personal.parse_command("unsubscribe Agentic Engineering Kit") == Command(
        "unsubscribe", "Agentic Engineering Kit"
    )


def test_parse_subscribe_without_arg_is_help():
    assert personal.parse_command("subscribe") == Command("help")
    assert personal.parse_command("unsubscribe") == Command("help")


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
    empty = personal.render_list([], "weekly")
    assert "subscribe" in empty.lower()
    assert "/babbla" not in empty          # managed via plain-language DM, not a slash command


def test_render_help_is_plain_language_not_slash_command():
    out = personal.render_help()
    assert "/babbla" not in out
    assert "subscribe" in out.lower()


def test_render_private_and_unknown():
    assert "private" in personal.render_private_refused("Secret").lower()
    assert "MyTV" in personal.render_unknown_project(["MyTV", "Stream"])


def _intent(reply):
    async def fn(text, names):
        return reply
    return fn


async def test_classify_intent_none_reply_is_not_a_command():
    assert await personal.classify_intent("how does auth work?", ["MyTV"], _intent("NONE")) is None


async def test_classify_intent_subscribe():
    assert await personal.classify_intent(
        "follow MyTV for me", ["MyTV"], _intent("subscribe MyTV")
    ) == Command("subscribe", "MyTV")


async def test_classify_intent_subscribe_multiword():
    assert await personal.classify_intent(
        "start following the agentic kit", ["Agentic Engineering Kit"],
        _intent("subscribe Agentic Engineering Kit"),
    ) == Command("subscribe", "Agentic Engineering Kit")


async def test_classify_intent_digest_and_list():
    assert await personal.classify_intent(
        "make my digest daily", [], _intent("digest daily")
    ) == Command("digest", "daily")
    assert await personal.classify_intent(
        "what am I following?", [], _intent("list")
    ) == Command("list")


async def test_classify_intent_unsubscribe():
    assert await personal.classify_intent(
        "stop sending me MyTV", ["MyTV"], _intent("unsubscribe MyTV")
    ) == Command("unsubscribe", "MyTV")


async def test_classify_intent_prose_reply_is_not_a_command():
    # Classifier answered in prose rather than the command grammar → fall through to Q&A.
    assert await personal.classify_intent(
        "hello", [], _intent("This looks like a question about the repo.")
    ) is None


async def test_classify_intent_empty_reply_is_not_a_command():
    assert await personal.classify_intent("hi", [], _intent("")) is None


async def test_classify_intent_strips_backticks():
    assert await personal.classify_intent("show me", [], _intent("`list`")) == Command("list")


async def test_classify_intent_ignores_reasoning_before_command_line():
    reply = "The user wants to set their digest cadence.\n\n`digest daily`"
    assert await personal.classify_intent(
        "make my digest daily", [], _intent(reply)
    ) == Command("digest", "daily")


async def test_classify_intent_none_after_reasoning_is_not_a_command():
    reply = "This reads like a question about the repo, not a subscription change.\nNONE"
    assert await personal.classify_intent("how does X work?", [], _intent(reply)) is None


def test_parse_topic_add():
    cmd = personal.parse_command("topic add MyTV | security | auth, secrets, CVEs")
    assert cmd.verb == "topic-add"
    assert cmd.project == "MyTV"
    assert cmd.name == "security"
    assert cmd.description == "auth, secrets, CVEs"


def test_parse_topic_add_multiword_project_and_desc():
    cmd = personal.parse_command("topic add Agentic Kit | rag | retrieval, embeddings | extra")
    assert cmd.verb == "topic-add"
    assert cmd.project == "Agentic Kit"
    assert cmd.name == "rag"
    assert cmd.description == "retrieval, embeddings"   # only first 3 pipe fields used


def test_parse_topic_remove():
    cmd = personal.parse_command("topic remove MyTV | security")
    assert cmd.verb == "topic-remove"
    assert cmd.project == "MyTV"
    assert cmd.name == "security"


def test_parse_topic_list():
    assert personal.parse_command("topic list").verb == "topic-list"


def test_parse_topic_malformed_is_help():
    assert personal.parse_command("topic add MyTV | security").verb == "help"   # missing description
    assert personal.parse_command("topic remove MyTV").verb == "help"           # missing name
    assert personal.parse_command("topic wat").verb == "help"
    assert personal.parse_command("topic list extra").verb == "help"   # list takes no args
    assert personal.parse_command("topic").verb == "help"              # no subcommand


def test_render_topic_added_shows_name_and_description():
    out = render_topic_added("MyTV", "security", "auth, secrets, CVEs")
    assert "security" in out and "MyTV" in out and "auth, secrets, CVEs" in out


def test_render_topic_removed_shows_project_and_name():
    out = render_topic_removed("MyTV", "security")
    assert "MyTV" in out and "security" in out


def test_render_topic_list_empty_and_grouped():
    assert "no digest topics" in render_topic_list({}).lower()
    grouped = render_topic_list({"MyTV": (("security", "x"), ("playback", "y"))})
    assert "MyTV" in grouped and "security" in grouped and "playback" in grouped


def test_render_topic_needs_follow():
    out = render_topic_needs_follow("MyTV")
    assert "MyTV" in out and "follow" in out.lower()


async def test_classify_intent_maps_topic_add():
    async def fake_intent_fn(text, names):
        return "topic add MyTV | security | auth, secrets, CVEs"
    cmd = await classify_intent("only show me security in MyTV", ["MyTV"], fake_intent_fn)
    assert cmd.verb == "topic-add" and cmd.name == "security"


async def test_make_intent_fn_isolated_and_tuned():
    from babbla.personal import make_intent_fn
    from babbla.runtime import RuntimeProfile

    captured = {}

    class _Msg:
        def __init__(self, result):
            self.result = result
            self.session_id = None

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield _Msg("NONE")

    intent = make_intent_fn(fake_query, RuntimeProfile(model="claude-c", effort="low"))
    await intent("hi", ["MyTV"])
    opts = captured["options"]
    assert opts.allowed_tools == []
    assert opts.mcp_servers == {}        # now isolated (was not before)
    assert opts.setting_sources == []    # now isolated (was not before)
    assert opts.model == "claude-c"
    assert opts.effort == "low"
