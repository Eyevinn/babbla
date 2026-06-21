from babbla.runtime import RuntimeProfile, tuning_kwargs


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
