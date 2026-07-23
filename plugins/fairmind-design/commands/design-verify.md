---
description: Full design-to-code verification gate for a component. Runs design verification, token analysis, composition review, and CLAUDE.md compliance in sequence. Stops on first failure unless --continue is passed.
argument-hint: <component-path> <figma-url> [--continue] [--threshold=N]
---

# /design-verify

Run the full design quality gate on a component.

## Arguments

- `$1` — component path (e.g., `src/components/Button.tsx`)
- `$2` — Figma node ID or URL of the source component
- `$ARGS` may also include:
  - `--continue` — keep running all agents even if one fails (default: stop on first fail)
  - `--threshold=N` — drift threshold passed to the token analyzer (default: 5)

If `$1` or `$2` is missing, ask the user before proceeding.

## Workflow

Run the four agents below in order. Between each step, check the previous agent's verdict. If `FAIL` and `--continue` was not passed, stop and report. If `--continue` was passed, record the failure and proceed.

### Step 1 — Design verification

Invoke `fairmind-design-verification` via the Task tool with:
- `component_path = $1`
- `figma_source = $2`

### Step 2 — Token analysis

Invoke `fairmind-token-analyzer` via the Task tool with:
- `target = $1`
- `threshold = <value of --threshold or 5>`

### Step 3 — Composition review

Invoke `fairmind-component-composition-reviewer` via the Task tool with:
- `component_path = $1`

### Step 4 — CLAUDE.md compliance

Invoke `fairmind-claude-md-compliance-checker` via the Task tool with:
- `task_description = "design verification on $1 against $2"`
- `files_modified = [$1]`

## Final report

Print a one-table summary:

| Step | Agent | Verdict |
|---|---|---|
| 1 | fairmind-design-verification | PASS/FAIL |
| 2 | fairmind-token-analyzer | PASS/FAIL |
| 3 | fairmind-component-composition-reviewer | PASS/FAIL |
| 4 | fairmind-claude-md-compliance-checker | READY/BLOCKED |

Then print a single overall verdict line: `OVERALL: PASS` or `OVERALL: FAIL`.
