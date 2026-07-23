---
description: Full accessibility audit on a component or rendered URL. Invokes the accessibility orchestrator, which fans out to WCAG, contrast, keyboard, and screen-reader specialists, and aggregates a single prioritized report.
argument-hint: <component-path-or-url> [--standard=AA|AAA]
---

# /a11y-audit

Run a full WCAG 2.1 accessibility audit.

## Arguments

- `$1` — component path or rendered URL
- `$ARGS` may also include `--standard=AA` (default) or `--standard=AAA`

If `$1` is missing, ask the user before proceeding.

## Workflow

Invoke `fairmind-accessibility-orchestrator` via the Task tool with:
- `target = $1`
- `standard = <AA or AAA from $ARGS, default AA>`

The orchestrator handles the four specialist agents internally and returns the aggregated report. Forward that report verbatim.

## Final report

Pass through the orchestrator's report. Append a single overall verdict line: `OVERALL: PASS` or `OVERALL: FAIL`.
