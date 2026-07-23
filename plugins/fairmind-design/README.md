# fairmind-design

Design and accessibility toolkit for Claude Code. Ten subagents, four orchestration commands, four reference skills.

## What's included

### Agents

| Agent | Purpose |
|---|---|
| `fairmind-design-verification` | Compares a component implementation against its Figma source value-by-value, state-by-state. Flags hardcoded values and design-token drift. |
| `fairmind-component-composition-reviewer` | Structural review of composite components. Checks DS reuse, prop typing, HTML semantics, CLAUDE.md compliance. |
| `fairmind-figma-code-connect-generator` | Generates and maintains `.figma.tsx` Code Connect mappings. Detects orphaned variants and dangling props. |
| `fairmind-token-analyzer` | Token coverage audit. Finds hardcoded values, suggests the closest semantic token, reports drift. |
| `fairmind-accessibility-orchestrator` | Coordinates the four a11y specialists below and aggregates a single prioritized report. |
| `fairmind-wcag-compliance-auditor` | WCAG 2.1 AA (or AAA) audit using axe-core via Playwright, plus manual-review notes. |
| `fairmind-color-contrast-specialist` | Contrast against WCAG 1.4.3 (text) and 1.4.11 (non-text), including opacity, gradients, blur. |
| `fairmind-keyboard-navigation-tester` | Tab order, focus visibility, modal focus trap, Escape behavior, documented shortcuts. |
| `fairmind-screen-reader-tester` | Accessible names, ARIA roles, live regions, image alt, DOM vs visual order. |
| `fairmind-claude-md-compliance-checker` | Mandatory closing step. Verifies the workflow followed the project CLAUDE.md rules. |

### Commands

| Command | What it does |
|---|---|
| `/design-verify <component-path> <figma-url> [--continue] [--threshold=N]` | Sequential gate: design-verification → token-analyzer → composition-reviewer → CLAUDE.md compliance. Stops on first fail unless `--continue`. |
| `/a11y-audit <component-path-or-url> [--standard=AA\|AAA]` | Full accessibility audit via the orchestrator. |
| `/code-connect <component-path> <figma-url>` | Generates or updates the Code Connect mapping. |
| `/design-task-close [task-description]` | Mandatory closing compliance check. |

### Skills

| Skill | When to use |
|---|---|
| `design-token-conventions` | When authoring or auditing CSS/Tailwind code touching color, spacing, typography, radius, shadow. |
| `wcag-quick-reference` | When interpreting an a11y audit finding or planning a fix. |
| `figma-mcp-workflow` | When pulling design context, screenshots, variables, or Code Connect data from Figma. |
| `fairmind-design-claude-md` | When bootstrapping a project's CLAUDE.md. Ships a copy-paste template. |

## Prerequisites

This plugin depends on two MCP servers:

- **Figma MCP** — needed by `fairmind-design-verification`, `fairmind-figma-code-connect-generator`, `fairmind-color-contrast-specialist`. Install and authenticate the Figma MCP integration so `mcp__claude_ai_Figma__*` tools are available.
- **Playwright MCP** — needed by all four a11y specialist agents. Install the Playwright plugin/extension so `mcp__plugin_playwright_playwright__*` tools are available.

Without these MCP servers, the corresponding agents will fail at the first MCP call.

## Install

From git:

```
/plugin marketplace add FairMind-Gen-AI-Studio/claude-plugins
/plugin install fairmind-design@fairmind-plugins
```

Or from a local checkout:

```
/plugin marketplace add /path/to/claude-plugins
/plugin install fairmind-design@fairmind-plugins
```

Verify:

```
/agents      # 10 fairmind-* agents listed
/help        # /design-verify, /a11y-audit, /code-connect, /design-task-close listed
```

## Examples

### Verify a single component

```
/design-verify src/components/Button.tsx https://www.figma.com/design/abc/My-File?node-id=12-34
```

Stops at the first failing agent. Pass `--continue` to run the full chain regardless.

### Audit accessibility on a live page

```
/a11y-audit http://localhost:3000/dashboard
```

The orchestrator calls the four specialists, deduplicates findings, and ranks by severity.

### Generate Code Connect for a new component

```
/code-connect src/components/Card.tsx https://www.figma.com/design/abc/My-File?node-id=45-67
```

Creates `src/components/Card.figma.tsx` (or shows a diff if one already exists).

### Close a design task

```
/design-task-close
```

Runs the compliance checker and prints `READY TO PROCEED` or `BLOCKED — see steps to unblock`.

## Configuration

This plugin reads project-level configuration from:

- `DESIGN.md` — design system source of truth
- `CLAUDE.md` (root and sub-folders) — invocation rules and quality gates
- `styles/primitives.css`, `styles/semantic.css` — token catalog
- `tailwind.config.js` (if present) — Tailwind utility mapping

Use the `fairmind-design-claude-md` skill's `templates/CLAUDE.md.example` as a starting point.

## License

MIT
