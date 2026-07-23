---
description: Mandatory closing step for any design task. Runs the CLAUDE.md compliance checker and prints READY/BLOCKED. Run this before declaring a design task complete.
argument-hint: [task-description]
---

# /design-task-close

Close a design task with the mandatory compliance check.

## Arguments

- `$ARGS` (optional) — short description of the task that was performed. If not provided, infer from the recent conversation context.

## Workflow

1. Determine `task_description`. Use `$ARGS` if provided. Otherwise summarize the work done in the recent conversation in one or two sentences.
2. Determine `files_modified`. Inspect the recent conversation for files that were edited or created. If unclear, ask the user.
3. Invoke `fairmind-claude-md-compliance-checker` via the Task tool with:
   - `task_description = <inferred or provided>`
   - `files_modified = <inferred list>`

## Final report

Forward the checker's report. End with a single line:

- `READY TO PROCEED` — task may be marked complete.
- `BLOCKED — see steps to unblock` — task is not complete; the steps to unblock from the checker's report must be performed first.
