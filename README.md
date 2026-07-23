# claude-plugins

Claude Code plugin marketplace by [Fairmind](https://fairmind.ai). Two plugins ship from this repository, each scoped to a different stage of the product lifecycle: **design and accessibility** for the UI, **coding workflow** for the engineering team.

| Plugin | Scope | Install |
|---|---|---|
| [`fairmind-design`](plugins/fairmind-design) | Verify React components against Figma, audit design tokens, run WCAG 2.1 AA accessibility audits, generate Code Connect mappings | `/plugin install fairmind-design@fairmind-plugins` |
| [`fairmind-coding`](plugins/fairmind-coding) | Six role-based agents (tech lead, engineer, QA, code review, debug, security), eleven skills, eight hooks, seventeen commands, plus an opt-in loop mode with an executed gate. Runs standalone; the Fairmind MCP and a `.fairmind/` session workspace enable connected mode | `/plugin install fairmind-coding@fairmind-plugins` |

The two plugins are independent. Install one, the other, or both.

---

## Install

### 1. Add the marketplace

```text
/plugin marketplace add FairMind-Gen-AI-Studio/claude-plugins
```

This pulls the marketplace metadata from `.claude-plugin/marketplace.json` so Claude Code knows which plugins are available. The marketplace registers itself as `fairmind-plugins` — that is the name the install commands below refer to.

### 2. Install the plugins you want

```text
/plugin install fairmind-design@fairmind-plugins
/plugin install fairmind-coding@fairmind-plugins
```

The `@fairmind-plugins` suffix disambiguates if you have multiple marketplaces installed.

### 3. Verify

```text
/agents     # plugin agents listed (10 fairmind-* design agents, 6 fairmind-coding agents)
/help       # slash commands listed (/design-verify, /a11y-audit, /fix-issue, /sonarqube-fix, ...)
```

If something is missing, the most common cause is a missing MCP prerequisite — see below.

---

## Plugins

### fairmind-design

Design and accessibility toolkit. Ten subagents, four orchestration commands, four reference skills.

**What it does**
- Verifies React components match their Figma source, value-by-value and state-by-state
- Enforces semantic design tokens, flags hardcoded colors / spacing / typography
- Generates and maintains Figma Code Connect mappings (`*.figma.tsx`)
- Runs full WCAG 2.1 AA audits — contrast, keyboard, screen reader, ARIA — via Playwright + axe-core
- Reviews component composition (design-system reuse, prop typing, semantics)
- Closes design tasks with a mandatory compliance check against the host project's own rules

**Headline commands** — `/design-verify`, `/a11y-audit`, `/code-connect`, `/design-task-close`.

**MCP prerequisites** — Figma (`mcp__claude_ai_Figma__*`), Playwright (`mcp__plugin_playwright_playwright__*`). This plugin is MCP-bound: without them its agents fail at the first MCP call.

Full reference: [plugins/fairmind-design/README.md](plugins/fairmind-design/README.md).

### fairmind-coding

Coding workflow plugin. Six role-based agents, eleven skills, eight hooks, seventeen commands.

**What it does**
- The **Technical Lead / Architect** bootstraps a `.fairmind/<project>/<session>/` workspace and returns the ordered plan the command dispatches — never implements
- The **Software Engineer**, **QA Engineer**, **Code Reviewer**, **Security Engineer**, and **Debugging Specialist** implement and validate against the plan, each with its own journal
- Hooks key off `.fairmind/active-context.json` to enforce scoped writes, refuse turn-end when a key agent skipped its journal, run the loop-mode gate, and record tool-call traces and sub-agent token usage
- **Loop mode** (`/fairmind-loop`) turns acceptance criteria into a machine-checkable stop condition and lets an executed gate drive implement→verify→iterate under a user-confirmed budget, with a final human gate; `/fairmind-add-check` authors custom checks and `/harness-audit` scores how loop-ready a repo is
- The remaining commands cover issue triage (`/fix-issue`, `/fix-frontend-issue`), SonarCloud cleanup (`/sonarqube-fix`), Kubernetes migration (`/migrate-to-k8s`), reporting and test scaffolding (`/report`, `/make-tests`, `/de-slop`), and the GitHub PR workflow (`/gh-commit`, `/gh-fix-ci`, `/gh-review-pr`, `/gh-address-pr-comments`)

**Prerequisites** — `python3`, `git`, and `bash` are enough: the plugin runs standalone on any repository. The Fairmind (`mcp__Fairmind__*`), Playwright, and MongoDB MCP servers are optional and only enable connected mode; the GitHub commands and the journal hook also want `gh` and `jq` on `$PATH`, and `/sonarqube-fix` needs `SONAR_TOKEN` plus a `sonar-project.properties` file.

Full reference: [plugins/fairmind-coding/README.md](plugins/fairmind-coding/README.md).

---

## What leaves your machine

`fairmind-coding` carries an ambient insight-capture path (session token totals, tool-call counts, skill names — never file contents, paths, or branch names) that reports to a Fairmind backend. It is **gated shut by default**: it activates only in a repository that has a Fairmind MCP server configured *per project*, and it announces itself once with a consent notice before capturing anything. Without that configuration nothing is collected and nothing is transmitted. Everything else — agents, skills, hooks, loop mode — runs entirely locally.

## Requirements

Both plugins assume Claude Code with the `/plugin` command. The MCP servers each plugin needs are listed above; authenticate them once in Claude Code's MCP settings — nothing in this repository configures them for you.

## Layout

```
.claude-plugin/marketplace.json   marketplace registration (lists every plugin)
plugins/
  fairmind-design/                plugin payload (agents/, commands/, skills/, plugin.json, README.md)
  fairmind-coding/                plugin payload (+ hooks/, scripts/)
```

## Updating an installed plugin

After a marketplace update on the remote, refresh locally:

```text
/plugin marketplace update fairmind-plugins
/plugin install fairmind-design@fairmind-plugins      # reinstall to pick up the new version
```

## Uninstall

```text
/plugin uninstall fairmind-design@fairmind-plugins
/plugin uninstall fairmind-coding@fairmind-plugins
/plugin marketplace remove fairmind-plugins
```

## Feedback

This repository is a published snapshot of an internal marketplace: it takes issues, not pull requests. Open an issue with what you ran, what you expected, and what happened.

## License

MIT — see [LICENSE](LICENSE).
