from babbla.blocks import DELETE_ACTION_ID, _md_to_mrkdwn, delete_button_blocks, notification_text


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


def test_delete_button_blocks_caps_block_count_for_huge_text():
    # Slack rejects messages with >50 blocks. A very long answer must be capped.
    blocks = delete_button_blocks("line\n" * 50000)   # ~250k chars
    assert len(blocks) <= 50
    assert blocks[-1]["type"] == "actions"            # button survives the cap
    assert all(len(b["text"]["text"]) <= 3000 for b in blocks if b["type"] == "section")


def test_md_to_mrkdwn_headings():
    assert _md_to_mrkdwn("# Title") == "*Title*"
    assert _md_to_mrkdwn("## Sub") == "*Sub*"
    assert _md_to_mrkdwn("### Deep") == "*Deep*"


def test_md_to_mrkdwn_bold():
    assert _md_to_mrkdwn("**bold**") == "*bold*"
    assert _md_to_mrkdwn("__bold__") == "*bold*"


def test_md_to_mrkdwn_strikethrough():
    assert _md_to_mrkdwn("~~gone~~") == "~gone~"


def test_md_to_mrkdwn_links():
    assert _md_to_mrkdwn("[text](https://example.com)") == "<https://example.com|text>"


def test_md_to_mrkdwn_preserves_code_fences():
    # Content inside ``` fences must not be transformed.
    src = "```\n**not bold**\n# not heading\n```"
    assert _md_to_mrkdwn(src) == src



def test_md_to_mrkdwn_plain_text_unchanged():
    assert _md_to_mrkdwn("Because PR #58") == "Because PR #58"


def test_notification_text_short_unchanged_long_truncated():
    # The chat.update `text` fallback is capped by Slack at 40k chars (msg_too_long).
    assert notification_text("Because PR #58") == "Because PR #58"
    long = "x" * 50000
    out = notification_text(long)
    assert len(out) <= 40000
    assert len(out) < len(long)
