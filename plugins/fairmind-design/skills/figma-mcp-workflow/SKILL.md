---
name: figma-mcp-workflow
description: How to use the Figma MCP tools for design-to-code work. Use when retrieving design context, screenshots, variables, or Code Connect mappings from a Figma source. Covers URL parsing, when to use each MCP tool, and the Code Connect mapping flow.
---

# Figma MCP Workflow

Reference for using `mcp__claude_ai_Figma__*` in Fairmind design tasks.

## URL parsing

Figma URLs encode `fileKey` and `nodeId`:

| URL pattern | fileKey | nodeId |
|---|---|---|
| `figma.com/design/:fileKey/:fileName?node-id=:nodeId` | `:fileKey` | `:nodeId` (replace `-` with `:`) |
| `figma.com/design/:fileKey/branch/:branchKey/:fileName` | `:branchKey` (overrides fileKey) | from query |
| `figma.com/board/:fileKey/:fileName?node-id=:nodeId` | `:fileKey` (use `get_figjam`) | `:nodeId` |
| `figma.com/make/:makeFileKey/:makeFileName` | `:makeFileKey` | — |

Always convert `node-id=1-23` from the URL to `nodeId=1:23` when calling tools.

## Which tool to call

| Goal | Tool |
|---|---|
| Get the rendered React+Tailwind reference for a node | `get_design_context` |
| See the visual to compare against the implementation | `get_screenshot` |
| Read the resolved variable values (colors, spacing) | `get_variable_defs` |
| Inspect the variant set, props, and metadata of a component | `get_metadata` |
| Find the existing Code Connect mapping for a node | `get_code_connect_map` |
| Get suggestions for a new mapping | `get_code_connect_suggestions` |
| Pull broader context for Code Connect work | `get_context_for_code_connect` |
| Save a new mapping back to Figma | `add_code_connect_map` |

## Design-to-code flow

1. **Parse the URL** to get `fileKey` and `nodeId`.
2. **Call `get_design_context`** first. The response contains:
   - A code reference (React + Tailwind) — treat as a *reference*, not as final code.
   - A screenshot.
   - Hints: Code Connect snippets (use these directly when present), component documentation links, design annotations, design tokens as CSS variables, raw hex colors.
3. **Adapt to the project's stack.** Replace generic Tailwind colors with the project's semantic tokens; substitute the project's DS components for any inline reimplementations.
4. **If you need precise values**, call `get_variable_defs` for tokens and `get_screenshot` for the visual.
5. **If you are auditing**, never trust the rendered snippet alone — compare it against `get_screenshot` and the project's DESIGN.md.

## Code Connect flow

When mapping a Figma component to a code component:

1. **Inspect Figma side.** `get_metadata` to enumerate variant axes (e.g., `size: sm|md|lg`, `state: default|hover|disabled`).
2. **Check for an existing mapping.** `get_code_connect_map` (file scope) reveals what is already declared.
3. **Read the code side.** Match each variant axis to a prop. If a Figma axis has no code counterpart, the component is incomplete in code (or the axis is decorative — confirm with the designer).
4. **Generate the `.figma.tsx` file.** Use `figma.connect(...)` with `props: { ... }` mapping each Figma property to the code prop expression.
5. **Add MCP instructions** for non-obvious behavior (conditional rendering, render-props, slots) so the model can construct correct calls later.
6. **Push** with `add_code_connect_map` after local validation.

## Caveats

- The output of `get_design_context` is enriched with hints but is not authoritative — always confirm tokens against the project's `styles/semantic.css`.
- Absolute positioning in the snippet usually means the design is loosely structured — fall back to the screenshot and rebuild the layout in semantic flow.
- `get_screenshot` is heavy; do not call it on every iteration. Cache the visual reference for a session.
- For FigJam (`figma.com/board/...`), use `get_figjam` and pass the original board URL as `figjamUrl` when available.
