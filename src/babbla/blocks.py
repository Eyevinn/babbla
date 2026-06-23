from __future__ import annotations

import re

DELETE_ACTION_ID = "babbla_delete_message"

def _normalize_code_fences(text: str) -> str:
    """Prepare code fences for Slack mrkdwn:
    1. Strip language identifiers (```bash → ```) anywhere they appear.
    2. Ensure every ``` is on its own line — Slack requires it to render as a block.
    """
    text = re.sub(r"```\w+", "```", text)
    # If ``` is not at the start of a line, prepend a newline.
    text = re.sub(r"([^\n])```", r"\1\n```", text)
    return text
_SECTION_LIMIT = 3000  # Slack section block text hard cap
_MAX_BLOCKS = 50       # Slack hard cap on blocks per message
_TEXT_FALLBACK_LIMIT = 3000  # keep the chat.update `text` field well under Slack's 40k cap
_TRUNCATION_NOTE = "_…answer truncated — it was too long to post in full._"


def notification_text(text: str) -> str:
    """A safe `text` fallback for chat.postMessage/chat.update.

    Slack caps the `text` field at 40k characters and rejects anything longer
    with `msg_too_long`. The blocks carry the full (chunked) content, so the
    fallback only needs a short preview. Short answers pass through unchanged.
    """
    text = text or " "
    if len(text) <= _TEXT_FALLBACK_LIMIT:
        return text
    return text[: _TEXT_FALLBACK_LIMIT - 1] + "…"


def _chunk(text: str, limit: int = _SECTION_LIMIT) -> list[str]:
    """Split text into <=limit pieces, preferring line boundaries; a single
    over-long line is hard-split so no chunk ever exceeds the limit."""
    chunks: list[str] = []
    buf = ""
    for line in (text or " ").splitlines(keepends=True):
        while len(line) > limit:  # a single line longer than the cap
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) > limit:
            chunks.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        chunks.append(buf)
    return chunks


def delete_button_blocks(text: str, owner_id: str = "") -> list[dict]:
    """Render text as section block(s) plus a 🗑 Delete button.

    Clicking the button fires DELETE_ACTION_ID and deletes the message it sits on.
    The button's value carries owner_id: when set, only that user may delete (the
    handler enforces it); empty means anyone who sees it may delete.
    """
    chunks = _chunk(_normalize_code_fences(text))
    # Slack rejects messages with more than 50 blocks. Reserve one for the
    # actions (button) block and, when content overflows, one for a truncation
    # note, so the message always posts rather than failing wholesale.
    max_sections = _MAX_BLOCKS - 1
    if len(chunks) > max_sections:
        chunks = chunks[: max_sections - 1] + [_TRUNCATION_NOTE]
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in chunks
    ]
    button: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": "🗑 Delete", "emoji": True},
        "action_id": DELETE_ACTION_ID,
    }
    # Slack rejects an empty button value (invalid_blocks). Only carry a value
    # when there's an owner; an absent value reads as "anyone may delete".
    if owner_id:
        button["value"] = owner_id
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    **button,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Delete this message?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": "This removes Babbla's message. It can't be undone.",
                        },
                        "confirm": {"type": "plain_text", "text": "Delete"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                }
            ],
        }
    )
    return blocks
