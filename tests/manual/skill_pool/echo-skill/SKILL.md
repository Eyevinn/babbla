---
name: echo-skill
description: Test skill that writes a sentinel file, proving headless skill loading works. Use whenever the user asks you to run the echo skill or prove skills load.
---

# echo-skill

When invoked, use the Write tool to create a file. Set `file_path` to exactly
`ECHO_OK.txt` — a RELATIVE path, with no directory prefix, so it lands in the
process working directory. The file's entire contents must be exactly:

echo-skill ran

Then reply with the single word: done
