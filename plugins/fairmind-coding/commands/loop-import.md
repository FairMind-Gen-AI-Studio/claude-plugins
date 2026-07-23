---
description: Turn an external ticket into a compiled loop-mode contract - detect the input form, classify acceptance criteria with task-compilation, compile+emit via loop_import.py, present the gap report, then hand off to /fairmind-loop to arm
allowed-tools: Bash(python3 "$CLAUDE_PLUGIN_ROOT"/scripts/loop_import.py:*), Bash(python3 "$CLAUDE_PLUGIN_ROOT"/scripts/run_gate_checks.py:*), Bash(python3 "$CLAUDE_PLUGIN_ROOT"/scripts/admit_check.py:*), Bash(gh issue view:*), Read, Write, Edit, Grep, Glob, Task
---

# loop-import

`/loop-import` is F3's **daily verb**: turn an external ticket — a gh issue, a pasted ticket, or (later) an MCP source, same `TaskDraft` shape either way — into a compiled loop-mode contract, ready for `/fairmind-loop` to pick up. Run it whenever a new ticket needs to become loop-ready.

The split is the same one `task-compilation` documents: `scripts/loop_import.py` is the **deterministic** half — an adapter, a validator, a compiler — and never classifies anything itself. The **judgment** half — turning prose acceptance criteria into a classification map — is this command's job, following the `task-compilation` skill. `/loop-import` is the pipeline that wires the two together against a real ticket and puts the result in front of the human before anything gets armed.

## Usage

```bash
/loop-import 13906                 # gh issue number (a full issue URL also works)
/loop-import path/to/ticket.txt    # pasted/raw ticket text
/loop-import                       # no argument: ask the user to paste a ticket or name an issue
```

## Resolving the script path

`$CLAUDE_PLUGIN_ROOT` is **empty in your (orchestrator) shell** — Claude Code substitutes it in the `allowed-tools` frontmatter above and sets it inside the plugin's own hooks, but it is not exported to the Bash calls you issue from this command body, so a literal `python3 "$CLAUDE_PLUGIN_ROOT"/scripts/…` runs as `/scripts/…` and errors. Resolve the install path once and substitute it for `$CLAUDE_PLUGIN_ROOT` in every call below (`loop_import.py`, `run_gate_checks.py`, `admit_check.py`):

```bash
python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['fairmind-coding@fairmind-plugins'][0]['installPath'])"
```

That absolute path is exactly what Claude Code expands `$CLAUDE_PLUGIN_ROOT` to in `allowed-tools`, so using it keeps every call inside the granted permissions. (When self-hosting inside the plugin repo itself, the repo copy `plugins/fairmind-coding/scripts/…` also works.)

## What it does

Loads the **`task-compilation`** skill (the judgment half) and, at the arm handoff, the same **`fairmind-gate`** skill `/fairmind-loop` uses for check authoring.

1. **Detect the input form, route to an adapter.**
   - A gh issue number/URL → fetch, then normalize:
     ```bash
     gh issue view <n> --json number,url,title,body,labels,assignees,author,state,createdAt,updatedAt,milestone > issue.json
     python3 "$CLAUDE_PLUGIN_ROOT"/scripts/loop_import.py --adapter gh --input issue.json > draft.json
     ```
   - Pasted ticket text (no external identifier to reuse):
     ```bash
     python3 "$CLAUDE_PLUGIN_ROOT"/scripts/loop_import.py --adapter pasted --input ticket.txt > draft.json
     ```
   - MCP-sourced tickets (ClickUp, Linear, …) plug in the same way once their adapters ship — same `TaskDraft` shape, a different `source.kind`. Not implemented yet; do not fabricate an adapter for one.
   - Confirm the draft is well-formed before proceeding:
     ```bash
     python3 "$CLAUDE_PLUGIN_ROOT"/scripts/loop_import.py --validate-draft --input draft.json
     ```

2. **Classify the acceptance criteria** — the judgment half, per the `task-compilation` skill: if `acceptance_criteria` is empty, extract every distinct testable claim from `title`/`body`; then classify each into `checked:<type>` / `evidence` / `unverifiable`, writing a classification map (`classification.json`) per `skills/task-compilation/references/gap-report.md`. **Ambiguity is resolved by interview, never by silent inference** — when a criterion could plausibly go more than one way, stop and put a bounded decision brief (2–4 concrete options + a recommendation) to the user rather than guessing a type or a threshold.

3. **Compile + emit.** Once the map looks right:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT"/scripts/loop_import.py --emit \
     --draft draft.json --classification classification.json \
     --task-ref <ref> --state "${FAIRMIND_BASE}/loop-state.json" \
     --contracts-dir .fairmind/contracts
   ```
   This writes `loop-state.json` (`status: "specified"`) + the reusable `.fairmind/contracts/<ref>.json` contract copy + a persisted `.fairmind/contracts/<ref>.gap.json` gap report, and runs admission on every emitted check — quarantining any weak descriptor before the loop ever arms. `--emit` exits 0 on mechanical success regardless of admission verdicts; a partial quarantine is a normal outcome, surfaced in the next step, not a failure of this command.

   `${FAIRMIND_BASE}` needs a bootstrapped workspace to resolve into — if `.fairmind/active-context.json` doesn't exist yet, dispatch the **Technical Lead / Architect** (`Task`) to bootstrap it first, the same repoint step `/fairmind-loop` Phase 0 runs as its own first mutation.

4. **Present the gap report as a first-class artifact — this is the coaching moment, not an error dump.** Read the persisted `.fairmind/contracts/<ref>.gap.json` and show the human:
   - the **coverage** number **alongside `counts`** — a low coverage from a high `evidence` count is fine (deliberately human-judged criteria); a low coverage from a high `unverifiable` count means more classification work is still needed;
   - a readable **per-criterion breakdown**: each criterion's `id`, its disposition (`checked:<type>` / `checked:evidence` / `unverifiable`), and for every `unverifiable` entry its suggested `rewrite`;
   - any descriptor names in the emitted `loop-state.json`'s `quarantine[]`, with its reason.

   Never skip or shortcut this step to reach arming faster — it is the reason `/loop-import` exists as its own command instead of folding straight into `/fairmind-loop`.

5. **Only after the gap report has been shown, offer to continue toward arming.** `/loop-import` never arms a loop itself. Hand off to `/fairmind-loop <task-ref>` — its Phase 0 already knows to reuse a pre-compiled contract (`checks[]` + `contract.criteria[]` already populated) instead of classifying from scratch, and its Phase 0b still runs the RED-first checker-side authoring pass on any check not yet admitted, budget confirmation, and the arm-time smoke before `run_gate_checks.py --arm`. You may run the read-only sanity check first to set expectations:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT"/scripts/run_gate_checks.py --validate-contract
   ```
   **This will very likely fail right after `--emit`, and that is expected** — a freshly-compiled contract's checks have only cleared `admit_check.py`'s own admission pass, not the RED-first checker-side authoring `/fairmind-loop` Phase 0 still runs on the checker side. Do not promise a contract that just came out of `--emit` arms immediately; report what `--validate-contract` actually says and point the user at `/fairmind-loop` to finish the job.

## Guarantee

A ticket that comes through `/loop-import` never reaches `/fairmind-loop --arm` un-triaged: every acceptance criterion has a recorded disposition (none silently dropped, none silently invented — enforced mechanically by `loop_import.py`'s id-set cross-check), every `unverifiable` one carries a concrete rewrite, and the gap report — not a vibes-based "looks loop-ready" — is what the human reviews before a single check gets authored.
