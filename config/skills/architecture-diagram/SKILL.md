---
name: architecture-diagram
description: Draw a project's architecture as a Markdown file with a Mermaid diagram, from its repository using only read-only GitHub access. Use when asked to diagram, draw, or visualize a service's components and data flow.
---

# Architecture diagram (read-only)

Produce a Markdown file with a Mermaid diagram showing the project's
components and data flow.

## Steps

1. Explore the repository over the GitHub tools only (README, files under
   `src/`, `docs/`, ADRs). Do not attempt any write, and do not assume a local
   checkout exists.
2. Identify the major components and the data/control flow between them.
3. Write ONE file `architecture.md` into the current working directory. It
   must contain:
   - A `# Architecture` heading and one short paragraph describing the system.
   - A fenced `mermaid` code block containing a `graph TD` or `graph LR`
     diagram — nodes for each major component, labelled edges for the
     data/control flows between them.
   - A brief legend section listing each node with a one-sentence description.
4. Reply with a 2-3 sentence summary of the architecture. Do not paste the
   Markdown into the reply.
