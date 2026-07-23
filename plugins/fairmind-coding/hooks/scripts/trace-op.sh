#!/usr/bin/env bash
# trace-op.sh — PostToolUse hook: append an append-only operations trace of the
# mechanical *what* of a Fairmind session (which tool touched what), so the run
# is reconstructable. Journals stay the narrative *why*; this is the ledger.
#
# Composition rule (same as the other hooks): fast path first — a repo with no
# active Fairmind workspace pays nothing and no trace is written. The hook never
# blocks a tool: every failure path exits 0.
#
# PL-A0 (PCF-16): interactive sessions always trace; in loop mode the trace is
# gated on the loop being LIVE. A stale active-context left pointing at a
# CLOSED/merged/terminal loop must not keep tracing on every unrelated op — that
# is the only no-op. The liveness gate and the locked append+window-safe rotation
# both live in scripts/_loop_ledger.py (resolve_loop_context / append_row, shared
# with capture-subagent-tokens.sh, so the two can never drift). Every row carries
# session_id (from stdin) + mode, and the active ledger is always capped (~2000
# rows): rows older than the loop started_at may roll while a row whose ts >=
# started_at NEVER does (those are read whole mid-loop by run_gate_checks
# settle/mutation-set, insights_flush_payload, loop_dashboard); before the loop
# arms it falls back to a newest-N cap so it stays bounded.
set -uo pipefail

CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"

# Fast path: no active-context.json → not a Fairmind session → do nothing.
[ -f "$CWD/.fairmind/active-context.json" ] || exit 0

# scripts/ dir (holds _loop_ledger.py) resolved from THIS hook's location, so the
# import works both under the installed plugin and when a test invokes the hook
# directly (CLAUDE_PLUGIN_ROOT is not set in the test env).
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$SELF_DIR/../../scripts"

# One python reader: stdin is the PostToolUse JSON; argv carries cwd + agent + scripts dir.
python3 -c '
import json, os, re, sys
from datetime import datetime, timezone

cwd = sys.argv[1]
sys.path.insert(0, sys.argv[3])
try:
    from _loop_ledger import resolve_loop_context, append_row
except Exception:
    sys.exit(0)  # cannot resolve the shared module -> record nothing (never block)

try:
    p = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Liveness gate + mode stamp + window boundary, all from the shared resolver.
# PCF-16: in loop mode, only trace while the loop is LIVE (non-terminal
# loop-state at base_path); interactive sessions always trace.
lc = resolve_loop_context(cwd)
if not lc.live:
    sys.exit(0)
mode = lc.mode

# Attribute the op to the acting agent. Inside a subagent call the PostToolUse
# payload carries agent_type (the subagent name); CLAUDE_AGENT_NAME is not
# propagated there, so without reading it every subagent op would ledger as
# "main". Precedence: payload agent_type -> env CLAUDE_AGENT_NAME -> "main".
agent = p.get("agent_type") or sys.argv[2] or "main"

tool = p.get("tool_name") or "unknown"
ti = p.get("tool_input") or {}

KIND = {
    "Write": "mutate", "Edit": "mutate", "MultiEdit": "mutate", "NotebookEdit": "mutate",
    "Bash": "exec",
    "Task": "dispatch", "Agent": "dispatch",
    "Read": "read", "Grep": "read", "Glob": "read", "LS": "read",
}
kind = KIND.get(tool, "other")

def tr(s, n=120):
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."

target = ""
if isinstance(ti, dict):
    for key in ("file_path", "command", "path", "pattern", "description", "url", "prompt"):
        if ti.get(key):
            raw = str(ti[key])
            # NOTE (no apostrophes in this block: it lives inside a bash
            # single-quoted string, see the wrapping python3 -c call below).
            # A mutate op target is the join key that T18 mutation-set
            # attribution normalizes against git repo-relative paths
            # (realpath+relpath, see run_gate_checks._normalize_trace_target).
            # Unlike a Bash command, Task prompt, description, etc, a
            # filesystem path is the exact-match join key, so truncating it
            # silently destroys attribution (the longest real mutate target
            # seen in this repo trace was within 2 chars of the old 120-char
            # cutoff). Truncation stays cosmetic only: everything except a
            # mutate op still gets tr().
            target = raw if kind == "mutate" else tr(raw)
            break

ref = lc.ref
safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or "session"

trace_dir = os.path.join(cwd, ".fairmind", "trace")
os.makedirs(trace_dir, exist_ok=True)
trace_file = os.path.join(trace_dir, safe + ".jsonl")
rec = {
    "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "session_id": p.get("session_id") or "",
    "mode": mode,
    "agent": agent,
    "tool": tool,
    "kind": kind,
    "target": target,
}
# Append + window-safe cap/rollover as ONE locked unit (shared, best-effort; a
# rotation failure never breaks the hook). Serializing them closes the race where
# a concurrently appended in-window row is clobbered between the rotation snapshot
# and its replace. Rotation rolls ONLY rows older than the loop started_at; a row
# with ts >= started_at is read whole mid-loop and must NEVER be dropped.
# (No apostrophes in this comment: it lives inside the bash single-quoted block.)
append_row(trace_file, json.dumps(rec), lc.started_at)
' "$CWD" "${CLAUDE_AGENT_NAME:-main}" "$SCRIPTS_DIR" || exit 0

exit 0
