#!/usr/bin/env python3
"""Shared loop-ledger primitives for the two capture hooks (PL-A0).

`trace-op.sh` (PostToolUse) and `capture-subagent-tokens.sh` (SubagentStop) both
need the same two things, and used to copy-paste them — the exact drift trap
`_usage_dedup.py` was created to avoid (and it HAD drifted: capture wrapped the
rotation write in try/except, trace-op did not). Both now live here, imported by
both hooks the way capture already imports `deduped_usage_totals`:

  - `resolve_loop_context(cwd)` — the PCF-16 LIVE-loop gate. Reads
    `.fairmind/active-context.json`; in loop mode it requires a non-terminal
    `loop-state.json` at `base_path` (mirrors check-journal.sh:84-95) and pulls
    `started_at` from `budget.spent.started_at`. The caller no-ops when the loop
    is not live, and captures otherwise.
  - `roll_window(path, started_at, cap=2000)` — window-safe, amortized rotation
    of an active JSONL ledger. NEVER drops a row whose `ts >= started_at` (those
    rows are read whole mid-loop by run_gate_checks settle/mutation-set,
    insights_flush_payload, loop_dashboard); only the OLDEST out-of-window rows
    roll. Pre-arm (started_at is None) it falls back to a pure newest-N cap so
    the ledger stays bounded before the loop arms.

stdlib only; a peer of `_usage_dedup.py`, importable with no third-party deps.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

# POSIX-only advisory file locking (finding 1). Guarded so the plugin still
# imports on Windows, where `fcntl` is absent and `append_row` degrades to a
# best-effort append plus the rotation's re-read fold.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# Reuse the canonical ISO parser rather than a weaker inline copy: it normalizes
# a trailing "Z" to +00:00 and forces tz-awareness, so a naive ts and an aware
# started_at never mix into a TypeError comparison. loop_tokens is a small
# stdlib-only sibling in scripts/; importing it is cheap even on the hot path.
from loop_tokens import _parse_iso

# The engine's terminal statuses: a loop in one of these is done iterating, so
# the capture hooks must no-op. Mirrors check-journal.sh:90-94 and loop-check.sh.
_TERMINAL_EXACT = {"passed_pending_human"}
_TERMINAL_PREFIX = "blocked_"

# getsize() proxy: a lower bound on the serialized length (incl. newline) of ANY
# row these hooks write. Every row carries at least a full ISO-8601 `ts`
# (~25 chars, e.g. "2026-01-01T00:00:00+00:00") plus several fixed keys, so it is
# always well over this. Because each row is >= _MIN_ROW_BYTES, a file of
# `size` bytes holds at most `size / _MIN_ROW_BYTES` rows; when
# `size <= cap * _MIN_ROW_BYTES` the row count cannot exceed `cap`, so rotation
# is provably unnecessary and we skip the full read (a stat(), not a scan). Kept
# conservatively small so the gate can never skip a rotation that is actually due.
#
# Known limitation (finding 7, low-risk): the proxy assumes EVERY row is at least
# `_MIN_ROW_BYTES`. A pathological ledger of many sub-40-byte rows could hold more
# than `cap` rows while still under `cap * _MIN_ROW_BYTES` bytes and thus bypass
# rotation. Real rows are always well over 40 bytes (a full ISO `ts` alone is
# ~25), so this is documented, not defended with a locked sidecar row counter
# (that would be over-engineering for an input these hooks never actually write).
_MIN_ROW_BYTES = 40


def _is_terminal(status):
    return status in _TERMINAL_EXACT or status.startswith(_TERMINAL_PREFIX)


@dataclass
class LoopContext:
    """What each hook needs to decide whether — and how — to capture.

    - `live` is the single go/no-go: interactive sessions are always live;
      a loop session is live only while its loop-state is present and
      non-terminal. The caller does `if not lc.live: sys.exit(0)`.
    - `mode`/`base`/`ref` feed the row stamp, the ledger path, and (trace) the
      per-taskRef filename.
    - `started_at` is the rotation window boundary; None pre-arm (or interactive).
    """

    mode: str
    base: str
    ref: str
    live: bool
    started_at: Optional[str]


def resolve_loop_context(cwd):
    """Resolve the capture context for a hook firing in `cwd`.

    Reads `.fairmind/active-context.json`. Outside loop mode (interactive, or an
    unreadable/missing context) the hook captures unconditionally -> live=True.
    In loop mode it captures ONLY while the loop is LIVE (PCF-16): a `mode:loop`
    marker with no loop-state at base_path, or one pointing at a TERMINAL loop,
    yields live=False and the caller no-ops.
    """
    ctx = {}
    try:
        with open(os.path.join(cwd, ".fairmind", "active-context.json"), encoding="utf-8") as fh:
            ctx = json.load(fh)
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}

    mode = ctx.get("mode") or "interactive"
    base = ctx.get("base_path") or ""
    ref = ctx.get("task_ref") or ctx.get("taskRef") or "session"

    if mode != "loop":
        # Interactive (or unknown mode): no liveness marker -> always capture.
        return LoopContext(mode=mode, base=base, ref=ref, live=True, started_at=None)

    # Loop mode liveness gate (mirrors check-journal.sh:84-95).
    ls_path = os.path.join(cwd, base, "loop-state.json") if base else ""
    if not ls_path or not os.path.isfile(ls_path):
        # No loop-state at base_path -> the loop never armed or is long gone.
        return LoopContext(mode=mode, base=base, ref=ref, live=False, started_at=None)
    try:
        with open(ls_path, encoding="utf-8") as fh:
            ls = json.load(fh)
    except Exception:
        return LoopContext(mode=mode, base=base, ref=ref, live=False, started_at=None)
    if not isinstance(ls, dict):
        return LoopContext(mode=mode, base=base, ref=ref, live=False, started_at=None)

    # Finding 3: a non-str status (int/list/dict) would make the terminal test's
    # `status.startswith(...)` / `status in {set}` raise — coerce to str first.
    status = str(ls.get("status") or "")
    if _is_terminal(status):
        # passed_pending_human / blocked_* -> the loop is done, nothing to record.
        return LoopContext(mode=mode, base=base, ref=ref, live=False, started_at=None)

    started_at = None
    try:
        started_at = (((ls.get("budget") or {}).get("spent") or {}).get("started_at")) or None
    except Exception:
        started_at = None
    return LoopContext(mode=mode, base=base, ref=ref, live=True, started_at=started_at)


def _atomic_write_lines(path, lines, reconcile_from=None):
    """Rewrite `path` from already-serialized JSONL `lines` (each ending in "\\n")
    atomically: mkstemp in the same dir, write, `os.replace`. Mirrors
    audit_run_meta._atomic_write_json / loop_ledger._write_rows — a rotation can
    never leave a reader with a truncated or half-written ledger.

    Reconcile channel (finding 1): when `reconcile_from` is not None, `_roll_window`
    passes the row count of the snapshot it read. This writer then re-reads `path`
    immediately before its replace and folds in any rows at positions past that
    boundary — i.e. appended by a CONCURRENT fire since the snapshot — so
    window-safe rotation never clobbers a concurrent in-window row. None disables
    the fold (a plain rewrite)."""
    lines = list(lines)
    boundary = reconcile_from
    if boundary is not None:
        try:
            with open(path, encoding="utf-8") as fh:
                current = [ln if ln.endswith("\n") else ln + "\n"
                           for ln in fh if ln.strip()]
            if len(current) > boundary:
                lines = lines + current[boundary:]
        except OSError:
            pass
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".loop-ledger.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _in_window(line, started):
    """True if `line`'s row must be KEPT (never rolled). With a window boundary
    `started`, a row is in-window iff its ts >= started; a row whose ts is
    missing or unparseable is treated as in-window (kept). Pre-arm (started is
    None) is handled by the caller — no row is window-protected there."""
    try:
        t = _parse_iso(json.loads(line).get("ts"))
    except Exception:
        t = None
    if t is None:
        return True
    try:
        return t >= started
    except TypeError:
        return True


def roll_window(path, started_at, cap=2000):
    """Best-effort window-safe rotation of the active JSONL ledger at `path`.

    Contract:
      - efficiency: an `os.path.getsize` gate returns after a single stat() when
        the file is small enough that its row count cannot exceed `cap` — the hot
        path (trace fires on EVERY PostToolUse) never reads the whole ledger.
      - hysteresis: rotation triggers only once the count exceeds `cap`, and then
        trims down to a lower watermark (~0.75*cap), so a steady stream of over-cap
        fires rewrites the file about once per 0.25*cap fires, not every fire
        (amortized O(1)).
      - window-safety (INVARIANT): a row with ts >= started_at is NEVER dropped;
        only the OLDEST out-of-window rows (ts < started_at) roll. A row with a
        missing/unparseable ts is kept.
      - pre-arm (started_at is None): no window exists yet, so fall back to a pure
        newest-N cap (keep the newest rows by file position) — the ledger stays
        bounded even before the loop arms.
      - a rotation failure must NEVER break the hook: everything here is
        swallowed, so the caller keeps its fail-open exit 0.
    """
    try:
        _roll_window(path, started_at, cap)
    except Exception:
        pass


def _roll_window(path, started_at, cap):
    # Finding 2: distinguish a truly-ABSENT started_at (None -> pre-arm; fall back
    # to the newest-N positional cap so the ledger stays bounded before the loop
    # arms) from one SUPPLIED but UNPARSEABLE (garbage). An unparseable boundary is
    # NOT trustworthy, so rotation is SKIPPED and every row kept — never risk
    # dropping a current-loop row behind a malformed window. (The old code parsed
    # both to None and took the pre-arm trim, dropping live rows on a garbage ts.)
    if started_at is None:
        started = None
    else:
        started = _parse_iso(started_at)
        if started is None:
            return

    try:
        size = os.path.getsize(path)
    except OSError:
        return
    # Size proxy: below this the row count provably cannot exceed cap -> skip the
    # full read+parse entirely (this is the every-fire fast path).
    if size <= cap * _MIN_ROW_BYTES:
        return

    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh if ln.strip()]
    except OSError:
        return

    n = len(lines)
    if n <= cap:  # high watermark == cap; nothing over the cap to roll
        return

    low = cap - cap // 4              # low watermark: trim target, headroom for amortization

    if started is None:
        # Pre-arm: no window to protect -> every row is roll-eligible, keep the
        # newest `low` by file position.
        protected, trimmable = [], list(range(n))
    else:
        protected, trimmable = [], []
        for i, ln in enumerate(lines):
            (protected if _in_window(ln, started) else trimmable).append(i)

    # Keep every protected row plus the NEWEST out-of-window rows up to the low
    # watermark; drop only the OLDEST out-of-window rows (by file position).
    keep_old = max(0, low - len(protected))
    n_drop = max(0, len(trimmable) - keep_old)
    if n_drop == 0:
        return  # nothing droppable (all rows are in-window) -> leave as is
    drop = set(trimmable[:n_drop])
    kept = [ln for i, ln in enumerate(lines) if i not in drop]

    # Pass the snapshot boundary so `_atomic_write_lines` can fold in any rows a
    # concurrent fire appended since we read `lines` (window-safety, finding 1).
    _atomic_write_lines(path, kept, reconcile_from=n)


def append_row(path, row, started_at, cap=2000):
    """Append one serialized JSONL `row` (no trailing newline required) to the
    active ledger at `path` and rotate it, as ONE unit, under a per-ledger
    advisory lock (finding 1).

    Both capture hooks call this INSTEAD of a bare `open(path,"a").write(row)`
    followed by a SEPARATE `roll_window`. Done as two steps, a concurrent fire
    could append an in-window row between the rotation's snapshot read and its
    `os.replace`, and that row was clobbered (lost) — violating window-safety,
    which the gate relies on when it reads the trace whole for the mutation set /
    settle timing / attribution.

    Portability + fail-open:
      - POSIX (`fcntl`): take a NON-BLOCKING `LOCK_EX` on the ledger and hold it
        across append+rotate. If the lock is contended, still append the row but
        SKIP rotation this fire (rotation defers to an uncontended one — the row
        is never lost and the hook never blocks).
      - Windows / no `fcntl`: best-effort append, then rotate; the rotation
        re-reads the file immediately before its atomic replace and folds in any
        rows appended since its snapshot, shrinking the clobber window.
      - Any lock/IO error is swallowed: the append is best-effort and this NEVER
        raises or blocks, so the caller keeps its exit-0 fail-open contract.
    """
    try:
        _append_row(path, row, started_at, cap)
    except Exception:
        pass


def _append_row(path, row, started_at, cap):
    line = row if row.endswith("\n") else row + "\n"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except OSError:
        pass

    if _fcntl is None:
        # No advisory locking (Windows): best-effort append, then rotate — the
        # rotation's re-read-before-replace fold is the concurrency safety net.
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            return
        roll_window(path, started_at, cap)
        return

    # POSIX: hold a per-ledger advisory lock across append+rotate as one unit.
    try:
        fh = open(path, "a", encoding="utf-8")
    except OSError:
        return
    locked = False
    try:
        try:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            locked = True
        except OSError:
            locked = False  # contended -> append only, defer rotation to a free fire
        fh.write(line)
        fh.flush()
        if locked:
            roll_window(path, started_at, cap)
    finally:
        try:
            if locked:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
