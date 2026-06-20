"""Does a STABLE cwd PATH whose contents are wiped between turns still resume?
If yes, the skilled path can use a deterministic per-thread scratch path, wipe
its contents after each ask (recreate next turn), keep resume working, AND keep
artifact capture simple (dir is empty at the start of each turn)."""
import asyncio
import os
import shutil

from claude_agent_sdk import ClaudeAgentOptions, query

MODEL = "claude-haiku-4-5-20251001"
SECRET = "BANANA47"
PLANT = f"Remember this secret word for later: {SECRET}. Reply with just: ok"
ASK = "What exact secret word did I ask you to remember earlier? Reply with just the word, or NONE."

BASE = os.path.join(os.path.dirname(__file__), "_thread_scratch_stable")  # deterministic path


async def turn(prompt: str, cwd: str, resume: str | None = None):
    last, sid = "", resume
    opts = ClaudeAgentOptions(
        model=MODEL, allowed_tools=[], permission_mode="dontAsk",
        mcp_servers={}, cwd=cwd, setting_sources=[],
    )
    if resume:
        opts.resume = resume
    async for m in query(prompt=prompt, options=opts):
        r = getattr(m, "result", None)
        if isinstance(r, str) and r:
            last = r
        s = getattr(m, "session_id", None)
        if s:
            sid = s
    return last, sid


async def main():
    # Turn 1: stable path exists, plant secret, then WIPE its contents (as after an ask).
    os.makedirs(BASE, exist_ok=True)
    open(os.path.join(BASE, "turn1_artifact.txt"), "w").write("from turn 1")
    _, sid = await turn(PLANT, BASE)
    shutil.rmtree(BASE, ignore_errors=True)        # wipe contents after the ask

    # Turn 2: recreate the SAME path (empty), resume the session.
    os.makedirs(BASE, exist_ok=True)
    try:
        reply, sid2 = await turn(ASK, BASE, resume=sid)
        empty = os.listdir(BASE)
        print(f"[STABLE PATH, wiped between turns] recall={SECRET in (reply or '')} "
              f"same_sid={sid == sid2}")
        print(f"    reply={reply!r}")
        print(f"    dir-empty-at-turn2-start verified by: leftover artifacts gone = {'turn1_artifact.txt' not in empty}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
