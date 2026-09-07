#!/usr/bin/env bash
set -euo pipefail

# Opt-in real-session validation. It creates and removes only its own fixture.
fixture="$(mktemp -d "${TMPDIR:-/tmp}/gateguard-codex-e2e.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
mkdir -p "$fixture/src"
git -C "$fixture" init -q
printf 'value = 1\n' > "$fixture/src/value.py"
git -C "$fixture" add .
git -C "$fixture" -c user.name=GateGuard -c user.email=gateguard@example.invalid commit -qm fixture

gateguard init "$fixture" --runtime codex
codex exec -C "$fixture" \
  "Change src/value.py so value is 2. This is a GateGuard-protected session: use its MCP tools, run a focused validation, then verify the GateGuard audit trail."

grep -qx 'value = 2' "$fixture/src/value.py"
GATEGUARD_STATE_DIR="${GATEGUARD_STATE_DIR:-$HOME/.gateguard}" gateguard audit --verify
printf 'GateGuard Codex E2E passed: %s\n' "$fixture"
