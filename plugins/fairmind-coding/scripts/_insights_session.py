#!/usr/bin/env python3
"""PL-A1a — the OPT-scoped ambient telemetry SESSION GATE (first slice of PL-A1).

This module holds ALL of the gate logic; two thin bash hooks
(`session-start-insights.sh` / `session-end-insights.sh`) shell out to it. It is
stdlib-only and unit-testable in isolation, a peer of PL-A0's `_loop_ledger.py`
(whose atomic-append discipline it reuses for the registry).

What A1a lays down (NO digester, NO outbox — those are A1b/A1c):

  * A SessionStart gate that is re-evaluated FRESH every session from LIVE config
    + consent VALUES (PCF-16: never from the mere existence of a leftover file, so
    a stale/planted registry row can never re-arm capture).
  * A per-tenant session registry (one JSONL row per live session) + a per-tenant
    one-time consent-notice flag, under a WRITABLE per-user data dir
    (`~/.fairmind/`, overridable via `FAIRMIND_INSIGHTS_HOME` so the suite runs
    hermetically).
  * A SessionEnd end-marker that stamps `ended_at` on the matching row.

Consent model (plan §6 dec 9 + SPIKE-A + scout, already decided):

  * FAIL CLOSED unless the repo has a PER-PROJECT Fairmind MCP configured — a
    user-global Fairmind entry (`~/.claude/settings.json` or top-level
    `~/.claude.json` mcpServers) MUST NOT arm capture (plan V7 "no honest
    tenant", the privacy guard). Any uncertainty (no `~/.claude.json`, non-git
    cwd, malformed JSON, unresolvable tenancy) resolves to capture=false.
  * Default-ON where configured + one-time notice + either-scope opt-out
    (repo-root `.fairmind-insights.json` OR per-user `insights-config.json`).

Privacy negative-space: the registry row / any wire-bound value carries ONLY the
OPAQUE tenancy id (a one-way hash of the canonicalized repo toplevel) — never the
raw repo path, `$HOME`, or the git branch name.
"""

import contextlib
import glob
import json
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# POSIX-only advisory file locking (the ambient digester's per-session flock,
# PL-A1b). Guarded the same way `_loop_ledger.py` guards it, so this module
# still IMPORTS on a non-POSIX host; `run_sweep` degrades to best-effort (no
# real cross-process mutual exclusion) when `fcntl` is unavailable rather than
# refusing to run.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _fcntl = None

# PL-A0 shared ledger primitives: a locked/bounded append and an atomic rewrite
# that folds in concurrent appends. Reuse them rather than re-roll lock/atomic
# logic here (the exact drift trap `_loop_ledger.py` itself was created to avoid).
# Sibling module in scripts/; make our own dir importable the way `_loop_ledger`
# imports `loop_tokens`.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _loop_ledger import append_row, _atomic_write_lines  # noqa: E402
# PL-A1b's pure transform (the digester); a sibling module, imported top-level
# like `_loop_ledger` above — no circularity (ambient_digest only imports
# `_usage_dedup`).
import ambient_digest  # noqa: E402

_SCHEMA = "fm-insights.session/1"

# Anchored Fairmind-key match: a genuine key is exactly "fairmind" or begins with
# a "fairmind" SEGMENT (followed by a non-alphanumeric boundary or end of string),
# case-insensitive. So "Fairmind", "Fairmind-dev", "fairmind_local" arm, but a key
# that merely CONTAINS the substring ("not-fairmind-proxy", "fairmindish") does
# NOT false-positive-arm (Grok #7).
_FAIRMIND_NAME_RE = re.compile(r"^fairmind(?![a-z0-9])", re.IGNORECASE)


def _is_fairmind_name(key):
    """True iff `key` names the Fairmind MCP (anchored, not a bare substring)."""
    return isinstance(key, str) and _FAIRMIND_NAME_RE.match(key) is not None


def _fairmind_keys(mapping):
    """The anchored Fairmind key names in `mapping` (empty list if it is not a
    dict or has no Fairmind key). Returns the names, not a bool, so the caller can
    cross-check them against a disabled-servers guard (N1)."""
    if not isinstance(mapping, dict):
        return []
    return [key for key in mapping if _is_fairmind_name(key)]


# --------------------------------------------------------------------------- #
# Paths (all under a WRITABLE per-user data dir, override for hermetic tests).
# --------------------------------------------------------------------------- #

def data_dir():
    """The writable per-user data dir. `$CLAUDE_PLUGIN_ROOT` is a read-only
    sha-addressed cache, so state cannot live there. Honors
    `FAIRMIND_INSIGHTS_HOME` (the test suite points it at a temp dir so it never
    touches the real ~/.fairmind); defaults to ~/.fairmind, which is
    gitignore-immune (it lives under $HOME, never inside a repo work tree)."""
    override = os.environ.get("FAIRMIND_INSIGHTS_HOME")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".fairmind")


def _registry_path(tenancy):
    return os.path.join(data_dir(), "insights", "sessions", tenancy + ".jsonl")


def _consent_path(tenancy):
    return os.path.join(data_dir(), "insights", "consent", tenancy + ".json")


def _user_optout_path():
    # Per-user opt-out (R3), sibling of the data dir root.
    return os.path.join(data_dir(), "insights-config.json")


def plugin_version():
    """Best-effort read of the sibling ../plugin.json version (this module lives
    in <plugin>/scripts/). Returns None on any failure — never raises."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "plugin.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Opaque tenancy: 'fm-' + sha256(realpath(git-common-dir itself))[:16].
# --------------------------------------------------------------------------- #

def _git_rev_parse(cwd):
    """ONE `git rev-parse` returning (toplevel, git_common_dir), each None on
    failure. Combined so the SessionStart fast path does a SINGLE git subprocess
    (well under the 5s hook budget) instead of one per field — the toplevel feeds
    the .mcp.json / opt-out lookups, the common-dir feeds the opaque tenancy."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel", "--git-common-dir"],
            capture_output=True, text=True, timeout=3)
    except Exception:
        return None, None
    if r.returncode != 0:
        return None, None
    lines = r.stdout.splitlines()
    top = lines[0].strip() if len(lines) > 0 else ""
    common = lines[1].strip() if len(lines) > 1 else ""
    return (top or None), (common or None)


def _tenancy_from_common(cwd, common):
    """Derive the OPAQUE tenancy id from the CANONICAL `git rev-parse
    --git-common-dir` ITSELF (NOT its parent dir, NOT --show-toplevel). Hashing
    the common-dir itself still collapses all linked worktrees of one repo to ONE
    tenancy (they share a single common-dir) but keeps sibling submodules /
    separate-git-dir repos DISTINCT (each has its own common-dir under
    <super>/.git/modules/<name>, which the old dirname-based formula collapsed to
    the shared parent, mixing their consent + sessions — N7). realpath collapses
    symlinks (e.g. macOS /tmp -> /private/tmp). It is a one-way hash, and ONLY
    this value is ever persisted or wire-bound. None on any uncertainty (fail
    closed)."""
    if not common:
        return None
    if not os.path.isabs(common):
        common = os.path.join(cwd, common)
    try:
        canonical = os.path.realpath(common)
    except Exception:
        return None
    return "fm-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def resolve_tenancy(cwd):
    """The OPAQUE tenancy id, or None when it cannot be resolved (fail closed).
    A non-git cwd yields None (a Fairmind consumer is always a git repo)."""
    _top, common = _git_rev_parse(cwd)
    return _tenancy_from_common(cwd, common)


# --------------------------------------------------------------------------- #
# The Fairmind-MCP presence signal (fail-closed, PER-PROJECT only).
# --------------------------------------------------------------------------- #

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def fairmind_configured(cwd, toplevel):
    """True iff a PER-PROJECT Fairmind MCP is configured for `cwd`, read from LIVE
    config — never a marker file. Fail-closed on any uncertainty. `toplevel` is
    the pre-resolved git toplevel (None if unresolvable).

    PRIMARY: ~/.claude.json -> projects[<cwd>].mcpServers has an anchored Fairmind
    key that is NOT in projects[<cwd>].disabledMcpServers (a malformed disabled
    guard fails closed) — with a launched-in-subdir fallback to
    projects[<git toplevel>].
    SECONDARY: a repo-root team-committed .mcp.json whose mcpServers has a Fairmind
    key AND that key is EXPLICITLY approved by THIS project entry — listed in
    projects[<proj>].enabledMcpjsonServers (or enableAllProjectMcpServers is True)
    and NOT in disabledMcpjsonServers. Mere presence of a committed key does NOT
    arm (owner-confirmed): a fresh clone must never start capturing unapproved, so
    no project entry => secondary is off.
    MUST NOT COUNT: a user-global Fairmind entry (top-level ~/.claude.json
    mcpServers or ~/.claude/settings.json) — counting it makes every repo a
    tenant (violates V7)."""
    claude_json = _read_json(os.path.join(os.path.expanduser("~"), ".claude.json"))
    projects = claude_json.get("projects") if isinstance(claude_json, dict) else None
    projects = projects if isinstance(projects, dict) else {}

    # Resolve the project entry: exact cwd string first (how the harness keys it),
    # then the git toplevel as a launched-in-subdir fallback.
    proj = projects.get(cwd)
    if not isinstance(proj, dict):
        proj = projects.get(toplevel) if toplevel else None
        if not isinstance(proj, dict):
            proj = None

    # PRIMARY signal: this project's own mcpServers carries a Fairmind key that
    # is NOT explicitly disabled. If the Fairmind server is listed in this
    # project's disabledMcpServers it must NOT arm; if that guard is present but
    # MALFORMED (not a list) we cannot verify the server is enabled, so we fail
    # closed and do NOT arm on the primary path (N1). Either way we fall through
    # to the secondary .mcp.json explicit-approval path rather than returning
    # early — a separate, well-formed secondary approval may still arm.
    if isinstance(proj, dict):
        matched = _fairmind_keys(proj.get("mcpServers"))
        if matched:
            disabled = proj.get("disabledMcpServers")
            if disabled is None:
                return True  # nothing disabled -> the primary key arms
            if isinstance(disabled, list) and any(k not in disabled for k in matched):
                return True  # at least one matched key is enabled

    # SECONDARY: a repo-root committed .mcp.json, but ONLY when THIS project entry
    # explicitly approved the Fairmind key. No project entry => not approved.
    if not isinstance(proj, dict) or not toplevel:
        return False
    mcp = _read_json(os.path.join(toplevel, ".mcp.json"))
    servers = mcp.get("mcpServers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict):
        return False
    enable_all = proj.get("enableAllProjectMcpServers") is True
    enabled = proj.get("enabledMcpjsonServers")
    enabled = enabled if isinstance(enabled, list) else []
    disabled = proj.get("disabledMcpjsonServers")
    disabled = disabled if isinstance(disabled, list) else []
    for key in servers:
        if _is_fairmind_name(key) and key not in disabled and (enable_all or key in enabled):
            return True
    return False


# --------------------------------------------------------------------------- #
# Either-scope opt-out (R3). Malformed config -> fail closed (treat as opted out).
# --------------------------------------------------------------------------- #

def _config_disables(path):
    """An opt-out config at `path` DISABLES ambient capture UNLESS it exists,
    parses as a dict, and carries `ambient_capture` as the explicit boolean True.
    Every other present-file shape disables (fail closed / privacy-preserving):
    boolean False, a wrong-typed value (the string "false", 0, null, an array),
    an unreadable/malformed file, or a non-dict. Only an ABSENT file (or an
    explicit boolean True) leaves capture enabled — a wrong-typed value must never
    silently ENABLE capture (N6)."""
    if not os.path.isfile(path):
        return False
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return True  # unreadable/malformed/non-dict -> fail closed
    return cfg.get("ambient_capture") is not True  # only explicit True enables


def is_opted_out(toplevel):
    """Opt-out at EITHER scope disables ambient: the committable repo-root
    .fairmind-insights.json (resolved from the git TOPLEVEL, so a launch from a
    subdirectory still honors a team opt-out), or the per-user
    <data_dir>/insights-config.json. An unresolvable toplevel fails closed
    (treated as opted out)."""
    if not toplevel:
        return True
    if _config_disables(os.path.join(toplevel, ".fairmind-insights.json")):
        return True
    if _config_disables(_user_optout_path()):
        return True
    return False


# --------------------------------------------------------------------------- #
# The gate: FRESH every call from LIVE values (PCF-16).
# --------------------------------------------------------------------------- #

class Decision:
    __slots__ = ("capture", "tenancy", "reason")

    def __init__(self, capture, tenancy, reason):
        self.capture = capture
        self.tenancy = tenancy
        self.reason = reason


def evaluate_gate(cwd):
    """Decide should-capture FRESH from live config/consent VALUES — no marker
    file is ever consulted as the signal (PCF-16). Fail-closed at every step."""
    # Resolve git toplevel + tenancy in ONE subprocess, shared by every step
    # below (Grok #8: no repeated git calls on the SessionStart fast path).
    toplevel, common = _git_rev_parse(cwd)
    tenancy = _tenancy_from_common(cwd, common)
    if not tenancy:
        return Decision(False, None, "no_tenancy")
    if not fairmind_configured(cwd, toplevel):
        return Decision(False, tenancy, "not_configured")
    if is_opted_out(toplevel):
        return Decision(False, tenancy, "opted_out")
    return Decision(True, tenancy, "capture")


# --------------------------------------------------------------------------- #
# Registry + consent state (atomic writes; opaque tenancy only).
# --------------------------------------------------------------------------- #

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# The SessionStart `source` values the harness emits; anything else normalizes to
# "other" so no arbitrary payload string is persisted verbatim into a row (N2b).
_ALLOWED_ENTRY_SOURCES = {"startup", "resume", "compact", "clear", "other"}
_MAX_SESSION_ID_LEN = 200
_BAD_ID_CHARS = frozenset("/\\\n\r\t")


def _clean_session_id(session_id):
    """A plausible session id: a non-empty, bounded string with no path separator
    or control char. Anything else (odd type, over-long, or path/newline junk like
    '../../etc/passwd\\ninjected') is DROPPED to "" so a raw path can never be
    injected into a supposedly path-free registry row (N2b). Applied identically
    on register and on end-match so a dropped id still pairs with its end-marker."""
    if not isinstance(session_id, str):
        return ""
    if not session_id or len(session_id) > _MAX_SESSION_ID_LEN:
        return ""
    if any(c in _BAD_ID_CHARS for c in session_id):
        return ""
    return session_id


def _clean_entry_source(entry_source):
    """Allowlist the SessionStart source; an unknown/odd value -> "other" (N2b)."""
    return entry_source if entry_source in _ALLOWED_ENTRY_SOURCES else "other"


def _ensure_private_dir(directory):
    """Create `directory` (and its immediate parent) and lock both to mode 0700 —
    owner-only — regardless of a permissive ambient umask (N5). Best-effort; never
    raises. `append_row`'s own makedirs(exist_ok=True) then leaves the mode
    untouched, so the sessions/consent dirs stay private."""
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return
    for d in (directory, os.path.dirname(directory)):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _ensure_private_file(path):
    """Ensure the registry/consent file at `path` exists mode 0600 — owner-only —
    before it is appended to, so `open(path, "a")` under a 0022 umask never lands
    it 0644 world-readable (N5). Best-effort; never raises."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_registry_lines(path):
    """The non-blank lines of the registry at `path`, or None if it cannot be
    read (absent/unreadable). Never raises."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return [ln for ln in fh if ln.strip()]
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Tenancy-wide registry lock (PL-A1b review Fix 3).
#
# The per-session flock in `_digest_one_session` only ever protects the SAME
# session against a second concurrent digest of ITSELF; it says nothing about
# two DIFFERENT sessions' registry rewrites racing on the ONE shared registry
# file. `_atomic_write_lines`'s reconcile-on-replace only re-folds a file that
# grew LONGER since the snapshot (a concurrent APPEND) — a same-length
# concurrent REWRITE (a concurrent stamp/end-marker) is invisible to it, so
# whichever writer's atomic replace lands LAST silently clobbers the other's
# already-applied change. `_stamp_digested` and `mark_session_end` — the two
# rewriters that matter for this fix's own RED tests — now hold this lock
# across their OWN read + `_atomic_write_lines`, so two concurrent rewrites of
# the SAME tenancy's registry always serialize instead of racing.
# `_touch_open_entry_source` deliberately does NOT take it; see its own
# docstring for why (a real, test-proven self-deadlock via `fcntl.flock`'s
# per-file-description, non-reentrant scoping, not a hypothetical concern).
# --------------------------------------------------------------------------- #

def _registry_lock_path(tenancy):
    return os.path.join(data_dir(), "insights", "locks", tenancy + ".registry.lock")


@contextlib.contextmanager
def _registry_write_lock(tenancy):
    """Serialize a registry read-modify-write rewrite TENANCY-WIDE. Blocking
    `fcntl.flock` (never `LOCK_NB` — a concurrent rewriter must WAIT its turn
    and still land its change, never skip and lose it), guarded the same way
    every other lock in this module is (`if _fcntl is not None`) so it
    degrades to a best-effort no-op (no real cross-process exclusion) on a
    non-POSIX host rather than refusing to run.

    This is a DIFFERENT lock file from the per-session digest lock
    (`_lock_path`), acquired only for the brief span of one rewrite and always
    released before the caller returns — never held while blocking
    indefinitely on anything else — so it cannot deadlock against the
    per-session lock: the per-session lock (held, at most, by
    `_digest_one_session`) may enclose an acquisition of THIS lock (via
    `_stamp_digested`), but this lock never tries to acquire a per-session
    lock, so the two can only nest in one direction, never both ways around."""
    lock_dir = os.path.join(data_dir(), "insights", "locks")
    _ensure_private_dir(lock_dir)
    lock_path = _registry_lock_path(tenancy)
    _ensure_private_file(lock_path)
    lock_fh = None
    try:
        lock_fh = open(lock_path, "a+")
    except OSError:
        lock_fh = None
    acquired = False
    try:
        if lock_fh is not None and _fcntl is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX)  # blocking
                acquired = True
            except OSError:
                acquired = False
        yield
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


def _touch_open_entry_source(path, session_id, entry_source):
    """Idempotency seam for N4: if a LIVE (ended_at is None) row for `session_id`
    already exists, update its entry_source in place (a repeated SessionStart from
    resume/compaction is the SAME session, not a new one) and return True; else
    return False so the caller appends a fresh row. Best-effort — any failure ->
    False (fall back to append). The rewrite reconciles concurrent appends so a
    row added since the snapshot is never clobbered.

    Deliberately does NOT take the tenancy-wide registry lock (Fix 3, PL-A1b
    review): `register_session` (this function's only caller) is itself
    sometimes invoked from WITHIN another rewriter's own critical section —
    e.g. `test_pla1a_review_fixes.py`'s test_C injects a concurrent
    `register_session` call from inside a patched `_atomic_write_lines` while
    `mark_session_end` still holds the registry lock for the SAME tenancy —
    and `fcntl.flock` is scoped to the open file description, not the
    process/thread, so a second, independent lock acquisition by the same
    thread on a different fd self-deadlocks (blocks forever waiting on a lock
    only that same thread could release). `mark_session_end` and
    `_stamp_digested` carry the lock instead, which already closes the
    concurrent-DIFFERENT-session-stamp race this fix targets."""
    lines = _read_registry_lines(path)
    if not lines:
        return False
    out, found = [], False
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            out.append(ln if ln.endswith("\n") else ln + "\n")
            continue
        if (isinstance(row, dict) and not found
                and (row.get("session_id") or "") == session_id
                and row.get("ended_at") is None):
            row["entry_source"] = entry_source
            found = True
        out.append(json.dumps(row) + "\n")
    if found:
        _atomic_write_lines(path, out, reconcile_from=len(lines))
    return found


def register_session(tenancy, session_id, started_at, entry_source):
    """Register a live session. Carries the OPAQUE tenancy id ONLY — never a raw
    path/branch. `session_id`/`entry_source` are VALIDATED first (N2b): a
    path/newline-bearing or over-long id is dropped to "", and an odd source
    normalizes to "other", so no attacker/odd payload value lands raw in a row.

    Idempotent per session_id (N4): a repeated SessionStart (resume/compaction) for
    an already-registered LIVE session updates that row's entry_source instead of
    appending a duplicate open row. An empty/unknown id cannot be identified, so it
    always appends. The dir/file are pre-created owner-only (0700/0600) so the
    registry is never world-readable under a permissive umask (N5). `append_row`
    (PL-A0) is locked, atomic and bounded and swallows every error, so a wedge here
    skips the row (fail-soft) rather than racing the end-marker with a bare append."""
    session_id = _clean_session_id(session_id)
    entry_source = _clean_entry_source(entry_source)
    path = _registry_path(tenancy)
    _ensure_private_dir(os.path.dirname(path))
    _ensure_private_file(path)
    if session_id and _touch_open_entry_source(path, session_id, entry_source):
        return
    row = {
        "schema": _SCHEMA,
        "tenancy": tenancy,
        "session_id": session_id,
        "started_at": started_at,
        "ended_at": None,
        "entry_source": entry_source,
        "digested_at": None,  # stamped by run_sweep (PL-A1b) once digested
    }
    append_row(path, json.dumps(row), None)


def mark_session_end(tenancy, session_id, ended_at):
    """Stamp `ended_at` on the matching (tenancy, session_id) row. No-op if the
    registry does not exist (a never-captured session writes nothing) — the end
    marker never CREATES a registry.

    The rewrite routes through the shared `_atomic_write_lines` with a
    `reconcile_from` boundary (the snapshot's row count): it re-reads the file
    immediately before the atomic replace and folds in any rows a CONCURRENT
    `register_session` appended since the snapshot, so a live row is never
    clobbered (Grok #3). `session_id` is normalized identically to register (via
    `_clean_session_id`) — a row registered with a missing/None or junk id ("")
    still matches its end-marker (Grok #4). Closes ALL matching open rows: N4 makes
    register idempotent so there is normally one, but if duplicate open rows already
    exist (from before the fix) SessionEnd stamps every matching one.

    Holds the tenancy-wide registry lock (Fix 3) across the read + write, so a
    concurrent _stamp_digested/_touch_open_entry_source rewrite of the SAME
    registry file is serialized rather than racing (last-writer-wins)."""
    path = _registry_path(tenancy)
    with _registry_write_lock(tenancy):
        lines = _read_registry_lines(path)
        if lines is None:
            return
        want = _clean_session_id(session_id)
        changed = False
        out = []
        for ln in lines:
            try:
                row = json.loads(ln)
            except Exception:
                out.append(ln if ln.endswith("\n") else ln + "\n")
                continue
            if (isinstance(row, dict)
                    and (row.get("session_id") or "") == want
                    and row.get("ended_at") is None):
                row["ended_at"] = ended_at
                changed = True
            out.append(json.dumps(row) + "\n")
        if changed:
            _atomic_write_lines(path, out, reconcile_from=len(lines))


# --------------------------------------------------------------------------- #
# PL-A1b — the ambient DIGESTER's SessionStart-driven sweep.
# --------------------------------------------------------------------------- #
#
# `run_sweep` finalizes crash-orphans (a session that ended but never got
# digested, e.g. the process died before its own end-of-session digest could
# run) on the NEXT SessionStart for that tenancy. It never digests a session
# whose per-session lock is held by another process — that's a LIVE digester
# already working it, not an orphan — so a live one is never reaped, only
# skipped this pass (it will be picked up on a later sweep once its holder
# releases the lock).

def _lock_path(tenancy, session_id):
    return os.path.join(data_dir(), "insights", "locks", f"{tenancy}.{session_id}.lock")


def _spool_path(tenancy):
    return os.path.join(data_dir(), "insights", "rollups", tenancy + ".jsonl")


def _spool_append(spool_path, line):
    """Durably append ONE line to the spool (PL-A1b review Fix 1). The spool is
    A1c's DURABLE DRAIN QUEUE — every rollup waits here until something reads
    and clears it downstream — never a bounded ledger, so unlike
    `_loop_ledger.append_row` this function NEVER rotates/trims: all rollups
    are retained regardless of how many accumulate.

    It also NEVER swallows an IO failure (disk full, permission, a directory
    sitting where the spool file belongs, ...): any exception propagates to
    the caller so `_digest_one_session` can decide NOT to stamp `digested_at`
    (Fix 2) rather than silently losing the rollup forever.

    Locking mirrors every other lock in this module: a BLOCKING
    `fcntl.flock` (never `LOCK_NB` — a concurrent appender must WAIT its turn,
    never skip its own write) guarded by `if _fcntl is not None`, degrading to
    a best-effort, non-exclusive append on a non-POSIX host rather than
    refusing to run. `flush()` + `os.fsync()` before releasing the lock make
    the append durable across a crash immediately after this call returns.

    AT-LEAST-ONCE semantics: a crash between a successful append here and the
    registry stamp in `_digest_one_session` leaves that row's `digested_at`
    unset, so the NEXT sweep re-digests and re-appends the same session — a
    duplicate rollup (same session_id) in the spool. This is the accepted
    A1b boundary; A1c's outbox dedups by session_id on drain, so exactly-once
    delivery is A1c's job, not this module's."""
    _ensure_private_dir(os.path.dirname(spool_path))
    _ensure_private_file(spool_path)
    fh = open(spool_path, "a", encoding="utf-8")  # raises IsADirectoryError etc.
    try:
        if _fcntl is not None:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX)  # blocking
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def _find_registry_row(registry_path, session_id):
    """The full registry row dict for `session_id`, or None if the registry
    is missing/unreadable or carries no matching row. Used by
    `_digest_one_session` (PL-A2a) as its FALLBACK ONLY — a fresh re-read of
    the registry when the caller did not already have the row in hand (a
    direct call with no `registry_row=` kwarg, e.g. the pre-existing
    4-positional calls in `test_ambient_spool_durability.py`). A
    missing/non-matching row degrades to None (never raises) — the caller
    then leaves started_at/ended_at unset rather than guessing, which is the
    correct behavior for a direct `_digest_one_session` call made with no
    registry row at all (Property 1's own fixture).

    PL-A2a round 2 (D7): `run_sweep` already parses the WHOLE registry once
    to build its own candidate list, so it now passes the row it already
    parsed straight through via `_digest_one_session`'s `registry_row=`
    keyword instead of making THIS function re-read + re-parse the same
    file a second time per session — this function is reached only on the
    direct-call fallback path, never from `run_sweep` itself."""
    lines = _read_registry_lines(registry_path)
    if not lines:
        return None
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("session_id") == session_id:
            return row
    return None


def _row_already_digested(registry_path, session_id):
    """Fresh re-read of the registry: True iff the row for `session_id` already
    carries `digested_at`. Used AFTER a lock is acquired, as the idempotency
    recheck that makes two racing sweeps converge on exactly one digest — the
    lock only serializes; without this recheck, a sweep that acquires the lock
    just after a concurrent sweep released it (having already digested) would
    digest a second time."""
    lines = _read_registry_lines(registry_path)
    if not lines:
        return False
    for ln in lines:
        try:
            row = json.loads(ln)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("session_id") == session_id:
            if row.get("digested_at"):
                return True
    return False


def _stamp_digested(tenancy, registry_path, session_id, digested_at):
    """Stamp `digested_at` on the matching (session_id) row, mirroring
    `mark_session_end`'s reconcile-on-rewrite discipline so a concurrent
    `register_session`/`mark_session_end` fire is never clobbered.

    Holds the tenancy-wide registry lock (Fix 3) across the read + write: the
    caller's per-session flock only ever protects THIS session against a
    second concurrent digest of itself, never a DIFFERENT session's
    concurrent stamp racing on the one shared registry file — this lock
    closes that gap."""
    with _registry_write_lock(tenancy):
        lines = _read_registry_lines(registry_path)
        if lines is None:
            return
        changed = False
        out = []
        for ln in lines:
            try:
                row = json.loads(ln)
            except Exception:
                out.append(ln if ln.endswith("\n") else ln + "\n")
                continue
            if (isinstance(row, dict) and row.get("session_id") == session_id
                    and row.get("ended_at") and not row.get("digested_at")):
                row["digested_at"] = digested_at
                changed = True
            out.append(json.dumps(row) + "\n")
        if changed:
            _atomic_write_lines(registry_path, out, reconcile_from=len(lines))


def _discover_sidecars(transcript_dir, session_id):
    """Subagent sidecar transcripts for `session_id`: every
    `<transcript_dir>/<session_id>/subagents/agent-*.jsonl`, sorted for
    deterministic aggregation order. The `.jsonl` suffix in the glob excludes
    the sibling `agent-*.meta.json` metadata files (not transcripts). No
    `subagents/` dir (the common no-subagent case) degrades to an empty list
    via `glob.glob`, never raises."""
    pattern = os.path.join(transcript_dir, session_id, "subagents", "agent-*.jsonl")
    return sorted(glob.glob(pattern))


def _digest_one_session(tenancy, session_id, entry_source, transcript_dir, *, registry_row=None):
    """Digest ONE ended-not-digested session under its own per-session flock.
    Wrapped so a bad/missing transcript or a mid-digest error for THIS session
    can never abort the sweep for the others. BUSY (a live digester holds the
    lock) -> skip, never reap; the session is picked up on a later sweep.

    `registry_row` (PL-A2a round 2, D7) is an OPTIONAL keyword: when the
    caller already has this session's parsed registry row in hand (`run_sweep`
    always does — it parses the whole registry once to build its candidate
    list), pass it here and this function uses it AS GIVEN, with zero extra
    disk I/O. When omitted (the pre-existing 4-positional call form, used
    directly by `test_ambient_spool_durability.py` and unaffected by this
    change), this function falls back to `_find_registry_row` exactly as
    before."""
    registry_path = _registry_path(tenancy)
    lock_dir = os.path.join(data_dir(), "insights", "locks")
    _ensure_private_dir(lock_dir)
    lock_path = _lock_path(tenancy, session_id)
    _ensure_private_file(lock_path)
    try:
        lock_fh = open(lock_path, "a+")
    except OSError:
        return

    acquired = False
    try:
        if _fcntl is not None:
            try:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                acquired = True
            except OSError:
                return  # BUSY -> a live digester holds it; skip, never reap
        else:
            acquired = True  # non-POSIX: best-effort, no real mutual exclusion

        # Re-check AFTER acquiring the lock: a concurrent sweep may have already
        # digested this session between our candidate listing and now (the
        # "clean sequential pass" interleaving of the two-sweep race).
        if _row_already_digested(registry_path, session_id):
            return

        transcript_path = os.path.join(transcript_dir, session_id + ".jsonl")
        sidecar_paths = _discover_sidecars(transcript_dir, session_id)
        # PL-A2a: started_at/ended_at are sourced from THIS session's own
        # registry row and copied VERBATIM into meta, so `ambient_digest.
        # digest` can echo them unchanged onto the spool row (AC1) exactly
        # the way it already echoes entry_source. Round 2 (D7): use the
        # caller-supplied `registry_row` AS GIVEN when present (no re-read);
        # only fall back to `_find_registry_row`'s own fresh disk read when
        # the caller did not already have the row in hand.
        if registry_row is None:
            registry_row = _find_registry_row(registry_path, session_id) or {}
        meta = {"session_id": session_id, "tenancy": tenancy, "entry_source": entry_source,
                "started_at": registry_row.get("started_at"), "ended_at": registry_row.get("ended_at")}
        try:
            rollup = ambient_digest.digest_transcript_file(
                transcript_path, meta, sidecar_paths=sidecar_paths)
        except Exception:
            # A transcript that exists but fails to parse must still finalize the
            # session (never re-attempted forever) — spool an explicitly degraded
            # record rather than crashing the sweep. Reuse the digester's own
            # empty-digest so the rollup schema + version oracle live in ONE place
            # (the degraded path never re-encodes digest()'s output shape).
            rollup = ambient_digest.digest([], meta)
            rollup["parserDegraded"] = True

        # Fix 1/2 (PL-A1b review): the spool is a durable drain queue, never a
        # bounded ledger, so it is appended via `_spool_append` (locked,
        # non-rotating, never swallows an IO failure) rather than
        # `_loop_ledger.append_row` (rotates past a 2000-row cap AND swallows
        # every append error). `digested_at` is stamped ONLY after a verified
        # successful append — a failed append must leave the row eligible so
        # the NEXT sweep retries it, rather than losing the rollup silently
        # while the registry claims it was already digested. See
        # `_spool_append`'s docstring for the resulting AT-LEAST-ONCE
        # semantics (a crash between append and stamp yields a duplicate on
        # the next sweep; A1c's outbox dedups by session_id on drain).
        spool = _spool_path(tenancy)
        try:
            _spool_append(spool, json.dumps(rollup))
        except Exception:
            return  # append failed -> stay eligible, retried on the next sweep
        _stamp_digested(tenancy, registry_path, session_id, _now_iso())
    finally:
        try:
            if acquired and _fcntl is not None:
                _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            lock_fh.close()
        except OSError:
            pass


def run_sweep(cwd, transcript_dir):
    """SessionStart-driven ambient sweep (PL-A1b): for `cwd`'s resolved tenancy,
    digest every registry row that is ENDED but not yet DIGESTED. Idempotent
    (an already-digested row is skipped) and never raises — a bad session, a
    missing transcript, or an unresolvable tenancy degrades to a no-op sweep,
    never a crash out of a background process a hook spawned detached.

    PL-A2a round 2 (D7): this function already parses the WHOLE registry once
    (right here) to build `candidates` — each candidate's own already-parsed
    row is carried through and handed to `_digest_one_session` via its
    `registry_row=` keyword, so that function never re-reads + re-parses the
    same registry file a second time per session (O(sessions x registry rows)
    of pure re-work at every session open, on the pre-fix tree)."""
    try:
        tenancy = resolve_tenancy(cwd)
        if not tenancy:
            return
        lines = _read_registry_lines(_registry_path(tenancy))
        if not lines:
            return
        candidates = []
        for ln in lines:
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("ended_at") and not row.get("digested_at"):
                session_id = row.get("session_id")
                if session_id:
                    candidates.append((session_id, row.get("entry_source"), row))
        for session_id, entry_source, registry_row in candidates:
            try:
                _digest_one_session(tenancy, session_id, entry_source, transcript_dir,
                                     registry_row=registry_row)
            except Exception:
                continue  # one bad session must never abort the sweep for the rest
    except Exception:
        return


def notice_needed(tenancy):
    """The one-time consent notice is due iff there is no VALID consent marker for
    THIS tenant. A corrupt / unreadable / wrong-tenancy marker is NOT trusted as
    'already shown' — it re-shows the notice (err toward consent, never toward
    SILENT capture: N3). Only a well-formed marker whose `tenancy` matches suppresses
    it, so a planted/other-tenant file cannot silence a genuine first-time notice."""
    path = _consent_path(tenancy)
    if not os.path.isfile(path):
        return True
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        return True  # corrupt/unreadable -> re-show
    if cfg.get("tenancy") != tenancy:
        return True  # wrong-tenancy marker -> re-show
    return False


def record_notice(tenancy, version):
    """Persist the one-time-notice flag so the notice is never re-shown. Uses the
    shared `_atomic_write_lines` (mkstemp -> os.replace, so the file lands 0600);
    it does not create parent dirs, so ensure the consent dir exists first, locked
    owner-only (0700) like the sessions dir (N5)."""
    path = _consent_path(tenancy)
    _ensure_private_dir(os.path.dirname(path))
    _atomic_write_lines(path,
                        [json.dumps({"tenancy": tenancy, "notice_shown_at": _now_iso(),
                                     "plugin_version": version}) + "\n"])


# --------------------------------------------------------------------------- #
# The one-time consent notice copy (systemMessage — the HUMAN channel).
# --------------------------------------------------------------------------- #

def notice_message():
    """Self-contained, unmissable copy (it renders alongside other startup output
    like '1 MCP server needs authentication'). Names the opt-out path and carries
    NO raw repo path / branch."""
    return ("Ambient insight capture is ON for this Fairmind project "
            "(harness-audit trends, agent decisions, loop/token/tool stats). "
            "Opt out any time in .fairmind-insights.json at the repo root "
            "(or per-user in ~/.fairmind/insights-config.json).")


# --------------------------------------------------------------------------- #
# CLI entrypoints for the thin hooks. ALWAYS exit 0 (fail-open).
# --------------------------------------------------------------------------- #

def _read_payload():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        p = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}
    return p if isinstance(p, dict) else {}


def _resolve_cwd(payload):
    """The cwd comes ONLY from the SessionStart/End payload (a confirmed harness
    field). We NEVER fall back to $CLAUDE_PROJECT_DIR or os.getcwd() to arm: a
    malformed/empty payload (-> {}) or one with no usable `cwd` field must make the
    hook NO-OP, not arm off the process/env cwd of whatever repo the session
    happens to open in (N2a). None -> the caller returns without touching state."""
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def cmd_session_start():
    """SessionStart: fresh-evaluate the gate; on capture, register the session and
    (first time only) emit the one-time consent notice as strict JSON on stdout.
    A virgin / non-Fairmind / opted-out session writes NOTHING and prints
    NOTHING."""
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    if not cwd:
        return  # N2a: no usable cwd in the payload -> no-op, never arm off env/getcwd.
    decision = evaluate_gate(cwd)
    if not decision.capture:
        return  # fail closed: no registry, no consent flag, no stdout.

    register_session(decision.tenancy,
                     payload.get("session_id"),
                     _now_iso(),
                     payload.get("source"))

    # One-time notice per (user, repo) on the HUMAN channel. Print FIRST so a
    # crash between print and record re-shows (twice) rather than never.
    if notice_needed(decision.tenancy):
        sys.stdout.write(json.dumps({"systemMessage": notice_message()}))
        sys.stdout.flush()
        record_notice(decision.tenancy, plugin_version())


def cmd_session_end():
    """SessionEnd: stamp ended_at on the matching row. No gate re-eval needed —
    mark_session_end no-ops unless the registry (hence a captured session) exists,
    so a never-captured repo writes nothing."""
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    if not cwd:
        return  # N2a: no usable cwd -> no-op (never resolve tenancy off env/getcwd).
    tenancy = resolve_tenancy(cwd)
    if not tenancy:
        return
    mark_session_end(tenancy, payload.get("session_id"), _now_iso())


def cmd_sweep():
    """--sweep: the ambient digester's SessionStart-spawned entrypoint (PL-A1b).
    Reads the SAME SessionStart payload shape as cmd_session_start (the hook
    forwards it a second time to a detached, niced background process) and
    derives `transcript_dir` from the payload's own `transcript_path` — the
    harness-provided path to THIS session's transcript file, whose dirname is
    where every sibling session's transcript (and subagent sidecars) also
    live. A payload missing either field no-ops (never guesses a transcript
    dir off env/getcwd, mirroring the N2a discipline of the other commands)."""
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    if not cwd:
        return
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return
    transcript_dir = os.path.dirname(transcript_path)
    if not transcript_dir:
        return
    run_sweep(cwd, transcript_dir)


def main(argv):
    try:
        if "--session-start" in argv:
            cmd_session_start()
        elif "--session-end" in argv:
            cmd_session_end()
        elif "--sweep" in argv:
            cmd_sweep()
    except Exception:
        pass  # fail-open: a session hook must NEVER block or slow session open.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
