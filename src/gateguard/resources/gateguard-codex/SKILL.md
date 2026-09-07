---
name: gateguard-codex
description: Work in a Codex session protected by GateGuard when `.gateguard.yml`, the GateGuard MCP server, or Codex GateGuard hooks are present. Use the guarded MCP tools so completed investigation is recorded; do not bypass native mutation blocks.
---

# GateGuard for Codex

Use this skill only for a session that is already connected to GateGuard.

1. Check that `.gateguard.yml` exists and that the `gateguard` MCP server is
   available. If either is absent, stop before any mutation and explain that
   `gateguard init --runtime codex` must be run by the operator.
2. Use the `gateguard` MCP tools for reads, searches, globs, writes, and
   commands. Do not retry a blocked native mutation through another native
   tool: the block is the mechanism working.
3. Treat evidence as successful-operation evidence. A failed MCP call is not
   proof that the underlying file or command was inspected.
4. Before reporting completion, run the relevant validation through the
   guarded command tool and, when the work contains mutations, run
   `gateguard audit --verify` through that same tool.

Do not install, trust, disable, or bypass GateGuard hooks. Those actions
change the operator's local Codex configuration and require an explicit
request outside this skill.
