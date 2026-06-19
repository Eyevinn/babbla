from __future__ import annotations

DELETE_ACTION_ID = "babbla_delete_message"
_SECTION_LIMIT = 3000  # Slack section block text hard cap


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
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": chunk}}
        for chunk in _chunk(text)
    ]
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🗑 Delete", "emoji": True},
                    "action_id": DELETE_ACTION_ID,
                    "value": owner_id or "",
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
