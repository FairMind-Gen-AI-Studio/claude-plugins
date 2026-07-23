# CLAUDE.md — fairmind-design plugin

Instructions for Claude Code when this plugin is active in a project. These rules govern when and how to invoke the plugin's agents.

## When to invoke which agent

| Situation | Invoke |
|---|---|
| New or modified component file | `fairmind-design-verification` (with Figma source) |
| Component file referencing color, spacing, typography literals | `fairmind-token-analyzer` |
| Component composing other DS components | `fairmind-component-composition-reviewer` |
| Component variants changed in Figma | `fairmind-figma-code-connect-generator` |
| Page-level a11y audit | `fairmind-accessibility-orchestrator` |
| Closing any design task | `fairmind-claude-md-compliance-checker` (mandatory) |

If the user asks for a "full check" of a component, prefer `/design-verify`. If the user asks about accessibility, prefer `/a11y-audit`.

## Quality gates

For any change to a file under `src/components/` or `styles/`:

1. Run `fairmind-design-verification` if a Figma source is known.
2. Run `fairmind-token-analyzer` on the modified file.
3. If the component is composite, run `fairmind-component-composition-reviewer`.
4. If the component is interactive or visible to end users, run the accessibility orchestrator.
5. **Always** close with `fairmind-claude-md-compliance-checker` (or `/design-task-close`).

## Cannot proceed if

- A design quality agent returned `FAIL` and was not re-run after a fix
- `fairmind-claude-md-compliance-checker` returned `BLOCKED`

## Agent failure recovery

When an agent fails:

1. Read its report.
2. Apply fixes in the affected files.
3. Re-invoke the same agent on the fixed files.
4. Continue only when the verdict is `PASS`.

Never declare a task complete with an unresolved `FAIL`.

## MCP prerequisites

Confirm at session start that the required MCP servers are reachable:

- Figma: `mcp__claude_ai_Figma__*`
- Playwright: `mcp__plugin_playwright_playwright__*`

If a tool call fails with "tool not found", the corresponding MCP server is not installed; advise the user to install it before retrying the agent.

## Output format

Agents emit markdown reports with explicit `PASS`/`FAIL` (or `READY TO PROCEED`/`BLOCKED`) verdicts. When forwarding an agent's report to the user, preserve the verdict line verbatim — downstream commands depend on parsing it.
