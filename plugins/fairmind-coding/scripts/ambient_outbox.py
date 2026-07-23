#!/usr/bin/env python3
"""PL-A1c — the ambient-telemetry OUTBOX: the durable-drain half of the pair
that opens with PL-A1b's digester.

A1b's `_insights_session._spool_append` writes one rollup line per digested
session to `<data_dir>/insights/rollups/<tenancy>.jsonl` and NEVER clears it —
that spool is an AT-LEAST-ONCE queue (a crash between append and the registry
stamp re-digests, hence re-spools, the same session — see
`_insights_session._spool_append`'s own docstring). This module is the reader
that turns that at-least-once queue into EXACTLY-ONCE delivery to the
project-context REST door (PC-A2): it dedups by `session_id`, tracks
delivered/dead-lettered sessions in a small per-tenant state file, and never
drops a still-pending rollup even under a durable row-count cap.

Design, matching the frozen PL-A1c interface contract:

  * `drain(tenancy, transport, ...)` is the whole state machine. `transport`
    is an INJECTABLE callable (`payload: dict -> Response`) so the module is a
    pure state machine over (spool, state) with no baked-in networking — the
    test suite never opens a socket, and `transport=None` ("no endpoint
    configured yet") makes `drain` a byte-for-byte NO-OP.
  * `build_wire_payload` is a PURE function (no clock/fs/env): it is the
    single place that decides what leaves this process, and it carries only
    the opaque `tenancy` id plus the already-privacy-scrubbed rollup — never a
    raw path, branch, user id, company, or auth token (those negative-space
    guarantees hold by construction: this module never even reads `cwd`, it
    only accepts it for interface symmetry with the resolve_tenancy-based
    siblings and never touches its value).
  * The PC-A2 status contract (200/401/403/413/422/429/503/other) is mapped in
    `_classify` and applied entirely inside `drain`; the per-tenant state file
    (`<data_dir>/insights/outbox/<tenancy>.json`) is the only durable record of
    delivered/dead-lettered sessions and of the current backoff/mute window,
    written atomically via the shared `_loop_ledger._atomic_write_lines`
    (mkstemp + os.replace — the same primitive `_insights_session.py` reuses
    for its own registry/consent rewrites).
  * Durable caps: the spool is compacted (terminal rows reclaimed) only once
    its row count exceeds `cap` — never on every ack — and a still-pending row
    is NEVER dropped, even if that leaves the spool over cap (in which case a
    backlog `doctor_hint` is raised instead). The rewrite is IN-PLACE under a
    blocking `fcntl.flock` on the spool's own fd (never mkstemp+`os.replace`,
    which would swap the inode out from under a concurrent
    `_insights_session._spool_append`), re-reading the spool under that same
    lock so a concurrent append is never lost.
  * `drain()` is itself serialized per tenancy by a non-blocking, tenant-wide
    `fcntl.flock` (`_tenant_drain_lock`): a second concurrent drain that loses
    the race returns immediately, before ever loading state — never a
    double-send. Within one drain, state is saved BEFORE the spool is
    compacted, so a crash between the two can never lose a terminal row's
    disposition; a session inside its own `backoff.next_attempt_at` window is
    skipped for SENDS only (disk maintenance still runs).

stdlib only.
"""

import collections
import contextlib
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _insights_session  # noqa: E402  (data_dir/_spool_path/resolve_tenancy)
import _loop_ledger  # noqa: E402  (shared atomic writer + tolerant ISO parser)
import ambient_digest  # noqa: E402  (the one degrade-graceful JSONL reader)

# POSIX-only advisory file locking — the tenant-wide drain lock (fix 4) and
# the in-place spool-compaction lock (fix 3). Guarded exactly the way
# `_insights_session.py` guards its own `_fcntl` import, so this module still
# IMPORTS on a non-POSIX host; both locks below degrade to best-effort (no
# real cross-process/cross-thread mutual exclusion) when unavailable, rather
# than refusing to run.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

Response = collections.namedtuple("Response", ["status", "body"])

# --------------------------------------------------------------------------- #
# Tunables. None of these are asserted at an exact value by the RED-first
# suite (only "strictly growing" / "exactly 24h" / "non-empty" are pinned);
# picked to be conservative production defaults, documented here rather than
# buried as magic numbers inline.
# --------------------------------------------------------------------------- #

_DEFAULT_CAP = 500  # durable spool row-count cap before compaction kicks in
_BACKOFF_BASE_SECONDS = 60  # attempt 1 -> 60s, attempt 2 -> 120s, ...
_BACKOFF_MAX_SECONDS = 3600  # never back off further than 1h between attempts
_MAX_RESPONSE_BYTES = 65536  # bounded read cap on a PC-A2 response body (an
# ack/error body is just `{"id": ...}` or a short message — never unbounded,
# so a hostile/oversized endpoint response cannot exhaust memory).
_MUTE_401_REASON = "auth_failed"
_MUTE_401_DURATION = timedelta(hours=1)  # shorter than 403: often a refreshable token
_MUTE_403_REASON = "ambient_disabled"
_MUTE_403_DURATION = timedelta(hours=24)  # PC-A2 kill-switch: exact per contract

_DOCTOR_HINT_401 = (
    "Ambient insight delivery is muted after an authentication failure (401) "
    "from the insights endpoint. Check the configured JWT/auth token for "
    "ambient capture; delivery resumes automatically once the mute window "
    "elapses."
)
_DOCTOR_HINT_403 = (
    "Ambient insight delivery was disabled by the insights endpoint (403) "
    "for 24h. Check whether ambient capture was turned off server-side for "
    "this tenant."
)


def _backlog_doctor_hint(pending_count, cap):
    return (
        f"Ambient outbox backlog: {pending_count} rollup(s) are pending "
        f"delivery, above the drain cap of {cap}. Investigate delivery "
        f"(network/auth) — the spool will keep growing (never dropping a "
        f"pending rollup) until it drains."
    )


def _legacy_row_doctor_hint(session_ids):
    """PL-A2a AC8: a legacy (pre-PL-A2a) spool row — no `started_at`/
    `ended_at` at all, so it predates the wire-schema enrichment and can
    never be safely built into a `build_wire_payload` payload — is dead-
    lettered LOCALLY, without ever being sent. The hint NAMES the condition
    (matched by the checker's `legacy|started_at|ended_at|pre-enrich` regex)
    so a doctor/operator reading it knows exactly what happened and why no
    retry will ever resolve it."""
    return (
        f"Ambient outbox: {len(session_ids)} legacy (pre-enrichment) spool "
        f"row(s) — missing started_at/ended_at — were dead-lettered locally "
        f"without being sent. These rows predate the PL-A2a wire-schema "
        f"convergence and will never be retried; they are safe to ignore "
        f"(they carry no data loss beyond the already-superseded local "
        f"rollup)."
    )


def _held_schema_row_doctor_hint(session_ids):
    """PL-A2a round 3 (F3 — AMENDS round 2's D4 disposition, see
    `_unsendable_reason`'s own docstring): a spool row carrying VALID
    `started_at`/`ended_at` but an absent or unrecognized `schema` stamp —
    the case the OLD, purely-structural (missing-timestamps only) sniff
    could never catch — is HELD (kept pending, never sent, never
    dead-lettered) rather than discarded, so a later plugin version that
    understands its schema can still deliver it. Round 2 dead-lettered this
    case unconditionally, which made a genuine future-schema row (or a
    transitional/partial-deploy row with no schema at all) PERMANENTLY
    unrecoverable — `digested_at` is already stamped at spool time, so there
    is no re-digest path. Named separately from `_legacy_row_doctor_hint`
    (worded to match the checker's `schema|version` regex while deliberately
    NOT matching its `legacy|started_at|ended_at|pre-enrich` regex) so a
    doctor merging every hint can tell a genuinely legacy row from a
    schema-mismatched (held) one."""
    return (
        f"Ambient outbox: {len(session_ids)} otherwise well-formed spool "
        f"row(s) carry an unrecognized (absent or unknown) schema version "
        f"— expected {ambient_digest.SCHEMA_VERSION!r} — and are being HELD "
        f"(kept pending, never sent) rather than delivered or discarded. "
        f"Investigate whether the plugin version that produced them "
        f"predates or postdates this build; a future plugin version that "
        f"understands this schema can still deliver them."
    )


def _incomplete_row_doctor_hint(session_ids):
    """PL-A2a round 3 (F4): a spool row carrying the CURRENT schema stamp
    but missing `started_at`/`ended_at` — a LIVE producer defect (a
    registry read failure or a corrupt registry row at digest time, or a
    direct `_digest_one_session` call), not an old, safe-to-ignore leftover
    — is dead-lettered LOCALLY, without ever being sent (unlike the held
    schema-mismatch case above, no schema bump ever recovers genuinely
    absent timestamps). Worded to stay distinguishable from
    `_legacy_row_doctor_hint` and `_held_schema_row_doctor_hint`: never
    claims "safe to ignore", never uses the word "legacy", so an operator
    investigates a live defect rather than dismissing it as old data."""
    return (
        f"Ambient outbox: {len(session_ids)} spool row(s) carry the CURRENT "
        f"schema version ({ambient_digest.SCHEMA_VERSION!r}) but are "
        f"missing started_at/ended_at. This is a live producer defect (a "
        f"registry read failure or corrupt registry row at digest time) "
        f"and needs investigation — these rows were dead-lettered locally "
        f"without being sent."
    )


def _unbuildable_row_doctor_hint(session_ids):
    """PL-A2a round 3 (F1): a row whose payload could not be built by
    `build_wire_payload` (e.g. a hostile/malformed `skills` value) is
    dead-lettered LOCALLY, without ever reaching the transport — this must
    never escape `drain()` as an exception (that would lose every ack
    already obtained this same drain and wedge the tenant's outbox on every
    subsequent drain). Named separately from every other unsendable-row
    hint so a doctor can tell "the row's own shape is broken" apart from
    "this row predates/postdates this build's schema"."""
    return (
        f"Ambient outbox: {len(session_ids)} spool row(s) could not be "
        f"built into a wire payload (a malformed field, e.g. skills) and "
        f"were dead-lettered locally without being sent — investigate the "
        f"producer that spooled them."
    )


# --------------------------------------------------------------------------- #
# Per-tenant outbox state: <data_dir>/insights/outbox/<tenancy>.json.
# --------------------------------------------------------------------------- #

def _outbox_state_path(tenancy):
    return os.path.join(_insights_session.data_dir(), "insights", "outbox", tenancy + ".json")


def _default_state():
    return {
        "delivered": [],
        "dead_letter": [],
        "backoff": {"attempts": 0, "next_attempt_at": None},
        "mute": {"until": None, "reason": None},
    }


def _load_state(tenancy):
    """The persisted outbox state for `tenancy`, normalized so every caller
    can trust the shape (list/dict types, no missing keys). A missing,
    unreadable, or malformed state file degrades to `_default_state()` —
    never raises; a corrupt state file must never wedge a drain."""
    path = _outbox_state_path(tenancy)
    raw = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        return _default_state()

    state = _default_state()
    delivered = raw.get("delivered")
    if isinstance(delivered, list):
        state["delivered"] = [s for s in delivered if isinstance(s, str)]

    dead_letter = raw.get("dead_letter")
    if isinstance(dead_letter, list):
        state["dead_letter"] = [
            e for e in dead_letter
            if isinstance(e, dict) and isinstance(e.get("session_id"), str)
        ]

    backoff = raw.get("backoff")
    if isinstance(backoff, dict):
        attempts = backoff.get("attempts")
        state["backoff"] = {
            "attempts": attempts if isinstance(attempts, int) else 0,
            "next_attempt_at": backoff.get("next_attempt_at"),
        }

    mute = raw.get("mute")
    if isinstance(mute, dict):
        state["mute"] = {"until": mute.get("until"), "reason": mute.get("reason")}

    return state


def _save_state(tenancy, state):
    path = _outbox_state_path(tenancy)
    _insights_session._ensure_private_dir(os.path.dirname(path))
    _loop_ledger._atomic_write_lines(path, [json.dumps(state, sort_keys=True) + "\n"])


# --------------------------------------------------------------------------- #
# Spool access (A1b's durable drain queue — read here, compacted here, never
# elsewhere; `_insights_session._spool_append` is the only writer of NEW rows).
# --------------------------------------------------------------------------- #

def _read_spool_rows(spool_path):
    """Every parseable JSON row in the spool, in file order — the drain queue's
    read side. Delegates to `ambient_digest._read_jsonl`, the single JSONL reader
    carrying the shared degrade-graceful discipline (missing file -> [],
    unparseable line skipped, never raises), so the two can never drift."""
    return ambient_digest._read_jsonl(spool_path)


def _write_spool_rows(spool_path, rows):
    """Atomically rewrite the spool to hold exactly `rows` (used only by the
    compaction step in `drain`, never for a plain append — new rows are always
    appended by `_insights_session._spool_append`)."""
    _insights_session._ensure_private_dir(os.path.dirname(spool_path))
    lines = [json.dumps(row) + "\n" for row in rows]
    _loop_ledger._atomic_write_lines(spool_path, lines)


def _pending_rows(rows, delivered, dead_letter_ids):
    """Yield (session_id, row) for every spool row that is a well-formed,
    still-PENDING entry — a dict with a non-empty str `session_id` that is
    neither delivered nor dead-lettered. The SINGLE definition of "pending":
    the drain loop, the pending count on the early-return paths, and spool
    compaction all drive off this one predicate rather than transcribing it."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid:
            continue
        if sid in delivered or sid in dead_letter_ids:
            continue
        yield sid, row


def _pending_session_ids(tenancy, state):
    """The distinct pending session_ids in the spool — used for the `pending`
    count on the two early-return paths (already-muted, transport=None) where
    the full drain loop never runs."""
    rows = _read_spool_rows(_insights_session._spool_path(tenancy))
    delivered = set(state["delivered"])
    dead_letter_ids = {e["session_id"] for e in state["dead_letter"]}
    return {sid for sid, _ in _pending_rows(rows, delivered, dead_letter_ids)}


# --------------------------------------------------------------------------- #
# The wire payload — PURE, and the single choke point for what ever leaves
# this process. PL-A2a pinned this to the field set the project-context ingest
# endpoint expects; the exact shape is held by a shared conformance fixture
# (`test_pla2a_wire_contract.py` / `test_wire_conformance.py`) rather than
# spelled out here, so client and endpoint can never drift apart silently.
# --------------------------------------------------------------------------- #

def _agents_from_rollups(rollups):
    """One `agents[]` entry per rollup, renaming the snake_case token fields
    to the payload's camelCase keys — VALUES pass through unchanged, only the
    keys are renamed. `outcome`/`agentRole` are never added (AC4): an ambient
    rollup is a per-MODEL token/skill aggregate, not a per-agent-run record,
    so there is no outcome to report and fabricating one (or a role) would
    poison downstream analytics with an invented value."""
    agents = []
    for rollup in rollups:
        if not isinstance(rollup, dict):
            continue
        agents.append({
            "modelId": rollup.get("model"),
            "inputTokens": rollup.get("input_tokens"),
            "outputTokens": rollup.get("output_tokens"),
            "cacheReadTokens": rollup.get("cache_read_input_tokens"),
            "cacheCreationTokens": rollup.get("cache_creation_input_tokens"),
        })
    return agents


def _raw_digest(row):
    """A deterministic hash of the SESSION's own content (PL-A2a round 2,
    D6): `row` — session_id, started_at/ended_at, entry_source, schema,
    skills, tool_counts, rollups, all of it — not merely one sub-part of it.
    Round 1 hashed `rollups` alone, so two DIFFERENT sessions sharing the
    same (frequently EMPTY) rollups list collided on the identical digest —
    e.g. a session whose only record carried no usage block produces
    `rollups == []` regardless of its own session_id/timestamps/tool_counts,
    so `sha256:<hash of []>` was reused across every such session. Hashing
    the whole row fixes this while staying PURE and clock-free: `row` never
    carries the three volatile runtime counters (flushLagS/
    pendingBacklogCount/evictedCount) — those arrive as this function's
    caller's OWN separate keyword parameters, never as part of `row` — so
    `rawDigest` can never be perturbed by drain-to-drain variation in them
    (AC6). `sort_keys=True` makes the canonical form independent of a dict's
    insertion order, mirroring the truncated-sha256-hex convention
    `_insights_session._tenancy_from_common` already uses for the opaque
    tenancy id."""
    canonical = json.dumps(row, sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_wire_payload(row, tenancy, *, flush_lag_s, pending_backlog_count, evicted_count):
    """The exact JSON body POSTed to the project-context ingest endpoint (PC-A2)
    — the closed field set it expects. PURE: no clock/fs/env read, so identical
    inputs always yield an identical (deep-equal, byte-identical once
    serialized) output.

    `row` is the PL-A2a ENRICHED spool-row dict (`session_id`/`started_at`/
    `ended_at`/`entry_source`/`schema`/`skills`/`pluginVersion`/
    `parserDegraded`/`tool_counts`/`rollups`, see `ambient_digest.digest`'s
    own return shape) — its own `row.get("tenancy")`, if present, is NEVER
    read; `tenancy` is always the caller's authoritative, pre-resolved opaque
    id. `flush_lag_s`/`pending_backlog_count`/`evicted_count` are the three
    PC-A2 runtime counters, computed and injected by `drain()` — this
    function never reads a clock, the filesystem, or drain's own state to
    derive them itself.

    `skills` (PL-A2a round 2, D1) is read from `row["skills"]` — the ROW
    level `ambient_digest.digest()` now hoists it to — never re-derived from
    `rollups` (a rollup no longer carries its own `skills` copy at all, and
    even when it did, a union over `rollups` is always `[]` whenever
    `rollups` itself is empty, silently dropping a row-level skill name for
    exactly the session this round's D1 fix targets).

    Identity and telemetry fields the endpoint adds downstream are not
    parameters this function accepts; `outcome`/`agentRole` are never
    fabricated at either level (AC4) — see `_agents_from_rollups`."""
    rollups = row.get("rollups") if isinstance(row.get("rollups"), list) else []
    skills = row.get("skills")
    return {
        "sessionId": row.get("session_id"),
        "repoRef": tenancy,
        "repoRefScheme": "opaque-tenancy",
        "entrySource": row.get("entry_source"),
        "pluginVersion": row.get("pluginVersion"),
        "startedAt": row.get("started_at"),
        "endedAt": row.get("ended_at"),
        "skills": sorted(skills) if isinstance(skills, list) else [],
        "toolCounts": row.get("tool_counts") if isinstance(row.get("tool_counts"), dict) else {},
        "agents": _agents_from_rollups(rollups),
        "decisionsCount": 0,
        "flushLagS": flush_lag_s,
        "pendingBacklogCount": pending_backlog_count,
        "evictedCount": evicted_count,
        "parserDegraded": bool(row.get("parserDegraded")),
        "rawDigest": _raw_digest(row),
    }


def _unsendable_reason(row):
    """PL-A2a round 2 (D4) + round 3 (F3/F4 — Codex+Grok convergent review).
    The ONE function deciding whether `row` can ever be safely built into a
    `build_wire_payload` call and sent. Returns `(kind, message)` — `kind`
    is one of `"legacy"`, `"held"`, or `"incomplete"` (message a non-empty,
    human-readable string distinguishable BY KIND — a doctor merging every
    dead_letter/hint list must be able to tell them apart) — or `None` if
    the row is sendable.

    SCHEMA is checked FIRST, then completeness (round 3, F4 — the reverse
    order round 2 shipped misdiagnoses a CURRENT-schema row with missing
    timestamps as an old, safe-to-ignore leftover instead of the LIVE
    producer defect it actually is):

      1. RECOGNIZED schema (`row["schema"] == ambient_digest.SCHEMA_VERSION`)
         + valid started_at/ended_at -> sendable (`None`).
      2. RECOGNIZED schema + missing started_at/ended_at -> `"incomplete"`
         (F4): a LIVE producer defect (a registry read failure, a corrupt
         registry row at digest time, or a direct `_digest_one_session`
         call) — terminal (dead-lettered LOCALLY): no schema bump ever
         recovers genuinely absent timestamps, so holding it forever would
         accomplish nothing but accumulate dead weight in the spool.
      3. UNRECOGNIZED schema (absent, or present but different from
         `SCHEMA_VERSION`) + valid started_at/ended_at -> `"held"` (F3, a
         round-3 AMENDMENT to round 2's own dead-letter-everything fix):
         kept PENDING — never sent, never terminal — so a LATER plugin
         version that understands the new schema can still deliver it.
         Round 2 dead-lettered this case unconditionally, which made a
         genuine future-version row (or a transitional/partial-deploy row
         with no schema at all) PERMANENTLY unrecoverable: `digested_at` is
         already stamped at spool time, so there is no re-digest path.
      4. UNRECOGNIZED schema + missing started_at/ended_at -> `"legacy"`
         (AC8, unchanged from round 2): the true pre-PL-A2a shape — no
         schema stamp AND no timestamps at all, never recoverable
         regardless of schema — terminal (dead-lettered LOCALLY).

    Only `"legacy"`/`"incomplete"` are terminal; `"held"` is left pending
    and is therefore never reclaimed by `_compact_spool` (which only ever
    reclaims rows already in `delivered`/`dead_letter`)."""
    schema = row.get("schema")
    schema_recognized = schema == ambient_digest.SCHEMA_VERSION
    has_timestamps = bool(row.get("started_at")) and bool(row.get("ended_at"))

    if schema_recognized:
        if has_timestamps:
            return None
        return ("incomplete",
                f"incomplete spool row: current schema "
                f"({ambient_digest.SCHEMA_VERSION!r}) but missing "
                f"started_at/ended_at — a live producer defect (not a "
                f"stale, pre-dating leftover), needs investigation")

    if not has_timestamps:
        return ("legacy",
                "legacy (pre-enrichment) spool row: missing started_at/ended_at "
                "— this row predates the PL-A2a wire-schema convergence")

    return ("held",
            f"unrecognized schema {schema!r} on an otherwise well-formed "
            f"row (expected {ambient_digest.SCHEMA_VERSION!r}) — held "
            f"pending, never sent, until a plugin version that understands "
            f"this schema can deliver it")


# --------------------------------------------------------------------------- #
# PC-A2 status -> action classification (the project-context server contract).
# --------------------------------------------------------------------------- #

def _classify(status):
    if status == 200:
        return "ack"
    if status in (413, 422):
        return "dead_letter"
    if status == 401:
        return "mute_auth"
    if status == 403:
        return "mute_kill"
    return "retry"  # 429/503 and any other/unknown status -> retry, never ack


def _drain_result(sent, acked, dead_lettered, retried, muted, mute_reason, doctor_hint, pending):
    return {
        "sent": sent,
        "acked": acked,
        "dead_lettered": dead_lettered,
        "retried": retried,
        "muted": bool(muted),
        "mute_reason": mute_reason,
        "doctor_hint": doctor_hint,
        "pending": pending,
    }


def _read_locked_spool_rows(fh):
    """Parse the CURRENT content of an already-open, already-locked spool file
    handle, in file order — the same degrade-graceful discipline as
    `ambient_digest._read_jsonl` (blank line skipped, unparseable line
    skipped) but reading from `fh` directly, since compaction re-reads under
    its own flock rather than opening a second, independent handle on the
    path."""
    fh.seek(0)
    rows = []
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return rows


def _compact_spool(spool_path, rows, delivered, dead_letter_ids, cap):
    """Durable caps — the A1b-F2 invariant. `rows` (the caller's earlier spool
    snapshot) decides only WHETHER compaction is worth attempting; when its
    row count exceeds `cap`, this rewrites the spool keeping ONLY still-pending
    rows (delivered and dead-lettered rows reclaimed) — a still-pending row is
    NEVER dropped, even if that leaves the spool over cap. Returns a backlog
    doctor-hint when the distinct pending set alone still exceeds `cap`
    (nothing left to reclaim), else None; a no-op returning None while under
    cap.

    The rewrite itself is IN-PLACE under a blocking `fcntl.flock` on the
    spool's own fd (guarded by `if _fcntl is not None`, degrading to a
    best-effort, non-exclusive rewrite on a non-POSIX host exactly like
    `_insights_session._spool_append`) — never mkstemp+`os.replace`, which
    would swap the spool's inode out from under a concurrent
    `_spool_append` (that function holds a blocking flock on the spool's OWN
    fd, so a rename-based rewrite shares no lock with it and a concurrent
    append lands on the dangling old inode and is silently lost). Coordinating
    on the SAME inode means this also RE-READS the spool under the lock — so
    any append since `rows` was snapshotted is folded in — before truncating
    and rewriting, rather than trusting the stale in-memory `rows` passed in."""
    if len(rows) <= cap:
        return None
    try:
        fh = open(spool_path, "r+", encoding="utf-8")
    except OSError:
        return None
    locked = False
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)  # blocking
            locked = True
        current_rows = _read_locked_spool_rows(fh)
        pending_pairs = list(_pending_rows(current_rows, delivered, dead_letter_ids))
        fh.seek(0)
        fh.truncate()
        for _, row in pending_pairs:
            fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        try:
            if locked and _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    distinct_pending = {sid for sid, _ in pending_pairs}
    if len(distinct_pending) > cap:
        return _backlog_doctor_hint(len(distinct_pending), cap)
    return None


# --------------------------------------------------------------------------- #
# Tenant-wide drain lock. Two concurrent `drain()` calls on ONE tenancy must
# never both read pending state and both send — this serializes the WHOLE
# drain body per tenancy, not per session, so a would-be double-send is
# caught before either drain even loads state. Distinct from the spool flock
# in `_compact_spool` above: that one coordinates compaction against a
# concurrent `_spool_append`; this one coordinates drain against drain.
# --------------------------------------------------------------------------- #

def _drain_lock_path(tenancy):
    return os.path.join(_insights_session.data_dir(), "insights", "locks", tenancy + ".outbox.lock")


@contextlib.contextmanager
def _tenant_drain_lock(tenancy):
    """Non-blocking, tenant-wide drain lock — mirrors A1b's per-session digest
    lock (`_insights_session._lock_path` / `_digest_one_session`'s
    `LOCK_EX | LOCK_NB`) but scoped to a whole `drain()` call rather than one
    session: BUSY (another drain for this tenancy already holds it) means
    that other drain owns delivery this round, so the caller must skip —
    never wait, never reap.

    Yields True if this call holds the lock (proceed), False if it lost the
    race (the caller must return immediately, without ever loading state).
    Degrades to best-effort (always True, no real mutual exclusion) on a
    non-POSIX host or if the lock file itself cannot be opened — the same
    fail-open discipline every other lock in this codebase follows, so
    locking being unavailable never blocks delivery outright."""
    lock_dir = os.path.join(_insights_session.data_dir(), "insights", "locks")
    _insights_session._ensure_private_dir(lock_dir)
    lock_path = _drain_lock_path(tenancy)
    _insights_session._ensure_private_file(lock_path)
    try:
        lock_fh = open(lock_path, "a+")
    except OSError:
        lock_fh = None
    acquired = False
    try:
        if lock_fh is not None and _fcntl is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False  # BUSY -> another drain holds it; skip, never reap
        else:
            acquired = True  # no real mutual exclusion possible; proceed best-effort
        yield acquired
    finally:
        if lock_fh is not None:
            try:
                if acquired and _fcntl is not None:
                    _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                lock_fh.close()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# drain() — the whole state machine.
# --------------------------------------------------------------------------- #

def drain(tenancy, transport, *, now=None, cwd=None, cap=_DEFAULT_CAP):
    """Deliver every PENDING (not yet delivered, not yet dead-lettered)
    session in `tenancy`'s spool to `transport`, exactly once per
    `session_id`, applying the PC-A2 status contract and persisting the
    result to the per-tenant outbox state file.

    `now` is the injected clock (defaults to the real UTC now) — every time
    computation in this function uses it, never `datetime.now()` directly, so
    the whole state machine is deterministic under test. `cwd` is accepted
    for interface symmetry with `_insights_session.resolve_tenancy(cwd)`
    (a production caller resolves tenancy from cwd once, upstream) but is
    deliberately UNUSED here — `tenancy` is always the authoritative,
    pre-resolved opaque id, so this function can never leak `cwd`'s raw value
    onto the wire or into persisted state by construction, and two different
    cwds that resolve to the same tenancy share exactly one state file.

    The WHOLE body runs under `_tenant_drain_lock(tenancy)` (fix 4): a second
    concurrent `drain()` call for the same tenancy that loses the race
    returns immediately, before ever loading state — never a double-send.

    PL-A2a additionally: (a) an UNSENDABLE row (legacy, held, or incomplete
    — see `_unsendable_reason`) is resolved LOCALLY, with zero transport
    calls, and never blocks its batch-mates (AC8/F1-F5); (b) every payload
    this drain sends carries the three PC-A2 runtime counters —
    pendingBacklogCount (this drain's own batch size), evictedCount (the
    rows THIS drain's own compaction step will reclaim), and flushLagS (now
    minus THAT row's own ended_at, per session) — computed here and passed
    into `build_wire_payload` as plain parameters, never read by that
    function itself (AC6/AC7). PL-A2a round 3: local classification of what
    can/cannot ever be sent (legacy/held/incomplete detection, and a row
    whose payload cannot even be BUILT) is disk/CPU work independent of
    whether SENDS are currently paced — it runs unconditionally, whether or
    not this drain is muted or backed off (F5); only the send loop itself
    is skipped while paced.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    with _tenant_drain_lock(tenancy) as acquired:
        if not acquired:
            # Another drain for this tenancy already holds the lock and owns
            # delivery this round — mirror A1b's per-session BUSY skip:
            # never reap, never double-send, zero transport calls.
            return _drain_result(0, 0, 0, 0, False, None, None, 0)

        state = _load_state(tenancy)

        # 1. Mute status — a BOOLEAN only, computed but not acted on until
        # the skip-sends decision below (PL-A2a round 3, F5): being muted no
        # longer short-circuits BEFORE the spool is even read, so local
        # unsendable-row classification always runs regardless of whether
        # sends are currently paced by an active mute window.
        mute = state["mute"]
        mute_until = _loop_ledger._parse_iso(mute.get("until"))
        already_muted = mute_until is not None and now < mute_until

        # 2. No endpoint configured -> byte-for-byte no-op (spool AND state
        # untouched), regardless of mute state. This still reports
        # `already_muted`/its reason in the result — identical to what the
        # OLD, separate mute early-return computed for this same
        # (muted, transport=None) combination (both branches used
        # `_pending_session_ids`  and returned all-zero counts), so folding
        # the two checks together changes no externally observable result.
        if transport is None:
            pending = len(_pending_session_ids(tenancy, state))
            muted_reason = mute.get("reason") if already_muted else None
            return _drain_result(0, 0, 0, 0, already_muted, muted_reason, None, pending)

        spool_path = _insights_session._spool_path(tenancy)
        rows = _read_spool_rows(spool_path)

        delivered = set(state["delivered"])
        dead_letter_entries = list(state["dead_letter"])
        dead_letter_ids = {e["session_id"] for e in dead_letter_entries}

        # PL-A2a evictedCount (AC7): `drain()` today compacts the spool AFTER
        # the sends (step 6, unchanged below — see the module docstring's
        # save-before-compact crash-safety invariant), but evictedCount must
        # be IN the payloads the send loop builds. Sends only ever ADD
        # terminal rows (an ack, or a legacy row's local dead-letter below) —
        # they never turn a terminal row back into a pending one — so the set
        # of rows compaction will reclaim is already computable HERE, from
        # the rows already terminal (delivered/dead-lettered) as of THIS
        # drain's START, before either the legacy split or the send loop run.
        # This is a safe, deliberately conservative count: it never includes
        # a row that becomes terminal DURING this same drain (that row is
        # reclaimed on a LATER drain's compaction instead), so it can never
        # overstate what THIS drain's compaction step is about to do.
        # Derived from `_pending_rows` — the SINGLE definition of "pending"
        # this module documents — rather than from a second, hand-written
        # "terminal" predicate. Compaction keeps exactly the pending rows and
        # drops everything else, so what it reclaims is precisely
        # `len(rows) - len(pending)`. A transcribed predicate had already
        # drifted from it: it counted only rows whose session_id is terminal,
        # while compaction ALSO drops a malformed row (no dict, or no non-empty
        # str session_id) that `_pending_rows` skips — so the counter reported
        # to the server undercounted exactly when the spool held a bad line.
        # Sends can only ADD terminal rows, so measuring at drain start is a
        # lower bound the payloads can carry before any send happens.
        pending_pairs = list(_pending_rows(rows, delivered, dead_letter_ids))
        evicted_count = (len(rows) - len(pending_pairs)) if len(rows) > cap else 0

        # 3. Group by session_id; a same-session duplicate row (A1b's
        # at-least-once spool duplicate) collapses to the LATEST SENDABLE
        # row for that session (PL-A2a round 3, F2/F2b) — never merely the
        # latest physical line irrespective of sendability. Round 2's
        # unconditional last-write-wins collapse could pick a CORRUPT
        # duplicate over an earlier, perfectly good row for the SAME
        # session (A1b's at-least-once re-spool can append more than one
        # physical line per session_id): the good row was never even
        # considered, and the session was dead-lettered outright with a
        # doctor_hint telling the operator it was "safe to ignore". Single
        # pass, file order: a SENDABLE row always overwrites whatever was
        # there (so the LATEST sendable duplicate wins, never merely the
        # first); an UNSENDABLE row only overwrites when the current holder
        # for that sid is itself still unsendable (`sid not in latest_row or
        # sid in unsendable`) — so a later corrupt duplicate can never evict
        # an already-established sendable candidate, but among a run of
        # unsendable-only duplicates the reason still tracks the latest one.
        # A session is classified unsendable only when EVERY physical
        # duplicate for it is unsendable.
        latest_row = {}
        unsendable = {}  # sid -> (kind, message), see _unsendable_reason
        for sid, row in pending_pairs:
            reason = _unsendable_reason(row)
            if reason is None:
                latest_row[sid] = row
                unsendable.pop(sid, None)
            elif sid not in latest_row or sid in unsendable:
                latest_row[sid] = row
                unsendable[sid] = reason
        sendable_sids = [sid for sid in latest_row if sid not in unsendable]

        # PL-A2a AC7/round-2 D2: pendingBacklogCount is this drain's own
        # SENDABLE batch size — one constant value every payload sent THIS
        # drain carries, not a running "remaining after this send" count that
        # would differ from send to send within the same drain, and never
        # `len(latest_row)` pre-split (which would also count rows this SAME
        # drain permanently, locally discards as unsendable — D2's exact
        # defect: those rows can never be retried, so counting them as
        # "pending" overstates what this drain can actually attempt/retain).
        pending_backlog_count = len(sendable_sids)

        backoff = dict(state["backoff"])
        sent = acked = dead_lettered = retried = 0
        muted_now = False
        mute_reason_now = None
        doctor_hint = None
        # PL-A2a round-2 D5 (widened round 3, F1): each locally-derived hint
        # is captured ONCE into its OWN variable — never into `doctor_hint`
        # itself — so a LATER, unrelated assignment in this same drain (a
        # same-drain 401/403 mute, or the end-of-drain backlog hint) can
        # never silently clobber it; see the final combine step below.
        unsendable_hint = None
        unbuildable_hint = None
        had_retry = had_ack = False

        # Unsendable rows are resolved LOCALLY and unconditionally — there is
        # nothing to send, so pacing (backoff OR mute — neither of which
        # applies to anything but SENDS) does not gate this; it runs
        # regardless, mirroring how disk maintenance (step 6) already runs
        # regardless. PL-A2a round 3 (F3): only "legacy" (true
        # pre-enrichment) and "incomplete" (current schema, no timestamps)
        # are TERMINAL — dead-lettered here; "held" (unrecognized/absent
        # schema, otherwise well-formed) is left PENDING so a later plugin
        # version that understands its schema can still deliver it.
        if unsendable:
            legacy_sids, held_sids, incomplete_sids = [], [], []
            for sid, (kind, message) in unsendable.items():
                if kind == "held":
                    held_sids.append(sid)
                    continue
                dead_letter_entries.append({"session_id": sid, "status": None, "reason": message})
                dead_letter_ids.add(sid)
                dead_lettered += 1
                (legacy_sids if kind == "legacy" else incomplete_sids).append(sid)
            legacy_sids.sort()
            held_sids.sort()
            incomplete_sids.sort()
            hints = []
            if legacy_sids:
                hints.append(_legacy_row_doctor_hint(legacy_sids))
            if held_sids:
                hints.append(_held_schema_row_doctor_hint(held_sids))
            if incomplete_sids:
                hints.append(_incomplete_row_doctor_hint(incomplete_sids))
            unsendable_hint = " ".join(hints)

        # 4. Backoff/mute ENFORCEMENT (fix 1, widened round 3 F5): either an
        # already-active mute or a still-pacing backoff window skips the
        # SEND loop entirely — zero transport calls — but local disk
        # maintenance (classification above, save state, compact below)
        # still runs regardless, so a paced-off drain still reclaims
        # terminal rows and persists them.
        backoff_until = _loop_ledger._parse_iso(backoff.get("next_attempt_at"))
        backed_off = backoff_until is not None and now < backoff_until
        skip_sends = already_muted or backed_off

        unbuildable_sids = []
        if not skip_sends:
            for sid in sendable_sids:
                if muted_now:
                    break  # a 401/403 this drain stops any further send (spec step 4)
                row = latest_row[sid]
                # PL-A2a flushLagS (AC7): per-session, from the INJECTED
                # clock `now` minus THIS row's own ended_at — never a single
                # shared constant, never a real clock read. A row that
                # somehow reaches here with an unparseable ended_at (should
                # not happen — unsendable rows were already filtered out
                # above) degrades to 0.0 rather than raising.
                ended_dt = _loop_ledger._parse_iso(row.get("ended_at"))
                flush_lag_s = max(0.0, (now - ended_dt).total_seconds()) if ended_dt is not None else 0.0
                # PL-A2a round 3 (F1): a row whose payload cannot be built
                # (e.g. a hostile/malformed `skills` value —
                # `build_wire_payload`'s own `sorted(skills)` raises
                # TypeError) must NEVER escape this loop as an exception —
                # that would abort `drain()` entirely, before `_save_state`
                # runs, silently losing every ack already obtained earlier
                # in THIS SAME drain and wedging the tenant's outbox on
                # every subsequent drain (the same row would raise again).
                # Handled exactly like any other locally-unsendable row:
                # dead-lettered here with a distinguishing reason, the loop
                # simply continues to the next row.
                try:
                    wire_payload = build_wire_payload(
                        row, tenancy,
                        flush_lag_s=flush_lag_s,
                        pending_backlog_count=pending_backlog_count,
                        evicted_count=evicted_count)
                except Exception as exc:
                    dead_letter_entries.append({
                        "session_id": sid, "status": None,
                        "reason": f"payload could not be built: {exc!r}"})
                    dead_letter_ids.add(sid)
                    dead_lettered += 1
                    unbuildable_sids.append(sid)
                    continue
                # PL-A2a round-2 D3: `session_id` travels to `transport` OUT
                # OF BAND — `transport(payload, session_id)` — never merged
                # into the wire payload dict itself. `wire_payload` (from
                # `build_wire_payload`) stays the pure, closed 16-key server
                # shape (AC3) all the way to `transport`; the "wire bytes are
                # correct" guarantee is therefore a property of THIS dict,
                # not of any one transport implementation choosing to strip a
                # widened key back off again before serializing.
                try:
                    response = transport(wire_payload, sid)
                except Exception:
                    response = None  # a transport that raises degrades to "retry", never ack
                sent += 1
                status = getattr(response, "status", None) if response is not None else None
                kind = _classify(status)

                if kind == "ack":
                    delivered.add(sid)
                    acked += 1
                    had_ack = True
                elif kind == "dead_letter":
                    dead_letter_entries.append({"session_id": sid, "status": status})
                    dead_letter_ids.add(sid)
                    dead_lettered += 1
                elif kind == "mute_auth":
                    muted_now = True
                    mute_reason_now = _MUTE_401_REASON
                    state["mute"] = {"until": (now + _MUTE_401_DURATION).isoformat(), "reason": _MUTE_401_REASON}
                    doctor_hint = _DOCTOR_HINT_401
                elif kind == "mute_kill":
                    muted_now = True
                    mute_reason_now = _MUTE_403_REASON
                    state["mute"] = {"until": (now + _MUTE_403_DURATION).isoformat(), "reason": _MUTE_403_REASON}
                    doctor_hint = _DOCTOR_HINT_403
                else:  # retry: 429/503/unknown -> bump backoff, never ack, never drop
                    retried += 1
                    had_retry = True

            if unbuildable_sids:
                unbuildable_sids.sort()
                unbuildable_hint = _unbuildable_row_doctor_hint(unbuildable_sids)

            # Backoff attempts increment AT MOST ONCE per drain (fix 6), not
            # once per retried session, and a drain that still had a retry
            # never resets backoff even if it also had an ack this round.
            if had_retry:
                attempts = backoff.get("attempts", 0) + 1
                delay = min(_BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), _BACKOFF_MAX_SECONDS)
                backoff = {"attempts": attempts, "next_attempt_at": (now + timedelta(seconds=delay)).isoformat()}
            elif had_ack:
                backoff = {"attempts": 0, "next_attempt_at": None}

        state["delivered"] = sorted(delivered)
        state["dead_letter"] = dead_letter_entries
        state["backoff"] = backoff

        # 5. State is persisted BEFORE the spool is compacted (fix 2) — a
        # crash between the two must never lose a terminal row's disposition.
        _save_state(tenancy, state)

        # 6. Durable caps / compaction — the A1b-F2 invariant (see
        # _compact_spool): reclaim only terminal rows, never a still-pending
        # one. A 401/403 mute this drain already owns doctor_hint, so a
        # backlog hint never clobbers it. Runs even when `backed_off` — disk
        # maintenance is independent of whether sends were paced this round.
        compaction_hint = _compact_spool(spool_path, rows, delivered, dead_letter_ids, cap)
        if doctor_hint is None:
            doctor_hint = compaction_hint

        # PL-A2a round-2 D5 fix (widened round 3, F1): combine every locally-
        # derived hint (captured, each ONCE, before or during the send loop,
        # into its own variable that nothing else in this function ever
        # writes to) with whatever mute/backlog hint this drain also
        # produced. Round 1's code assigned mute's hint directly into the
        # SAME `doctor_hint` variable the unsendable-row branch had already
        # set, with no `is None` guard — so a same-drain 401/403 silently
        # discarded the only local signal that rows were dropped, despite
        # this function's own prior comment claiming otherwise. Combining at
        # the very end, from variables that are each written in exactly one
        # place, makes the survival guarantee structural rather than a
        # discipline every future branch must remember to preserve.
        final_hint = " ".join(h for h in (unsendable_hint, unbuildable_hint, doctor_hint) if h) or None

        # PL-A2a round 3 (F5): `muted` in the result reflects EITHER an
        # already-active mute this drain honored (sends skipped) OR a NEW
        # mute this drain's own send loop just triggered — never only the
        # latter, which would silently report `muted=False` on a drain that
        # in fact made zero sends because it was already muted.
        final_muted = muted_now or already_muted
        final_mute_reason = mute_reason_now if muted_now else (mute.get("reason") if already_muted else None)

        pending = len([sid for sid in latest_row if sid not in delivered and sid not in dead_letter_ids])
        return _drain_result(sent, acked, dead_lettered, retried, final_muted, final_mute_reason, final_hint, pending)


# --------------------------------------------------------------------------- #
# Production transport factory. Never exercised by the gate (stdlib
# urllib only, config-gated by the caller passing a real `endpoint_url`).
# --------------------------------------------------------------------------- #

def make_urllib_transport(endpoint_url, jwt_provider):
    """A `transport` callable that POSTs `payload` as JSON to `endpoint_url`
    via stdlib `urllib`. `jwt_provider` is called ONCE PER REQUEST, at call
    time — the token is never cached, stored, or logged by this module. A
    falsy `endpoint_url` ("no endpoint configured yet") returns None, so the
    caller's `drain(tenancy, make_urllib_transport(cfg.get("endpoint"), ...))`
    degrades to the spool-only NO-OP path without a separate feature flag."""
    if not endpoint_url:
        return None

    def _transport(payload, session_id=None):
        # PL-A2a round-2 D3: `drain()` calls EVERY transport — this real
        # urllib one included — as `transport(payload, session_id)`, with
        # `session_id` OUT OF BAND. `payload` is therefore ALREADY
        # `build_wire_payload`'s own pure, closed 16-key shape (the one
        # verified live to return 200) — nothing to strip here; the bytes
        # POSTed are `payload` itself, byte-for-byte. `session_id` is accepted
        # for interface symmetry with every other `transport` in this module
        # (FakeTransport included) but not needed to build the request: the
        # response is correlated back to its session by `drain()`'s own call
        # site, not by this closure.
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {jwt_provider()}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                # Bounded read (fix 5): the ack/error body is just `{"id": ...}`
                # or a short message — never an unbounded read from a
                # hostile/oversized endpoint response.
                raw = resp.read(_MAX_RESPONSE_BYTES)
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read(_MAX_RESPONSE_BYTES)
            status = e.code
        except Exception:
            # DNS/timeout/connection-refused/etc: no HTTP status available.
            # Status 0 classifies as "retry" in `_classify`, never ack.
            return Response(0, None)
        try:
            body_parsed = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            body_parsed = None
        return Response(status, body_parsed)

    return _transport
