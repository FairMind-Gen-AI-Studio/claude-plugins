#!/usr/bin/env bash
# capture-subagent-tokens.sh — SubagentStop hook.
#
# When a dispatched sub-agent finishes, Claude Code fires SubagentStop with the
# sub-agent's own transcript path (`agent_transcript_path`). We read that transcript,
# sum its token usage, and append ONE row to the loop's subagent-token ledger
# (`${base_path}/subagent-tokens.jsonl`) — so the dashboard can show per-loop token
# stats. Capturing at completion (not at render time) is immune to transcript
# retention/cleanup.
#
# Best-effort by construction: the transcript schema is a Claude Code internal, so any
# parse failure degrades silently. Like the other Fairmind hooks: fast path first (no
# active workspace → do nothing), and NEVER block (always exit 0).
#
# PL-A0:
#   - PCF-16 liveness gate + window-safe rotation are shared with trace-op.sh via
#     scripts/_loop_ledger.py (resolve_loop_context / append_row), so the two
#     hooks can never drift. INTERACTIVE sessions (mode != loop) always capture,
#     mirroring check-journal.sh; in loop mode capture is gated on the loop being
#     LIVE (a non-terminal loop-state at base_path). The ONLY no-op is a stale
#     active-context pointing at a closed/merged/terminal loop — that records
#     nothing.
#   - PCF-15 dedup: a streamed message repeats its usage per content block under
#     one message.id, so the naive per-line sum over-counts ~2.7x. Dedup is routed
#     through scripts/_usage_dedup.py (the SINGLE source of truth PL-A1's digester
#     also imports) — never re-implemented inline, so the two can never drift.
#   - Every row carries session_id (from stdin) + mode; the active ledger is
#     always capped (~2000 rows) — window-anchored once the loop is armed (a row
#     with ts >= the loop started_at never rolls) and newest-N before arm.
set -uo pipefail

CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"
CTX="$CWD/.fairmind/active-context.json"

# Fast path: not a Fairmind session → drain stdin and no-op.
[ -f "$CTX" ] || { cat >/dev/null 2>&1; exit 0; }

# scripts/ dir (holds _usage_dedup.py + _loop_ledger.py) resolved from THIS hook's
# location, so the import works both under the installed plugin and when a test
# invokes the hook directly (CLAUDE_PLUGIN_ROOT is not set in the test env).
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$SELF_DIR/../../scripts"

python3 -c '
import json, os, sys
from datetime import datetime, timezone

cwd = sys.argv[1]
sys.path.insert(0, sys.argv[2])
try:
    from _usage_dedup import deduped_usage_totals
    from _loop_ledger import resolve_loop_context, append_row
except Exception:
    sys.exit(0)  # cannot dedup / gate safely -> record nothing (never block)

try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Liveness gate (PCF-16) + mode stamp + ledger home + window boundary, all from
# the shared resolver. Interactive sessions always capture; in loop mode capture
# is gated on the loop being LIVE (a stale/terminal loop is the only no-op).
lc = resolve_loop_context(cwd)
if not lc.live:
    sys.exit(0)
mode, base, ref, started_at = lc.mode, lc.base, lc.ref, lc.started_at

atp = p.get("agent_transcript_path")
if not atp or not os.path.isfile(atp):
    sys.exit(0)

# Stream the transcript once: dedup consumes the generator, which flags whether
# ANY usage block was seen. This preserves the deliberate "usage present but
# zero" (record a zero row) vs "no usage at all" (record nothing) distinction
# without materializing the whole transcript into a list.
state = {"has_usage": False}
def _records(fh):
    for line in fh:
        try:
            o = json.loads(line)
        except Exception:
            continue
        # A transcript line may be valid JSON but NOT an object (null, []); guard
        # before .get() so it never unwinds and discards the whole capture.
        if not isinstance(o, dict):
            continue
        m = o.get("message")
        if isinstance(m, dict) and isinstance(m.get("usage"), dict):
            state["has_usage"] = True
        yield o

try:
    with open(atp, encoding="utf-8") as fh:
        totals = deduped_usage_totals(_records(fh))
except Exception:
    sys.exit(0)
if not state["has_usage"]:
    sys.exit(0)  # no usage in the transcript -> nothing trustworthy to record

out_dir = os.path.join(cwd, base) if base else os.path.join(cwd, ".fairmind")
led = os.path.join(out_dir, "subagent-tokens.jsonl")
try:
    os.makedirs(out_dir, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "session_id": p.get("session_id") or "",
        "mode": mode,
        "agent_id": p.get("agent_id"),
        "agent_type": p.get("agent_type"),
        "task_ref": ref,
        "in": totals["input_tokens"],
        "out": totals["output_tokens"],
        "cache_creation": totals["cache_creation_input_tokens"],
        "cache_read": totals["cache_read_input_tokens"],
    }
    # Append + window-safe cap/rollover as ONE locked unit (shared, best-effort):
    # a concurrent fire cannot clobber this in-window row, and rotation rolls ONLY
    # rows older than the loop started_at, never a row with ts >= started_at.
    append_row(led, json.dumps(rec), started_at)
except Exception:
    sys.exit(0)
' "$CWD" "$SCRIPTS_DIR" || exit 0

exit 0
