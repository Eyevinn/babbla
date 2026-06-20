from babbla.config import ProjectBinding
from babbla.lobby import (
    CatalogEntry,
    build_catalog,
    discovery_reply,
    make_classify_fn,
    pointer_suffix,
    route,
)


def _b(name, visibility="public", channel_id="C1"):
    return ProjectBinding(name, "owner", name, visibility, channel_id, False)


def test_build_catalog_carries_description():
    def get_json(path):
        assert path == "/repos/owner/MyTV"
        return {"description": "Streaming MyTV service"}

    cat = build_catalog([_b("MyTV")], get_json)
    assert cat[0].binding.name == "MyTV"
    assert cat[0].description == "Streaming MyTV service"


def test_build_catalog_degrades_on_fetch_failure():
    def get_json(path):
        raise RuntimeError("github down")

    cat = build_catalog([_b("MyTV")], get_json)
    assert cat[0].description is None  # other entries unaffected by one failure


def test_build_catalog_includes_private_and_blank_description():
    def get_json(path):
        return {"description": "   "} if "Secret" in path else {"description": "desc"}

    cat = build_catalog([_b("MyTV"), _b("Secret", "private")], get_json)
    names = {e.binding.name for e in cat}
    assert names == {"MyTV", "Secret"}              # private included
    secret = next(e for e in cat if e.binding.name == "Secret")
    assert secret.description is None               # blank -> None


async def test_route_exact_name_returns_entry():
    cat = build_catalog([_b("MyTV"), _b("Other")], lambda p: {"description": None})

    async def classify(text, catalog):
        return "MyTV"

    entry = await route("how does playback work?", cat, classify)
    assert entry.binding.name == "MyTV"


async def test_route_none_reply_returns_none():
    cat = build_catalog([_b("MyTV")], lambda p: {"description": None})

    async def classify(text, catalog):
        return "NONE"

    assert await route("unrelated", cat, classify) is None


async def test_route_prose_or_unknown_returns_none():
    cat = build_catalog([_b("MyTV")], lambda p: {"description": None})

    async def classify(text, catalog):
        return "It's probably about MyTV I think"  # name embedded mid-sentence, no clean name line

    assert await route("q", cat, classify) is None


async def test_route_matches_clean_name_line_after_reasoning():
    # Opus 4.8 routinely reasons first, then states the bare name on its own final line.
    cat = build_catalog([_b("MyTV"), _b("Babbla")], lambda p: {"description": None})

    async def classify(text, catalog):
        return "The description is a placeholder, so I'm inferring from the name.\n\nMyTV"

    entry = await route("q", cat, classify)
    assert entry.binding.name == "MyTV"


async def test_route_none_line_after_reasoning_returns_none():
    cat = build_catalog([_b("MyTV")], lambda p: {"description": None})

    async def classify(text, catalog):
        return "This question doesn't match any project.\n\nNONE"

    assert await route("q", cat, classify) is None


async def test_route_matches_multiword_name_with_emphasis_and_punctuation():
    # Bare name line may carry markdown emphasis or trailing punctuation.
    cat = build_catalog([_b("Agentic Engineering Kit", "private", None)], lambda p: {"description": None})

    async def classify(text, catalog):
        return "Reasoning about the match.\n\n**Agentic Engineering Kit.**"

    entry = await route("q", cat, classify)
    assert entry.binding.name == "Agentic Engineering Kit"


async def test_route_prefers_final_conclusion_line():
    # If the model lists candidates then concludes, the final name line wins.
    cat = build_catalog([_b("MyTV"), _b("Babbla")], lambda p: {"description": None})

    async def classify(text, catalog):
        return "Could be MyTV.\nCould be Babbla.\n\nBabbla"

    entry = await route("q", cat, classify)
    assert entry.binding.name == "Babbla"


def test_discovery_reply_lists_open_excludes_private():
    cat = build_catalog(
        [_b("MyTV", channel_id="C1"), _b("Internal", "internal", "C2"), _b("Secret", "private", "C3")],
        lambda p: {"description": None},
    )
    msg = discovery_reply(cat)
    assert "MyTV" in msg and "<#C1>" in msg
    assert "Internal" in msg and "<#C2>" in msg
    assert "Secret" not in msg and "<#C3>" not in msg  # private never advertised


def test_discovery_reply_omits_link_when_no_channel():
    cat = build_catalog([_b("MyTV", channel_id=None)], lambda p: {"description": None})
    msg = discovery_reply(cat)
    assert "MyTV" in msg and "<#" not in msg


def test_pointer_suffix_with_and_without_channel():
    cat = build_catalog([_b("MyTV", channel_id="C1")], lambda p: {"description": None})
    assert "<#C1>" in pointer_suffix(cat[0])
    cat2 = build_catalog([_b("MyTV", channel_id=None)], lambda p: {"description": None})
    assert "<#" not in pointer_suffix(cat2[0])
    assert "MyTV" in pointer_suffix(cat2[0])


async def test_make_classify_fn_returns_model_text():
    class _Msg:
        def __init__(self, result):
            self.result = result
            self.session_id = None

    async def fake_query(*, prompt, options):
        yield _Msg("MyTV")

    cat = build_catalog([_b("MyTV")], lambda p: {"description": "d"})
    classify = make_classify_fn(fake_query, "claude-x")
    assert (await classify("question", cat)).strip() == "MyTV"


async def test_classify_fn_isolated_from_project_context():
    # The classifier must be a pure label-emitter: no CLAUDE.md / project settings,
    # no MCP servers. Otherwise it answers like a full assistant and emits prose.
    captured = {}

    class _Msg:
        def __init__(self, result):
            self.result = result
            self.session_id = None

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield _Msg("MyTV")

    cat = build_catalog([_b("MyTV")], lambda p: {"description": "d"})
    classify = make_classify_fn(fake_query, "claude-x")
    await classify("question", cat)
    opts = captured["options"]
    assert opts.setting_sources == []   # no CLAUDE.md / filesystem settings loaded
    assert not opts.mcp_servers         # no MCP servers
    assert opts.allowed_tools == []     # already tools-less; assert it stays so
