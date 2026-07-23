#!/usr/bin/env python3
"""
loop_open.py — the deterministic opening of the loop-family commands, in ONE call,
plus the `--repoint` verb that makes the stale-context fix an engine step (F23/F40).
`--mode {loop,develop}` selects which command is opening: it picks the banner and the
map's tier-2 marker, and, on --repoint, the `mode` stamped into active-context.json.

No-arg (the opener): prints the banner and, on a first run in this repo, the
stacked-loop map. The model runs this as the command's first action and does NOT
re-emit the text — the output is shown to the user as tool output, so the opening
is deterministic (a script's stdout can't drift) and cheap (one tool call instead
of re-typing ~30 lines). The transactional dashboard is rendered separately, as a
Markdown table the model relays into its message (`loop_dashboard.py --md`).

`--repoint --task-ref <ref> [--base-path <path>]` (F23/F40): atomically point
`.fairmind/active-context.json` at the run that is starting NOW. In a repo already
used, active-context.json still carries the PREVIOUS run's `task_ref` and `base_path`;
every op before Phase 0 repoints it (the Stop-hook gate resolving `loop-state.json`,
`trace-op.sh` naming the trace file) is otherwise attributed to the stale run — worst
case a terminal prior loop whose gate no-ops, silently disabling enforcement for the
new run. This verb makes the repoint the run's first mutation instead of a manual chore
an orchestrator can forget. It read-merge-writes in place: it sets `task_ref`,
`base_path` (when given), and `mode` (the mode's own value — see MODES), PRESERVING
every OTHER field (fairmind, project, …); it bootstraps the minimal shape when the
file is absent, and is idempotent for a given mode.

First-run detection mirrors the command contract: the map prints iff
`.fairmind/active-context.json` is absent (the command has not been bootstrapped in
this repo yet). Stdlib only.
"""

import argparse
import json
import os
import sys
import tempfile

BANNERS = {}

BANNERS["loop"] = """\
▶  THE FAIRMIND LOOP IS STARTING                 /fairmind-loop · fairmind-coding

   What it does   Turns this task into a machine-checkable stop condition, then
                  drives implement → verify → iterate on its own: an executed gate
                  re-runs the checks at every turn end and refuses to stop until
                  they pass.
   Your control   You confirm the budget before the loop is armed, and you approve
                  the result at the end. No auto-merge, no auto-deploy.
   Coming up      contract → check authoring (RED-first, maker ≠ checker) →
                  admission → budget (your confirmation) → armed loop → final report.
   Mode           Standalone unless a Fairmind workspace is connected.
                  Related: /fairmind-add-check · /fix-issue · /sonarqube-fix · /report"""

BANNERS["develop"] = """\
▶  FAIRMIND DEVELOP IS STARTING               /fairmind-develop · fairmind-coding

   What it does   Pulls the story or task from Fairmind — every task under a story,
                  not just the first page — finds the implementation roadmap in the
                  project documents, and drives the team task by task:
                  implement → test → review.
   Your control   You confirm the order and the budget before any code is written,
                  and the run stops between tasks for you.
   How it ends    You approve. There is no executed gate here — /fairmind-loop is
                  the gated twin, for one task with machine-checkable criteria.
   Mode           Connected mode only: a Fairmind workspace must be configured.
                  Related: /fairmind-loop · /loop-import · /report"""

# The stacked-loop map — where this command sits in Fairmind's model. Shown once
# per repo (first run), so the user sees loop 2 (the command they ran) nested in the
# outer loops that live on the Fairmind platform. The tier-2 marker line is rendered
# per mode from ONE template (`_TIER2`), padded back to the box's fixed width: the
# art stays byte-aligned without a second copy of the whole map to drift against.
_TIER2 = "│ │ │ ◀ you are here: {cmd}{pad}exit: {exit} │ │ │"
_TIER2_WIDTH = 88

MAP = """\
0 · FOUNDATION   Evidence Collection: code · logs · DB · UI  →  Project Context
                 runs once, up front — feeds every loop below

┌─ 4 · OPTIMIZE ── Conductor · Optimize ───────────────────────────────────────────────┐
│                                         exit: agent-ready codebase · every N sprints │
│ ┌─ 3 · SPRINT ── Agile Studio → Working Session ───────────────────────────────────┐ │
│ │                                            exit: Working Session closed · ≈ days │ │
│ │ ┌─ 2 · TASK ── Claude Code + verifier agents ──────────────────────────────────┐ │ │
{tier2}
│ │ │ ┌─ 1 · AGENT TURN ── the harness ──────────────────────────────────────────┐ │ │ │
│ │ │ │ ◀ hooks · skills · subagents             exit: turn complete · ≈ minutes │ │ │ │
│ │ │ │ [implement] → [hooks + skills guide the turn] → [second opinion] → close │ │ │ │
│ │ │ └──────────────────────────────────────────────────────────────────────────┘ │ │ │
│ │ └──────────────────────────────────────────────────────────────────────────────┘ │ │
│ └──────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘

● design-time on FairMind: 0 · 3 · 4            ● runtime in Claude Code: 1 · 2"""


# Per-mode facts the opening is rendered from. `context_mode` is the value --repoint
# stamps into active-context.json's `mode`: each command asserts its OWN mode, because
# a repoint fires exactly when that command's run is starting, so the mode it wants is
# always the correct one to write. The alternative — preserving whatever was there —
# leaves the field stale across a mode switch in the same repo, and both directions of
# staleness are wrong: a leftover `"loop"` under a finished loop DISABLES trace capture
# and journal enforcement for a develop run whose loop-state is absent (the PCF-11/PCF-16
# liveness gate reads it as "not live" — see `_loop_ledger.resolve_loop_context` and
# `check-journal.sh`), and a leftover `"interactive"` under a starting loop mislabels
# that loop's trace/token rows and forfeits window-anchored rotation. Forcing removes
# both, and it deletes the "preserve" special case entirely.
MODES = {
    "loop": {
        "cmd": "/fairmind-loop",
        "exit": "both checks pass · ≈ hours",
        "context_mode": "loop",
    },
    "develop": {
        "cmd": "/fairmind-develop",
        "exit": "you approve · ≈ hours",
        "context_mode": "interactive",
    },
}


def is_first_run(cwd):
    """First run per repo: no workspace bootstrapped yet."""
    return not os.path.isfile(os.path.join(cwd, ".fairmind", "active-context.json"))


def render_map(mode="loop"):
    """The stacked-loop map with its tier-2 line naming the command that was run."""
    spec = MODES[mode]
    unpadded = _TIER2.format(cmd=spec["cmd"], pad="", exit=spec["exit"])
    pad = " " * max(1, _TIER2_WIDTH - len(unpadded))
    return MAP.replace("{tier2}", _TIER2.format(cmd=spec["cmd"], pad=pad, exit=spec["exit"]))


def render_opening(cwd, mode="loop"):
    blocks = [BANNERS[mode]]
    if is_first_run(cwd):
        blocks.append(render_map(mode))
    return "\n\n".join(blocks)


def _context_path(cwd):
    return os.path.join(cwd, ".fairmind", "active-context.json")


def _atomic_write_json(path, data):
    """Write `data` as JSON to `path` atomically: serialize to a temp file in the
    SAME directory, then os.replace it over the target. A crash mid-write leaves
    either the old file or the new one — never a half-written context the gate
    would then mis-resolve. The temp file is unlinked on any serialization error
    so a failed repoint leaves no stray .fairmind/ debris."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".active-context.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def repoint(cwd, task_ref, base_path=None, mode="loop"):
    """Point `.fairmind/active-context.json` at the loop starting now (F23/F40).

    Read-merge-write, atomically: set `task_ref` (required) and, when given,
    `base_path`, PRESERVING every other field already in the file. When the file
    is absent, bootstrap the minimal shape the command uses — which needs
    `base_path` to be supplied, since the Stop-hook gate resolves `loop-state.json`
    through it. Idempotent: re-running with the same args rewrites the same bytes.
    Prints a one-line old→new confirmation."""
    if not task_ref:
        raise SystemExit("loop_open.py --repoint requires --task-ref")
    # An empty --base-path is rejected on EVERY path (not just bootstrap): writing
    # base_path="" would make the Stop-hook gate resolve loop-state.json through an
    # empty path — a silent no-op, the exact F40 failure this verb exists to prevent.
    if base_path is not None and not base_path.strip():
        raise SystemExit(
            "loop_open.py --repoint: --base-path must not be empty (the gate resolves "
            "loop-state.json through it)")
    ctx_dir = os.path.join(cwd, ".fairmind")
    ctx_path = _context_path(cwd)
    if os.path.isfile(ctx_path):
        with open(ctx_path, encoding="utf-8") as fh:
            try:
                ctx = json.load(fh)
            except ValueError as exc:
                # Malformed / truncated / git-merge-marked (<<<<<<<) context — exactly
                # the "repo already used for a loop" case --repoint targets. Fail with a
                # clean message, never a raw traceback; json.load raises before any write,
                # so the original file is left byte-intact (never wiped).
                raise SystemExit(
                    f"loop_open.py --repoint: {ctx_path} is not valid JSON ({exc}); "
                    "refusing to overwrite — fix or remove it by hand")
        if not isinstance(ctx, dict):
            raise SystemExit(
                f"loop_open.py --repoint: {ctx_path} is not a JSON object; refusing to overwrite")
        old_ref = ctx.get("task_ref")
    else:
        if not base_path:
            raise SystemExit(
                "loop_open.py --repoint needs --base-path to bootstrap an absent "
                "active-context.json (the gate resolves loop-state.json through it)")
        ctx = {
            "mode": MODES[mode]["context_mode"],
            "fairmind": "none",
            "project": os.path.basename(os.path.abspath(cwd)),
            "base_path": base_path,
            "task_ref": task_ref,
        }
        old_ref = None
    ctx["task_ref"] = task_ref
    if base_path is not None:
        ctx["base_path"] = base_path
    # Each command asserts its own mode (see MODES): a repoint means this command's run
    # is starting, so the field is always safe — and required — to (re)write.
    ctx["mode"] = MODES[mode]["context_mode"]
    os.makedirs(ctx_dir, exist_ok=True)
    _atomic_write_json(ctx_path, ctx)
    old_label = old_ref if old_ref is not None else "(none)"
    print(f"repointed .fairmind/active-context.json: task_ref {old_label} -> {task_ref}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="The deterministic /fairmind-loop opener, plus --repoint.")
    parser.add_argument("--cwd", default=None, help="Repo root (defaults to process cwd).")
    parser.add_argument("--repoint", action="store_true",
                        help="Point active-context.json at the loop starting now, atomically (F23/F40).")
    parser.add_argument("--task-ref", default=None,
                        help="With --repoint: the task/story ref for the loop starting now (required).")
    parser.add_argument("--base-path", default=None,
                        help="With --repoint: the workspace folder to scope this run to — in loop "
                             "mode the folder holding loop-state.json; in develop mode the .fairmind "
                             "workspace root (narrowed once the session slugs are known). Required "
                             "only when bootstrapping an absent active-context.json.")
    parser.add_argument("--mode", choices=sorted(MODES), default="loop",
                        help="Which command is opening: loop (default) or develop. Selects the "
                             "banner and the map's tier-2 marker; with --repoint, develop also "
                             "stamps mode=interactive so a stale loop context cannot mute the hooks.")
    args = parser.parse_args(argv)
    cwd = args.cwd or os.getcwd()
    if args.repoint:
        return repoint(cwd, args.task_ref, args.base_path, args.mode)
    print(render_opening(cwd, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
