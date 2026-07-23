#!/usr/bin/env python3
"""
insights_flush_payload.py — Agentic Insights terminal-flush payload builder (PL-4).

`/fairmind-loop`'s Exit gate, on a closed loop, assembles two payloads for the
`Insights_record_loop_stats` / `Insights_record_agent_decisions` MCP tools and
hands them to project-context, which owns persistence (Mongo -> Neo4j +
fairmind-telemetry). This script is the assembly step: it reads the closed
loop's own on-disk artifacts (loop-state.json, the loop ledger, the trace
ledger, the sub-agent token ledger, the PL-3 decisions log) and builds the
exact wire shape each MCP tool expects — nothing more, since the server
injects its own `userId`/`company`/`rawPayload`.

A re-flush of an unchanged close must be a no-op, so a small on-disk cursor
(`.fairmind/insights-sync.json`) tracks what has already gone out: one entry
per flushed `loop_id` (keyed to the `closed_at` it was flushed at, so a
REVIVED loop's later close is pending again) and one entry per flushed
`decisionId`. Nothing here calls the network — `main()` only emits the
pending payloads (or `null`) so the command body can call the MCP tools
itself and then `--commit` what it actually sent.

Path contract (deliberately mixed fixed vs. base-relative — see the PL-4
dispatch): `loop-state.json` and `subagent-tokens.jsonl` live under the
loop's OWN `base_path` (resolved from `.fairmind/active-context.json` when
not given explicitly); the loop ledger, the trace ledger, the decisions log,
and the sync cursor are FIXED under `.fairmind/` regardless of `base_path` —
they are per-repo, not per-loop.

Never reads the wall clock: every field here is derived from what is already
on disk, so `build_loop_payload`/`build_decisions_batch` are byte-identical
across repeated calls on an unchanged tree (loop-mode determinism gates on
exactly this). Stdlib only.
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_run_meta  # noqa: E402 — reused for normalize_git_remote + git helpers + atomic write
import loop_ledger  # noqa: E402 — reused for the ledger path/reader + the loop_id key formula
from loop_tokens import _parse_iso, _FIELDS as _TOKEN_FIELDS  # noqa: E402 — the reader loop_tokens uses on subagent-tokens.jsonl

LOOP_CONTRACT_VERSION = "fm-insights.loop/1"
DECISION_CONTRACT_VERSION = "fm-insights.decision/1"
AUDIT_CONTRACT_VERSION = "fm-insights.audit/1"

# Display name (agent_type / trace `agent` field) -> {slug, model_id}. Pinned
# 1:1 with the PL-4 dispatch; the test suite asserts this table verbatim.
AGENT_ROLE_MAP = {
    "Technical Lead / Architect": {"slug": "technical-lead", "model_id": "claude-opus-4-8"},
    "Software Engineer": {"slug": "software-engineer", "model_id": "claude-sonnet-5"},
    "QA Engineer": {"slug": "qa-engineer", "model_id": "claude-sonnet-5"},
    "Code Reviewer": {"slug": "code-reviewer", "model_id": "claude-sonnet-5"},
    "Security Engineer": {"slug": "security-engineer", "model_id": "claude-sonnet-5"},
    "Debugging Specialist": {"slug": "debugging-specialist", "model_id": "claude-sonnet-5"},
}

# ---------------------------------------------------------------------------
# Small shared helpers. sanitize_ref mirrors hooks/scripts/trace-op.sh:75 —
# genuinely cross-language (the bash writer cannot be imported). _parse_iso /
# _TOKEN_FIELDS (imported above), the atomic cursor write, and the ledger
# reader + loop_id formula are IMPORTED from the same-dir stdlib siblings
# (loop_tokens / audit_run_meta / loop_ledger) so the two readers of a shared
# file (subagent-tokens.jsonl) and the two spellings of the loop_id cursor key
# can never drift.
# ---------------------------------------------------------------------------

def sanitize_ref(ref):
    """Byte-identical to hooks/scripts/trace-op.sh:75 — the trace filename
    for a given task_ref/agent display name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or "session"


def normalize_agent(agent_type):
    """(slug, model_id) for a display name, via AGENT_ROLE_MAP; an unmapped
    name falls back to (sanitize_ref(name).lower(), "unknown") so an unknown
    agent still gets a stable, filesystem-safe slug instead of failing."""
    entry = AGENT_ROLE_MAP.get(agent_type)
    if entry:
        return entry["slug"], entry["model_id"]
    return sanitize_ref(agent_type).lower(), "unknown"


def _read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return rows  # an unreadable ledger degrades to empty, like the sibling readers
    try:
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # a corrupt line never breaks the flush
    except (OSError, UnicodeError):
        pass  # corrupt bytes mid-file degrade to whatever rows parsed so far
    return rows


def _as_dict(x):
    """`x` if it is a dict, else `{}` — a read-boundary guard so a
    valid-JSON-but-wrong-shape value (a bare list/scalar top level, or a
    non-dict nested field) degrades instead of raising on the next `.get`."""
    return x if isinstance(x, dict) else {}


# ---------------------------------------------------------------------------
# Path resolution — base_path is per-loop, everything else is fixed per-repo.
# ---------------------------------------------------------------------------

def _active_context(cwd):
    return _as_dict(_read_json(os.path.join(cwd, ".fairmind", "active-context.json"), {}))


def _resolve_base(cwd, base=None):
    if base:
        return base
    return _active_context(cwd).get("base_path") or ".fairmind"


def _trace_path(cwd, ref):
    return os.path.join(cwd, ".fairmind", "trace", sanitize_ref(ref) + ".jsonl")


def _decisions_path(cwd):
    return os.path.join(cwd, ".fairmind", "insights", "decisions.jsonl")


def _cursor_path(cwd):
    return os.path.join(cwd, ".fairmind", "insights-sync.json")


def _audit_run_meta_path(cwd):
    return os.path.join(cwd, audit_run_meta.DEFAULT_OUT)


def _audit_summary_path(cwd):
    return os.path.join(cwd, ".fairmind", "audit", "summary.json")


# ---------------------------------------------------------------------------
# Loop payload
# ---------------------------------------------------------------------------

def _ledger_row_for(cwd, target_ref):
    """The LAST loop-ledger.jsonl row whose `task` == target_ref, or None.
    Reads via loop_ledger (which owns the ledger path + reader) so the flush
    and the ledger can never disagree on the file or its parse."""
    rows = [r for r in loop_ledger._read_rows(cwd) if r.get("task") == target_ref]
    return rows[-1] if rows else None


def _loop_identity(cwd, state):
    """(loop_id, closed_at) for the closed loop this state names — the loop's
    identity, cheaply: the LAST matching ledger row on the happy path, else the
    loop_ledger._loop_id(state) fallback (the SAME formula the ledger row was
    written with, so both paths key the cursor identically) with closed_at
    falling back to started_at. Touches only loop-state + the ledger, so a
    --commit needn't rebuild the whole payload just to key the cursor.
    `state` is re-coerced here (every call site already reads it via
    _as_dict, but this keeps the function safe to call standalone) so a
    non-dict `target`/`budget` degrades instead of raising."""
    state = _as_dict(state)
    target_ref = _as_dict(state.get("target")).get("ref")
    row = _ledger_row_for(cwd, target_ref)
    if row is not None:
        return row.get("loop_id"), row.get("closed_at")
    started_at = _as_dict(_as_dict(state.get("budget")).get("spent")).get("started_at")
    return loop_ledger._loop_id(state), started_at


def _loop_agents(cwd, base, started_at, closed_at, trace_rows):
    """One entry per distinct agent_type seen in <base>/subagent-tokens.jsonl
    rows whose `ts` falls in [started_at, closed_at], token-summed over that
    window and enriched with a trace-derived tool-call count windowed the
    SAME way — a revived loop's shared token/trace files would otherwise leak
    stats from outside this close into the count."""
    start = _parse_iso(started_at)
    end = _parse_iso(closed_at)
    tokens_path = os.path.join(cwd, base, "subagent-tokens.jsonl")

    def _in_window(ts_raw):
        ts = _parse_iso(ts_raw)
        return ts is not None and start is not None and end is not None and start <= ts <= end

    sums = {}  # agent_type -> {field: total}
    for row in _read_jsonl(tokens_path):
        if not isinstance(row, dict):
            continue  # a malformed ledger row never aborts the whole rollup
        agent_type = row.get("agent_type")
        if not agent_type or not _in_window(row.get("ts")):
            continue
        tot = sums.setdefault(agent_type, {k: 0 for k in _TOKEN_FIELDS})
        for k in _TOKEN_FIELDS:
            value = row.get(k, 0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                tot[k] += value  # a non-numeric field is skipped rather than crashing the sum

    tool_calls = {}
    for row in trace_rows:
        agent_type = row.get("agent")
        if agent_type and _in_window(row.get("ts")):
            tool_calls[agent_type] = tool_calls.get(agent_type, 0) + 1

    agents = []
    for agent_type, tot in sums.items():
        slug, model_id = normalize_agent(agent_type)
        agents.append({
            "agentRole": slug,
            "modelId": model_id,
            "inputTokens": tot["in"],
            "outputTokens": tot["out"],
            "cacheReadTokens": tot["cache_read"],
            "cacheCreationTokens": tot["cache_creation"],
            "toolCalls": tool_calls.get(agent_type, 0),
        })
    agents.sort(key=lambda a: a["agentRole"])
    return agents


def build_loop_payload(cwd, base=None):
    """Assemble the `Insights_record_loop_stats` wire payload for the closed
    loop this repo's active-context.json / base_path currently names.
    Deterministic: every field is read off disk, nothing from the clock."""
    base = _resolve_base(cwd, base)
    ctx = _active_context(cwd)

    state = _as_dict(_read_json(os.path.join(cwd, base, "loop-state.json"), {}))
    target_ref = _as_dict(state.get("target")).get("ref")
    spent = _as_dict(_as_dict(state.get("budget")).get("spent"))
    started_at = spent.get("started_at")

    loop_id, closed_at = _loop_identity(cwd, state)

    task_ref = ctx.get("task_ref") or target_ref
    trace_rows = _read_jsonl(_trace_path(cwd, task_ref))
    artifacts = sorted({r.get("target") for r in trace_rows
                        if r.get("kind") == "mutate" and r.get("target")})

    checks = state.get("checks")
    iterations = spent.get("iterations")

    return {
        "loop_id": loop_id,
        "target_ref": target_ref,
        "status": state.get("status"),
        "tier": state.get("hermeticity_tier", "B"),
        "checks": len(checks) if isinstance(checks, list) else 0,
        "iter": iterations if isinstance(iterations, int) and not isinstance(iterations, bool) else 0,
        "started_at": started_at,
        "closed_at": closed_at,
        "owner_session": state.get("owner_session"),
        "project_id": ctx.get("projectId", "unknown"),
        "task_ref": task_ref,
        "artifacts": artifacts,
        "agents": _loop_agents(cwd, base, started_at, closed_at, trace_rows),
        "contract_version": LOOP_CONTRACT_VERSION,
    }


# ---------------------------------------------------------------------------
# Decisions batch
# ---------------------------------------------------------------------------

def _repository_name(cwd):
    toplevel = audit_run_meta._run_git(cwd, "rev-parse", "--show-toplevel")
    if toplevel.returncode == 0:
        return os.path.basename(os.path.normpath(toplevel.stdout.strip()))
    return os.path.basename(os.path.abspath(cwd))


def _git_remote(cwd):
    remote = audit_run_meta._run_git(cwd, "remote", "get-url", "origin")
    if remote.returncode != 0:
        return None
    return audit_run_meta.normalize_git_remote(remote.stdout.strip())


def _decision_id(row):
    """Stable id: same (agent, at, decision, rationale) -> same id, always —
    so a re-flush of an unchanged decisions.jsonl never produces new ids."""
    key = [row.get("agent"), row.get("at"), row.get("decision"), row.get("rationale")]
    digest = hashlib.sha256(
        json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return "sha256:" + digest


def _decision_wire_row(row):
    return {
        "decisionId": _decision_id(row),
        "ts": row.get("at"),
        "agent": row.get("agent"),
        "kind": "process",
        "title": row.get("decision"),
        "rationale": row.get("rationale"),
        "files": [],
        "functions": None,
    }


def build_decisions_batch(cwd, base=None):
    """Assemble the `Insights_record_agent_decisions` wire payload from the
    PL-3 decision-capture convention's `.fairmind/insights/decisions.jsonl`."""
    base = _resolve_base(cwd, base)
    ctx = _active_context(cwd)
    state = _as_dict(_read_json(os.path.join(cwd, base, "loop-state.json"), {}))

    rows = _read_jsonl(_decisions_path(cwd))
    return {
        "repository": _repository_name(cwd),
        "decisions": [_decision_wire_row(r) for r in rows if isinstance(r, dict)],
        "git_remote": _git_remote(cwd),
        "session_ref": state.get("owner_session"),
        "task_ref": ctx.get("task_ref"),
        "contract_version": DECISION_CONTRACT_VERSION,
    }


# ---------------------------------------------------------------------------
# Audit payload — this builder OWNS the `Insights_record_harness_audit` wire
# contract; `commands/harness-audit.md` step 5 delegates to it (via `--emit
# audit`/`--commit audit`) rather than hand-assembling the payload itself.
# Assembled from the two FIXED per-repo files that command writes:
# `.fairmind/audit/run-meta.json` (repo identity) and
# `.fairmind/audit/summary.json` (criteria results). Unlike the loop/decisions
# builders these paths are NOT base-relative — an audit run is per-repo, not
# per-loop, same as the sync cursor itself.
# ---------------------------------------------------------------------------

def _audit_pillar_wire(p):
    # `name` is REQUIRED by the consumer: the server dispatches a pillar carrying
    # `criteria_passed`/`criteria_total` into `_normalize_plugin_pillars`, which reads
    # `pillar['name']` and raises without it. It was dropped here while the server's
    # own docstring quoted `harness_audit.py::evaluate_catalog` (which has it) as the
    # producer — the shape that crosses the wire is the one this function builds.
    return {"id": p.get("id"), "name": p.get("name"), "level": p.get("level"),
            "criteria_passed": p.get("criteria_passed"), "criteria_total": p.get("criteria_total")}


def _audit_dimension_wire(dim):
    return {"id": dim.get("id"), "score": dim.get("score"), "status": dim.get("status")}


def _audit_missing_advisory(path):
    """The one-line 'run /harness-audit first' advisory for a missing audit
    source. Only the explicit `--emit audit` request raises it (via the
    `advise` flag below); the bulk `all` path and `commit()` stay silent,
    because a null audit run is the normal case there — a `/fairmind-loop`
    close has no run-meta, and `/fairmind-sync-insights` runs `--emit all` on
    every repo whether or not `/harness-audit` was ever run."""
    print(f"insights_flush_payload: no {path} — audit category degraded to null "
          "(run /harness-audit first)", file=sys.stderr)


def build_audit_payload(cwd, advise=False):
    """The `Insights_record_harness_audit` wire payload for the audit run
    currently on disk, or None when either source file is missing/unreadable.
    A pure disk→wire assembler like the loop/decisions builders: silent by
    default, printing the missing-source advisory to stderr only when `advise`
    is set (the explicit `--emit audit` path). Reads only disk, never the
    clock, so a re-flush of an unchanged audit run is byte-identical across
    calls."""
    run_meta_path = _audit_run_meta_path(cwd)
    run_meta_raw = _read_json(run_meta_path)
    if run_meta_raw is None:
        if advise:
            _audit_missing_advisory(run_meta_path)
        return None
    run_meta = _as_dict(run_meta_raw)

    summary_path = _audit_summary_path(cwd)
    summary_raw = _read_json(summary_path)
    if summary_raw is None:
        if advise:
            _audit_missing_advisory(summary_path)
        return None
    summary = _as_dict(summary_raw)

    totals = _as_dict(summary.get("totals"))
    pillars = summary.get("pillars")
    dimensions = summary.get("dimensions")

    payload = {
        # snake_case, matching this module's own loop/decisions builders and the
        # server's `contract_version` parameter. `source` is NOT sent: the server
        # owns provenance (it labels its own code-ingestion path), and the MCP tool
        # exposes no such parameter, so sending it was rejected outright.
        "contract_version": AUDIT_CONTRACT_VERSION,
        "repository": run_meta.get("repo_name"),
        "commit_sha": run_meta.get("commit_sha"),
        "executed_at": run_meta.get("executed_at"),
        "criteria_version": summary.get("criteria_version"),
        "totals": {"criteria": totals.get("criteria"), "passed": totals.get("passed")},
        "pillars": [_audit_pillar_wire(p) for p in (pillars if isinstance(pillars, list) else [])
                    if isinstance(p, dict)],
    }
    if run_meta.get("git_remote") is not None:
        payload["git_remote"] = run_meta["git_remote"]
    if isinstance(dimensions, list):
        payload["dimensions"] = [_audit_dimension_wire(dm) for dm in dimensions if isinstance(dm, dict)]
    return payload


def _audit_key_from_payload(payload):
    """The cursor key for an audit payload: `<commit_sha>@<executed_at>` — same
    spelling convention as the loop cursor's `loop_id`, so re-running the audit
    at a different commit or a later timestamp is pending again."""
    commit_sha = payload.get("commit_sha")
    executed_at = payload.get("executed_at")
    if commit_sha is None or executed_at is None:
        return None
    return f"{commit_sha}@{executed_at}"


def _audit_unkeyable_advisory():
    """The 'identity keys missing' advisory for a run-meta.json that EXISTS
    and parses fine but lacks `commit_sha`/`executed_at`, so
    `_audit_key_from_payload` cannot form a cursor key. Without this, that
    case is silently indistinguishable from "already flushed" (null, empty
    stderr) though nothing was ever flushed and there is no cursor entry to
    delete to recover it. Same explicit-only gating as
    `_audit_missing_advisory`: only the explicit `--emit audit` request (the
    `advise` flag, forwarded from `pending_audit`) raises it; `--emit all` /
    `commit()` stay silent, since a null audit run is the normal case there."""
    print("insights_flush_payload: run-meta.json lacks commit_sha/executed_at "
          "— audit run unkeyable; re-run /harness-audit", file=sys.stderr)


# ---------------------------------------------------------------------------
# Cursor — {"loops": {loop_id: closed_at}, "decisions": {decisionId: true},
#           "audits": {commit_sha@executed_at: true}}
# ---------------------------------------------------------------------------

def read_cursor(cwd):
    return _read_json(_cursor_path(cwd), {}) or {}


def pending_loop(cwd, base=None):
    """The loop payload if this close has not yet been flushed (absent from
    the cursor, or flushed at an earlier `closed_at` — a revived loop's later
    close is pending again); else None."""
    payload = build_loop_payload(cwd, base)
    cursor = read_cursor(cwd)
    committed_closed_at = (cursor.get("loops") or {}).get(payload["loop_id"])
    if committed_closed_at is None:
        return payload
    committed = _parse_iso(committed_closed_at)
    current = _parse_iso(payload["closed_at"])
    if committed is not None and current is not None:
        return payload if current > committed else None
    # Unparseable timestamps: fall back to a straight inequality rather than
    # silently treating this close as already flushed.
    return payload if committed_closed_at != payload["closed_at"] else None


def pending_decisions(cwd, base=None):
    """The decisions batch filtered to rows not yet in the cursor, or None
    when every row is already committed (including an empty source log)."""
    batch = build_decisions_batch(cwd, base)
    committed = (read_cursor(cwd).get("decisions") or {})
    pending = [d for d in batch["decisions"] if d["decisionId"] not in committed]
    if not pending:
        return None
    batch["decisions"] = pending
    return batch


def pending_audit(cwd, advise=False):
    """The audit payload currently on disk if it has not yet been flushed
    (absent from the cursor's `audits` map, keyed `<commit_sha>@<executed_at>`);
    else None. Also None when the source files are missing (degraded, see
    `build_audit_payload`) — a missing run-meta.json is never "pending" — or
    when a PRESENT run-meta.json is unkeyable (missing `commit_sha`/
    `executed_at`, so no cursor key can be formed). `advise` is forwarded to
    the builder so only the explicit `--emit audit` request surfaces the
    missing-source advisory, and gates the unkeyable-source advisory here the
    same way."""
    payload = build_audit_payload(cwd, advise=advise)
    if payload is None:
        return None
    key = _audit_key_from_payload(payload)
    if key is None:
        if advise:
            _audit_unkeyable_advisory()
        return None
    committed = (read_cursor(cwd).get("audits") or {})
    if key in committed:
        return None
    return payload


def commit(cwd, base=None, loop=True, decisions=True, audit=True):
    """Atomically merge the current loop/decisions/audit state into the
    cursor. Never clobbers a category not being committed. For loop and
    decisions it derives only the keys it records — the loop identity, the
    decision ids — rather than rebuilding the whole payload (which would
    re-read the token/trace ledgers and spawn git just to discard everything
    but those keys). The audit key comes from the full `build_audit_payload`
    (silent here), which is only two small local JSON reads — no ledgers, no
    git — and reusing it keeps the emittability guard (the cursor advances
    only when the run is fully emittable, exactly what `--emit audit` would
    send) in one place."""
    cursor = read_cursor(cwd)
    loops = dict(cursor.get("loops") or {})
    committed_decisions = dict(cursor.get("decisions") or {})
    committed_audits = dict(cursor.get("audits") or {})

    if loop:
        base_r = _resolve_base(cwd, base)
        state = _as_dict(_read_json(os.path.join(cwd, base_r, "loop-state.json"), {}))
        loop_id, closed_at = _loop_identity(cwd, state)
        loops[loop_id] = closed_at
    if decisions:
        for row in _read_jsonl(_decisions_path(cwd)):
            if not isinstance(row, dict):
                continue  # a malformed decision row is skipped, not committed as an id
            committed_decisions[_decision_id(row)] = True
    if audit:
        payload = build_audit_payload(cwd)
        if payload is not None:
            key = _audit_key_from_payload(payload)
            if key is not None:
                committed_audits[key] = True

    audit_run_meta._atomic_write_json(
        _cursor_path(cwd),
        {"loops": loops, "decisions": committed_decisions, "audits": committed_audits})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _categories(spec):
    return {"loop", "decisions", "audit"} if spec == "all" else {spec}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Assemble (and track flush state for) the Agentic Insights "
                    "terminal-flush payloads for a closed /fairmind-loop run "
                    "or a /harness-audit run."
    )
    parser.add_argument("--cwd", default=None, help="repo root (default: process cwd)")
    parser.add_argument("--base", default=None,
                         help="loop base_path (default: resolved from active-context.json)")
    parser.add_argument("--emit", choices=["loop", "decisions", "audit", "all"],
                         help="print {\"loop\": ..., \"decisions\": ..., \"audit\": ...} "
                              "restricted to these categories")
    parser.add_argument("--commit", choices=["loop", "decisions", "audit", "all"],
                         help="mark these categories' current payload as flushed")
    args = parser.parse_args(argv)

    cwd = args.cwd or os.getcwd()

    if args.emit:
        cats = _categories(args.emit)
        out = {
            "loop": pending_loop(cwd, args.base) if "loop" in cats else None,
            "decisions": pending_decisions(cwd, args.base) if "decisions" in cats else None,
            "audit": pending_audit(cwd, advise=(args.emit == "audit")) if "audit" in cats else None,
        }
        print(json.dumps(out))

    if args.commit:
        cats = _categories(args.commit)
        commit(cwd, args.base, loop="loop" in cats, decisions="decisions" in cats,
               audit="audit" in cats)

    if not args.emit and not args.commit:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
