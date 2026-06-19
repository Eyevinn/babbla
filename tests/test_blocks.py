from babbla.blocks import DELETE_ACTION_ID, delete_button_blocks


def _button(blocks):
    actions = [b for b in blocks if b["type"] == "actions"]
    assert len(actions) == 1
    return actions[0]["elements"][0]


def test_delete_button_blocks_renders_text_and_button():
    blocks = delete_button_blocks("Because PR #58")
    section_text = "".join(b["text"]["text"] for b in blocks if b["type"] == "section")
    assert "Because PR #58" in section_text
    btn = _button(blocks)
    assert btn["action_id"] == DELETE_ACTION_ID
    assert "Delete" in btn["text"]["text"]


def test_delete_button_embeds_owner_as_value():
    assert _button(delete_button_blocks("x", owner_id="U123"))["value"] == "U123"
    # No owner -> the value key is OMITTED, not set to "". Slack rejects an
    # empty button value (invalid_blocks), and the handler reads an absent
    # value as "" = "anyone may delete".
    btn = _button(delete_button_blocks("x"))
    assert "value" not in btn
    assert (btn.get("value") or "") == ""  # handler's _delete_owner semantics


def test_delete_button_has_confirm_dialog():
    confirm = _button(delete_button_blocks("x"))["confirm"]
    assert confirm["title"]["text"]
    assert confirm["confirm"]["text"]
    assert confirm["deny"]["text"]


def test_delete_button_is_not_danger_style():
    # Neutral button (confirm dialog provides the safety), not the red danger style.
    assert _button(delete_button_blocks("x")).get("style") != "danger"


def test_delete_button_blocks_chunks_long_text():
    long = "x" * 7000
    blocks = delete_button_blocks(long)
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) >= 3
    assert all(len(b["text"]["text"]) <= 3000 for b in sections)
    assert "".join(b["text"]["text"] for b in sections) == long
