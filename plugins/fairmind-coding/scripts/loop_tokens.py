#!/usr/bin/env python3
"""
loop_tokens.py — best-effort per-loop token totals for the loop dashboard.

Two sources, both attributed to a loop's `[start, end]` window:
  - orchestrator (main) tokens — summed from the Claude Code session transcript(s)
    `~/.claude/projects/<mangled-cwd>/*.jsonl`. The transcript SCHEMA is a CC internal
    (undocumented, may change), so this is best-effort: any failure returns None → the
    dashboard shows `n/a`.
  - sub-agent tokens — summed from `${base}/subagent-tokens.jsonl`, which the
    SubagentStop hook (capture-subagent-tokens.sh) writes at each dispatch. That file is
    ours, so it is robust; still windowed by timestamp.

Token fields are kept raw ({in, out, cache_creation, cache_read}); the dashboard forms
"↑" = in + cache_creation and "↓" = out (matching the harness `subagent_tokens`).
Stdlib only.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

_FIELDS = ("in", "out", "cache_creation", "cache_read")


def _parse_iso(s):
    if not s:
        return None
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mangle(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def orchestrator_transcripts(cwd, home=None):
    """The CC session transcript files for this repo (best-effort; [] if none)."""
    home = home or os.path.expanduser("~")
    d = os.path.join(home, ".claude", "projects", _mangle(cwd))
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl"))


def sum_usage_in_window(paths, start, end):
    """Sum message.usage over transcript files for lines whose top-level `timestamp`
    is in [start, end]. Returns a dict, or None if nothing parseable was found."""
    tot = {k: 0 for k in _FIELDS}
    seen = False
    for p in paths:
        try:
            fh = open(p, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                u = (o.get("message") or {}).get("usage")
                if not u:
                    continue
                ts = _parse_iso(o.get("timestamp"))
                if ts is None or ts < start or ts > end:
                    continue
                seen = True
                tot["in"] += u.get("input_tokens", 0) or 0
                tot["out"] += u.get("output_tokens", 0) or 0
                tot["cache_creation"] += u.get("cache_creation_input_tokens", 0) or 0
                tot["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
    return tot if seen else None


def read_subagent_ledger(path, start, end):
    """Sum the SubagentStop-captured rows in [start, end]. Our file → robust."""
    if not os.path.isfile(path):
        return None
    tot = {k: 0 for k in _FIELDS}
    seen = False
    try:
        for line in open(path, encoding="utf-8"):
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = _parse_iso(o.get("ts"))
            if ts is None or ts < start or ts > end:
                continue
            seen = True
            for k in _FIELDS:
                tot[k] += o.get(k, 0) or 0
    except OSError:
        return None
    return tot if seen else None


def loop_tokens(cwd, base, start_iso, end_iso=None, home=None):
    """Best-effort {orchestrator, subagent} token dicts (each dict or None) for a loop
    window. Everything is guarded — a broken/absent source degrades to None, never
    raises, so the dashboard can always render."""
    try:
        start = _parse_iso(start_iso)
        if start is None:
            return {"orchestrator": None, "subagent": None}
        end = _parse_iso(end_iso) or datetime.now(timezone.utc)
        try:
            orch = sum_usage_in_window(orchestrator_transcripts(cwd, home), start, end)
        except Exception:
            orch = None
        led = os.path.join(cwd, base, "subagent-tokens.jsonl") if base \
            else os.path.join(cwd, ".fairmind", "subagent-tokens.jsonl")
        try:
            sub = read_subagent_ledger(led, start, end)
        except Exception:
            sub = None
        return {"orchestrator": orch, "subagent": sub}
    except Exception:
        return {"orchestrator": None, "subagent": None}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Best-effort per-loop token totals (JSON).")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--base", default="")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=None)
    args = ap.parse_args(argv)
    print(json.dumps(loop_tokens(args.cwd or os.getcwd(), args.base, args.start, args.end)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
