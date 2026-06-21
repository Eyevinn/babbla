from babbla.runtime import RuntimeProfile, tuning_kwargs, classifier_options


def test_tuning_kwargs_empty_for_default_profile():
    assert tuning_kwargs(RuntimeProfile()) == {}


def test_tuning_kwargs_omits_none_and_never_includes_model():
    p = RuntimeProfile(model="claude-haiku-4-5", effort="low")
    kw = tuning_kwargs(p)
    assert kw == {"effort": "low"}
    assert "model" not in kw  # model is keyed separately at the call-site


def test_tuning_kwargs_includes_all_set_knobs():
    p = RuntimeProfile(
        model="m", effort="xhigh", fallback_model="claude-opus-4-8",
        max_turns=4, max_budget_usd=1.5,
    )
    assert tuning_kwargs(p) == {
        "effort": "xhigh",
        "fallback_model": "claude-opus-4-8",
        "max_turns": 4,
        "max_budget_usd": 1.5,
    }


def test_classifier_options_structural_isolation():
    opts = classifier_options(RuntimeProfile(), "sys prompt")
    assert opts.system_prompt == "sys prompt"
    assert opts.allowed_tools == []     # tools-less
    assert opts.mcp_servers == {}       # no MCP servers
    assert opts.setting_sources == []   # no CLAUDE.md / host settings
    assert opts.model == "claude-opus-4-8"  # DEFAULT_MODEL
    # inert: no tuning knobs set on a default profile
    assert opts.effort is None
    assert opts.max_turns is None


def test_classifier_options_applies_profile_tuning():
    p = RuntimeProfile(model="claude-haiku-4-5", effort="low", max_turns=1)
    opts = classifier_options(p, "sys")
    assert opts.model == "claude-haiku-4-5"
    assert opts.effort == "low"
    assert opts.max_turns == 1
