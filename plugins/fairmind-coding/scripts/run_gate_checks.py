#!/usr/bin/env python3
"""
run_gate_checks.py — the executed gate of fairmind-coding loop mode.

Reads the authoritative loop-state.json, evaluates every admitted machine
check (and reads evidence verdicts), applies the confirmation-gated stop rule
(K consecutive green evaluations), maintains the budget / consecutive-failure
accounting, and reports a decision that the Stop hook maps to an exit code.

Portable by construction: standard library only, no sandbox dependency.
Tier A hermeticity (Anthropic `srt`) is used when present and requested;
otherwise Tier B applies (k-run determinism probe) and checks are reported
as `hermeticity-unverified`. Determinism is *detected* anywhere; the sandbox
only upgrades detection to prevention.

Exit codes (consumed by hooks/scripts/loop-check.sh):
  0   allow stop   — no active loop, or terminal state reached
                     (passed_pending_human / blocked_*)
  10  iterate      — not green with budget remaining, or green awaiting
                     more confirmations; stdout carries routed feedback
  1   internal error — unreadable / inconsistent loop-state.json

The maker (implementer) never runs this to self-certify: the gate is invoked
by the Stop hook, and each check it runs was authored by an agent other than
the maker (recorded in check.source.authored_by).
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone

# --- exit codes / decisions -------------------------------------------------

EXIT_ALLOW_STOP = 0
EXIT_ITERATE = 10
EXIT_INTERNAL_ERROR = 1

DECISION_NOOP = "noop"
DECISION_ITERATE = "iterate"
DECISION_STOP_PASSED = "stop_passed"
DECISION_STOP_BLOCKED = "stop_blocked"

# Verdicts a single check can yield.
GREEN = "green"
RED = "red"
ERROR = "error"
INCONCLUSIVE = "inconclusive"

# The two vocabularies the contract fixes (T10 / R9). Exported so the doc-agreement
# assertion (AC8(d)) and admit_check have a single machine source of truth instead
# of a hand-copied list that rots.
#
# CRITERION_DISPOSITIONS — the grammar of `contract.criteria[].disposition` (R2):
# three `<prefix>:<check_id>` forms plus the bare `unverifiable`. The `<id>` is
# data, not vocabulary — the members are spelled with the placeholder so the
# grammar reads at a glance; consumers compare on the prefix (see AC8(d)'s
# `_normalize_enum_token`, which splits on the first ":").
CRITERION_DISPOSITIONS = ("checked:<id>", "evidence:<id>", "quarantined:<id>", "unverifiable")

# CHECK_KINDS — the descriptor `kind` vocabulary. `guard` (T10/R4) is the new
# member: a check GREEN at spec by construction. NOT `regression_guard`, which is
# the pre-existing baseline-predicate FIELD (a member of _CONTRACT_FIELDS), never
# a kind value.
CHECK_KINDS = ("machine", "evidence", "guard")

# TERMINAL_STATUSES (adversarial-review amendment A1) — the vocabulary of
# `state["status"]` values that END a loop for good: the one succeeding state
# (`passed_pending_human`) plus every `blocked_*` state a fail-closed guard can
# leave behind (`blocked_budget`/`blocked_failures`/`blocked_timeout` from
# `budget_exhausted`; `blocked_no_checks`; `blocked_scope`; `blocked_worktree`
# from `resolve_work_dir`, H1/F34) or a human recovery action can set
# (`blocked_recovered`, `--recover`). Exported for the same reason as
# CRITERION_DISPOSITIONS/CHECK_KINDS above: a single machine source of truth
# for the doc-agreement assertion (AC8(d)) instead of a hand-copied list that
# rots. `running` is deliberately EXCLUDED — it is the one active,
# non-terminal status; the pre-arm `specified` status (or an absent/unknown
# one) is likewise not a member — this constant names the states a loop STOPS
# in, not every value the `status` field can ever hold.
TERMINAL_STATUSES = (
    "passed_pending_human",
    "blocked_budget",
    "blocked_failures",
    "blocked_timeout",
    "blocked_no_checks",
    "blocked_scope",
    "blocked_recovered",
    "blocked_worktree",
)

# CHECK_ENV_SCRUB (F31/AC4) — every var run_command() strips from a check's
# child environment before subprocess.run. A var belongs here IFF (a) the gate
# or its hooks read it to resolve state / cwd / sandbox / policy, AND (b) a
# check retains a correct OWN-source fallback without it. That second clause
# is what stops this list rotting into "every var that looks gate-ish" — it is
# also why CLAUDE_PLUGIN_ROOT is deliberately NOT a member (see below).
#
# Per-var reason, honestly scoped (they are not equally earned):
#   FAIRMIND_BASE, CWD        — F27's original state pointers. Own-source
#                                fallback: active-context.json / $PWD.
#   CLAUDE_PROJECT_DIR        — the real F31 fix. All six hooks under
#                                hooks/scripts/*.sh resolve
#                                `CWD="${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"` —
#                                this var OUT-RANKS the already-scrubbed CWD,
#                                so F27's scrub was nearly a no-op for any
#                                check that spawns a hook. Fallback: $PWD, the
#                                check's own cwd (subprocess.run(cwd=...)).
#   FAIRMIND_SRT_CMD,
#   FAIRMIND_SRT_PREFIX       — proven cross-resolution: a check that spawns a
#                                NESTED engine would otherwise inherit the
#                                OUTER gate's sandbox config, and the inner
#                                loop can then report AND PERSIST a FALSE
#                                hermeticity_tier ("B"). Fallback: the check's
#                                own PATH/config resolves `srt` independently.
#   FAIRMIND_GATE_DEADLINE_S  — class-closure only; no demonstrated harm (the
#                                OUTER gate's own deadline always fires first
#                                in practice, so a nested engine's inherited
#                                deadline never bites). Included for
#                                consistency with the other policy knobs, not
#                                because a concrete leak was observed.
#
# CLAUDE_PLUGIN_ROOT is DELIBERATELY EXCLUDED. It locates the plugin's CODE,
# not this loop's STATE: two loops share one installed plugin, so there is
# nothing to cross-resolve. It also fails clause (b) above — it has NO
# own-source fallback (hooks/scripts/loop-check.sh:42 fails CLOSED without it,
# "does not point at the fairmind-coding plugin", rc=2) — so scrubbing it would
# turn a working nested invocation into a silent refusal. A guard test
# (test_check_env_hermeticity.py, AC3) pins this exemption: it stays GREEN
# today and must FAIL if CLAUDE_PLUGIN_ROOT is ever added here, because an
# over-scrub would otherwise ship silently — nothing else in the suite would
# notice.
CHECK_ENV_SCRUB = (
    "FAIRMIND_BASE", "CWD", "CLAUDE_PROJECT_DIR",
    "FAIRMIND_SRT_CMD", "FAIRMIND_SRT_PREFIX", "FAIRMIND_GATE_DEADLINE_S",
)

# Visual markers for reports. Glyphs only — no ANSI color: the gate's stdout is
# re-fed to the model as the next turn's input, so terminal escape codes would be
# noise. Emoji render in every modern terminal and stay parseable as text.
_VERDICT_GLYPH = {GREEN: "🟢", RED: "🔴", ERROR: "🟠", INCONCLUSIVE: "🟡"}


def glyph(verdict):
    return _VERDICT_GLYPH.get(verdict, "⚪")  # ⚪ = no prior verdict (new check)

DEFAULT_CONFIRMATION_K = 3

# Fail-closed wall-clock cap for a whole gate evaluation. 540 = the Stop-hook
# timeout (600s) minus a 60s teardown margin, so the engine always finishes and
# reports before the hook can be killed mid-run (a kill could end the turn
# unguarded — a false green). The env var FAIRMIND_GATE_DEADLINE_S can only
# tighten this, never extend it.
DEFAULT_DEADLINE_CAP_S = 540


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(value):
    """Parse an ISO datetime string to an *aware* UTC datetime, or return None if
    absent/garbage/wrong type. The single predicate both `run_gate`'s stamping
    site and `budget_exhausted`'s deadline guard use to agree on what counts as a
    *resolved* `started_at` — so "the stamp is missing" and "the deadline
    can't be computed" can never disagree with each other.

    A naive result (no tzinfo) is pinned to UTC before returning: `budget_
    exhausted` subtracts this from an aware `now_utc()`, and subtracting a naive
    from an aware datetime raises `TypeError` — a hand-written or externally
    supplied `started_at` lacking a "+00:00" offset would otherwise crash the
    deadline guard (exit 1 on every Stop, a wedged loop) instead of resolving.
    A trailing "Z" (UTC designator) is normalized to "+00:00" so it parses on
    every supported Python, not only 3.11+."""
    if not isinstance(value, str) or not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- state resolution & IO --------------------------------------------------

def resolve_state_path(args):
    """Locate loop-state.json.

    Priority: explicit --state, then FAIRMIND_BASE env, then base_path read
    from <cwd>/.fairmind/active-context.json (relative to the repo root),
    then the conventional <cwd>/.fairmind/loop-state.json when that file
    actually exists on disk (never fabricated — see below).
    Returns (state_path or None, cwd).
    """
    cwd = args.cwd or os.environ.get("CWD") or os.getcwd()

    if args.state:
        return os.path.abspath(args.state), cwd

    base = os.environ.get("FAIRMIND_BASE")
    if base:
        return os.path.join(cwd, base, "loop-state.json"), cwd

    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    if os.path.isfile(ctx):
        try:
            with open(ctx, encoding="utf-8") as fh:
                base_path = json.load(fh).get("base_path")
            if base_path:
                return os.path.join(cwd, base_path, "loop-state.json"), cwd
        except (OSError, ValueError):
            return None, cwd
        # active-context.json exists but carries no base_path: fall back to
        # the conventional sibling loop-state.json, but only when it's
        # genuinely there — a missing base_path must never fabricate a path.
        fallback = os.path.join(cwd, ".fairmind", "loop-state.json")
        if os.path.isfile(fallback):
            return fallback, cwd

    return None, cwd


def load_state(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(path, state):
    """Atomic write: temp file in the same dir, then replace."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".loop-state.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# --- signal extraction ------------------------------------------------------

_SELECTOR_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve_selector(obj, selector):
    """Resolve a minimal JSONPath subset: $.a.b[0].c

    Raises KeyError/IndexError/TypeError when the path is absent so callers
    can treat "missing signal" distinctly from a present value.
    """
    if selector in (None, "", "$"):
        return obj
    path = selector[2:] if selector.startswith("$.") else selector.lstrip("$")
    cur = obj
    for key, idx in _SELECTOR_TOKEN.findall(path):
        if idx != "":
            cur = cur[int(idx)]
        else:
            cur = cur[key]
    return cur


def coerce_value(value, value_type):
    if value_type in ("count", "int", "integer"):
        return int(value)
    if value_type in ("number", "float", "duration_ms", "ms"):
        return float(value)
    if value_type in ("bool", "boolean"):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "pass", "passed")
        return bool(value)
    return value


class MissingSignal(Exception):
    """Raised when the signal cannot be located — never read as a pass."""


def extract_signal(check, run):
    """Pull the raw signal from a completed run according to check.signal.

    `run` is a dict: {returncode, stdout, stderr, result_file_json}.
    Raises MissingSignal when the value is absent (clean-signal guarantee).
    """
    signal = check.get("signal", {})
    src = signal.get("from", "exit_code")
    selector = signal.get("selector")

    if src == "exit_code":
        raw = run["returncode"]
    elif src == "file_json":
        data = run.get("result_file_json")
        if data is None:
            raise MissingSignal("result file missing or not JSON")
        try:
            raw = resolve_selector(data, selector)
        except (KeyError, IndexError, TypeError) as exc:
            raise MissingSignal(f"selector {selector!r} not found: {exc}")
    elif src == "stdout_json":
        text = run["stdout"].strip()
        if not text:
            raise MissingSignal("empty stdout")
        try:
            data = json.loads(text)
            raw = resolve_selector(data, selector)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise MissingSignal(f"stdout JSON / selector error: {exc}")
    elif src == "stdout_regex":
        match = re.search(signal.get("pattern", ""), run["stdout"])
        if not match:
            raise MissingSignal("regex did not match stdout")
        raw = match.group(1) if match.groups() else match.group(0)
    else:
        raise MissingSignal(f"unknown signal source {src!r}")

    return coerce_value(raw, signal.get("value_type", "count"))


# --- predicate evaluation ---------------------------------------------------

_OPERATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def eval_predicate(value, predicate):
    op = _OPERATORS.get(predicate.get("operator", "=="))
    if op is None:
        raise ValueError(f"unknown predicate operator {predicate.get('operator')!r}")
    return op(value, predicate.get("value"))


def baseline_value(baseline):
    """A baseline is either a bare number (back-compat) or a provenance object
    `{value, ref, clean}`. Return the numeric value for guard comparison."""
    if isinstance(baseline, dict):
        return baseline.get("value")
    return baseline


def baseline_dirty(baseline):
    """True only for an object baseline explicitly captured on a dirty tree.
    A bare number is assumed clean (nothing to surface)."""
    return isinstance(baseline, dict) and baseline.get("clean") is False


def eval_regression_guards(value, guards, baseline):
    """Guards protect a reduce/improve baseline from regression.

    Each guard: {operator, value|"baseline"}. `baseline` substitutes the
    frozen baseline. Returns (ok, failed_descriptions).
    """
    failed = []
    for guard in guards or []:
        target = guard.get("value")
        if target == "baseline":
            target = baseline
        op = _OPERATORS.get(guard.get("operator", "<="))
        # Fail-closed: a misconfigured guard (unknown operator or an unresolved
        # target such as a missing baseline) must not be silently skipped — that
        # would drop a regression protection and risk a false green.
        if op is None:
            failed.append(f"unknown guard operator {guard.get('operator')!r}")
            continue
        if target is None:
            failed.append(f"regression guard target unresolved (missing baseline?) for {guard}")
            continue
        if not op(value, target):
            failed.append(f"{value} {guard.get('operator')} {target}")
    return (len(failed) == 0, failed)


# --- mutation set (T18) ------------------------------------------------------
# Ground-truths the loop's mutation set against git rather than the trace.
# `hooks/scripts/trace-op.sh` classifies Write/Edit/MultiEdit/NotebookEdit as
# `kind: "mutate"` and Bash as `kind: "exec"`, so a file changed by a *script*
# invoked through Bash (e.g. a heredoc write) leaves no mutate trace op for
# that path — proven live in the T14-T15 internal run (finding F12). Git
# decides WHAT changed; the trace only decorates WHO changed it. Consumed by
# the T8 scope-boundary hard stop (`evaluate_scope`, in the next section) —
# this section only defines the helper itself.

MUTATION_SET_DEGRADED_NO_GIT = "no-git-work-tree"
# A git query INSIDE a real work tree can still fail (unresolvable arm_ref
# after a reset/gc, a loop-state copied into another clone, a typo). Such a
# failure must never be swallowed to `[]`: with a bad arm_ref the
# tracked-modified half of the set would vanish silently while the untracked
# half kept landing, yielding a healthy-looking, non-empty, PARTIAL set —
# worse than an empty one, because nothing signals what is missing.
MUTATION_SET_DEGRADED_GIT_QUERY_FAILED = "git-query-failed"
# No arm-time sha to diff against. Without a baseline ref there is no instant
# to measure "changed since" FROM, so the set is UNKNOWN — not empty. This is
# its own marker (never folded into `git-query-failed`) because its remedy is
# specific and nameable: re-arm the loop, or write the sha. The alternative —
# letting a `None` ref reach a git argv — raised a raw TypeError that escaped
# `_GitQueryError`, so the gate exited 1, never saved state, and left `status`
# stuck on "running": every subsequent Stop re-crashed and `--arm` refuses a
# running loop, so the documented re-arm recovery did not exist.
MUTATION_SET_DEGRADED_NO_BASELINE_REF = "no-baseline-ref"

# The loop's own workspace. Everything under it (loop-state.json, the trace
# ledger, journals) is the loop's BOOKKEEPING, not the run's work product, and
# is excluded from the mutation set STRUCTURALLY — by this constant, not by
# `--exclude-standard` and therefore not by the consumer repo's .gitignore.
# Relying on the ignore was a hard-stop-on-our-own-bookkeeping bug: nothing in
# the plugin ever establishes that precondition (the zero-config bootstrap
# writes active-context.json and never touches .gitignore), so in a repo that
# had not gitignored `.fairmind/`, the gate's own state file and trace landed
# in the untracked half of the set and tripped the scope boundary against the
# loop itself. `--exclude-standard` stays — it still serves the CONSUMER's
# ignores (build output, node_modules, ...), which are genuinely not ours.
LOOP_WORKSPACE_DIR = ".fairmind"


def _is_loop_workspace_path(path):
    """True for a repo-relative path inside the loop's own workspace, which is
    never a member of the mutation set — gitignored or not, tracked or not."""
    return path == LOOP_WORKSPACE_DIR or path.startswith(LOOP_WORKSPACE_DIR + "/")


def _working_tree_sha(cwd, path):
    """A content hash (sha256 of the raw bytes) of the working-tree file at
    `<cwd>/<path>`, or None when it cannot be read (missing, a directory,
    unreadable). The SAME function anchors a pre-dirty path at arm time
    (`pre_dirty_anchors`) and re-checks it in `compute_mutation_set`, so
    "unchanged since arm" is a byte-identity test the loop cannot bluff. It is a
    content anchor, never a security signature — both sides of the comparison
    are the loop's own tree; only equality against the recorded anchor matters."""
    abspath = path if os.path.isabs(path) else os.path.join(cwd, path)
    try:
        with open(abspath, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return "sha256:" + hashlib.sha256(data).hexdigest()


def pre_dirty_anchors(cwd, paths):
    """Build `contract.mutation_set.baseline.pre_dirty` in its ANCHORED shape,
    `[{"path": <repo-relative str>, "sha": <content hash or None>}, ...]`, for
    the given already-dirty paths — called at ARM time so each pre-dirty path is
    frozen to the exact bytes it had when the loop started. `compute_mutation_
    set` marks such a path `pre_existing` ONLY while it stays byte-identical to
    this anchor: a pre-dirty path rewritten mid-run becomes an ordinary member
    subject to scope, so a file dirty at arm can no longer be edited out of
    scope for free. A path whose bytes cannot be read is anchored `None` (it can
    never prove unchanged → fails closed to a scoped member)."""
    return [{"path": p, "sha": _working_tree_sha(cwd, p)} for p in paths]


class _GitQueryError(Exception):
    """Raised by a git-query helper on a non-zero exit. Carries the exact
    "<argv> -> exit <code>: <stderr>" string `compute_mutation_set` surfaces
    verbatim as the degraded result's "error" field — never swallowed to []."""


def _is_git_work_tree(cwd):
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=cwd, capture_output=True, text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _run_git_query(cwd, *args):
    """Run one git subcommand used to build the mutation set. Returns stdout
    on success; raises `_GitQueryError` on a non-zero exit rather than
    swallowing the failure to an empty list — an empty return here used to be
    indistinguishable from "genuinely nothing to report" (AC6).

    Decoding is pinned to UTF-8 with `surrogateescape` rather than left to the
    process locale: under a C/POSIX locale the default decoder would raise on
    the very non-ASCII path bytes `-z` exists to deliver intact, and a decode
    crash here is a wedged gate, not a degraded one."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          encoding="utf-8", errors="surrogateescape")
    if proc.returncode != 0:
        stderr = " ".join(proc.stderr.split())  # whitespace-collapsed, trimmed
        raise _GitQueryError(
            f"git {' '.join(args)} -> exit {proc.returncode}: {stderr}")
    return proc.stdout


def _split_nul(stdout):
    """Split NUL-delimited git output. `-z` is the ONLY way to read a path from
    git verbatim: without it git C-quotes any path with a non-ASCII byte, a
    space or a quote — `src/caffè.ts` arrives as the literal 9-token string
    `"src/caff\\303\\250.ts"`, quotes and octal escapes included. That mangled
    string matches no glob, no pre_dirty entry and no trace target, so a file
    INSIDE the declared scope was reported as an out-of-scope violation and
    hard-stopped the loop. Newline-splitting was also wrong for a path that
    legitimately contains a newline; NUL cannot appear in a path at all."""
    return [p for p in stdout.split("\0") if p]


def _git_changed_paths(cwd, arm_ref):
    """Staged + unstaged + committed-since-arm, diffed against the frozen
    `arm_ref` sha captured at arm time — never live HEAD. This repo's loops
    commit mid-run (T16 committed while running), and diffing live HEAD would
    silently erase every already-committed mutation from the set — the same
    false-empty failure mode T18 exists to kill. Raises `_GitQueryError`
    (never returns a silent partial result) if `arm_ref` cannot be resolved.

    `--no-renames` is load-bearing, not a style choice. Git's default rename
    detection collapses a move into a single R entry and `--name-only` then
    prints ONLY the destination: `git mv legacy/old.ts src/old.ts` under scope
    `src/**` reported exactly `['src/old.ts']` and passed — the out-of-scope
    half of the move, the DELETION of `legacy/old.ts`, never appeared in the
    set at all. Splitting the rename restores both sides, which is what the
    boundary is actually asserting over."""
    stdout = _run_git_query(cwd, "diff", "--name-only", "-z", "--no-renames", arm_ref)
    return _split_nul(stdout)


def _git_untracked_paths(cwd):
    """Untracked paths. `--exclude-standard` honors the CONSUMER's .gitignore
    (build output, node_modules, ...) and is kept for that reason — but it is
    NOT what keeps the loop's own bookkeeping out of the set: `.fairmind/` is
    excluded structurally, by `_is_loop_workspace_path`, precisely because a
    consumer repo is under no obligation to have gitignored it. Raises
    `_GitQueryError` (never a silent partial result) if the query fails."""
    stdout = _run_git_query(cwd, "ls-files", "--others", "--exclude-standard", "-z")
    return _split_nul(stdout)


def _resolve_repo_root(cwd):
    """`realpath(git rev-parse --show-toplevel)` — the anchor the trace's
    absolute `target`s are normalized against. Returns None if the query
    fails (e.g. a stripped-down or corrupted work tree): attribution then
    becomes UNAVAILABLE (every path reports agent "unknown"), but this is
    NOT a degraded marker — git already answered the membership question via
    `_is_git_work_tree`/`_git_changed_paths`/`_git_untracked_paths`; only the
    "who" half is lost, not the "what"."""
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return os.path.realpath(proc.stdout.strip())


def _normalize_trace_target(target, repo_root):
    """Normalize one trace op's `target` to a POSIX, repo-relative path
    comparable to git's own output, or return None if it cannot be
    attributed. `trace-op.sh` writes `target` as the tool's raw
    `tool_input.file_path` — verified ABSOLUTE against this loop's own real
    trace — while git reports repo-relative paths; comparing them raw never
    matches (the amendment-2 bug: every path came back "unknown", including
    ones the trace explicitly attributed). Rules, in order, per
    `contract.mutation_set.attribution.normalization`:

      1. empty / non-string target -> unattributable.
      2. target ending in "..." -> the hook's truncation marker
         (`tr(s, n=120)`, trace-op.sh) -> UNATTRIBUTABLE. Never prefix-,
         substring- or fuzzy-matched: a wrong attribution is worse than
         "unknown".
      3. absolute target -> `relpath(realpath(target), repo_root)`.
         `realpath` BOTH sides (a symlinked temp root, e.g. macOS /tmp ->
         /private/tmp, would otherwise never join). A result that escapes
         the tree (starts with "..") -> unattributable (the op is ignored;
         the git-reported path, if any, is never dropped because of it).
      4. already-relative target -> accepted unchanged (liberal in what we
         accept — the hook's format may vary by tool/version).
      5. POSIX "/" separators — the form git uses.
    """
    if not target or not isinstance(target, str):
        return None
    if target.endswith("..."):
        return None
    if os.path.isabs(target):
        if repo_root is None:
            return None  # attribution unavailable — see _resolve_repo_root
        rel = os.path.relpath(os.path.realpath(target), repo_root)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None  # escapes the work tree — ignore the op
        target = rel
    return target.replace(os.sep, "/")


def _load_trace_attribution(trace_path, repo_root):
    """path -> agent, sourced from `kind == "mutate"` trace lines only (an
    "exec" line must never attribute, even if its `target` string happens to
    match a mutated path). Each `target` is normalized via
    `_normalize_trace_target` before it can join a git-reported path — see
    that function for the absolute/truncated/escaping-target rules. A
    missing trace file, an unreadable line, an unattributable target, or no
    `trace_path`/`repo_root` at all yields no entry for that op — never
    raises, never drops a git-reported path; callers default unattributed
    paths to "unknown".

    Contested paths (more than one mutate op normalizing to the same path)
    resolve to the LAST op in trace order: the trace is append-only
    chronological, so later entries overwrite earlier ones in this dict —
    last-writer-wins. `agent` is reported verbatim, never reformatted."""
    attribution = {}
    if not trace_path or not os.path.isfile(trace_path):
        return attribution
    with open(trace_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                op = json.loads(line)
            except ValueError:
                continue
            if not isinstance(op, dict):
                continue  # a bare JSON scalar/array is not an op — never raise on .get()
            if op.get("kind") != "mutate":
                continue
            agent = op.get("agent")
            if not agent:
                continue
            normalized = _normalize_trace_target(op.get("target"), repo_root)
            if normalized is None:
                continue
            attribution[normalized] = agent  # last mutate op wins (append-only order)
    return attribution


def compute_mutation_set(cwd, arm_ref, pre_dirty=None, trace_path=None):
    """The loop's mutation set, ground-truthed against git — see
    `skills/fairmind-gate/references/loop-state.md` (`contract.mutation_set`)
    for the frozen contract this implements.

    Membership is `union(git diff --name-only <arm_ref>, git ls-files
    --others --exclude-standard)` MINUS the loop's own workspace (`.fairmind/`,
    dropped structurally via `_is_loop_workspace_path`, gitignored or not),
    never trace `kind == "mutate"` alone: a script writing through Bash leaves
    no mutate op for its target (F12), so a trace-only view silently misses it.
    `arm_ref` must be the sha frozen at arm time, not live HEAD (see
    `_git_changed_paths`).

    Attribution decorates, it does not filter: a git-reported path with a
    matching `mutate` trace op — after normalizing that op's `target` to a
    repo-relative POSIX path, see `_normalize_trace_target` — is tagged with
    that op's agent; anything else (no trace file, no matching op after
    normalization, a truncated/out-of-tree target, or a non-mutate op on the
    same target) is reported with `agent: "unknown"` — never dropped. If the
    repo-root query itself fails (`_resolve_repo_root` returns None),
    attribution is UNAVAILABLE — every path reports "unknown" — but
    membership stays intact and this is explicitly NOT a degraded marker
    (git already answered "what changed"; only "who" is unresolved).

    `pre_dirty` (paths already dirty at arm time, supplied by the caller —
    git alone cannot tell *when* a path became dirty) marks a matching path
    `pre_existing: True` ONLY while it is byte-identical to its arm-time content
    anchor; a pre-dirty path rewritten during the run becomes an ordinary
    member, fully subject to scope. Two shapes are accepted: the anchored
    `[{"path","sha"}]` shape (`pre_dirty_anchors`, written by `--arm`) and a
    legacy bare-string list — but a legacy / anchor-less entry cannot be proven
    unchanged and FAILS CLOSED to `pre_existing: False`. Every member is still
    reported, never dropped; policy on them is T8's, not this helper's.

    Three distinguishable degraded markers, checked in this order, `paths`
    always `[]` for any of them:
      - no baseline ref at all (`arm_ref` None/empty) →
        `{"degraded": "no-baseline-ref", "paths": []}`. Checked FIRST, before
        any git argv is built, so a None ref can never reach `git diff` as a
        raw TypeError. The set is UNKNOWN (no instant to measure "changed
        since" FROM), never empty; remedy is re-arm / write the sha.
      - no git work tree at all → `{"degraded": "no-git-work-tree", "paths": []}`.
      - inside a work tree, but a git query used to build the set fails
        (unresolvable `arm_ref`, corrupted object, ...) →
        `{"degraded": "git-query-failed", "paths": [], "error": "<failing
        git argv> -> exit <code>: <stderr>"}`. This is a WHOLE-set failure:
        if either the changed-paths query or the untracked-paths query
        fails, the other query's (possibly successful) result is discarded
        too — a partial set would look like a healthy, non-empty set while
        actually missing half its members (AC6).

    A consumer MUST treat any non-null `degraded` as "the mutation set is
    UNKNOWN", never as "nothing was mutated" — fail closed / surface to a
    human rather than silently proceeding as if the set were empty.
    `paths` is sorted by path for determinism when not degraded.
    """
    # No baseline ref → no arm-time instant to diff "changed since" FROM, so the
    # set is UNKNOWN, not empty. Checked BEFORE any git argv is built: a
    # None/empty ref reaching `git diff <ref>` raises a raw TypeError that
    # escapes `_GitQueryError`, crashing the gate (exit 1, state never saved)
    # instead of degrading. Its own marker — the remedy is re-arm / write the sha.
    if not arm_ref:
        return {"degraded": MUTATION_SET_DEGRADED_NO_BASELINE_REF, "paths": []}

    if not _is_git_work_tree(cwd):
        return {"degraded": MUTATION_SET_DEGRADED_NO_GIT, "paths": []}

    try:
        changed = _git_changed_paths(cwd, arm_ref)
        untracked = _git_untracked_paths(cwd)
    except _GitQueryError as exc:
        # WHOLE-set failure: neither query's result is used, even if the
        # other one succeeded — a partial set is indistinguishable from a
        # complete one and is exactly the false-negative AC6 closes.
        return {"degraded": MUTATION_SET_DEGRADED_GIT_QUERY_FAILED,
                "paths": [], "error": str(exc)}

    # The loop's own workspace (`.fairmind/`) is BOOKKEEPING, never work product.
    # Drop it from both halves STRUCTURALLY — not via `--exclude-standard` —
    # because a consumer repo is under no obligation to have gitignored it, and
    # without this the gate's own state file and trace ledger land in the
    # untracked half and trip the scope boundary against the loop itself.
    members = {p for p in (set(changed) | set(untracked))
               if not _is_loop_workspace_path(p)}
    repo_root = _resolve_repo_root(cwd)  # None -> attribution unavailable, NOT degraded
    attribution = _load_trace_attribution(trace_path, repo_root)

    # pre_dirty carries a per-path arm-time content anchor. A member is
    # pre_existing ONLY while byte-identical to its anchor — a pre-dirty path
    # rewritten during the run is an ordinary member, fully subject to scope.
    # Two shapes are accepted: the anchored [{"path","sha"}] shape (`--arm`
    # writes it via `pre_dirty_anchors`) and the legacy bare-string list. A
    # legacy entry — or an anchored entry whose sha could not be captured —
    # carries NO proof of arm-time content, so it CANNOT be shown unchanged and
    # FAILS CLOSED to pre_existing=False (never exempt on path membership alone).
    anchor_by_path = {}
    for entry in pre_dirty or []:
        if isinstance(entry, dict) and entry.get("path"):
            anchor_by_path[entry["path"]] = entry.get("sha")
        # a legacy bare string records no anchor → absent here → fails closed

    def _pre_existing(path):
        anchor = anchor_by_path.get(path)
        if not anchor:
            return False  # legacy / anchor-less → cannot prove unchanged (fail closed)
        return _working_tree_sha(cwd, path) == anchor

    paths = [
        {
            "path": path,
            "agent": attribution.get(path, "unknown"),
            "pre_existing": _pre_existing(path),
        }
        for path in sorted(members)
    ]
    return {"degraded": None, "paths": paths}


# --- worktree resolution (H1/F34) --------------------------------------------
# `scripts/loop_worktree.py:144` is the SOLE writer of the top-level
# `state["worktree"] = {"path": ..., "branch": "loop/<ref>"}` key (T9's
# opt-in isolation offer). Before this fix nothing in this engine ever READ
# it: every git query and every check subprocess ran against a single `cwd`
# scalar (== `--cwd` == the STATE root), so a loop that opted into worktree
# isolation had its checks silently evaluated against the MAIN tree while the
# maker's actual change lived only in the worktree (F34) — including the T8
# scope boundary, which then passed VACUOUSLY on a worktree-only mutation.
#
# The fix splits that one scalar into three named, independently-motivated
# roles:
#   state_root  — `--cwd` exactly as resolved by `resolve_state_path`. Owns
#                 state resolution (`load_state`/`save_state`) and the run
#                 ledger (`loop_ledger.record_terminal`). NEVER changed by
#                 worktree resolution.
#   work_dir    — `worktree.path` when (and ONLY when) the state records one
#                 that resolves to a real, currently-registered worktree of
#                 state_root's own repo; else `state_root` (AC3: absent key
#                 -> identical to today). Feeds every git query and every
#                 check subprocess: `compute_mutation_set`, and — via
#                 `run_gate` — `evaluate_check`/`run_command` (functional
#                 checks execute against the tree the maker actually edited).
#   trace_root  — ALWAYS `state_root`. `hooks/scripts/trace-op.sh` writes the
#                 trace file under `<CLAUDE_PROJECT_DIR>/.fairmind/trace/`,
#                 i.e. under state_root, and nowhere else — a worktree's own
#                 `.fairmind/` does not exist at all (it is excluded from the
#                 mutation set structurally, `_is_loop_workspace_path`).
#                 Conflating trace_root with work_dir would silently kill
#                 agent attribution (every path would report "unknown")
#                 without ever touching membership.
#
# `resolve_work_dir` is the ONLY function that decides work_dir. A recorded
# `worktree.path` that cannot be PROVEN to be a real, registered worktree of
# state_root's own repo must NEVER be silently substituted with state_root —
# that silent substitution is exactly F34 wearing a different hat. Every
# failure shape below returns a distinct `(reason, detail)` pair; the caller
# (`run_gate`) fails the WHOLE evaluation closed on any non-None
# degradation — no check may run anywhere, and the condition is named in
# PERSISTED state (`status` + an `iterations[]` audit entry), never only on
# stdout.

def resolve_work_dir(state, state_root):
    """Returns `(work_dir, degradation)`.

    `degradation` is `None` in exactly two cases: no `worktree` key at all
    (AC3 — legitimate, backward-compatible no-op, `work_dir == state_root`),
    or a `worktree.path` that resolves to a real, currently-registered
    worktree of `state_root`'s own repo (`work_dir == that path`).

    Otherwise `work_dir` is `None` and `degradation` is a `(reason, detail)`
    pair naming EXACTLY why the recorded worktree could not be trusted. The
    caller MUST fail the evaluation closed on this path — it must never fall
    back to `state_root`.
    """
    wt = state.get("worktree")
    if wt is None:
        return state_root, None  # AC3: nothing declared -> identical to today

    if not isinstance(wt, dict):
        return None, ("worktree-malformed", f"state['worktree'] is not an object: {wt!r}")

    path = wt.get("path")
    if not path or not isinstance(path, str):
        return None, ("worktree-path-missing",
                      f"state['worktree']['path'] is missing/empty/non-string: {path!r}")

    try:
        exists = os.path.exists(path)
        is_dir = exists and os.path.isdir(path)
    except OSError as exc:
        return None, ("worktree-path-unreadable", f"stat({path!r}) failed: {exc}")

    if not exists:
        # VERIFIED expected shape, not exotic: `loop_worktree.py --cleanup`
        # (`do_cleanup`) removes the worktree's directory AND its git
        # registration but never clears `state["worktree"]`, so a loop that
        # ran --cleanup carries exactly this dangling path indefinitely.
        return None, ("worktree-path-not-found",
                      f"worktree.path does not exist on disk: {path!r}")
    if not is_dir:
        return None, ("worktree-path-not-a-directory",
                      f"worktree.path exists but is not a directory: {path!r}")
    if not _is_git_work_tree(path):
        return None, ("worktree-not-git-work-tree",
                      f"worktree.path is not inside a git work tree: {path!r}")

    # Not merely "a" git work tree — a REGISTERED worktree of THIS state's own
    # repo, ruling out a foreign checkout or a copied loop-state.json pointing
    # at an unrelated repo's worktree. Two work trees linked by `git worktree
    # add` share one object database and therefore one `--git-common-dir`.
    try:
        wt_common = _run_git_query(path, "rev-parse", "--git-common-dir").strip()
        root_common = _run_git_query(state_root, "rev-parse", "--git-common-dir").strip()
    except _GitQueryError as exc:
        return None, ("worktree-git-query-failed", str(exc))

    wt_common_real = os.path.realpath(os.path.join(path, wt_common))
    root_common_real = os.path.realpath(os.path.join(state_root, root_common))
    if wt_common_real != root_common_real:
        return None, ("worktree-not-registered",
                      f"worktree.path {path!r} is not a registered worktree of "
                      f"state_root's own repo ({wt_common_real!r} != {root_common_real!r})")

    return path, None


# --- scope boundary (T8) -----------------------------------------------------
# A loop contract may declare an allowed mutation scope (`contract.scope.
# allowed_paths`, a glob list). `run_gate` cross-checks this run's git-derived
# mutation set (T18's `compute_mutation_set` above) against it BEFORE any
# check verdict is considered: a mutated path outside scope is a TERMINAL hard
# stop (`status: "blocked_scope"`) — never an ordinary red, never counted
# against budget. Membership is git (T18), never the trace; the trace only
# attributes *who* mutated a path, decorating the report for the human.

def _active_context_ref(cwd):
    """The active task ref from `<cwd>/.fairmind/active-context.json` —
    `task_ref`, then `taskRef`, else "session" — resolved EXACTLY as
    `hooks/scripts/trace-op.sh` resolves it, so the gate reads the very file the
    hook wrote. Absent or unreadable → "session"."""
    ctx = os.path.join(cwd, ".fairmind", "active-context.json")
    try:
        with open(ctx, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "session"
    return data.get("task_ref") or data.get("taskRef") or "session"


def trace_path(cwd, ref=None):
    """The production trace-file location for `cwd`: `<cwd>/.fairmind/trace/
    <safe>.jsonl`, `safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or
    "session"` — byte-for-byte the path `hooks/scripts/trace-op.sh` writes and
    `scripts/loop_dashboard.py` reads.

    `ref=None` (the scope boundary's live case) resolves the ref the SAME way
    the hook does — from `<cwd>/.fairmind/active-context.json` — NOT from
    loop-state's `target.ref`. The hook keys the filename off active-context and
    never sees loop-state; the two can differ (a session working a task ref
    other than the loop's target), and reading the wrong file made every path
    "unknown", silently erasing attribution. A caller replaying a HISTORICAL row
    (`loop_dashboard`) already knows the row's ref and passes it explicitly."""
    if ref is None:
        ref = _active_context_ref(cwd)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(ref)) or "session"
    return os.path.join(cwd, ".fairmind", "trace", f"{safe}.jsonl")


def evaluate_scope(state, cwd, dry_run=False, trace_root=None):
    """Cross-check this run's git-derived mutation set against a declared
    `contract.scope.allowed_paths`. `cwd` here is `work_dir` (H1/F34: the
    tree to diff — the worktree when the state records a valid one, else
    state_root; see `resolve_work_dir`). `trace_root` is the SEPARATE root
    the trace FILE lives under — ALWAYS `state_root` — and defaults to `cwd`
    when omitted, which is exactly today's behavior for every caller that
    predates the worktree split (work_dir == state_root == trace_root when
    no worktree is recorded, AC3). Returns `None` when there is nothing to
    enforce (AC3: no `contract.scope`, or an empty/missing `allowed_paths` —
    identical to pre-T8 behavior; the absence of a trace file is NOT a
    trigger). Otherwise returns `(audit_entry, feedback)` describing a
    terminal `blocked_scope` stop, either because the mutation set itself is
    degraded/unknown (AC5, fail-closed) or because a genuine out-of-scope
    mutation was found (AC1). The audit entry carries a `"degraded"` key
    (the marker, e.g. `"no-git-work-tree"`) ONLY on the degraded path (A1a);
    a real, non-degraded violation's entry has NO `"degraded"` key at all
    (A1b) — presence/absence of the key is itself the persisted signal
    distinguishing "set unknown" from "set known, violation found".

    `evaluate_check`/`admitted_checks` are never consulted here — this must
    run and short-circuit BEFORE any check verdict is considered (see
    `run_gate`), because a scope violation is not a check result and must
    never be folded into (or masked by) the ordinary green/red decision.

    `dry_run` (F26): pre-arm, `contract.mutation_set.baseline.ref` does not
    exist yet — arming is the only thing that ever freezes it (see `arm()`).
    A missing ref is otherwise fail-closed (`no-baseline-ref`, AC5/W1.4) for
    good reason: on an ARMED loop it signals a genuine, unexplained loss of
    the diff anchor. But on a `--dry-run` smoke run of a never-armed loop
    there is no transient to fail closed against — the baseline is simply not
    frozen yet, exactly as expected pre-arm — so failing closed here only
    prevents the smoke run from ever reaching a single check evaluation
    (F26). Scoped STRICTLY to `dry_run AND never armed`: the real (non-dry-run)
    path below is untouched, and still fails closed to `blocked_scope` /
    `no-baseline-ref` on an armed loop missing its baseline (test_scope_
    boundary.py's W1.4, pinned again as a companion guard in
    test_dryrun_scope_smoke.py).

    A1 (adversarial-review amendment): "never armed" is NOT the same
    predicate as "no `arm_ref`" — `arm()` sets `state["status"] = "running"`
    unconditionally but only writes `contract.mutation_set.baseline.ref` when
    the arm-time git query succeeds, so an ARMED loop can still have no
    `arm_ref` (outside a git work tree, on an unborn HEAD, or under a
    transient git failure at arm time). Gating the deferral on `not arm_ref`
    would wrongly wave such a loop's `--dry-run` smoke run through. The
    deferral is keyed on the T19 write-once `budget.spent.first_armed_at`
    marker instead (see `ever_armed` below), which is set by `arm()` and
    never cleared — so an armed-but-baseline-less loop still fails closed
    even under `--dry-run` (test_dryrun_scope_smoke.py's
    test_dry_run_armed_but_no_baseline_still_fails_closed).
    """
    scope = (state.get("contract") or {}).get("scope") or {}
    allowed_paths = scope.get("allowed_paths")
    if not allowed_paths:
        return None  # AC3: no declaration -> backward-compatible no-op

    baseline = ((state.get("contract") or {}).get("mutation_set") or {}).get("baseline") or {}
    arm_ref = baseline.get("ref")
    # A1 (adversarial-review amendment): the deferral must key off *never
    # armed*, not off `arm_ref` presence. `not arm_ref` is also true for a
    # loop that HAS been armed but couldn't freeze a baseline at arm time
    # (arm() sets status="running" unconditionally, but only writes
    # contract.mutation_set.baseline.ref when _is_git_work_tree(cwd) holds
    # AND rev-parse HEAD resolves AND the arm-time set is not itself
    # degraded) — such a loop must still fail closed under --dry-run, exactly
    # like the non-dry-run path below. `budget.spent.first_armed_at` is the
    # T19 write-once marker `arm()` stamps and never clears, so it is the
    # correct ever-armed signal, computed None-safely against a state that
    # may be missing `budget`/`spent` entirely.
    ever_armed = bool(((state.get("budget") or {}).get("spent") or {}).get("first_armed_at"))
    if dry_run and not ever_armed:
        print("scope enforcement deferred until --arm freezes a baseline (dry-run)",
              file=sys.stderr)
        return None
    pre_dirty = baseline.get("pre_dirty") or []
    # The trace path is resolved through the ONE canonical helper, ref=None →
    # the hook's own active-context.json derivation. NOT state.target.ref: the
    # hook keys the trace filename off active-context, never off loop-state, so
    # keying attribution off target.ref reads a different file and loses every
    # agent to "unknown". Resolved against `trace_root` (ALWAYS state_root,
    # H1/F34) — never `cwd`/work_dir: the hook writes the trace file under
    # state_root's `.fairmind/trace/` and nowhere else, so under a worktree
    # loop the trace FILE stays put while the git diff (below) follows the
    # worktree.
    trace_file = trace_path(trace_root if trace_root is not None else cwd)

    mutation_set = compute_mutation_set(cwd, arm_ref, pre_dirty, trace_file)

    if mutation_set.get("degraded"):
        # AC5 / T18 consumer rule: a degraded set is UNKNOWN, never "nothing
        # mutated" — fail closed rather than risk an undetected out-of-scope
        # mutation slipping through as if the set were empty.
        reason = mutation_set["degraded"]
        detail = f": {mutation_set['error']}" if mutation_set.get("error") else ""
        feedback = (
            "⛔ LOOP STOPPED — blocked_scope: a scope is declared "
            "(contract.scope.allowed_paths) but this run's mutation set could not "
            f"be determined (compute_mutation_set degraded: {reason}{detail}). The "
            "set is UNKNOWN, not empty — failing closed rather than allowing a "
            "possible out-of-scope mutation to go undetected. A human must resolve "
            "the git condition (or re-arm) before this loop can proceed."
        )
        # A1 (amendment): the reason must be readable from the PERSISTED
        # audit entry alone (iterations[], no stdout needed) — naming it only
        # in `feedback` (ephemeral stdout) left a real violation with zero
        # paths indistinguishable from a degraded/UNKNOWN set. The marker is
        # stamped ONLY on this degraded path — never on a real (non-degraded)
        # violation's entry (A1b guard) — so its mere presence means "the set
        # was unknown", never "the set was known and happened to be empty".
        audit_entry = {"event": "scope_violation", "at": iso(now_utc()),
                       "paths": [], "agents": [], "degraded": reason}
        if mutation_set.get("error"):
            audit_entry["error"] = mutation_set["error"]
        return audit_entry, feedback

    violations = [
        p for p in mutation_set["paths"]
        if not p["pre_existing"] and not any(fnmatch.fnmatch(p["path"], g) for g in allowed_paths)
    ]
    if not violations:
        return None  # AC4: every mutated path is in scope

    paths = [v["path"] for v in violations]
    agents = [v["agent"] for v in violations]
    lines = [
        f"⛔ LOOP STOPPED — blocked_scope: {len(violations)} mutated path(s) outside "
        f"the declared scope {allowed_paths!r}:",
    ]
    lines.extend(f"  - {p} (agent: {a})" for p, a in zip(paths, agents))
    lines.append(
        "This is a TERMINAL hard stop — never counted against budget, never an "
        "ordinary check failure. A human must reconcile the scope declaration or the "
        "mutation before this loop can proceed (re-arm after resolving)."
    )
    audit_entry = {"event": "scope_violation", "at": iso(now_utc()), "paths": paths, "agents": agents}
    return audit_entry, "\n".join(lines)


# --- degraded scope self-heal (F28) -----------------------------------------
# A `blocked_scope` stop is TERMINAL by construction (main's `status != "running"`
# guard silent-no-ops every subsequent Stop) — correct for a REAL violation,
# which only a human `--arm` should resolve. But a DEGRADED stop (the mutation
# set itself was UNKNOWN — a git hiccup, a worktree momentarily unavailable,
# the pre-arm no-baseline-ref condition on an already-armed loop, ...) may be
# purely transient: the very next Stop could find the set resolvable again with
# nothing to enforce. The persisted `scope_violation` entry's `"degraded"` key
# (present ONLY on the fail-closed path, A1a/A1b — see `evaluate_scope`) is the
# one signal already on disk that tells the two apart, so self-heal keys off it
# rather than off `blocked_scope` status alone.

# Bounds how many CONSECUTIVE degraded re-evaluations a loop will attempt on
# its own before handing back to a human `--arm`, exactly like a real
# violation — a transient that never clears must not retry forever.
DEGRADED_SCOPE_RETRY_CAP = 3


def _trailing_degraded_scope_run(state):
    """Walk `iterations[]` from the end. Returns `(latest, deg)`: `latest` is
    the most recent entry IF it is a `scope_violation` (else None), and `deg`
    is how many CONSECUTIVE trailing `scope_violation` entries carry the
    `"degraded"` key. The walk stops at the first entry that is either not a
    `scope_violation` or a `scope_violation` with NO `"degraded"` key (a REAL
    violation) — a real violation is itself always terminal (no further
    `scope_violation` entries are ever appended after one, since it is never
    auto-recovered), so in practice this never has to break mid-run except at
    exactly that boundary."""
    latest = None
    deg = 0
    for it in reversed(state.get("iterations", [])):
        if it.get("event") != "scope_violation":
            break
        if latest is None:
            latest = it
        if "degraded" not in it:
            break
        deg += 1
    return latest, deg


def _degraded_scope_recoverable(state):
    """True when a `blocked_scope` loop is eligible for a self-heal
    re-evaluation on the NEXT Stop (F28/AC2): the most recent scope_violation
    entry must carry a `"degraded"` key (a REAL violation, carrying none,
    NEVER auto-recovers — AC2(b)) AND the cap must not be reached yet
    (AC2(c)): `deg < DEGRADED_SCOPE_RETRY_CAP`."""
    latest, deg = _trailing_degraded_scope_run(state)
    return latest is not None and "degraded" in latest and deg < DEGRADED_SCOPE_RETRY_CAP


# --- command execution ------------------------------------------------------

def srt_available():
    return shutil.which(os.environ.get("FAIRMIND_SRT_CMD", "srt")) is not None


def srt_prefix():
    """Tokens prepended to run the inner argv inside `srt`, network denied."""
    return os.environ.get("FAIRMIND_SRT_PREFIX", "srt exec --deny-network --").split()


def build_argv(check, tier):
    """Build the argv to execute, honouring hermeticity.

    A descriptor may provide `exec.argv` (a list — run verbatim, no shell) or
    `exec.command` (a string — run through the platform shell so pipes,
    redirects and env expansion behave as a developer expects). We invoke the
    shell *explicitly* (POSIX `/bin/sh -c` or Windows `cmd /c`) rather than
    subprocess `shell=True`, keeping the shell choice explicit and portable.

    `command` is trusted in-repo config: it lives in loop-state.json, authored
    by the checker-side agent (the QA Engineer / the Code Reviewer), at the same trust level as an
    npm or Make script. Containment of untrusted *code under test* is the
    Tier-A `srt` layer (network-denied), not shell avoidance — our hermeticity
    need is determinism, not defense.

    Tier A wraps the WHOLE inner argv (including the shell) inside `srt`, i.e.
    `srt exec --deny-network -- /bin/sh -c '<command>'`, so pipes/redirections
    run *inside* the sandbox rather than being interpreted by an outer shell.
    If `srt` is absent we degrade to Tier B — the sandbox is never required.
    """
    exec_spec = check.get("exec", {})
    argv = exec_spec.get("argv")
    if isinstance(argv, list) and argv:
        inner = list(argv)  # run verbatim, no shell involved
    else:
        command = exec_spec.get("command", "")
        if os.name == "nt":
            inner = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
        else:
            inner = ["/bin/sh", "-c", command]

    if tier == "A" and exec_spec.get("network") == "forbidden" and srt_available():
        return srt_prefix() + inner
    return inner


def run_command(check, cwd, tier, deadline=None):
    exec_spec = check.get("exec", {})
    timeout_s = exec_spec.get("timeout_s", 300)
    env = dict(os.environ)
    # A check is a hermetic black box: it must resolve its OWN state from its own
    # --state/--cwd/active-context, never silently inherit the outer loop's
    # pointer. Handing it the gate's state-resolution, sandbox, or policy vars is
    # the leak (a check that spawns a nested engine, or one of this plugin's own
    # hooks, would cross-resolve to the OUTER loop's state/config). See
    # CHECK_ENV_SCRUB's docstring above for the membership rule and the
    # deliberate CLAUDE_PLUGIN_ROOT exemption. The check's working directory
    # comes from subprocess.run(cwd=…) below, NOT from any of these env vars, so
    # scrubbing them does not change where the check runs.
    for v in CHECK_ENV_SCRUB:
        env.pop(v, None)

    # Cap this run's timeout to the nearer of the check's own timeout and the
    # remaining gate-deadline budget, so no single check can overrun the gate.
    # A kill caused by the deadline (not the check's timeout) is flagged so the
    # verdict attributes it to the deadline — fail-closed, never the check's fault.
    effective_timeout = timeout_s
    capped_by_deadline = False
    if deadline is not None:
        remaining = deadline - time.monotonic()  # same monotonic clock as the deadline
        if remaining < effective_timeout:
            effective_timeout = max(0.05, remaining)
            capped_by_deadline = True

    # Capture the moment just before execution so we can reject a stale result
    # file that this run did not (re)produce — a stale file would otherwise be a
    # silent false green if the command failed without rewriting it.
    started = time.time()
    try:
        proc = subprocess.run(
            build_argv(check, tier), cwd=cwd, env=env,
            capture_output=True, text=True, timeout=effective_timeout,
        )
        returncode, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        returncode, stdout, stderr, timed_out = 124, exc.stdout or "", exc.stderr or "", True

    run = {"returncode": returncode, "stdout": stdout, "stderr": stderr,
           "timed_out": timed_out, "deadline_timeout": timed_out and capped_by_deadline,
           "result_file_json": None}

    # Load a JSON result file when the check reports its signal through one —
    # but only if it was produced by THIS run (mtime at/after start). A missing
    # or stale file leaves result_file_json = None → MissingSignal downstream.
    signal = check.get("signal", {})
    if signal.get("from") == "file_json":
        result_file = signal.get("file") or exec_spec.get("result_file")
        if result_file:
            fpath = result_file if os.path.isabs(result_file) else os.path.join(cwd, result_file)
            try:
                if os.path.getmtime(fpath) + 1e-3 >= started:
                    with open(fpath, encoding="utf-8") as fh:
                        run["result_file_json"] = json.load(fh)
                # else: stale (older than this run) → treated as missing signal
            except (OSError, ValueError):
                run["result_file_json"] = None
    return run


# --- single-check evaluation ------------------------------------------------

def maker_checker_error(check):
    """maker != checker: the check must be authored by an agent other than the
    maker who fixes it. Returns a reason string when violated, else None. The
    engine enforces this structurally so a self-authored check can never close
    the loop even if admission missed it. Both roles must be explicit — a
    missing owner or authored_by makes the separation unverifiable, which is an
    error, never a pass."""
    authored_by = check.get("source", {}).get("authored_by")
    owner = check.get("owner")
    if not owner:
        return "check has no owner (maker unknown; maker != checker unverifiable)"
    if not authored_by:
        return "check has no source.authored_by (maker != checker unverifiable)"
    if authored_by == owner:
        return f"check authored_by ({authored_by}) == owner ({owner}); maker != checker violated"
    return None


# Fields that define the check contract. If any changes after admission the
# descriptor was tampered with (e.g. a maker relaxing the predicate) — the
# engine recomputes this hash and refuses to gate on a mutated descriptor.
# (External test-artifact immutability + a git-identity firewall remain in the
# hardening backlog; this closes the in-descriptor mutation path.)
#
# The hash must cover EVERY descriptor field a verdict is computed from, or a
# post-admission edit of an uncovered field silently changes the verdict without
# tripping `integrity_error`. The top-level fields below plus the `source.*`
# fields folded into the payload by `descriptor_hash` are that complete set:
#   - `source.authored_by` gates maker != checker;
#   - `source.evidence_hash` is the evidence-freshness anchor an evidence check
#     compares its verdict file against — editing it to MATCH a stale verdict
#     file would revive that stale pass as GREEN, so it must be under the hash.
# Deliberately EXCLUDED: `source.admitted_hash` is this hash's own storage slot
# (covering it would be self-referential); `id` names the check but no verdict is
# read from it; `source.red_first_proof` is admission-time evidence the gate
# never consults at evaluation time. `admission.status` is not a descriptor field
# — it is admission's own output, and a check flipped to admitted without a
# matching `admitted_hash` is caught by admission, not by this hash.
_CONTRACT_FIELDS = ("type", "kind", "owner", "exec", "signal",
                    "predicate", "regression_guard", "baseline", "determinism")


def descriptor_hash(check):
    payload = {k: check.get(k) for k in _CONTRACT_FIELDS}
    source = check.get("source", {})
    payload["authored_by"] = source.get("authored_by")
    # evidence_hash joins the payload ONLY when the descriptor carries one (every
    # admitted evidence check does; admission requires it). Folding an absent
    # anchor in as a constant None would change the hash of every non-evidence
    # check too, mass-invalidating already-recorded admitted_hashes on deploy —
    # a spurious integrity_error for checks whose verdict never reads the field.
    if source.get("evidence_hash") is not None:
        payload["evidence_hash"] = source["evidence_hash"]
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def integrity_error(check):
    """ERROR if the descriptor changed since admission (admitted_hash mismatch).
    Skipped when no admitted_hash is recorded (backward compatible)."""
    admitted = check.get("source", {}).get("admitted_hash")
    if admitted and descriptor_hash(check) != admitted:
        return "descriptor changed since admission (integrity hash mismatch) — re-run admit_check.py"
    return None


def evaluate_check(check, cwd, tier, deadline=None):
    """Run one machine check `determinism.runs` times; return a result dict."""
    mc = maker_checker_error(check)
    if mc:
        return _result(check, ERROR, None, mc, tier)
    integrity = integrity_error(check)
    if integrity:
        return _result(check, ERROR, None, integrity, tier)

    runs = max(1, int(check.get("determinism", {}).get("runs", 1)))
    values = []
    on_missing = check.get("signal", {}).get("on_missing", "error")

    for _ in range(runs):
        run = run_command(check, cwd, tier, deadline)
        if run["timed_out"]:
            reason = "gate deadline exceeded" if run.get("deadline_timeout") else "check timed out"
            return _result(check, ERROR, None, reason, tier)
        try:
            values.append(extract_signal(check, run))
        except MissingSignal as exc:
            # Clean-signal guarantee: absence never reads as a passing value.
            if on_missing == "error":
                return _result(check, ERROR, None, f"missing signal: {exc}", tier)
            if on_missing == "fail":
                return _result(check, RED, None, f"missing signal (treated as fail): {exc}", tier)
            values.append(coerce_value(on_missing, check["signal"].get("value_type", "count")))

    # Determinism: differing values across runs → inconclusive (collect more).
    if len({repr(v) for v in values}) > 1:
        return _result(check, INCONCLUSIVE, values, f"non-deterministic signal across {runs} runs: {values}", tier)

    value = values[0]
    passed = eval_predicate(value, check.get("predicate", {}))
    guards_ok, guard_fail = eval_regression_guards(
        value, check.get("regression_guard"), baseline_value(check.get("baseline")))
    if passed and guards_ok:
        return _result(check, GREEN, value, "predicate satisfied", tier)
    reason = "predicate not satisfied" if not passed else "regression guard violated: " + "; ".join(guard_fail)
    return _result(check, RED, value, reason, tier)


def _result(check, verdict, value, reason, tier):
    return {
        "id": check.get("id"),
        "type": check.get("type"),
        "owner": check.get("owner"),
        "verdict": verdict,
        "value": value,
        "reason": reason,
        "hermeticity": "enforced" if tier == "A" else "unverified",
    }


def evaluate_evidence(check, cwd):
    """Evidence checks are settled by a verdict artifact written by an agent
    that is not the maker. We recompute the AND — never trust a transcript."""
    mc = maker_checker_error(check)
    if mc:
        return _result(check, ERROR, None, mc, "B")
    integrity = integrity_error(check)
    if integrity:
        return _result(check, ERROR, None, integrity, "B")
    exec_spec = check.get("exec", {})
    artifact = exec_spec.get("verdict_file") or check.get("signal", {}).get("file")
    if not artifact:
        return _result(check, ERROR, None, "evidence check has no verdict_file", "B")
    fpath = artifact if os.path.isabs(artifact) else os.path.join(cwd, artifact)
    try:
        with open(fpath, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return _result(check, ERROR, None, f"verdict artifact unreadable: {exc}", "B")

    # Freshness: an optional content hash anchors the verdict to what was seen.
    expected_hash = check.get("source", {}).get("evidence_hash")
    if expected_hash and data.get("evidence_hash") != expected_hash:
        return _result(check, ERROR, data.get("verdict"),
                       "evidence hash mismatch (stale verdict)", "B")

    verdict_by = data.get("verifier")
    maker = check.get("owner")
    if not verdict_by:
        return _result(check, ERROR, None,
                       "evidence verdict has no 'verifier' (maker != checker unverifiable)", "B")
    if maker and verdict_by == maker:
        return _result(check, ERROR, None,
                       f"evidence verified by the maker ({maker}); maker != checker violated", "B")

    verdict = str(data.get("verdict", "")).lower()
    if verdict in ("pass", "green", "true"):
        return _result(check, GREEN, verdict, data.get("notes", "evidence verdict: pass"), "B")
    return _result(check, RED, verdict, data.get("notes", "evidence verdict: fail"), "B")


# --- budget / accounting ----------------------------------------------------

def budget_exhausted(state):
    """Return a blocked-reason string when a budget guard trips, else None.
    Pure read — no mutation of state."""
    budget = state.get("budget", {})
    spent = budget.get("spent", {})

    if spent.get("iterations", 0) >= budget.get("max_iterations", 8):
        return "blocked_budget"

    cap = budget.get("max_consecutive_failures", 3)
    # Only the checks the gate actually evaluates can trip the failure cap. A
    # quarantined (or never-admitted) check is excluded from every evaluation, so
    # `run_gate` never advances its `consecutive_failures` — a stale count left on
    # it from before it was quarantined must not block a loop whose admitted
    # checks are healthy. `admitted_checks` is the one definition of "admitted"
    # `run_gate` selects on, reused verbatim so the cap and the evaluation loop
    # can never disagree about which checks count.
    admitted, _ = admitted_checks(state)
    for check in admitted:
        if check.get("consecutive_failures", 0) >= cap:
            return "blocked_failures"

    timeout_min = budget.get("timeout_min")
    if timeout_min:
        # Fail closed: a timeout budget that cannot resolve its start instant
        # must never be silently skipped — that silent skip (the old
        # `if timeout_min and started_at:` short-circuit, paired with
        # `setdefault` never overwriting a bootstrap-written null) is exactly
        # the bug this guards against. In normal operation the stamping site
        # in `run_gate` makes `started_at` always resolvable by the time this
        # runs, so this branch is defence in depth for a hand-edited or
        # otherwise corrupted state file, not the primary fix.
        start = _parse_iso(spent.get("started_at"))
        if start is None:
            return "blocked_timeout"
        if (now_utc() - start).total_seconds() > timeout_min * 60:
            return "blocked_timeout"
    return None


def confirmation_threshold(state):
    """Consecutive greens required to stop. Floored at DEFAULT_CONFIRMATION_K
    (design invariant: K >= 3) so no descriptor can lower it to a single green."""
    ks = [DEFAULT_CONFIRMATION_K]
    for c in state.get("checks", []):
        try:
            ks.append(int(c.get("determinism", {}).get("confirmation_k", DEFAULT_CONFIRMATION_K)))
        except (TypeError, ValueError):
            ks.append(DEFAULT_CONFIRMATION_K)
    return max(ks)


# --- feedback ---------------------------------------------------------------

BOARD_W = 84        # target total width of a board row — see `status_board`
BOARD_WHY_MIN = 28  # ...but never squeeze WHY below this, however long the ids
BOARD_WHY_LINES = 3  # WHY wraps up to this many lines; beyond it, see board_why


def _pad(s, w):
    """Left-align a plain-text cell to `w` columns. Never truncates: a check id
    the board shortened would not be the id the maker greps for."""
    return str(s).ljust(w)


def board_why(r, width):
    """The WHY cell, wrapped to `width`: why the check reads the way it does, the
    value it measured, and any degradation tag. Returns `(lines, full_reason_if_
    truncated)`.

    The reason is whitespace-collapsed first, so a multi-line stderr excerpt
    cannot tear the table apart, then wrapped INSIDE the column — the terminal
    wrapping a long row at column 0 is what destroys the alignment the table
    exists for.

    A green check carries no reason (`predicate satisfied` restates the glyph);
    it carries its VALUE, which the glyph does not — that is what makes a green
    row worth its width.

    Only text past `BOARD_WHY_LINES` lines (a crash trace, not a reason) is cut,
    and the caller restates it in full below the table: `iterations[]` persists
    id / verdict / value but NOT reason, so this feedback is the only place a
    reason is ever written and the board must never be why it is lost.
    Degradation tags survive truncation by construction — they are appended
    after the cut, never inside it, because a `[DEGRADED: ...]` label the board
    silently swallowed is a downgrade the maker never learns about."""
    one_line = " ".join(str(r.get("reason") or "").split())
    val = "" if r.get("value") is None else f"value={r['value']}"
    body = (val or "—") if r["verdict"] == GREEN else one_line + (f" · {val}" if val else "")
    tags = "".join(f" [DEGRADED: {d}]" for d in r.get("degraded", []))

    limit = max(BOARD_WHY_MIN, width * BOARD_WHY_LINES - len(tags))
    truncated = len(body) > limit
    if truncated:
        body = body[:limit - 1] + "…"
    return textwrap.wrap(body + tags, width) or ["—"], (one_line if truncated else None)


def status_board(results, state, prev_verdicts):
    """The whole gate as one table — greens included — grouped by the owner who
    has to act on it and the kind of check they are acting on. Each row carries
    the verdict transition since the previous evaluation (Δ), the check id, its
    consecutive-failure count against the cap, and why it reads the way it does.
    `prev_verdicts` maps id → the previous iteration's verdict.

    A row carries ONLY what varies per check, because everything a row repeats
    unchanged is what made the old board unreadable — six checks printing six
    copies of the same three ratios. `conf`, `eval` and `budget` are the LOOP's
    counters, identical on every row by construction; they belong to the `▸ loop`
    progress line in `build_feedback` and appear there exactly once. `owner` and
    `type` are constant WITHIN a group by construction, so they are stated once
    in the group header: `owner` because "who is up?" is the first question a red
    board has to answer, `type` because it decides what acting on the row even
    means (a red `functional` is code to fix; a red `evidence` is a verdict
    artifact to request from someone who is not the maker). Grouping is what
    keeps a row inside ~80 columns — a wrapped row is not a table.

    (`eval` and `budget` are also two DIFFERENT counters — see `build_feedback`
    for why they must never be conflated into one `iter` ratio (F25/F11).)"""
    prev_verdicts = prev_verdicts or {}
    if not results:
        return []
    max_cf = state.get("budget", {}).get("max_consecutive_failures", 3)
    cf_by_id = {c.get("id"): c.get("consecutive_failures", 0) for c in state.get("checks", [])}

    def fails(r):
        return f"{cf_by_id.get(r.get('id'), 0)}/{max_cf}"

    groups = {}
    for r in results:
        groups.setdefault((r.get("owner") or "—", r.get("type") or "?"), []).append(r)
    # Groups with red work first, then the busiest, then alphabetical — a total
    # order over the data, so the same gate always renders the same board.
    order = sorted(groups, key=lambda key: (all(r["verdict"] == GREEN for r in groups[key]),
                                            -len(groups[key]), key))

    id_w = max([len("CHECK")] + [len(str(r.get("id"))) for r in results])
    f_w = max([len("FAILS")] + [len(fails(r)) for r in results])
    # A `🟢→🔴` cell is 5 columns wide (two double-width glyphs + an arrow) while
    # being 3 characters long, so the Δ header and every continuation indent are
    # padded to 5 by hand — `str.ljust` would count the glyphs as 1 and shear the
    # column. Everything right of Δ is plain text and pads normally.
    head_w = 2 + 5 + 2 + id_w + 2 + f_w + 2
    why_w = max(BOARD_WHY_MIN, BOARD_W - head_w)

    # The column header belongs to the BOARD, not to each group: repeating it per
    # group would reintroduce, one level up, the same restatement this table was
    # rewritten to remove.
    lines = [f"  Δ      {_pad('CHECK', id_w)}  {_pad('FAILS', f_w)}  WHY"]
    details = []
    for key in order:
        owner, kind = key
        rows = groups[key]
        green_n = sum(1 for r in rows if r["verdict"] == GREEN)
        lines.append(f"{green_n}/{len(rows)} green · {kind} · owner {owner}")
        for r in rows:
            prev = prev_verdicts.get(r["id"])
            prev_glyph = glyph(prev) if prev else "⚪"
            why, full = board_why(r, why_w)
            lines.append(f"  {prev_glyph}→{glyph(r['verdict'])}  {_pad(r.get('id'), id_w)}  "
                         f"{_pad(fails(r), f_w)}  {why[0]}")
            lines.extend(" " * head_w + w for w in why[1:])
            if full:
                details.append(f"  {r.get('id')}: {full}")
    # Only a reason too long for its cell is restated in full — the feedback text
    # is the only place a reason is ever written (iterations[] persists id /
    # verdict / value, not reason), so the board may shorten it but must never be
    # the reason it is lost.
    if details:
        lines.append("Full reason for the truncated row(s) above:")
        lines.extend(details)
    return lines


def cost_ask(results, state):
    """The tail of a blocked report: what remains red, the trend across the last
    few evaluations, and the human-only verb to grant more budget. The gate never
    extends itself — it lays out the cost and hands the decision to the human."""
    problems = [r for r in results if r["verdict"] != GREEN]
    ids = [r["id"] for r in problems]
    eval_iters = [it for it in state.get("iterations", []) if "results" in it]
    counts = [sum(1 for x in it["results"] if x.get("verdict") != GREEN) for it in eval_iters[-3:]]
    trend = " → ".join(str(c) for c in counts) if counts else "n/a"
    return [
        f"Cost to continue: {len(problems)} check(s) still not green {ids}; "
        f"red/error count (last {len(counts)} eval(s)): {trend}.",
        "This is a HUMAN decision — the gate never extends its own budget. If the remaining work "
        "is worth more budget, a human grants it with:",
        '  run_gate_checks.py --extend-budget iterations=<n>[,failures=<n>][,timeout_min=<n>] '
        '--user-confirmed "<the human\'s answer>"',
    ]


def build_feedback(results, state, decision, blocked_reason=None, prev_verdicts=None, iter_n=None):
    lines = []
    # One-time entry banner on the first evaluation, so the user sees the loop go live.
    if iter_n == 1:
        lines.append(f"▶ ENTERING LOOP — fairmind gate (Tier {state.get('hermeticity_tier', 'B')}, "
                     f"K={confirmation_threshold(state)})")
    if decision == DECISION_STOP_PASSED:
        k = confirmation_threshold(state)
        lines.append(f"🟢 LOOP GREEN — all {len(results)} admitted check(s) passed on {k} consecutive "
                     "evaluations. status=passed_pending_human. Awaiting the final human gate; "
                     "no auto-merge/deploy.")
        quarantine = state.get("quarantine", [])
        if quarantine:
            ids = [q.get("id") for q in quarantine]
            lines.append(f"NOTE: {len(quarantine)} check(s) QUARANTINED and excluded from the gate — "
                         f"the human must confirm their criteria are otherwise covered: {ids}")
    elif decision == DECISION_STOP_BLOCKED:
        lines.append(f"⛔ LOOP STOPPED — {blocked_reason}. The stop condition was NOT met.")
    elif decision == DECISION_ITERATE:
        greens = [r for r in results if r["verdict"] == GREEN]
        problems = [r for r in results if r["verdict"] != GREEN]
        if not problems:
            k = confirmation_threshold(state)
            c = state.get("confirmations", 0)
            lines.append(f"🟢 All {len(greens)} check(s) GREEN — confirmation {c}/{k}. "
                         "Re-verifying for stability; make no changes, just let the gate re-run.")
        else:
            lines.append(f"🔴 Gate RED — {len(problems)} of {len(results)} check(s) not green. "
                         "Address the following, then the loop will re-verify:")

    # Loop-level progress line, distinct from the per-check status board below:
    # a single-glance readout of where this loop stands (green ratio, confirmation
    # streak, evaluations run, budget consumed), emitted on every evaluation that
    # reaches feedback.
    #
    # `eval` and `budget` are TWO DIFFERENT COUNTERS and must never share a word or
    # a slash (F25, and F11 before it). `eval` counts every evaluation the gate has
    # run and is unbounded — a green evaluation is still an evaluation — so it has
    # no denominator. `budget` is `spent.iterations`, which counts ONLY the
    # budget-consuming (red/error) evaluations, against `max_iterations`. Printing
    # them as one `iter {n}/{max_iterations}` ratio divided an evaluation count by a
    # budget cap and told the operator a green evaluation had burned budget when it
    # burns none. Read `spent` here, never `n`.
    ref = state.get("target", {}).get("ref")
    green_n = sum(1 for r in results if r["verdict"] == GREEN)
    total_n = len(results)
    conf_n = state.get("confirmations", 0)
    k_n = confirmation_threshold(state)
    n = iter_n if iter_n is not None else sum(1 for it in state.get("iterations", []) if "results" in it)
    budget = state.get("budget", {})
    max_iter = budget.get("max_iterations", 8)
    spent_n = budget.get("spent", {}).get("iterations", 0)
    lines.append(f"▸ loop {ref} · {green_n}/{total_n} green · conf {conf_n}/{k_n} · "
                 f"eval {n} · budget {spent_n}/{max_iter}")

    if any((r.get("reason") == "gate deadline exceeded") for r in results):
        lines.append("[DEGRADED: gate deadline] the gate ran out of wall-clock budget; "
                     "unfinished checks are ERROR (fail-closed), never green.")

    # One table, one row per check. It replaced a board plus a per-item list that
    # restated every non-green check's id, verdict and owner a second line down —
    # so a six-check gate printed twelve rows to say six things.
    board = status_board(results, state, prev_verdicts)
    if board:
        lines.append("Status board · Δ = change since the previous evaluation · "
                     "fails = consecutive/cap:")
        lines.extend(board)

    by_owner = {}
    for r in results:
        if r["verdict"] != GREEN:
            by_owner.setdefault(r["owner"], []).append(r["id"])

    if decision == DECISION_STOP_BLOCKED:
        lines.extend(cost_ask(results, state))

    if any(r["verdict"] != GREEN for r in results):
        lines.append("Journal rule: APPLY or REBUT every non-green item above; "
                     "rebuttals go to the checker, never edit descriptors.")

    primary_owner = None
    if by_owner:
        primary_owner = sorted(by_owner.items(), key=lambda kv: -len(kv[1]))[0][0]
    return "\n".join(lines), primary_owner


# --- commitment boundaries --------------------------------------------------

def _anti_correlated_pair(window):
    """Given up to N result-lists (each `[{id, verdict}, ...]`), return a pair of
    check ids that are *strictly anti-correlated* over the last 3 evaluations
    (one green exactly when the other is not) and that actually vary — a genuine
    tension, not just one always-passing + one always-failing. Else None."""
    recent = [w for w in window if w][-3:]
    if len(recent) < 3:
        return None
    series = {}
    for res in recent:
        greens = {x["id"]: (x.get("verdict") == GREEN) for x in res}
        for cid, g in greens.items():
            series.setdefault(cid, []).append(g)
    ids = [cid for cid, s in series.items() if len(s) == 3]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            sa, sb = series[a], series[b]
            if all(x != y for x, y in zip(sa, sb)) and len(set(sa)) > 1:
                return (a, b)
    return None


def commitment_boundaries(results, state, in_flight=False):
    """Warn-and-route boundaries read from iteration history. Never quarantine —
    only surface a strategic question and re-route the feedback. Returns
    `(banners, routing_override, strategy_ids)`.

    `in_flight` (H8/PCF-10): the current evaluation is reading a half-written
    tree, so its verdicts are unreliable and must not seed the anti-correlation
    detector; past in-flight evaluations are dropped from the window too."""
    banners = []
    routing_override = None
    strategy_ids = []

    checks_by_id = {c.get("id"): c for c in state.get("checks", [])}
    already = {cid for it in state.get("iterations", []) for cid in it.get("strategy_turn", [])}

    # (a) Strategy turn — a check one failure below the cap. The question is no
    # longer "fix it" but "is the CHECK wrong, or the APPROACH?" Route once to the
    # checker (authored_by), not the maker, before the last iteration is burned.
    for r in results:
        c = checks_by_id.get(r["id"], {})
        if c.get("consecutive_failures") == 2 and r["id"] not in already:
            checker = c.get("source", {}).get("authored_by")
            banners.append(
                f"STRATEGY TURN [{r['id']}]: 2 consecutive failures (one below the cap). "
                f"Is the check wrong, or the approach? Routing to the checker ({checker}) to "
                "reconsider the check before the maker burns the last iteration.")
            strategy_ids.append(r["id"])
            if routing_override is None:
                routing_override = checker

    # (b) Contract conflict — two checks strictly anti-correlated over the last 3
    # evaluations: satisfying one breaks the other, so the two criteria may be in
    # tension. Warn and route to the Technical Lead to reconcile the contract.
    #
    # H8/PCF-10: consider only SETTLED evaluations. An in-flight evaluation reads a
    # half-written tree, so its verdicts are noise — feeding them here produced a
    # FALSE CONTRACT CONFLICT when two checks flipped one evaluation apart across
    # an amendment that was merely mid-write. Drop in-flight iterations from the
    # window, and skip this eval's own verdicts while it is itself in-flight;
    # `_anti_correlated_pair` needs 3, so a window thinned below 3 yields nothing.
    prior = [it["results"] for it in state.get("iterations", [])
             if "results" in it and not it.get("in_flight")]
    window = prior[-2:]
    if not in_flight:
        window = window + [[{"id": r["id"], "verdict": r["verdict"]} for r in results]]
    conflict = _anti_correlated_pair(window)
    if conflict:
        a, b = conflict
        banners.append(
            f"CONTRACT CONFLICT [{a} vs {b}]: strictly anti-correlated over the last 3 "
            "evaluations — satisfying one breaks the other. The two criteria may conflict. "
            "Routing to the Technical Lead to reconcile the contract (warn only — neither "
            "check is quarantined).")
        routing_override = "tech-lead"

    return banners, routing_override, strategy_ids


# --- main evaluation --------------------------------------------------------

def admitted_checks(state):
    """Split state['checks'] into (admitted, pending) using the ONE definition of
    "admitted": `admission.status == "passed"` AND `source.admitted_hash` is
    PRESENT and equals `descriptor_hash(check)`, AND `id` not in `quarantine[]`.

    (F35/H2) `admission.status == "passed"` alone is a CLAIM, not proof: a
    hand-forged descriptor — a plausible `authored_by`, a hand-typed
    `admission.status: "passed"`, never actually probed by `admit_check.py` —
    used to satisfy this predicate and could gate a loop green forever.
    `admitted_hash` is the one artifact only `admit_check.py`'s `_finalize`
    stamps (`admit_check.py:230-231`, on every admission path — `admit_one`,
    `admit_evidence`, `admit_guard`), computed by the same `descriptor_hash`
    this function calls: possession of a hash that still matches the live
    descriptor IS the proof admission genuinely ran over THIS exact
    descriptor. A missing hash (legacy/hand-forged) or a present-but-stale one
    (descriptor edited post-admission — the tamper case `integrity_error`
    also independently ERRORs on, once selected) both fail this predicate —
    neither is "admitted", so neither can be selected into the evaluation
    loop, gate the confirmation streak, or satisfy `--arm`'s coverage check.

    Migration policy (AC5, deliberate, REFUSE — never auto-heal): a legacy
    `passed`-with-no-hash check is NOT silently granted a stamped hash on
    first sight — that would launder a forged/never-probed descriptor into a
    "proven" one. It is treated as UNADMITTED: `run_gate` reports it pending
    (ERROR, "not admitted") and `--arm` refuses until a human re-runs
    `admit_check.py`.

    Shared by `run_gate` (evaluation), `budget_exhausted`, `_classify_criteria`
    and `--arm` (C3/AC1) so the engine never carries two drifting definitions
    of 'admitted'."""
    quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}
    admitted, pending = [], []
    for c in state.get("checks", []):
        if c.get("id") in quarantine_ids:
            continue  # explicitly quarantined → surfaced to the human, excluded
        admitted_hash = c.get("source", {}).get("admitted_hash")
        if (c.get("admission", {}).get("status") == "passed"
                and admitted_hash
                and admitted_hash == descriptor_hash(c)):
            admitted.append(c)
        else:
            pending.append(c)
    return admitted, pending


# --- contract validation (T10) ----------------------------------------------
# Arm-time / contract-time only (R8): NEVER called from run_gate — the evaluation
# path gains no criteria logic. `--validate-contract` (read-only) and `--arm`
# (which refuses on error) share this ONE code path so the guarantee lives in the
# engine, not in an orchestrator's compliance with a markdown instruction (R1).

# The disposition prefixes that assert real coverage vs. the advisory escape hatch.
_COVERAGE_PREFIXES = ("checked", "evidence")
_ADVISORY_UNVERIFIABLE = "unverifiable"


def _parse_disposition(disposition):
    """Parse `contract.criteria[].disposition` into `(kind, check_id, malformed)`.

    `kind` ∈ {"checked", "evidence", "quarantined", "unverifiable"} (the prefix
    set of CRITERION_DISPOSITIONS); `check_id` is the id a prefix form names (None
    for the bare `unverifiable`). `malformed` is a reason string when the value is
    not a well-formed disposition at all (null/absent, empty, unknown prefix, or a
    prefix form with no id) — AC2(e). A well-formed disposition still has to AGREE
    with reality; that cross-check is `_classify_criteria`'s job, not this one's."""
    if disposition is None:
        return None, None, "disposition is null or absent"
    if not isinstance(disposition, str):
        return None, None, f"disposition is not a string ({type(disposition).__name__})"
    d = disposition.strip()
    if d == "":
        return None, None, "disposition is empty"
    if d == _ADVISORY_UNVERIFIABLE:
        return _ADVISORY_UNVERIFIABLE, None, None
    if ":" in d:
        prefix, _, cid = d.partition(":")
        prefix, cid = prefix.strip(), cid.strip()
        if prefix not in _COVERAGE_PREFIXES + ("quarantined",):
            return None, None, f"unknown disposition prefix {prefix!r}"
        if not cid:
            return None, None, f"disposition {prefix!r} names no check id"
        return prefix, cid, None
    # A bare token that is not `unverifiable` and carries no ':' — e.g. a bare
    # `checked` (prefix with no id and no colon).
    return None, None, f"malformed disposition {d!r} (prefix with no check id)"


def _admission_gap_reason(check):
    """(H2/AC5) WHY a check failed `admitted_checks`'s strengthened predicate —
    three factually distinct human fixes, never conflated into one string that
    could lie about a check's actual `admission.status`:

      - "never admitted"       — `admission.status` is not (yet) 'passed'.
        `admission.status != 'passed'` is a TRUE claim here.
      - "admitted, hash missing" — status IS 'passed' but `source.admitted_hash`
        is absent (the legacy/hand-forged shape H2 exists to catch). Saying
        "admission.status != 'passed'" for this check would be FALSE — the
        status genuinely is 'passed'; only the proof artifact is missing.
      - "hash mismatched"      — status is 'passed' and a hash IS present, but
        it no longer matches `descriptor_hash(check)` (the descriptor was
        edited after admission — the same tamper `integrity_error` also
        catches independently once a check reaches evaluation).

    Every branch names `admit_check.py` / re-admission as the true remedy, so
    a reader who only sees this string still knows what to run."""
    status = check.get("admission", {}).get("status")
    admitted_hash = check.get("source", {}).get("admitted_hash")
    if status != "passed":
        return ("never admitted (admission.status is not 'passed') — run "
                "admit_check.py")
    if not admitted_hash:
        return ("admission.status is 'passed' but source.admitted_hash is "
                "MISSING — admission was never proven for this exact "
                "descriptor (legacy or hand-authored 'passed'); re-admit with "
                "admit_check.py")
    return ("admission.status is 'passed' but source.admitted_hash no longer "
            "matches descriptor_hash(check) — the descriptor changed since "
            "admission; re-admit with admit_check.py")


def _classify_criteria(state):
    """Classify every `contract.criteria[]` entry against the loop's real check
    state, returning `(errors, advisories)` — each a list of
    `{"id", "disposition", "reason"}`.

    `errors` are the coverage failures that make the loop UNARMABLE (AC2/AC4):
    a HARD criterion not backed by a live, admitted check. `advisories` are the
    explicit `hard: false` holes — armable, but named on stdout so the human who
    signs the arm sees them (AC3, a silent pass is a defect).

    Reuses `admitted_checks(state)` — the engine's ONE definition of "admitted"
    (`admission.status == "passed"` AND not in `quarantine[]`) — so validation can
    never carry a second, drifting definition (AC2(c))."""
    contract = state.get("contract") or {}
    criteria = contract.get("criteria")
    if not criteria:  # absent, null, or empty → mandatory, never inferred (AC4)
        return ([{"id": None, "disposition": None,
                  "reason": "contract.criteria is absent, null, or empty — it is "
                            "mandatory for arming and is never inferred"}], [])

    admitted, _pending = admitted_checks(state)
    admitted_ids = {c.get("id") for c in admitted}
    checks = state.get("checks") or []
    all_ids = {c.get("id") for c in checks}
    check_by_id = {c.get("id"): c for c in checks}
    quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}

    errors, advisories = [], []
    for crit in criteria:
        disp = crit.get("disposition")
        hard = crit.get("hard")
        if hard is None:
            hard = True  # R3: fail-closed — hardness defaults to True
        entry = {"id": crit.get("id"), "disposition": disp}

        def err(reason):
            errors.append({**entry, "reason": reason})

        def adv(reason):
            advisories.append({**entry, "reason": reason})

        kind, target, malformed = _parse_disposition(disp)
        if malformed is not None:
            err(malformed)  # AC2(e)
            continue

        if kind == _ADVISORY_UNVERIFIABLE:
            if hard:
                err("hard criterion is 'unverifiable' — a hard criterion must be "
                    "covered by a live, admitted check")  # AC2(a)
            else:
                adv("advisory (hard:false) criterion is unverifiable")  # AC3
            continue

        if kind == "quarantined":
            if target not in all_ids:
                err(f"disposition names check {target!r}, which is absent from "
                    "checks[]")  # AC2(b)
            elif hard:
                err(f"hard criterion is covered only by quarantined check {target!r}, "
                    "which contributes nothing to the stop condition")  # AC2(d)
            elif target not in quarantine_ids:
                err(f"disposition claims check {target!r} is quarantined, but it is "
                    "not in quarantine[]")
            else:
                adv(f"advisory (hard:false) criterion covered only by quarantined "
                    f"check {target!r}")  # AC3
            continue

        # kind ∈ {"checked", "evidence"} — a claim of real coverage.
        if target not in all_ids:
            err(f"disposition names check {target!r}, which is absent from "
                "checks[]")  # AC2(b)
            continue
        if target not in admitted_ids:
            # The disposition must AGREE with the engine's own admitted predicate
            # (AC2(c)). Distinguish WHY it is not admitted — the two failures need
            # two different fixes, so the reason must actually differ (AC6 teeth).
            # (H2/AC5) A quarantined check is reported via the quarantine branch;
            # anything else routes through `_admission_gap_reason`, which itself
            # distinguishes never-admitted / hash-missing / hash-mismatched so the
            # message can never falsely claim `admission.status != 'passed'` for a
            # check whose status genuinely IS 'passed'.
            if target in quarantine_ids:
                err(f"check {target!r} is quarantined (admitted, then excluded from "
                    "the stop condition) — re-admit it with admit_check.py")
            else:
                err(f"check {target!r} is not admitted: "
                    f"{_admission_gap_reason(check_by_id.get(target, {}))}")
            continue
        if kind == "evidence":
            tc = check_by_id.get(target, {})
            if not (tc.get("kind") == "evidence" or tc.get("type") == "evidence"):
                err(f"disposition 'evidence:{target}' but check {target!r} is not an "
                    "evidence-kind check")
                continue
        # Fully covered by a live, admitted check — nothing to report.
    return errors, advisories


def validate_contract(state):
    """The single contract-validation predicate (R1). Returns the list of coverage
    errors (empty ⇒ armable). A HARD criterion is covered iff its disposition is
    `checked:<id>` or `evidence:<id>` at an ADMITTED check (and, for `evidence:`,
    that check is evidence-kind). Everything else on a hard criterion — an absent
    contract, `unverifiable`, `quarantined:<id>`, an absent/non-admitted check, or
    a malformed disposition — is an error. Read-only: mutates nothing, evaluates
    no check (R8)."""
    errors, _advisories = _classify_criteria(state)
    return errors


def _contract_offender_line(offender):
    """One human-readable offender line: names the criterion id, its disposition,
    and the specific reason it is uncovered."""
    cid = offender.get("id")
    disp = offender.get("disposition")
    return f"  - {cid} ({disp!r}): {offender['reason']}"


def _emit_contract_refusal(errors, verb):
    """The refusal text on STDERR, shared verbatim by `--validate-contract` and
    `--arm` (AC6): every offender by id + disposition + reason, then an explicit
    recommendation to run the task in interactive mode rather than arm a weak gate.
    The recommendation lives where it is EXECUTED — a human who never reads the
    command doc still gets it."""
    n = len(errors)
    print(f"{verb} refused: {n} hard criterion/criteria are not covered by a live, "
          "admitted check — this contract is not armable:", file=sys.stderr)
    for e in errors:
        print(_contract_offender_line(e), file=sys.stderr)
    print("RECOMMENDATION: do not arm a weak gate. A task whose hard criteria "
          "cannot be covered by a live, admitted check should be run in INTERACTIVE "
          "mode, not armed behind a gate that cannot enforce them. Cover each "
          "criterion with an admitted check, or downgrade a genuinely advisory one "
          "to `hard: false`, then re-run.", file=sys.stderr)


def validate_contract_verb(state):
    """`--validate-contract` (AC1): the read-only twin of `--arm`'s validation.
    Prints the coverage report to STDOUT (naming every offender AND every advisory
    hole), and on failure the actionable refusal to STDERR. Never mutates
    loop-state.json and never evaluates a check. Exit 0 when armable, non-zero
    otherwise."""
    errors, advisories = _classify_criteria(state)

    # Coverage report → stdout, on both the pass and the fail path.
    if errors:
        print(f"Contract coverage: {len(errors)} uncovered hard criterion/criteria "
              "— this loop is NOT armable:")
        for e in errors:
            print(_contract_offender_line(e))
    else:
        print("Contract coverage: OK — every hard criterion is covered by a live, "
              "admitted check.")
    if advisories:
        print("Advisory (hard:false) uncovered criteria — armable, but the human "
              "must confirm each hole is acceptable:")
        for a in advisories:
            print(_contract_offender_line(a))

    if errors:
        _emit_contract_refusal(errors, "--validate-contract")
        return EXIT_INTERNAL_ERROR
    return EXIT_ALLOW_STOP


# --- no-work re-evaluation signal (H3/F21+F33) -------------------------------
# Today `run_gate`'s not-green branch advances `budget.spent.iterations` and
# every admitted check's `consecutive_failures` on EVERY turn-ending
# evaluation, even when nobody did any work since the previous evaluation — a
# background maker whose Stop hook fires twice against the same half-written
# tree burns two budget iterations and can trip a spurious STRATEGY TURN
# (`commitment_boundaries`, at consecutive_failures == 2) on a check that is
# not genuinely failing twice in a row. `_no_work_signature` answers "did work
# happen since the immediately preceding results-bearing evaluation?" so
# `run_gate` can freeze that accounting on a no-work re-evaluation instead of
# blindly advancing it.

def _no_work_signature(state, work_dir, trace_root):
    """A content-sensitive fingerprint of the loop's current mutation
    footprint, used by `run_gate` to tell a genuine re-evaluation (real work
    landed) apart from a no-work re-evaluation (the tree is byte-identical to
    what the immediately preceding evaluation already looked at).

    Computed UNCONDITIONALLY by `run_gate` — independent of `contract.scope`.
    `evaluate_scope` is the only OTHER caller of `compute_mutation_set` and it
    early-returns whenever no scope is declared (T8, AC3), so a scope-less
    loop (this loop family declares none on purpose — the gate itself is the
    subject under change) would never otherwise compute a mutation set at all.

    Reuses `compute_mutation_set` for membership (the same git-grounded path
    set the scope boundary trusts) but membership alone is NOT sufficient: the
    same file edited twice in a row has an IDENTICAL path set but DIFFERENT
    content — real work a path-only signature would misread as no-work. Each
    member path is therefore re-hashed via `_working_tree_sha` (the same
    byte-identity primitive `pre_dirty_anchors` uses to anchor arm-time dirty
    files), and the signature is the `[[path, sha], ...]` list, sorted by path
    — `compute_mutation_set` already sorts `paths`, but sorting here too keeps
    this function's own equality contract explicit and independent of that
    detail ever changing.

    `work_dir` is the tree to diff (H1/F34: the worktree when the state
    records a valid one, else state_root — the SAME tree `run_gate` evaluates
    checks against). `trace_root` is ALWAYS state_root (the trace FILE never
    moves with a worktree), mirroring `evaluate_scope`'s `trace_root` split.

    Returns `(signature, degraded)`:
      - `degraded is None` and `signature` a (possibly empty) list when the
        mutation set was computed cleanly. The empty list IS a valid,
        comparable signature — "nothing has been touched since arm at all".
      - `degraded` is `compute_mutation_set`'s degraded marker and
        `signature is None` when "did work happen?" is UNANSWERABLE. The
        caller MUST fail closed on this (count the evaluation exactly as
        pre-H3) — never read "unknown" as "no work". Three
        `compute_mutation_set` degraded markers reach here:
          - `no-baseline-ref` — the loop was never armed inside a git work
            tree (or the arm-time freeze itself failed), so
            `contract.mutation_set.baseline.ref` is absent.
          - `no-git-work-tree` — `work_dir` is not a git work tree at
            evaluation time (arm-time git-ness can drift from eval-time).
          - `git-query-failed` — a git query needed to build the set failed
            (e.g. `baseline.ref` no longer resolves — a corrupted/rewritten
            object).
    """
    baseline = ((state.get("contract") or {}).get("mutation_set") or {}).get("baseline") or {}
    arm_ref = baseline.get("ref")
    pre_dirty = baseline.get("pre_dirty") or []
    tfile = trace_path(trace_root)
    mutation_set = compute_mutation_set(work_dir, arm_ref, pre_dirty, tfile)
    if mutation_set.get("degraded"):
        return None, mutation_set["degraded"]
    signature = sorted(
        [p["path"], _working_tree_sha(work_dir, p["path"])]
        for p in mutation_set["paths"]
    )
    return signature, None


# H8 (PCF-8/PCF-5): the settle signal — "is the tree still being written?"
#
# `_no_work_signature` (H3) freezes accounting when the tree is byte-IDENTICAL
# to the previous evaluation ("no work happened since"). It cannot help the
# OTHER half of the async-maker problem: a background maker that HAS written
# since the last evaluation but is NOT YET DONE. The signature moved, so H3
# counts the evaluation, and the gate draws a conclusion from a half-written
# tree — charging budget, advancing consecutive_failures, and firing a STRATEGY
# TURN against work that is merely incomplete (PCF-5, live), or — worse —
# advancing the confirmation streak on a FALSE green when the RED-making test
# simply has not been written yet (PCF-8 case 3; a false GREEN is the one error
# class the whole confirmation design exists to prevent, K≥3 notwithstanding).
#
# `_settle_age` reads the append-only trace and returns how many seconds ago the
# most recent WORK-PRODUCT `mutate` op landed (a maker actively typing). Within
# the settle window `run_gate` treats the evaluation as in-flight and freezes
# the SAME accounting a no-work re-evaluation freezes (budget /
# consecutive_failures / STRATEGY TURN) AND freezes the confirmation streak
# (mirroring an H4 --hold), so neither a red nor a green reading of a
# half-written tree can move the loop. It never changes a verdict.
#
# Fail toward COUNTING on every uncertainty (no trace, no repo root to classify
# targets, no work-product mutate, an unparseable ts): a missing signal must
# degrade to today's behavior, never freeze — else a maker that writes
# continuously without going green could dodge the budget cap forever. Targets
# under `.fairmind/` are skipped (journal/state writes are bookkeeping, not work
# in flight), mirroring `compute_mutation_set`'s workspace drop. The wall-clock
# `timeout_min` guard remains the backstop against a tree that never settles.
SETTLE_WINDOW_S = 45.0

# The settle window may DEFER a not-green BUDGET charge, but never PREVENT it
# indefinitely: after this many CONSECUTIVE in-flight budget-freezes the next
# not-green evaluation charges normally. Without this cap a maker that writes
# within the window before every turn-end would freeze the budget forever, making
# `max_iterations` — an UNCONDITIONAL backstop pre-H8 — depend on a `timeout_min`
# the engine does not require. Bounds only the IN-FLIGHT path: the H3 `no_work`
# path is uncapped by design (a byte-identical tree can only waste compute, never
# false-close). The consecutive-failure freeze and the green streak freeze are
# BOTH deliberately uncapped — an in-flight eval is never a completed attempt (so
# it must never advance `consecutive_failures`, H8-F-A) and a stalled green never
# closes falsely and burns no budget; the wall-clock timeout resolves either.
SETTLE_MAX_CONSECUTIVE = 3


def _settle_window_s():
    """The settle window in seconds. `FAIRMIND_GATE_SETTLE_S` overrides the
    default (a test/tuning seam, same idiom as `FAIRMIND_GATE_DEADLINE_S`); a
    non-positive value disables the settle signal entirely."""
    env = os.environ.get("FAIRMIND_GATE_SETTLE_S")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return SETTLE_WINDOW_S


def _settle_max_consecutive():
    """How many consecutive in-flight freezes the not-green accounting tolerates
    before it must charge again. `FAIRMIND_GATE_SETTLE_MAX` overrides the default
    (a test/tuning seam); a value < 1 is floored to 1 (at least one charge is
    always eventually forced, so the iteration cap can never be starved)."""
    env = os.environ.get("FAIRMIND_GATE_SETTLE_MAX")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return SETTLE_MAX_CONSECUTIVE


def _settle_age(work_dir, trace_root, now):
    """Seconds since the most recent WORK-PRODUCT `mutate` op in the trace, or
    None when that is unknown/absent — no trace file, an unresolvable repo root
    (so targets cannot be classified), no such op, or an unparseable ts. Never
    raises. A target under `.fairmind/` (a journal or state write) is skipped:
    it is bookkeeping, not a maker still writing code. A target that cannot be
    normalized (truncated / escaping the tree) is also skipped — an
    unclassifiable op must not freeze the loop (fail toward counting).

    `work_dir`/`trace_root` mirror `_no_work_signature`'s split (H1/F34): the
    trace FILE never moves with a worktree, so it is read from `trace_root`
    (always state_root), while targets are normalized against `work_dir` (the
    worktree when one is recorded — the tree the mutations actually land in)."""
    tfile = trace_path(trace_root)
    if not tfile or not os.path.isfile(tfile):
        return None
    repo_root = _resolve_repo_root(work_dir)
    if repo_root is None:
        return None  # cannot tell work product from bookkeeping — fail toward counting
    latest = None
    try:
        with open(tfile, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    op = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(op, dict):
                    continue  # a bare JSON scalar/array is not an op — never raise on .get()
                if op.get("kind") != "mutate":
                    continue
                normalized = _normalize_trace_target(op.get("target"), repo_root)
                if normalized is None or _is_loop_workspace_path(normalized):
                    continue  # unclassifiable, or bookkeeping (.fairmind/…) — not work in flight
                ts = _parse_iso(op.get("ts"))
                if ts is None:
                    continue
                if latest is None or ts > latest:
                    latest = ts
    except OSError:
        return None
    if latest is None:
        return None
    return (now - latest).total_seconds()


def run_gate(state, cwd, dry_run=False):
    """Evaluate all checks, mutate state in place, return a decision dict.

    `cwd` is `state_root` — unchanged (state resolution, the run ledger, and
    the trace file all still key off it). `dry_run` is threaded through to
    `evaluate_scope` ONLY (F26) — every other behavior in this function is
    unaffected by it; `main` still gates its own persistence and
    terminal-status semantics on `args.dry_run` separately.

    H1/F34: the FIRST thing this function does is resolve `work_dir` — the
    tree every git query and every check subprocess actually run against
    (`resolve_work_dir`). A recorded `worktree.path` that cannot be trusted
    degrades the WHOLE evaluation closed, before the scope boundary and
    before any check runs: a silent fallback to state_root here would be
    F34 recurring under a different name."""
    work_dir, wt_degradation = resolve_work_dir(state, cwd)
    if wt_degradation is not None:
        reason, detail = wt_degradation
        state["status"] = "blocked_worktree"
        # Named in PERSISTED state (status + an iterations[] audit entry, the
        # same family as `scope_violation`) — never only on stdout/feedback.
        # No "results" key: every consumer that counts EVALUATIONS by testing
        # `"results" in it` (confirmation streaks, budget accounting) stays
        # blind to this entry, exactly like `arm`'s C8 audit entry — this is
        # not an evaluation of any check, it is a refusal to evaluate at all.
        audit_entry = {
            "event": "worktree_degraded",
            "at": iso(now_utc()),
            "reason": reason,
            "detail": detail,
        }
        state.setdefault("iterations", []).append(audit_entry)
        feedback = (
            "⛔ LOOP STOPPED — blocked_worktree: this loop records a "
            f"worktree.path that could not be resolved to a real, registered "
            f"worktree of this repo's own git history ({reason}: {detail}). "
            "Failing closed rather than silently evaluating the main tree — "
            "a silent fallback there would be the exact defect (F34) this "
            "guard exists to prevent. A human must resolve the worktree "
            "(re-create it via loop_worktree.py --create, or clear "
            "state['worktree'] and re-arm) before this loop can proceed."
        )
        return {"decision": DECISION_STOP_BLOCKED, "feedback": feedback, "results": []}

    # T8: the scope-boundary hard stop runs BEFORE anything else — a check's
    # verdict (or even whether there is an admitted check at all) must never
    # mask an out-of-scope mutation. `evaluate_scope` is a no-op (returns
    # None) whenever no scope is declared, so this is transparent to every
    # loop that doesn't use `contract.scope` (AC3). Diffs `work_dir` (the
    # worktree when one is recorded and valid) but resolves the trace FILE
    # against `trace_root=cwd` (ALWAYS state_root — H1/F34's three-concept
    # split; see `evaluate_scope`'s docstring).
    scope_result = evaluate_scope(state, work_dir, dry_run=dry_run, trace_root=cwd)
    if scope_result is not None:
        audit_entry, feedback = scope_result
        state["status"] = "blocked_scope"
        state.setdefault("iterations", []).append(audit_entry)
        return {"decision": DECISION_STOP_BLOCKED, "feedback": feedback, "results": []}

    requested_tier = state.get("hermeticity_tier", "B")
    tier = requested_tier
    hermeticity_downgraded = False
    if tier == "A" and not srt_available():
        tier = "B"  # graceful degradation — sandbox absent at run time
        hermeticity_downgraded = True  # requested A, forced to B → surface it
        state["hermeticity_tier"] = "B"

    admitted, pending = admitted_checks(state)
    quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}

    # No admitted check → the loop can never legitimately close. Stop and flag
    # for the human rather than silently passing or burning the whole budget.
    if not admitted:
        reason = "no admitted checks to gate on"
        if pending:
            reason += f"; {len(pending)} check(s) not admitted (run admit_check.py)"
        if quarantine_ids:
            reason += f"; {len(quarantine_ids)} quarantined"
        state["status"] = "blocked_no_checks"
        state.setdefault("iterations", []).append(
            {"n": len(state.get("iterations", [])) + 1, "at": iso(now_utc()),
             "results": [], "feedback_to": None})
        return {"decision": DECISION_STOP_BLOCKED,
                "feedback": f"LOOP STOPPED — {reason}. The stop condition was NOT met.",
                "results": []}

    # Compute the fail-closed wall-clock deadline for this whole evaluation.
    # Budget = min(sum(timeout_s × runs over admitted checks) + 60, 540s cap);
    # FAIRMIND_GATE_DEADLINE_S can only tighten it (a test escape hatch). Once the
    # budget is spent, every unfinished check is ERROR — which can never be green,
    # so the gate never exits 0 on a deadline.
    natural = sum(
        int(c.get("exec", {}).get("timeout_s", 300))
        * max(1, int(c.get("determinism", {}).get("runs", 1)))
        for c in admitted
    ) + 60
    budget_s = min(natural, DEFAULT_DEADLINE_CAP_S)
    env_deadline = os.environ.get("FAIRMIND_GATE_DEADLINE_S")
    if env_deadline:
        try:
            budget_s = min(budget_s, float(env_deadline))
        except ValueError:
            pass
    # Monotonic: the deadline must measure REAL elapsed time, immune to a wall-clock
    # jump (NTP step, DST, a laptop waking from sleep) that would otherwise distort the
    # remaining budget and fail-OPEN the very cap this deadline exists to enforce.
    deadline = time.monotonic() + budget_s

    results = []
    deadline_hit = False
    for check in admitted:
        if deadline_hit or time.monotonic() >= deadline:
            deadline_hit = True
            results.append(_result(check, ERROR, None, "gate deadline exceeded", tier))
            continue
        try:
            if check.get("type") == "evidence" or check.get("kind") == "evidence":
                # Evidence artifacts (`verdict_file`) are written by an agent
                # (e.g. the QA Engineer) operating on state_root — a
                # worktree's own `.fairmind/` does not exist at all (H1/F34)
                # — so evidence resolution stays on `cwd` (state_root), never
                # `work_dir`. Only a check's own EXEC subprocess follows the
                # worktree; the artifact it reads about does not move.
                r = evaluate_evidence(check, cwd)
            else:
                r = evaluate_check(check, work_dir, tier, deadline=deadline)
        except Exception as exc:  # noqa: BLE001 — one bad check must not crash the gate
            r = _result(check, ERROR, None, f"evaluation crashed: {exc}", tier)
        results.append(r)
        # Once the deadline bites, skip remaining checks fast (no more subprocesses).
        if r.get("reason") == "gate deadline exceeded":
            deadline_hit = True

    for check in pending:
        results.append(_result(check, ERROR, None,
                               "check not admitted (run admit_check.py)", tier))

    # Append an iteration record. Capture the previous *evaluation's* verdicts
    # BEFORE appending, so the status board can show each check's transition.
    # `iterations[]` may also hold non-evaluation audit entries (extend_budget);
    # count and look past them by testing for a "results" key. Moved ahead of
    # the consecutive-failure accounting below (H3/F21+F33) — the no-work
    # signal that gates that accounting needs `prev_iter` to read the
    # immediately preceding results-bearing evaluation's mutation signature.
    state.setdefault("iterations", [])
    n = sum(1 for it in state["iterations"] if "results" in it) + 1
    prev_iter = next((it for it in reversed(state["iterations"]) if "results" in it), None)
    prev_verdicts = {x["id"]: x["verdict"] for x in prev_iter["results"]} if prev_iter else {}

    # H3/F21+F33: "did work happen since the immediately preceding
    # results-bearing evaluation?" Computed UNCONDITIONALLY (independent of
    # `contract.scope` — see `_no_work_signature`). `no_work` gates ONLY the
    # not-green branch's budget/consecutive-failures accounting below; it
    # never affects which checks ran, their verdicts, or the all-green
    # confirmation streak.
    #
    # Fail-closed default (`no_work = False`, i.e. "count this evaluation as
    # today") unless ALL of the following hold:
    #   1. a predecessor results-bearing iteration exists (`prev_iter is not
    #      None`) — AC1's "the FIRST evaluation always counts".
    #   2. THIS evaluation's signal is not degraded (`sig_degraded is None`)
    #      — AC4, an unanswerable "did work happen?" is never read as "no
    #      work".
    #   3. the PREDECESSOR iteration recorded a non-degraded signature
    #      (`prev_iter.get("mutation_signature") is not None`) — the same
    #      fail-closed rule applied to the other side of the comparison: a
    #      predecessor whose own signal was unknown (or predates H3, so the
    #      key is simply absent) can never prove "unchanged" either.
    # Only when both signatures are known and byte-identical is this a
    # genuine no-work re-evaluation.
    current_signature, sig_degraded = _no_work_signature(state, work_dir, cwd)
    prev_signature_known = prev_iter is not None and prev_iter.get("mutation_signature") is not None
    no_work = (
        prev_iter is not None
        and sig_degraded is None
        and prev_signature_known
        and current_signature == prev_iter.get("mutation_signature")
    )

    # H8 (PCF-8/PCF-5): is the tree still being WRITTEN? A background maker that
    # keeps writing across turn boundaries makes the Stop-hook gate judge a
    # half-written tree — the signature moved (so `no_work` above is False) yet
    # the fix attempt is not complete. `_settle_age` reads the trace for the most
    # recent work-product mutation; within the settle window this evaluation is
    # "in flight". Two distinct freezes result (they intentionally differ — see
    # `freeze_cf` vs `freeze_budget` below); the green branch checks `in_flight`
    # on its own to freeze the confirmation streak. Fail toward counting on any
    # unknown (`settle_age is None`), and skip the whole probe when the window is
    # disabled (<= 0).
    now = now_utc()
    settle_window = _settle_window_s()
    settle_age = _settle_age(work_dir, cwd, now) if settle_window > 0 else None
    # `0 <= settle_age` floors the signal: a future / forward-skewed trace ts
    # (negative age) is NOT read as in-flight — it fails toward counting rather
    # than freezing the loop on a bad clock or a tampered trace (H8-F4).
    in_flight = settle_age is not None and 0 <= settle_age < settle_window
    # Count the run of results-bearing iterations immediately preceding this one
    # that were already in-flight. A non-results audit entry (arm / extend_budget
    # / recover / hold / release — a human control action) BREAKS the run, so a
    # resumed loop gets a fresh grace window rather than inheriting an exhausted
    # one (H8-F-C); a settled (non-in-flight) eval breaks it too.
    trailing_in_flight = 0
    for it in reversed(state["iterations"]):
        if "results" not in it:
            break  # a human control action starts a fresh grace run (H8-F-C)
        if it.get("in_flight"):
            trailing_in_flight += 1
        else:
            break
    settle_grace_left = trailing_in_flight < _settle_max_consecutive()
    # The two freezes differ on purpose:
    #   • `freeze_cf` — an in-flight evaluation is NEVER a completed failed
    #     attempt, so it must never advance `consecutive_failures` (which would
    #     feed a misattributed STRATEGY TURN or `blocked_failures` against work
    #     that is merely still being written — H8-F-A). Frozen on ANY in-flight
    #     eval, grace or no grace.
    #   • `freeze_budget` — deferred while in-flight, but only up to
    #     SETTLE_MAX_CONSECUTIVE evals, after which it charges so `max_iterations`
    #     stays a backstop that does not depend on `timeout_min` (H8-F1). (This
    #     bounds only the IN-FLIGHT path; the H3 `no_work` path is uncapped by
    #     design — a byte-identical tree can never go green, so it can only waste
    #     compute, never false-close, and the wall clock / human bound it.)
    freeze_cf = no_work or in_flight
    freeze_budget = no_work or (in_flight and settle_grace_left)

    # Per-check consecutive-failure accounting (admitted checks only). A frozen
    # re-evaluation (H3 no-work, or H8 in-flight) freezes the RED/ERROR side of
    # this — the check's `consecutive_failures` stays exactly where it was,
    # because nothing has actually failed a second time (or the failure is not
    # yet a complete attempt); a still-GREEN check's reset to 0 is unaffected
    # either way (idempotent).
    result_by_id = {r["id"]: r for r in results}
    for check in admitted:
        r = result_by_id.get(check["id"])
        if r and r["verdict"] == GREEN:
            check["consecutive_failures"] = 0
        elif not freeze_cf:
            check["consecutive_failures"] = check.get("consecutive_failures", 0) + 1
        # else: frozen re-evaluation of a still-red/error check — cf frozen.

    # Provenance degradations — attach every weakened guarantee to the result so
    # the status board surfaces it: a Tier-A→B downgrade (sandbox absent) or a
    # baseline measured on a dirty tree. A green with a weaker provenance is still
    # a green, but the human must see how it was proven.
    checks_by_id = {c.get("id"): c for c in state.get("checks", [])}
    for r in results:
        degraded = []
        if hermeticity_downgraded:
            degraded.append("hermeticity-unverified")
        if baseline_dirty(checks_by_id.get(r["id"], {}).get("baseline")):
            degraded.append("baseline dirty-tree")
        r["degraded"] = degraded

    all_green = bool(results) and all(r["verdict"] == GREEN for r in results)

    budget = state.setdefault("budget", {})
    spent = budget.setdefault("spent", {})
    # Engine-owned accounting field (same class as `confirmations`, T11): every
    # loop bootstrap writes `started_at: null`, and `setdefault` does not
    # overwrite a *present* null, so the stamp never landed and the timeout
    # guard below ran permanently disarmed. The engine's own clock must own
    # this field: stamp it on whichever evaluation first finds it unresolvable
    # (absent, null, or garbage), then never touch it again — a value that
    # already parses (including one this same stamp wrote on a prior
    # evaluation) is left alone.
    if _parse_iso(spent.get("started_at")) is None:
        spent["started_at"] = iso(now_utc())

    # `n`, `prev_iter` and `prev_verdicts` were computed earlier (H3/F21+F33),
    # ahead of the consecutive-failure accounting above, so they are already
    # available here. `mutation_signature` (and, when degraded, `mutation_
    # signature_degraded`) is persisted on EVERY results-bearing iteration —
    # regardless of verdict — so the NEXT evaluation (whatever its own
    # verdict) has a predecessor signature to compare against.
    iteration = {
        "n": n,
        "at": iso(now_utc()),
        "results": [{"id": r["id"], "verdict": r["verdict"], "value": r["value"]} for r in results],
        "mutation_signature": current_signature,
    }
    if sig_degraded is not None:
        iteration["mutation_signature_degraded"] = sig_degraded

    if all_green:
        k = confirmation_threshold(state)

        # A fresh loop's streak must start at 0 regardless of any persisted
        # (possibly seeded) value — n==1 means no prior results-bearing iteration
        # exists yet, so this is the very first evaluation. Done BEFORE the
        # hold/in-flight freeze below (H8-F3): otherwise a first evaluation that
        # is held or in-flight would consume the n==1 slot, and the next
        # (counting) evaluation — seeing n==2 — would build on a seeded streak
        # that was never reset, closing the loop on fewer than K genuine greens.
        if n == 1:
            state["confirmations"] = 0

        # H4/F24: a hold in force means a human-approved contract amendment is
        # in flight — this green evaluation must not be allowed to advance,
        # let alone close, the confirmation streak, because the very check(s)
        # it satisfies are what the amendment exists to replace. Checked
        # BEFORE the n==1 reset and BEFORE any increment below, so a held
        # loop's `confirmations` never moves at all (frozen, not merely
        # capped) — H4-AC1 evaluates a held gate 6 times, well past K, and
        # requires it stay at 0 (or whatever it already was) throughout. The
        # iteration record still carries `results` and `mutation_signature`
        # (H3 stays intact — a held evaluation IS a real evaluation) plus
        # `"held": True` so a human reading `iterations[]` can tell a held
        # green apart from an ordinary one. Without a hold this branch is a
        # no-op and behavior below is byte-identical to pre-H4.
        # Two independent reasons an all-green evaluation must NOT advance the
        # confirmation streak, frozen identically (H4-AC1: frozen, not merely
        # capped):
        #   • H4/F24 --hold: a human-approved amendment is in flight, so the
        #     checks this green satisfies are the ones the amendment replaces.
        #   • H8/PCF-8: the tree is still being written, so this "green" may be a
        #     read of a half-written tree whose RED-making test has not landed
        #     yet — a FALSE green, the one class a streak must never advance on.
        # The banner names whichever reason applies (hold wins when both hold,
        # being the human-driven one). Without either, this block is a no-op and
        # behavior below is byte-identical to pre-H8.
        hold = state.get("hold")
        if hold or in_flight:
            if hold:
                iteration["held"] = True
            if in_flight:
                iteration["in_flight"] = True
            feedback, owner = build_feedback(results, state, DECISION_ITERATE,
                                             prev_verdicts=prev_verdicts, iter_n=n)
            iteration["feedback_to"] = owner
            state["iterations"].append(iteration)
            if hold:
                reason_line = (
                    f"⏸ HOLD IN FORCE (since {hold.get('at', 'unknown')}) — a human-approved "
                    "contract amendment is in flight. This all-green evaluation does NOT advance "
                    f"the confirmation streak (frozen at {state.get('confirmations', 0)}/{k}), and "
                    "the loop can NEVER reach passed_pending_human while held. Release with "
                    "--release once the amendment lands — release also zeroes the streak, so a "
                    "streak earned against the superseded check does not carry over."
                )
            else:
                reason_line = (
                    f"⏳ TREE STILL SETTLING (H8) — a maker wrote work product {settle_age:.0f}s "
                    f"ago, within the {settle_window:.0f}s settle window. This all-green evaluation "
                    "is treated as in-flight and does NOT advance the confirmation streak (frozen "
                    f"at {state.get('confirmations', 0)}/{k}): a green read of a half-written tree "
                    "is a false green. Hold the orchestrator turn until the maker completes so the "
                    "gate evaluates a finished tree."
                )
            return {"decision": DECISION_ITERATE, "feedback": reason_line + "\n" + feedback,
                    "results": results}

        state["confirmations"] = state.get("confirmations", 0) + 1
        if state["confirmations"] >= k:
            state["status"] = "passed_pending_human"
            iteration["feedback_to"] = None
            state["iterations"].append(iteration)
            feedback, _ = build_feedback(results, state, DECISION_STOP_PASSED,
                                         prev_verdicts=prev_verdicts, iter_n=n)
            return {"decision": DECISION_STOP_PASSED, "feedback": feedback, "results": results}
        # Confirmation turns do not consume max_iterations budget: a genuinely
        # green run must always be allowed to reach K without being starved.
        feedback, owner = build_feedback(results, state, DECISION_ITERATE,
                                         prev_verdicts=prev_verdicts, iter_n=n)
        iteration["feedback_to"] = owner
        state["iterations"].append(iteration)
        return {"decision": DECISION_ITERATE, "feedback": feedback, "results": results}

    # Not green: a red/error evaluation consumes budget — UNLESS this is a
    # budget-frozen re-evaluation (`freeze_budget`): either nobody did any work
    # since the immediately preceding evaluation (H3 no-work, the SAME tree
    # already charged for) or the change is still in progress and within grace
    # (H8 in-flight, a half-written tree). The verdicts still record below (the
    # status board still shows red); only the spend is frozen. Because
    # `consecutive_failures` is frozen on EVERY in-flight eval (`freeze_cf`,
    # above), `commitment_boundaries` cannot reach the cap-1 threshold from
    # in-flight work, so no STRATEGY TURN is ever misattributed to a tree that is
    # merely still being written (H8-F-A) — even once budget grace is exhausted.
    state["confirmations"] = 0
    if not freeze_budget:
        spent["iterations"] = spent.get("iterations", 0) + 1
    feedback, owner = build_feedback(results, state, DECISION_ITERATE,
                                     prev_verdicts=prev_verdicts, iter_n=n)

    # Commitment boundaries — computed BEFORE appending this iteration so history
    # is the prior evaluations; may prepend banners and re-route the feedback.
    banners, routing_override, strategy_ids = commitment_boundaries(results, state, in_flight=in_flight)
    banner_prefix = ("\n".join(banners) + "\n\n") if banners else ""
    if strategy_ids:
        iteration["strategy_turn"] = strategy_ids
    # H8: surface WHY a red/error evaluation was or was not charged, and mark the
    # iteration so a human reading `iterations[]` can tell an in-flight evaluation
    # from a genuine one. `iteration["in_flight"]` is stamped whenever the tree is
    # in-flight — INCLUDING a grace-exhausted evaluation that now counts — so the
    # trailing-in-flight run keeps growing and every subsequent evaluation keeps
    # charging (H8-F1) until the tree genuinely settles.
    if in_flight:
        iteration["in_flight"] = True
        if freeze_budget:
            settle_banner = (
                f"⏳ TREE STILL SETTLING (H8) — a maker wrote work product {settle_age:.0f}s ago, "
                f"within the {settle_window:.0f}s settle window. This evaluation is treated as "
                f"in-flight and is NOT charged: budget frozen at "
                f"{spent.get('iterations', 0)}/{budget.get('max_iterations', 8)}, no consecutive-"
                "failure counted, no strategy turn. The gate is reading a half-written tree — hold "
                "the orchestrator turn until the maker completes. The wall-clock timeout still applies."
            )
        else:
            settle_banner = (
                f"⏳ SETTLE GRACE EXHAUSTED (H8) — the tree has been in-flight for "
                f"{trailing_in_flight + 1} consecutive evaluations (cap {_settle_max_consecutive()}). "
                "The settle window only DEFERS a budget charge — it can never suspend the budget "
                f"forever — so this red evaluation now consumes one: budget "
                f"{spent.get('iterations', 0)}/{budget.get('max_iterations', 8)}. Consecutive-failure "
                "is still NOT counted (an in-flight tree is not a completed attempt, so no STRATEGY "
                "TURN). If the maker is genuinely still working, hold the orchestrator turn until it "
                "completes rather than ending the turn into the gate."
            )
        banner_prefix = settle_banner + "\n\n" + banner_prefix
    iteration["feedback_to"] = routing_override or owner
    state["iterations"].append(iteration)

    blocked = budget_exhausted(state)
    if blocked:
        state["status"] = blocked
        feedback, _ = build_feedback(results, state, DECISION_STOP_BLOCKED, blocked,
                                     prev_verdicts=prev_verdicts, iter_n=n)
        return {"decision": DECISION_STOP_BLOCKED,
                "feedback": banner_prefix + feedback, "results": results}

    return {"decision": DECISION_ITERATE,
            "feedback": banner_prefix + feedback, "results": results}


_EXTEND_KEYS = {"iterations": "max_iterations",
                "failures": "max_consecutive_failures",
                "timeout_min": "timeout_min"}


def extend_budget(state, state_path, args):
    """Human-only verb: grant more budget to a *blocked* loop and resume it.

    Refused on any non-blocked status — a running loop still has budget, and the
    gate must never extend its own budget (that would defeat the point of a cap).
    There is no fake technical lock (in-band channel separation is impossible in
    Claude Code): the guard is procedural — the command asks the user first — plus
    this auditable record, `user_confirmed`, which the final human gate reviews.

    Resuming zeroes the confirmation streak AND every check's consecutive-failure
    counter, exactly as `--arm` zeroes the streak. Both are load-bearing: a
    resumed loop that kept a stale streak would close on fewer than K greens, and
    one that kept a check's failure count at the cap would re-block
    `blocked_failures` on its very next evaluation — spending the granted budget
    on nothing, since the failure guard reads that same counter.

    Honors `--dry-run`: prints what WOULD change to stderr and persists nothing.
    """
    status = state.get("status", "")
    if not status.startswith("blocked_"):
        print(f"--extend-budget refused: status is {status!r}, not a blocked_* state. Only a "
              "blocked loop can be extended (a running loop still has budget).", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # The procedural guard promised in the docstring: no non-empty --user-confirmed,
    # no extension. Checked before any mutation so a refusal leaves state untouched.
    if not (args.user_confirmed or "").strip():
        print("--extend-budget refused: --user-confirmed is required (must be a non-empty "
              "string) to extend a blocked loop's budget.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    budget = state.setdefault("budget", {})
    changes = {}
    for pair in args.extend_budget.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            print(f"--extend-budget: bad token {pair!r} (want key=value)", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        key, _, raw = pair.partition("=")
        key = key.strip()
        if key not in _EXTEND_KEYS:
            print(f"--extend-budget: unknown key {key!r} (allowed: {sorted(_EXTEND_KEYS)})",
                  file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        try:
            delta = int(raw)
        except ValueError:
            print(f"--extend-budget: {key} value must be an integer, got {raw!r}", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if delta <= 0:
            print(f"--extend-budget: {key} must be positive (grants more budget)", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        field = _EXTEND_KEYS[key]
        old = budget.get(field, 0) or 0
        budget[field] = old + delta
        changes[field] = {"from": old, "to": budget[field]}

    if not changes:
        print("--extend-budget: no changes parsed", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_status = status
    # Resume like --arm: zero the confirmation streak, and clear every check's
    # consecutive-failure counter so the granted budget is not immediately
    # re-consumed by a stale count already sitting at the cap (the failure guard
    # in budget_exhausted reads these counters on the very next evaluation).
    confirmations_reset_from = state.get("confirmations", 0)
    state["confirmations"] = 0
    failures_cleared = [c.get("id") for c in state.get("checks", [])
                        if c.get("consecutive_failures", 0)]
    for c in state.get("checks", []):
        c["consecutive_failures"] = 0
    state["status"] = "running"
    audit = {
        "event": "extend_budget",
        "at": iso(now_utc()),
        "prev_status": prev_status,
        "changes": changes,
        "confirmations_reset_from": confirmations_reset_from,
        "consecutive_failures_cleared": failures_cleared,
        "user_confirmed": args.user_confirmed,
    }
    summary = (f"{prev_status} → running; {changes}; confirmations "
               f"{confirmations_reset_from} → 0; consecutive_failures cleared for "
               f"{failures_cleared}; user_confirmed={args.user_confirmed!r}")

    if args.dry_run:
        print(f"[dry-run] extend_budget: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"extend_budget: {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


# `iterations[]` entries `run_gate` itself appends OUTSIDE a results-bearing
# evaluation (`"results" in it`), and which — unlike an audit-verb entry
# (arm/extend_budget/recover/hold/release) — can ONLY exist because the loop
# was genuinely `status == "running"` at the moment they were appended (both
# come from `run_gate`, reached only through the non-dry-run `status !=
# "running"` early return in `main()`, which itself requires a prior `--arm`).
# Used by `arm()`'s fresh-vs-re-arm classification below (H4 defect #2) as the
# legacy fallback's ALLOWLIST — narrower than "iterations[] is non-empty".
_ENGINE_EVALUATION_EVENTS = ("scope_violation", "worktree_degraded")


def _iterations_prove_prior_arm(iterations):
    """True iff `iterations[]` contains an entry that could only have been
    appended while the loop was genuinely `running` — a results-bearing
    evaluation (including the empty-results `blocked_no_checks` entry), or
    one of `run_gate`'s own non-results audit events (`_ENGINE_EVALUATION_
    EVENTS`). Both require a prior successful `--arm` to have happened at
    all (status can only reach "running" through `arm()`).

    Deliberately NOT satisfied by a bare verb-audit entry (`arm`,
    `extend_budget`, `recover`, `hold`, `release`) or by any unrecognized
    event — those prove nothing about whether the loop was EVER armed
    on their own. This is the H4 defect #2 hardening: before this, ANY
    non-empty `iterations[]` (`bool(state.get("iterations"))`) was read as
    proof of a prior arm, which a pre-arm `--hold`/`--release` audit entry
    (H4 defect #1, now closed by `hold_verb`/`release_verb`'s own status
    guard) — or any OTHER future writer that appends to `iterations[]`
    without going through a real evaluation — could poison into skipping
    the arm-time baseline freeze on a truly fresh arm."""
    for it in iterations or []:
        if "results" in it:
            return True
        if it.get("event") in _ENGINE_EVALUATION_EVENTS:
            return True
    return False


def arm(state, state_path, cwd, args):
    """Engine verb (T19): the ONLY place that flips a loop into `running`. Owns
    `budget.spent.started_at`, `budget.spent.first_armed_at`, `confirmations`,
    and the arm-time `contract.mutation_set` baseline, so no orchestrator ever
    hand-writes an accounting field again — see loop-state.json contract.arming
    (decisions C1-C10) for the full ruling this implements.

    Refuses (state untouched — checked before any mutation, same idiom as
    `extend_budget`) in these cases:
      - the loop is already `running` (C4/AC2) — arming it again would silently
        re-stamp a live loop's start instant and reset its confirmation streak;
      - there is no admitted check to gate on (C3/AC1), using the engine's own
        `admitted_checks` predicate — a loop with nothing admitted could never
        legitimately close;
      - a `validate_contract` coverage failure (T10/AC5) — a hard criterion is
        not wired to any admitted check;
      - (H7/F6) an admitted `kind:"guard"` check is NOT green at arm time — its
        guarded artifact has already regressed, so arming would only flip to
        `running` and burn the whole budget blocking on a guard doomed at t=0.
        This is the ONE place arm evaluates a check's live value, and it does so
        for GUARDS ONLY (a red-first machine check is RED at arm by construction).

    Every other status (`specified`, every `blocked_*` including
    `blocked_recovered`, or an absent/unknown status) is armable — this is the
    re-arm path after a human gate rejection or a `--recover`.

    Fresh-vs-re-arm turns on whether the loop was EVER armed — a durable fact
    (`budget.spent.first_armed_at`, plus `_iterations_prove_prior_arm` as the
    fallback for a loop armed before that marker existed). It is NEVER
    inferred from bare "has a results-bearing iteration", nor (H4 defect #2)
    from bare "iterations[] is non-empty": a `blocked_scope` loop whose only
    history is a `{"event":"scope_violation"}` entry HAS run and HAS been
    armed (W1.7a) and correctly counts, but a loop whose only history is a
    verb-only audit entry (`arm`/`extend_budget`/`recover`/`hold`/`release`,
    or any other unrecognized event landing in `iterations[]` before the
    first arm) has NOT — `_iterations_prove_prior_arm` allowlists only the
    entries `run_gate` itself can append (which require a prior `running`
    status to exist at all), so a stray pre-arm audit entry can never again
    misread a truly fresh arm as a re-arm and skip the baseline freeze below.

    On success: sets `status = "running"`, resets `confirmations` to 0, and:
      - FRESH arm — stamps `started_at` and `first_armed_at` from the engine's
        own clock (overwriting any pre-seeded value), and freezes the arm-time
        `contract.mutation_set` baseline: the `HEAD` sha to diff "changed since
        arm" FROM, plus each already-dirty path anchored to its exact arm-time
        bytes (`pre_dirty_anchors`), so a file dirty at arm cannot later be
        rewritten out of scope for free.
      - RE-ARM (already armed once) — PRESERVES `started_at`, `first_armed_at`,
        budget spend and the frozen baseline (C5: a re-stamp would truncate the
        whole-run window and mint a second ledger loop_id; re-freezing the
        baseline to the post-mutation `HEAD` would erase every mutation committed
        before the re-arm from the set), only stamping `started_at` if it is
        itself unresolvable (backstop for a hand-armed/legacy loop).
    Always clears `owner_session` (C7 — a stale id would strand a re-armed loop
    behind the foreign-session no-op branch) and appends one `{"event": "arm",
    ...}` audit entry to `iterations[]` with no `results` key (C8), so every
    consumer that counts evaluations by testing `"results" in it` stays blind.

    Honors `--dry-run`: prints what WOULD change to stderr and persists nothing.

    Never invoked by the gate itself: `--arm` is a human/orchestrator verb,
    exactly like `--extend-budget`. Arming does not RUN the gate; its one
    deliberate evaluation is the H7/F6 guard-only pre-check above, which
    evaluates admitted `kind:"guard"` checks solely to REFUSE an already-broken
    loop up front — it never confirms, mutates, or persists.
    """
    status = state.get("status")

    # C4: the ONE status --arm refuses is 'running' — no silent re-stamp of
    # started_at, no silent streak reset, no audit entry under a live gate.
    if status == "running":
        print("--arm refused: the loop is already 'running'. Arming a live loop "
              "would silently re-stamp its start instant and reset its "
              "confirmation streak — nothing to do.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # C3/AC1: reuse the engine's own admitted-check predicate verbatim — a
    # second, drift-prone definition of "admitted" is a bug waiting to happen.
    admitted, pending = admitted_checks(state)
    if not admitted:
        quarantine_ids = {q.get("id") for q in state.get("quarantine", [])}
        reason = "no admitted check to gate on"
        if pending:
            reason += f"; {len(pending)} check(s) not admitted (run admit_check.py)"
        if quarantine_ids:
            reason += f"; {len(quarantine_ids)} quarantined"
        print(f"--arm refused: {reason}.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # (3) T10/AC5/R1: contract validation — the guarantee cannot be bypassed by an
    # orchestrator that forgets to run --validate-contract. Placed LAST so the more
    # precise C4/C3 diagnostics above still fire first (a nothing-admitted loop is
    # told to run admit_check.py, not handed a list of criteria uncovered *because*
    # nothing is admitted). Refused before ANY mutation — same idiom as C3/C4 — so
    # loop-state.json is byte-untouched, status is not flipped, started_at is not
    # stamped, and no arm audit entry is appended. Honors --dry-run (nothing is
    # persisted on this path either, because we return before the dry-run block).
    errors = validate_contract(state)
    if errors:
        _emit_contract_refusal(errors, "--arm")
        return EXIT_INTERNAL_ERROR

    # (4) H7/F6: arm-time GUARD gate. A `kind:"guard"` check is proven GREEN exactly
    # ONCE, at admission (admit_guard's green_at_spec); its admitted_hash freezes the
    # DESCRIPTOR, not the world it guards. So a loop whose guarded artifact has since
    # regressed — guard admitted, descriptor untouched (hash still valid) — would
    # otherwise sail through --arm, flip to 'running', and burn its whole budget
    # blocking on a guard that was doomed at t=0. Here, and ONLY here, arm deliberately
    # crosses the "arm never evaluates" line: it evaluates the LIVE value of every
    # admitted GUARD (via the engine's own evaluate_check) and REFUSES if any is not
    # GREEN.
    #
    # GUARD-SCOPED, on purpose: a red-first MACHINE/functional/metric check is RED at
    # arm BY CONSTRUCTION, so evaluating those here would refuse every legitimate loop
    # — hence the `kind == "guard"` filter and NOT `admitted` wholesale. This step is
    # SEPARATE from validate_contract, which stays PURE-STATIC (no evaluation folded
    # in). FAIL-CLOSED: RED *or* ERROR (crash / missing signal / non-determinism) is
    # not a pass. Refused BEFORE any mutation — the same untouched-state idiom as the
    # C3/C4/contract refusals above (loop-state.json byte-unchanged, status not
    # flipped, started_at not stamped, no arm audit entry); this path returns before
    # the dry-run persist block, so --dry-run writes nothing either way. Both the TREE
    # (work_dir) and the TIER are resolved exactly as run_gate does: work_dir via
    # resolve_work_dir, so the guards evaluate against the SAME tree the running gate
    # will (H1/F34 — the recorded worktree when valid, else state_root), failing
    # closed on an untrustworthy worktree rather than silently falling back to
    # state_root; the tier by requested tier degrading A→B when `srt` is absent but
    # WITHOUT persisting the downgrade (a refusal must leave the bytes intact); a
    # bounded monotonic deadline over just the guards keeps arm from hanging on a
    # runaway guard command.
    guards = [c for c in admitted if c.get("kind") == "guard"]
    if guards:
        # H1/F34: evaluate the guards against the SAME tree run_gate uses (work_dir),
        # resolved by the very helper run_gate calls. A recorded worktree.path that
        # cannot be PROVEN to be a real, registered worktree of this repo must FAIL
        # CLOSED here — refuse to arm, naming the condition — never silently evaluate
        # state_root instead: that silent fallback is exactly F34 wearing a different
        # hat (arm would accept a loop whose guard is green on the main tree but red
        # in the worktree the gate actually runs against). Same byte-untouched refusal
        # idiom as the C3/C4/contract refusals above — resolve_work_dir reads state,
        # it never mutates it, so loop-state.json is left byte-for-byte intact.
        work_dir, wt_degradation = resolve_work_dir(state, cwd)
        if wt_degradation is not None:
            reason, detail = wt_degradation
            print(
                "--arm refused: this loop records a worktree.path that could not be "
                f"resolved to a real, registered worktree of this repo ({reason}: "
                f"{detail}). Failing closed rather than silently evaluating the main "
                "tree — a silent fallback there would be the exact defect (F34) this "
                "guard exists to prevent, since the running gate evaluates the "
                "worktree. A human must resolve the worktree (re-create it via "
                "loop_worktree.py --create, or clear state['worktree']) before this "
                "loop can be armed.",
                file=sys.stderr)
            return EXIT_INTERNAL_ERROR

        guard_tier = state.get("hermeticity_tier", "B")
        if guard_tier == "A" and not srt_available():
            guard_tier = "B"  # sandbox absent at arm time — degrade locally, don't persist
        natural = sum(
            int(c.get("exec", {}).get("timeout_s", 300))
            * max(1, int(c.get("determinism", {}).get("runs", 1)))
            for c in guards
        ) + 60
        budget_s = min(natural, DEFAULT_DEADLINE_CAP_S)
        env_deadline = os.environ.get("FAIRMIND_GATE_DEADLINE_S")
        if env_deadline:
            try:
                budget_s = min(budget_s, float(env_deadline))
            except ValueError:
                pass
        guard_deadline = time.monotonic() + budget_s

        not_green = []
        for c in guards:
            try:
                r = evaluate_check(c, work_dir, guard_tier, deadline=guard_deadline)
                verdict, reason = r.get("verdict"), r.get("reason")
            except Exception as exc:  # noqa: BLE001 — fail closed: a crash is not a pass
                verdict, reason = ERROR, f"evaluation crashed: {exc}"
            if verdict != GREEN:
                not_green.append((c.get("id"), verdict, reason))

        if not_green:
            detail = "; ".join(f"{cid!r} → {verdict} ({reason})"
                               for cid, verdict, reason in not_green)
            print(
                f"--arm refused: {len(not_green)} admitted guard(s) NOT green at arm "
                f"time — the guarded behaviour is already broken, so arming would only "
                f"burn the whole budget blocking on a guard doomed at t=0: {detail}. "
                "Fix the guarded artifact (or re-author the guard via admit_check.py), "
                "then re-arm.",
                file=sys.stderr)
            return EXIT_INTERNAL_ERROR

    prev_status = status
    confirmations_reset_from = state.get("confirmations", 0)

    budget = state.setdefault("budget", {})
    spent = budget.setdefault("spent", {})

    # W1.7a + H4 defect #2: fresh-vs-re-arm on a DURABLE "ever armed" fact,
    # never the results proxy `any("results" in it ...)` (would misread a
    # blocked_scope loop whose only iteration entry is a
    # {"event":"scope_violation"} as fresh) and never bare "iterations[] is
    # non-empty" (would misread a pre-arm verb-only audit entry — e.g. a
    # {"event":"hold"/"release"} that slipped in before hold_verb/release_verb
    # grew their own status guard, or any other future pre-arm audit writer —
    # as proof of a prior arm). `first_armed_at` is stamped once at the first
    # arm and never cleared; `_iterations_prove_prior_arm` is the legacy
    # fallback for a loop armed before that marker existed, narrowed to the
    # entries `run_gate` itself can append (which require a prior `running`
    # status to exist at all — see that helper's docstring).
    ever_armed = bool(spent.get("first_armed_at")) or _iterations_prove_prior_arm(
        state.get("iterations"))
    fresh = not ever_armed

    # C5: a FRESH arm stamps started_at UNCONDITIONALLY from the engine's own
    # clock, overwriting any pre-seeded value on disk. A RE-ARM PRESERVES the
    # existing resolvable started_at, only stamping if it is itself unresolvable
    # (backstop for a hand-armed/legacy loop).
    if fresh or _parse_iso(spent.get("started_at")) is None:
        spent["started_at"] = iso(now_utc())
    started_at = spent["started_at"]
    # Durable ever-armed marker: stamped once (to the value now on disk), never
    # overwritten — a re-arm leaves the original first-arm instant intact.
    if not spent.get("first_armed_at"):
        spent["first_armed_at"] = started_at

    # W1.2-arm: freeze the arm-time mutation-set baseline on a FRESH arm inside a
    # git tree — the frozen HEAD sha the scope boundary diffs "changed since arm"
    # FROM, and each already-dirty path anchored to its exact arm-time bytes so a
    # file dirty at arm cannot be rewritten out of scope for free (pre_existing
    # is byte-identity, not path membership). A re-arm PRESERVES the original
    # baseline: re-freezing to the post-mutation HEAD would erase every mutation
    # this run already committed from the set. Outside a git tree the baseline is
    # left as-is and the scope boundary fails closed (no-baseline-ref) rather
    # than silently under-blocking.
    #
    # H1/F34 EXPLICIT DECISION: this freeze deliberately stays on `cwd`
    # (state_root), never `worktree.path`, even when the state already
    # records a worktree at arm time. A worktree is created from HEAD
    # (`loop_worktree.py:192-193`, `worktree add -b <branch> <target> HEAD`),
    # so at the instant of a fresh `--create` the two HEADs coincide; but the
    # worktree's branch (`loop/<ref>`) is a tip the MAKER advances during the
    # run, while state_root's HEAD is not. Freezing on state_root's HEAD is
    # therefore the anchor that stays correct across the whole run regardless
    # of what the maker commits in the worktree afterward — freezing on the
    # worktree's HEAD would risk silently erasing every pre-arm mutation from
    # the set the moment the maker's first worktree commit lands. `run_gate`'s
    # OWN mutation-set query at evaluation time still runs against `work_dir`
    # (the worktree, when valid) — only the FROZEN REFERENCE point is pinned
    # to state_root; the two trees share one object database (a linked
    # worktree), so `git diff <state_root's frozen HEAD>` resolves correctly
    # from inside the worktree at evaluation time (see `compute_mutation_set`
    # called with `work_dir` — the `arm_ref` sha it diffs against is portable
    # across both trees).
    if fresh and _is_git_work_tree(cwd):
        try:
            head_sha = _run_git_query(cwd, "rev-parse", "HEAD").strip()
        except _GitQueryError:
            head_sha = None
        if head_sha:
            arm_set = compute_mutation_set(cwd, head_sha)
            if not arm_set.get("degraded"):
                baseline = (state.setdefault("contract", {})
                                 .setdefault("mutation_set", {})
                                 .setdefault("baseline", {}))
                baseline["recorded_at_arm"] = True
                baseline["ref"] = head_sha
                baseline["pre_dirty"] = pre_dirty_anchors(
                    cwd, [p["path"] for p in arm_set["paths"]])

    state["status"] = "running"
    state["confirmations"] = 0  # C6: every arm resets the streak, fresh or re-arm
    # C7: clear owner_session so a re-arm from a NEW session (the normal case —
    # work resumes tomorrow, in a fresh Claude Code session) can still claim the
    # loop; a stale id would send that session's Stop hook down the
    # foreign-session no-op branch and the gate would never fire again.
    state.pop("owner_session", None)

    # C8: one uniform audit entry on every successful arm (fresh and re-arm) —
    # no "n" key, no "results" key, so every consumer that counts evaluations by
    # testing "results" in it stays blind to it, exactly like extend_budget.
    entry = {
        "event": "arm",
        "at": iso(now_utc()),
        "prev_status": prev_status,
        "confirmations_reset_from": confirmations_reset_from,
        "started_at": started_at,
    }
    if (args.user_confirmed or "").strip():
        entry["user_confirmed"] = args.user_confirmed

    # C6: arming grants no budget — warn (never block) when re-arming a loop
    # that is already at/over its iteration cap, so the human sees it will
    # legitimately re-block on the next non-green evaluation unless they also
    # run --extend-budget.
    warn = ""
    max_iterations = budget.get("max_iterations", 8)
    if spent.get("iterations", 0) >= max_iterations:
        warn = (f" WARNING: spent.iterations ({spent.get('iterations', 0)}) already >= "
                f"max_iterations ({max_iterations}) — this loop will re-block on its next "
                "non-green evaluation unless the budget is also extended via --extend-budget.")

    if args.dry_run:
        print(f"[dry-run] arm: WOULD set {prev_status!r} → running; started_at={started_at}; "
              f"confirmations {confirmations_reset_from} → 0.{warn} NOTHING persisted.",
              file=sys.stderr)
        return EXIT_ALLOW_STOP

    state.setdefault("iterations", []).append(entry)
    save_state(state_path, state)
    print(f"arm: {prev_status!r} → running; started_at={started_at}; "
          f"confirmations {confirmations_reset_from} → 0.{warn}", file=sys.stderr)
    return EXIT_ALLOW_STOP


def recover(state, state_path, args):
    """Human-only recovery verb (W2.1): free a loop wedged in `running` whose
    owning session is gone. No other verb reaches that state — `--arm` refuses a
    running loop (C4), `--extend-budget` refuses a non-blocked one, a fresh
    session's Stop hook silently no-ops on the ownership mismatch, and
    `timeout_min` is only ever re-evaluated by an actual evaluation that, with no
    live session driving stops, never comes. So a crashed/closed session leaves
    its loop pinned `running` forever with no engine path out.

    Gated ONLY on an explicit, audited human confirmation (`--user-confirmed`) —
    NEVER on a session-id mismatch. A mismatch is exactly what a *second*
    concurrent session trips while the first is still legitimately driving the
    loop; recovering on that signal would let the second session silently steal a
    live loop. Only a human asserting "the session is gone" may force the release.

    Transitions `running` → `blocked_recovered` (a `blocked_*` status: `--arm`
    re-arms it, `--extend-budget` extends it, and the Stop hook allows the
    wedged turn to end) and clears `owner_session` so the next session can claim
    the re-armed loop. Refused (state untouched) on any non-`running` status — a
    non-running loop is already recoverable via `--arm`/`--extend-budget`.

    Honors `--dry-run`: prints what WOULD change to stderr and persists nothing.
    """
    status = state.get("status")
    if status != "running":
        print(f"--recover refused: status is {status!r}, not 'running'. Recovery only "
              "applies to a loop wedged in 'running' with no live session; a non-running "
              "loop is already recoverable via --arm (re-arm) or --extend-budget.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Human-only, checked before any mutation so a refusal leaves state untouched.
    if not (args.user_confirmed or "").strip():
        print("--recover refused: --user-confirmed is required (a non-empty human reason). "
              "Recovery force-frees a running loop, so it is gated on an explicit human "
              "confirmation, never on a session-id mismatch — a second concurrent session "
              "must never silently steal a loop the first is still driving.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_owner = state.get("owner_session")
    audit = {
        "event": "recover",
        "at": iso(now_utc()),
        "prev_status": "running",
        "prev_owner_session": prev_owner,
        "user_confirmed": args.user_confirmed,
    }
    summary = (f"running → blocked_recovered; owner_session {prev_owner!r} → cleared; "
               f"user_confirmed={args.user_confirmed!r}")

    if args.dry_run:
        print(f"[dry-run] recover: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state["status"] = "blocked_recovered"
    state.pop("owner_session", None)
    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"recover: {summary}. Re-arm with --arm (or grant budget with --extend-budget) "
          "to resume.", file=sys.stderr)
    return EXIT_ALLOW_STOP


def hold_verb(state, state_path, args):
    """Human-only verb (H4/F24): suspend confirmation counting while a
    human-approved contract amendment is in flight, so a green loop cannot
    close on the very check the amendment exists to replace (F24). Sets
    `state["hold"] = {"at": <iso>[, "user_confirmed": <str>]}` — the marker
    `run_gate`'s all-green branch reads (see there) to freeze the
    confirmation streak and force ITERATE regardless of how green the
    checks are, for as long as the marker stays present.

    Refuses (state untouched, checked before any mutation) on any status
    OTHER than `running` (H4 adversarial-pass amendment). A hold suspends
    CONFIRMATION COUNTING, which is only a meaningful concept on a live,
    running loop — a pre-arm hold used to be accepted silently (the original
    "conservative by construction, needs no status guard" design) and that
    is exactly what broke `arm()`'s fresh-vs-re-arm classification: a hold
    audit entry landing in `iterations[]` BEFORE the first `--arm` made a
    truly fresh arm look like a re-arm and skip the baseline freeze (see
    `arm()`'s `ever_armed`, and `test_h4_hold.py`'s
    `test_hold_and_release_refused_when_not_running`). "Conservative" now
    means "can only prevent a close on a loop that could otherwise close" —
    which presupposes the loop is running; it does not mean "accepted in any
    state". A hold on a genuinely `running` loop still works exactly as
    before.

    No mandatory `--user-confirmed` — recorded on the audit entry when
    supplied, but not required. Idempotent: holding an already-held loop
    simply overwrites the marker with a fresh timestamp.

    Appends one `{"event": "hold", ...}` audit entry to `iterations[]` with
    NO "results" key — the same shape as `arm`/`extend_budget`/`recover` —
    so evaluation numbering and the confirmation streak stay blind to it
    (H4-AC3).

    Honors `--dry-run`: prints what WOULD change to stderr and persists
    nothing.
    """
    status = state.get("status")
    if status != "running":
        print(f"--hold refused: status is {status!r}, not 'running'. A hold suspends "
              "confirmation counting, which is only meaningful on a live, running loop — "
              "a pre-arm hold is exactly what poisons --arm's fresh-vs-re-arm "
              "classification (see arm()'s ever_armed). Arm the loop first (--arm), "
              "then --hold.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_hold = state.get("hold")
    at = iso(now_utc())
    hold_marker = {"at": at}
    confirmed = (args.user_confirmed or "").strip()
    if confirmed:
        hold_marker["user_confirmed"] = confirmed
    audit = {"event": "hold", "at": at, "prev_hold": prev_hold}
    if confirmed:
        audit["user_confirmed"] = confirmed
    summary = (f"hold set (at={at}); confirmations frozen at "
               f"{state.get('confirmations', 0)} until --release")

    if args.dry_run:
        print(f"[dry-run] hold: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state["hold"] = hold_marker
    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"hold: {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


def release_verb(state, state_path, args):
    """Human-only verb (H4/F24): end a hold set by `--hold` and resume
    confirmation counting FROM 0 (H4-AC2) — a streak earned against the
    superseded check the amendment just replaced must not carry over once
    the amendment lands. Clears `state["hold"]` and zeroes
    `state["confirmations"]` UNCONDITIONALLY, even if no hold was in force:
    a release with nothing to release is harmless (there is nothing to
    un-freeze, and zeroing an already-zero streak changes nothing
    observable), and keeping the verb unconditional avoids a second,
    drifting definition of "is a hold active" from creeping into this
    function alone.

    Refuses (state untouched, checked before any mutation) on any status
    OTHER than `running` (H4 adversarial-pass amendment, mirroring
    `hold_verb`'s guard) — a release only makes sense undoing a hold that
    could only ever have been placed on a running loop now that `--hold`
    itself refuses pre-arm. Keeping the two verbs' status guards symmetric
    also closes the same `arm()`-poisoning path from the release side (a
    release-only audit entry landing in `iterations[]` before the first
    `--arm` would trip the identical fresh-vs-re-arm misread).

    Appends one `{"event": "release", ...}` audit entry to `iterations[]`
    with NO "results" key — the same shape as `hold`/`arm`/`extend_budget`
    — so evaluation numbering and the confirmation streak stay blind to it
    (H4-AC3).

    Honors `--dry-run`: prints what WOULD change to stderr and persists
    nothing.
    """
    status = state.get("status")
    if status != "running":
        print(f"--release refused: status is {status!r}, not 'running'. A release only "
              "makes sense on a loop that --hold could have suspended, which itself now "
              "requires 'running'. Arm the loop first (--arm) if it needs one.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    prev_hold = state.get("hold")
    confirmations_reset_from = state.get("confirmations", 0)
    confirmed = (args.user_confirmed or "").strip()
    audit = {
        "event": "release",
        "at": iso(now_utc()),
        "prev_hold": prev_hold,
        "confirmations_reset_from": confirmations_reset_from,
    }
    if confirmed:
        audit["user_confirmed"] = confirmed
    summary = (f"hold cleared (was {prev_hold!r}); confirmations "
               f"{confirmations_reset_from} → 0, resuming the streak from scratch")

    if args.dry_run:
        print(f"[dry-run] release: WOULD {summary}. NOTHING persisted.", file=sys.stderr)
        return EXIT_ALLOW_STOP

    state.pop("hold", None)
    state["confirmations"] = 0
    state.setdefault("iterations", []).append(audit)
    save_state(state_path, state)
    print(f"release: {summary}", file=sys.stderr)
    return EXIT_ALLOW_STOP


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the fairmind-coding loop gate.")
    parser.add_argument("--state", help="Explicit path to loop-state.json (testing/inspection).")
    parser.add_argument("--cwd", help="Repository root (defaults to $CWD or process cwd).")
    parser.add_argument("--emit-json", help="Also write the full decision object to this path.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate and report without mutating loop-state.json.")
    parser.add_argument("--extend-budget", metavar="KEY=VALUE[,KEY=VALUE]",
                        help="Human-only: grant a blocked loop more budget "
                             "(keys: iterations, failures, timeout_min). Never invoked by the gate.")
    parser.add_argument("--arm", action="store_true",
                        help="Human/orchestrator-only: flip a loop into 'running', stamping "
                             "budget.spent.started_at and zeroing confirmations. Refused on an "
                             "already-'running' loop or with no admitted check. Never invoked "
                             "by the gate itself.")
    parser.add_argument("--recover", action="store_true",
                        help="Human-only: free a loop wedged in 'running' whose owning session "
                             "is gone (running -> blocked_recovered, owner_session cleared) so "
                             "--arm/--extend-budget can resume it. Requires --user-confirmed; "
                             "gated on that human confirmation, never on a session-id mismatch.")
    parser.add_argument("--hold", action="store_true",
                        help="Human-only (H4/F24): suspend confirmation counting while a "
                             "human-approved contract amendment is in flight, so a green loop "
                             "cannot close on the very check the amendment exists to replace. "
                             "Sets state['hold']; a held all-green evaluation stays 'running' "
                             "forever (never reaches passed_pending_human). Refused on any "
                             "status other than 'running' (a pre-arm hold poisons --arm's "
                             "fresh-vs-re-arm classification) — arm the loop first. "
                             "--user-confirmed is optional. Never invoked by the gate itself.")
    parser.add_argument("--release", action="store_true",
                        help="Human-only (H4/F24): clear a hold set by --hold and resume "
                             "confirmation counting FROM 0 — a streak earned against the "
                             "superseded check does not carry over. Refused on any status "
                             "other than 'running', mirroring --hold's guard. --user-confirmed "
                             "is optional. Never invoked by the gate itself.")
    parser.add_argument("--validate-contract", action="store_true",
                        help="Read-only: check that every HARD contract.criteria[] entry is "
                             "covered by a live, admitted check. Exits non-zero (naming every "
                             "offender + recommending interactive mode) on an uncovered hard "
                             "criterion. Never mutates loop-state.json, never evaluates a check. "
                             "--arm runs the SAME validation internally and refuses on error.")
    parser.add_argument("--user-confirmed",
                        help="Records the human's literal confirmation in the "
                             "arm/extend-budget/recover audit entry.")
    parser.add_argument("--session-id",
                        help="Claude Code session id (from the Stop hook). Binds a running loop to the "
                             "first session that drives it; other sessions in the same repo no-op.")
    args = parser.parse_args(argv)

    state_path, cwd = resolve_state_path(args)

    # No active loop → silent no-op so the Stop hook composes outside loop mode.
    if not state_path or not os.path.isfile(state_path):
        if args.extend_budget:
            print("--extend-budget: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.arm:
            print("--arm: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.recover:
            print("--recover: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.hold:
            print("--hold: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.release:
            print("--release: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        if args.validate_contract:
            print("--validate-contract: no loop-state.json found", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
        return EXIT_ALLOW_STOP
    try:
        state = load_state(state_path)
    except (OSError, ValueError) as exc:
        print(f"loop-check: cannot read {state_path}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Human-only budget extension: an explicit flag, never an implicit env path.
    if args.extend_budget:
        return extend_budget(state, state_path, args)

    # Human/orchestrator-only arming verb (C1/C2): handled here, BEFORE the
    # status != "running" early return below — placing it after that guard
    # would make arming a `specified` (never-armed) loop a silent no-op, exit
    # 0 with nothing done. Also short-circuits before the session-ownership
    # claim: arming never evaluates checks.
    if args.arm:
        return arm(state, state_path, cwd, args)

    # Human-only recovery verb (W2.1): the ONLY path out of a `running` loop
    # whose session is gone. Handled here, before the status != "running" guard
    # (a running loop would otherwise fall through to the gate) and before the
    # session-ownership claim (recovery is deliberately session-agnostic).
    if args.recover:
        return recover(state, state_path, args)

    # Human-only hold/release verbs (H4/F24): handled here, ahead of the
    # generic status != "running" guard below, but each now enforces ITS OWN
    # equivalent guard internally (hold_verb/release_verb both refuse on any
    # status other than "running" — H4 adversarial-pass amendment). A hold
    # settable on any status, including pre-arm, was the original design and
    # is exactly what broke arm()'s fresh-vs-re-arm classification: a
    # pre-arm hold/release audit entry in iterations[] made a genuinely
    # fresh --arm look like a re-arm and skip the baseline freeze. Dispatched
    # here (before the generic guard, before the session-ownership claim)
    # only so each verb's own refusal message and exit code are the ones the
    # caller sees, matching --arm/--recover's placement — not because the
    # status check itself is skipped.
    if args.hold:
        return hold_verb(state, state_path, args)

    if args.release:
        return release_verb(state, state_path, args)

    # T10/AC1: `--validate-contract` — the read-only twin. Handled here, before
    # the `status != "running"` guard below (it must answer for a `specified`,
    # never-armed loop, which is exactly when a human asks "is this armable?"),
    # and before the session-ownership claim (it evaluates nothing and mutates
    # nothing, so ownership is irrelevant). Composes with --state/--cwd like every
    # other verb; --dry-run is a no-op for it (it is read-only either way).
    if args.validate_contract:
        return validate_contract_verb(state)

    # AC6(a): `--dry-run` evaluates the gate regardless of `status` — it never
    # persists (the `save_state` call below is itself gated on `not
    # args.dry_run`), so there is nothing to protect by refusing it on a
    # not-yet-armed (`specified`) or terminal loop. This is what lets the
    # arm-time smoke run (Phase 0, before `--arm` is ever called) actually
    # evaluate something, instead of forcing the orchestrator to hand-write
    # `status: "running"` first — the exact accounting write T19 exists to
    # abolish (AC6, mid-loop contract amendment).
    #
    # AC6(b), non-negotiable: this relaxation is scoped to `args.dry_run`
    # ONLY. The real (non-dry-run) path — what the Stop hook actually drives —
    # MUST still early-return on any status != "running": the loop stays an
    # inert signal outside itself. Do not widen this bypass to the plain path.
    #
    # F28 special case: a `blocked_scope` loop is NOT necessarily as terminal
    # as every other blocked_*/passed_pending_human status — a DEGRADED stop
    # (the mutation set was merely UNKNOWN, never a real violation) may be a
    # transient that has since cleared. `_degraded_scope_recoverable` keys
    # this off the one signal already persisted on disk (the trailing
    # scope_violation entries' `"degraded"` key, A1a/A1b) and bounds the
    # retries at DEGRADED_SCOPE_RETRY_CAP: a REAL violation (no `"degraded"`
    # key) or a cap-exhausted transient falls through to the same terminal
    # no-op as before. Eligible cases fall through to `run_gate` below instead
    # of no-op'ing — optimistically flipping status back to "running" first so
    # a genuinely recovered evaluation proceeds exactly like any other running
    # loop; if the transient has NOT cleared, `evaluate_scope`/`run_gate`
    # re-block to "blocked_scope" and append another degraded audit entry.
    if state.get("status") != "running" and not args.dry_run:
        if state.get("status") == "blocked_scope" and _degraded_scope_recoverable(state):
            state["status"] = "running"
        else:
            return EXIT_ALLOW_STOP  # already terminal — nothing to enforce

    # Session ownership (portable multi-session guard). loop-state.json is repo-global
    # and the Stop hook fires on EVERY session's stop in this repo — so without this an
    # unrelated session (a different workstream in the same checkout) would have its stop
    # gated by, and could iterate/consume budget on, a loop it has nothing to do with.
    # The first session that drives a running gate CLAIMS the loop; any other session is a
    # silent no-op (allow stop), never blocked and never a competing maker. Skipped under
    # --dry-run (inspection) and when no session id is supplied (backward compatible).
    sid = (args.session_id or "").strip()
    if not args.dry_run and sid:
        owner = state.get("owner_session")
        if not owner:
            state["owner_session"] = sid  # claim; persisted by the save_state below
        elif owner != sid:
            return EXIT_ALLOW_STOP  # foreign session → silent no-op, no mutation

    try:
        decision = run_gate(state, cwd, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — a gate crash must be surfaced, not silent
        print(f"loop-check: gate evaluation error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if not args.dry_run:
        save_state(state_path, state)

    if args.emit_json:
        with open(args.emit_json, "w", encoding="utf-8") as fh:
            json.dump({"decision": decision["decision"],
                       "results": decision["results"],
                       "status": state.get("status")}, fh, indent=2)

    feedback = decision.get("feedback", "")
    if feedback:
        print(feedback)

    if decision["decision"] == DECISION_ITERATE:
        return EXIT_ITERATE
    # Block ONCE on the transition into a terminal state (passed_pending_human or
    # blocked_*) so the Stop hook surfaces the final report — the human gate on a
    # pass, the cost-ask on a block — and the orchestrator is re-invoked to present
    # it, instead of the turn ending silently. The terminal status is now persisted,
    # so the NEXT stop returns EXIT_ALLOW_STOP at the `status != "running"` guard
    # above: this blocks exactly once and never loops. Skipped under --dry-run (state
    # is not persisted, so it is inspection only and would otherwise block forever).
    if not args.dry_run and decision["decision"] in (DECISION_STOP_PASSED, DECISION_STOP_BLOCKED):
        # Record the closed loop in the run ledger. Lazy, guarded import: the ledger
        # is cosmetic, so a missing or broken loop_ledger must never fail the gate.
        try:
            import loop_ledger
            loop_ledger.record_terminal(cwd, state, decision["results"])
        except Exception:  # noqa: BLE001 — never let a ledger error fail-close a check
            pass
        return EXIT_ITERATE
    return EXIT_ALLOW_STOP  # noop, or a terminal state already surfaced on a prior stop


if __name__ == "__main__":
    sys.exit(main())
