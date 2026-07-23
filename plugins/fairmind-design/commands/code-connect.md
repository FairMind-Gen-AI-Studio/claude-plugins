---
description: Generate or update the Figma Code Connect mapping (.figma.tsx) for a component. Detects orphaned variants and dangling props in existing mappings.
argument-hint: <component-path> <figma-url>
---

# /code-connect

Generate or update Code Connect for a component.

## Arguments

- `$1` — component path (e.g., `src/components/Button.tsx`)
- `$2` — Figma node ID or URL of the source component

If either is missing, ask the user before proceeding.

## Workflow

Invoke `fairmind-figma-code-connect-generator` via the Task tool with:
- `component_path = $1`
- `figma_source = $2`

The agent will either create `<Component>.figma.tsx` next to the component or produce a diff against the existing one.

## Final report

Forward the agent's output. If the agent reported orphaned variants or dangling props, surface them with a `WARNING:` prefix at the top of the report.
