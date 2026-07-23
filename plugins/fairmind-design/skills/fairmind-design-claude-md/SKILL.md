---
name: fairmind-design-claude-md
description: Reference template and guidance for writing a project CLAUDE.md that integrates the fairmind-design plugin. Use when bootstrapping a new project, when refreshing CLAUDE.md after the design system evolves, or when the compliance checker keeps flagging undocumented rules. Ships a copy-paste template under templates/CLAUDE.md.example.
---

# Fairmind Design CLAUDE.md

Guidance for writing a `CLAUDE.md` that the `fairmind-design` plugin can enforce.

## Why CLAUDE.md matters here

The `fairmind-claude-md-compliance-checker` agent reads `CLAUDE.md` files (root and sub-folder) and verifies that every declared rule was followed. Rules without a CLAUDE.md entry cannot be enforced. Rules that are too vague (`"be careful"`) cannot be checked. CLAUDE.md must be specific.

## Sections to include

A CLAUDE.md that integrates well with this plugin should include:

1. **Available Agents** — list the agents that exist in the workspace (this plugin + others) so Claude Code knows which to invoke when.
2. **Quality Gates** — which agents are mandatory for which kinds of changes.
3. **Cannot Proceed If** — hard blocks: the conditions that must be cleared before declaring a task complete.
4. **If An Agent Fails** — the recovery procedure.
5. **Design system pointers** — paths to `styles/primitives.css`, `styles/semantic.css`, `DESIGN.md`.
6. **Anti-patterns** — explicit list (hardcoded hex, Tailwind arbitrary values, `<div onClick>`, etc.) so they can be grepped.
7. **Build/lint/test commands** — exact commands the compliance checker can confirm passed.

## Template

A ready-to-use template lives at `templates/CLAUDE.md.example` next to this `SKILL.md`. Copy it to the project root, fill in the placeholders, and add sub-folder CLAUDE.md files for areas with specific rules (e.g., `src/components/CLAUDE.md`).

## How sub-folder CLAUDE.md files compose

The compliance checker walks from each modified file up to the project root and loads every CLAUDE.md it finds. Rules in a sub-folder CLAUDE.md apply only to files inside that folder. Conflicts are resolved in favor of the deepest CLAUDE.md (closest to the file).

A typical layout:

```
project/
├── CLAUDE.md                    # global rules
├── DESIGN.md                    # design system source of truth
├── styles/
│   ├── CLAUDE.md                # rules specific to token files
│   ├── primitives.css
│   └── semantic.css
└── src/
    └── components/
        ├── CLAUDE.md            # rules specific to components
        └── Button/
            └── Button.tsx
```

## Common mistakes

- Listing rules that have no enforcement (no agent, no command, no greppable pattern). Either add a check or delete the rule.
- Mentioning agents that are not actually installed.
- Rules in prose rather than checklists. The checker works with bullets and tables.
- Overlapping rules between root and sub-folder CLAUDE.md. Pick one location.
- No build/lint/test commands. The checker cannot verify "tests passed" if the command is not declared.
