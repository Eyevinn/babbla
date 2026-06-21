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


import pytest

from babbla.runtime import load_profiles


def test_load_profiles_defaults_to_opus_when_nothing_set():
    ask, clf = load_profiles({})
    assert ask.model == "claude-opus-4-8"
    assert clf.model == "claude-opus-4-8"
    assert ask.effort is None and clf.effort is None
    assert ask.max_turns is None and ask.max_budget_usd is None


def test_load_profiles_babbla_model_is_shared_default():
    ask, clf = load_profiles({"BABBLA_MODEL": "claude-sonnet-4-6"})
    assert ask.model == "claude-sonnet-4-6"
    assert clf.model == "claude-sonnet-4-6"


def test_load_profiles_per_surface_overrides():
    env = {
        "BABBLA_MODEL": "claude-opus-4-8",
        "BABBLA_ASK_EFFORT": "xhigh",
        "BABBLA_ASK_MAX_TURNS": "6",
        "BABBLA_ASK_MAX_BUDGET_USD": "2.5",
        "BABBLA_ASK_FALLBACK_MODEL": "claude-opus-4-7",
        "BABBLA_CLASSIFIER_MODEL": "claude-haiku-4-5",
        "BABBLA_CLASSIFIER_EFFORT": "low",
    }
    ask, clf = load_profiles(env)
    assert ask.model == "claude-opus-4-8"
    assert ask.effort == "xhigh"
    assert ask.max_turns == 6
    assert ask.max_budget_usd == 2.5
    assert ask.fallback_model == "claude-opus-4-7"
    assert clf.model == "claude-haiku-4-5"
    assert clf.effort == "low"


def test_load_profiles_rejects_bad_effort():
    with pytest.raises(RuntimeError, match="EFFORT"):
        load_profiles({"BABBLA_ASK_EFFORT": "turbo"})


def test_load_profiles_rejects_non_int_turns():
    with pytest.raises(RuntimeError, match="MAX_TURNS"):
        load_profiles({"BABBLA_ASK_MAX_TURNS": "lots"})


def test_load_profiles_rejects_non_positive_budget():
    with pytest.raises(RuntimeError, match="MAX_BUDGET_USD"):
        load_profiles({"BABBLA_CLASSIFIER_MAX_BUDGET_USD": "0"})
