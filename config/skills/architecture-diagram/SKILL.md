---
name: architecture-diagram
description: Draw a project's architecture as a self-contained HTML+SVG file from its repository, using only read-only GitHub access. Use when asked to diagram, draw, or visualize a service's components and data flow.
---

# Architecture diagram (read-only)

Produce a polished dark-themed architecture diagram as a single self-contained
HTML file written into the current working directory.

## Steps

1. Explore the repository over the GitHub tools only (README, files under
   `src/`, `docs/`, ADRs). Do not attempt any write, and do not assume a local
   checkout exists.
2. Identify the major components and the data/control flow between them.
3. Write ONE file `architecture.html` into the current working directory: an
   inline `<style>` + inline `<svg>` (no external assets, no network) showing
   the components as boxes and the flows as labelled arrows.
4. Reply with a 2-3 sentence summary of the architecture. Do not paste the HTML
   into the reply.

Keep the file self-contained: all CSS inline, all geometry inline SVG.
