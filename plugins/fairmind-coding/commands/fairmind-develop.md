---
description: Implement a Fairmind user story or task with the full team - the Technical Lead pulls the work and the roadmap, you confirm the order, then engineer/QA/review run task by task under your control
allowed-tools: Task, Bash(python3 "$CLAUDE_PLUGIN_ROOT"/scripts/loop_open.py:*), Read, Write, Edit, Grep, Glob, TodoWrite, mcp__Fairmind__Studio_bulk_update_status, mcp__Fairmind__Studio_process_journal, mcp__Fairmind__Insights_record_agent_decisions
---

# fairmind-develop

Implement a Fairmind **user story or task** with the whole team, human-driven. The Technical Lead pulls the work from Fairmind — every task under a story, plus the implementation roadmap in the project documents — and returns an ordered plan. You confirm it. Then this command drives the team task by task: implement → test → review.

## Usage

```bash
/fairmind-develop US-142        # a user story: every task under it, in roadmap order
/fairmind-develop TASK-871      # a single task
```

The argument is **required**: this command implements the work you name, not whatever `active-context.json` happens to point at.

## The twin, and the difference that matters

`/fairmind-loop` and `/fairmind-develop` share a bootstrap and differ in what ends them:

| | `/fairmind-loop` | `/fairmind-develop` |
|---|---|---|
| Scope | one task | a story (all its tasks) or one task |
| Driver | an **executed gate** re-runs the checks at every turn end | you, between tasks |
| "Done" means | the checks passed | **you approved** |
| Needs | machine-checkable acceptance criteria | a Fairmind workspace |

**There is no executed gate here.** Say so in the closing report: this mode produces a reviewed diff, not a passed stop condition. When a task's acceptance criteria *are* machine-checkable, offer to run that one task through `/fairmind-loop` instead — that is the stronger guarantee, and it is one task wide.

## Connected mode only

This command's whole input — story, tasks, tests, roadmap — comes from Fairmind, so it runs only when the platform is reachable. The test is **whether the `mcp__Fairmind__*` tools are available to you**, not what `active-context.json` says: that file's `fairmind` field is a cached answer, and Phase 0 bootstraps it to `"none"` on a fresh repo before anyone has checked, so trusting the field would fail every first run. Make this the **first substantive step of Phase 0, right after the banner** (the banner is a fixed opener; printing it before a clean "not connected — stopping" message is not misleading, and it keeps the deterministic-opening rule intact). If the tools are absent, stop and point the user at `/fairmind-loop` (which runs standalone) or at plain interactive work — do not improvise a story from the repo. When they are present, the Technical Lead stamps `fairmind: "configured"` as it queries (Phase 1), so the field ends the run agreeing with reality.

## Operational quickstart (gotchas)

- **`$CLAUDE_PLUGIN_ROOT` is empty in your (orchestrator) shell.** Claude Code substitutes it in `allowed-tools` and inside hooks, but not in the Bash calls this command body issues, so a literal `python3 "$CLAUDE_PLUGIN_ROOT"/scripts/…` runs as `/scripts/…` and errors. Resolve the install path once and use that absolute path everywhere below:

  ```bash
  python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['fairmind-coding@fairmind-plugins'][0]['installPath'])"
  ```

- **The Technical Lead cannot dispatch the team — you can.** A sub-agent has no `Task` tool (the harness withholds it regardless of the agent's own frontmatter), so every dispatch in this command is made by **you**, the orchestrator, one level deep. The Technical Lead plans and returns; it never engages another agent. This is the same topology `/fairmind-loop` uses.

- **Repoint before any `.fairmind/` write.** `validate-fairmind-path.sh` scopes writes to `active-context.json`'s `base_path`, and a context left behind by a previous run points somewhere else. Worse, a stale `"mode": "loop"` whose loop is finished **mutes the journal hook and the trace capture** for this whole run (the liveness gate reads a dead loop as "not live"). `loop_open.py --repoint --mode develop` fixes both in one atomic write — it stamps `mode: "interactive"`. Run it as the run's first mutation.

- **A story's tasks are paginated.** `Studio_list_tasks_by_session` returns 20 at a time. "All the tasks" means paging until `hasMore` is false — a first-page fetch silently drops the rest of the story.

## Phase 0 — Opening

**Always the first user-visible output**, before any preparation reading. Run the opener and show its output as-is — do not rewrite, summarise, or repeat it:

```bash
python3 <install-path>/scripts/loop_open.py --mode develop
```

**Then check connected mode** (see above): if the `mcp__Fairmind__*` tools are not available, stop here with a clear "not connected" message and the pointer to `/fairmind-loop`. Only continue when they are.

Then repoint the context at the work starting now (`<ref>` is the command's argument):

```bash
python3 <install-path>/scripts/loop_open.py --repoint --mode develop --task-ref <ref> --base-path .fairmind
```

`.fairmind` is deliberately wide at this point: the real `base_path` is `.fairmind/<project-slug>/<session-slug>`, and the Technical Lead only resolves those slugs after it queries Fairmind. Narrow it in Phase 2, once you know them.

Everything after this is silent preparation until the plan lands: no play-by-play of which file or tool you are reading.

## Phase 1 — Plan (Technical Lead, one dispatch)

Dispatch **one** `Technical Lead / Architect` sub-agent. It bootstraps the workspace, pulls the work, finds the roadmap, writes the work packages, and **returns**. Its brief must be explicit that it plans and does not dispatch:

> This is an **interactive develop** run — never enter loop mode, never set `mode: "loop"`, and skip the loop contract/budget phase. Resolve `<ref>` and prepare the work, then STOP and report — do not engage any other agent (you have no `Task` tool; the orchestrator dispatches the team).
> 1. Phase 0 bootstrap: project/session slugs, the `.fairmind/<project>/<session>/` tree, `context.json`. **Merge** `.fairmind/active-context.json` — set `base_path` and the identity fields, and set `fairmind: "configured"` (you reached the platform), but keep the `mode: "interactive"` the command already wrote; do not overwrite it.
> 2. Resolve the ref. A **task** → that task. A **user story** → the story and *every* task under it, paging until `hasMore` is false.
> 3. Find the implementation roadmap in the project documents and persist it under `execution_plans/`. Derive the task order from it. No roadmap → `.no-roadmap` marker, order by dependencies then priority.
> 4. Persist the tests for every story involved under `requirements/tests/` (the QA Engineer's single source of truth).
> 5. Write one work package per task and role, and the conductor summaries.
> Report back: the ordered task list with the reason for the order, the roadmap document you used (or that none exists), the work-package paths, and every gap or risk you found.

## Phase 2 — Confirm the plan (the one mandatory human touchpoint)

Narrow the scope to the real workspace now that the slugs exist:

```bash
python3 <install-path>/scripts/loop_open.py --repoint --mode develop --task-ref <ref> --base-path .fairmind/<project-slug>/<session-slug>
```

Then present, in this order, and **wait**:

1. **The ordered task list** — one line per task, with what it delivers.
2. **Where the order comes from** — the roadmap document by name, or "no roadmap found: ordered by dependencies, then priority". Two distinct questions, in order: first, *is this the right document?* — a retrieved doc is a candidate, so have the user confirm it is the roadmap they mean. Then, once confirmed, its order is the **default**: if it conflicts with a task's own steps, the roadmap wins — say so out loud. The user may still override the order explicitly; what you must never do is silently pick the roadmap over an order the user stated, or silently pick your own over the roadmap.
3. **The dispatch budget** — a **floor**, not a cap: `1 + tasks × 3` (one Technical Lead plan already spent, plus engineer + QA + review for each task; the closing report is your own message, not a dispatch). On top of that, budget for what the run may still need and cannot pre-count: **up to two repair dispatches** per failing task, and a **Security Engineer** for each task that warrants one. Present the floor and these add-ons together so the confirmed number is not one a single failing task blows through. The run **stops between tasks** for your go-ahead; offer the alternative (run through, report at the end) rather than assuming it.
4. **Gaps** — a task with no acceptance criteria, a story with no tests, a missing blueprint. These do not block, but they are the things that will make the review inconclusive.

The user confirms or adjusts the order, the budget, and the stop-between-tasks default before a single line of code is written.

## Phase 3 — Implement, task by task

For each task, in the confirmed order:

1. **`Software Engineer`** — brief it with its work-package path, the skill(s) to load (the tech skill named in the package, plus `fairmind-tdd`), and the acceptance criteria. It writes its own journal; the `SubagentStop` hook refuses its completion otherwise.
2. **`QA Engineer`** — brief it with the test files under `${FAIRMIND_BASE}/requirements/tests/`. It reads them from disk; it does not re-query Fairmind.
3. **`Code Reviewer`** — the diff for this task against its acceptance criteria.
4. **`Security Engineer`** — **only when the task touches** authentication, authorization, secrets, cryptography, payments, personal data, or an externally-reachable input. Say why you did or did not engage it; do not run it by reflex.
5. **Fixes** — send failures back to the `Software Engineer` with a targeted fix package, at most **twice**. Still failing after the second round means the problem is not a coding slip: stop the task, summarise what fails and the two attempts, and hand it to the user. Do not keep re-dispatching.
6. **Stop** and report the task's outcome before starting the next one, unless the user chose run-through.

Track progress with `TodoWrite` so the user can see where the run is without asking.

## Exit

1. **Report** — per task: delivered / failed / handed back, the review verdict, the journals. Then the run totals against the budget. State plainly that this was a human-reviewed run, not a gated one.
2. **The final approval gate — this is what "done" means here.** There is no executed gate, so *your approval of the completed work* is the entire stop condition. Present the report and **wait for the user to approve it**, even in run-through mode — run-through skips the between-task check-ins, not this. Do not treat the plan approval in Phase 2, or a passing review, as this approval: those are about *what to do* and *whether a step succeeded*; this is the human signing off on the result. Nothing below happens until the user approves; if they reject, the rejected tasks go back to Phase 3, not forward.
3. **Write back to Fairmind — a second, separate ask.** Only after the approval above, and asked for on its own: advance the task status (`Studio_bulk_update_status`) and attach the journals (`Studio_process_journal`). This mutates the platform outside this repo — never automatic, never folded into the approval in step 2.
4. **Offer the stronger guarantee** — for any task whose acceptance criteria are machine-checkable, offer `/fairmind-loop <task-ref>`: the same work, driven by an executed gate instead of by a review.
