#!/usr/bin/env bash
# session-start-insights.sh — SessionStart hook (PL-A1a + PL-A1b).
#
# The OPT-scoped ambient telemetry GATE, evaluated FRESH every session (outside
# loop mode). It shells to scripts/_insights_session.py, which:
#   - fail-CLOSES unless this repo has a PER-PROJECT Fairmind MCP configured (a
#     user-global Fairmind entry MUST NOT arm capture — plan V7 "no honest
#     tenant", the privacy guard) and is not opted out (repo-root
#     .fairmind-insights.json OR per-user ~/.fairmind/insights-config.json);
#   - on capture, registers the session (opaque tenancy id ONLY — no raw
#     path/branch) and, the FIRST time only per (user, repo), emits the one-time
#     consent notice as strict JSON {"systemMessage": ...} on stdout — the HUMAN
#     channel a SessionStart hook makes visible before the first turn.
#
# PL-A1b: after the foreground gate above returns, this hook ALSO spawns the
# ambient DIGESTER's sweep (`_insights_session.run_sweep`, via `--sweep`) —
# detached (`&`) and niced (`nice -n 10`) so it never blocks or slows session
# open. The sweep finalizes any of this tenancy's crash-orphans (an ended
# session whose digest never ran, e.g. the process died mid-digest); a session
# with a live digester already holding its per-session lock is skipped, never
# reaped. The same payload is fed to both invocations, so stdin is drained
# ONCE into a variable rather than consumed twice.
#
# Fail-open + fast (SPIKE-A): a SessionStart hook must NEVER block or slow session
# open. Fast path first (module absent -> drain stdin, exit 0); the gate is a
# couple of cheap file reads + one `git rev-parse`; every path exits 0.
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
MODULE="$SELF_DIR/../../scripts/_insights_session.py"

# Fast path: module missing -> not installed -> drain stdin and no-op.
[ -f "$MODULE" ] || { cat >/dev/null 2>&1; exit 0; }

PAYLOAD="$(cat)"

printf '%s' "$PAYLOAD" | python3 "$MODULE" --session-start || true

# Detached + niced ambient digester sweep (PL-A1b). Backgrounded before this
# script exits; a non-interactive script's background children are not sent
# SIGHUP on the parent's exit, so this keeps running after the hook returns.
printf '%s' "$PAYLOAD" | nice -n 10 python3 "$MODULE" --sweep >/dev/null 2>&1 &

exit 0
