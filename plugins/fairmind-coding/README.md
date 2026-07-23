# fairmind-coding

Coding workflow plugin for Claude Code. Six role-based agents, eleven skills, eight hooks, seventeen commands — among them a human-driven team mode (`/fairmind-develop`) and an opt-in **loop mode** (`/fairmind-loop`) with a machine-checkable stop condition. **Runs standalone on any repo, zero-config**; the Fairmind MCP and the `.fairmind/` session workspace produced by Fairmind AI Studio are optional and only enable connected mode.

## What's included

### Agents

| Agent | Role |
|---|---|
| `Technical Lead / Architect` | Bootstraps `.fairmind/<project>/<session>/`, pulls the work and the roadmap, writes the work packages, and returns the ordered plan for the command to dispatch — a sub-agent has no `Task` tool of its own. **Never implements code.** |
| `Software Engineer` | Versatile implementer (frontend / backend / AI). Loads the matching tech skill per task. |
| `Code Reviewer` | Post-implementation review against plan, journal, and code standards. |
| `QA Engineer` | Test execution and validation. Playwright by default. |
| `Debugging Specialist` | Methodical, hypothesis-driven root-cause investigation. |
| `Security Engineer` | Web security review — OWASP, STRIDE, CVSS-classified findings. |

### Skills

| Skill | When to load |
|---|---|
| `fairmind-context` | Pulling project / session / user-story / requirements / test context from the Fairmind platform |
| `fairmind-tdd` | Implementing features against Fairmind acceptance criteria with journal traceability |
| `fairmind-code-review` | Reviewing implementation work — plan→journal→code traceability |
| `frontend-react-nextjs` | React, NextJS, TypeScript, Tailwind, Shadcn UI, Zustand |
| `backend-nextjs` | NextJS API routes, MongoDB, authentication |
| `backend-python` | FastAPI, Pydantic, async, pytest |
| `backend-langchain` | LangChain, LangGraph, RAG, prompt engineering |
| `qa-playwright` | Playwright test patterns, selectors, visual testing, CI integration |
| `ai-ml-systems` | LLM optimization, agent architecture, evaluation, cost |
| `fairmind-gate` | Designing a loop-mode stop condition — the five check types, RED-first authoring, admission |
| `custom-check-authoring` | Authoring a custom loop check with the admission self-test (verify the verifier) |

### Hooks

| Hook | Event | Purpose |
|---|---|---|
| `validate-fairmind-path` | `PreToolUse` on `Write\|Edit` | Blocks writes to `.fairmind/` paths outside the scope active-context.json's declared `base_path` names, and any path carrying a `..` segment |
| `inject-context` | `PreToolUse` on `Task` | Surfaces `FAIRMIND_BASE`, `project_id`, `session_mindstreamId` to subagents |
| `check-journal` | `SubagentStop` | Refuses a sub-agent's completion if it mutated non-`.fairmind/` code and wrote no journal — enforced for any code-mutating sub-agent, not a fixed role allowlist |
| `loop-check` | `Stop` | Loop-mode gate — runs the checks and blocks the turn until the stop condition holds. Silent no-op unless a loop is active |
| `trace-op` | `PostToolUse` on all tools | Appends the mechanical *what* of each tool call to `.fairmind/trace/<taskRef>.jsonl` (`kind`: mutate/exec/dispatch/read/other). Silent no-op outside a Fairmind workspace |
| `capture-subagent-tokens` | `SubagentStop` | Sums a finished sub-agent's token usage from its transcript and appends one row to `${FAIRMIND_BASE}/subagent-tokens.jsonl`, so the loop dashboard has per-loop token stats. Best-effort and never blocking; silent no-op outside a Fairmind workspace |

### Commands

| Command | What it does |
|---|---|
| `/fairmind-loop [ref]` | Run a task/story in loop mode: the Technical Lead builds a machine-checkable stop condition, then the executed gate drives implement→verify→iterate under budget until it passes and a human approves |
| `/fairmind-develop <US-\|TASK-ref>` | Implement a story (every task under it, in roadmap order) or a single task with the full team, human-driven: the Technical Lead plans, you confirm the order, then engineer → QA → review run task by task. Connected mode only; no executed gate — `/fairmind-loop` is the gated twin |
| `/loop-import [gh-issue\|ticket-file]` | Turn an external ticket into a compiled loop-mode contract (adapter → `task-compilation` classification → `loop_import.py --emit`), present the gap report, then hand off to `/fairmind-loop` to arm |
| `/fairmind-add-check` | Author a custom loop-mode check (open descriptor contract + admission self-test) |
| `/harness-audit [--test-command "<cmd>"]` | Audit the repo against the Loop Readiness criteria catalog (81 criteria / 9 pillars / 5 dimensions) and render a self-contained HTML report under `.fairmind/audit/` |
| `/fix-issue [issue-name] [--type fe-fe\|fe-be\|be-be]` | Classify an issue, confirm with user, dispatch the Software Engineer with the matching skill |
| `/fix-frontend-issue [issue-file]` | Frontend fix loop with Playwright validation, max 5 iterations |
| `/sonarqube-fix` | Pull PR-scoped SonarCloud issues, fix BLOCKER → INFO, run tests, commit |
| `/migrate-to-k8s [app-name]` | 7-phase migration: Kustomize base+overlay, FluxCD image automation, External Secrets, Istio VirtualService, IRSA, gradual traffic shift |
| `/report` | Task report — executive / sprint / standup |
| `/make-tests` | Coverage-driven test scaffolding (pytest by default) |
| `/de-slop` | Strip AI-generated artifacts (NOTES.md, redundant comments, etc.) before PR |
| `/gh-commit` | Conventional commits with branch safety |
| `/gh-fix-ci` | Diagnose and fix CI failures |
| `/gh-review-pr` | Structured PR review |
| `/gh-address-pr-comments` | Walk PR comments and apply fixes |

## How Fairmind stacks the loops

Fairmind's take on loop engineering is a foundation plus four concentric loops, each with its own exit condition and time scale. This plugin operates the two innermost loops (runtime in Claude Code); the outer two and the foundation live on the Fairmind platform (design-time):

```text
0 · FOUNDATION   Evidence Collection: code · logs · DB · UI  →  Project Context
                 runs once, up front — feeds every loop below

┌─ 4 · OPTIMIZE ── Conductor · Optimize ───────────────────────────────────────────────┐
│                                         exit: agent-ready codebase · every N sprints │
│ ┌─ 3 · SPRINT ── Agile Studio → Working Session ───────────────────────────────────┐ │
│ │                                            exit: Working Session closed · ≈ days │ │
│ │ ┌─ 2 · TASK ── Claude Code + verifier agents ──────────────────────────────────┐ │ │
│ │ │ ◀ you are here: /fairmind-loop              exit: both checks pass · ≈ hours │ │ │
│ │ │ ┌─ 1 · AGENT TURN ── the harness ──────────────────────────────────────────┐ │ │ │
│ │ │ │ ◀ hooks · skills · subagents             exit: turn complete · ≈ minutes │ │ │ │
│ │ │ │ [implement] → [hooks + skills guide the turn] → [second opinion] → close │ │ │ │
│ │ │ └──────────────────────────────────────────────────────────────────────────┘ │ │ │
│ │ └──────────────────────────────────────────────────────────────────────────────┘ │ │
│ └──────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘

● design-time on FairMind: 0 · 3 · 4            ● runtime in Claude Code: 1 · 2
```

- **0 · Foundation** — Evidence Collection (code, logs, DB, UI) builds the **Project Context** once, up front: a loop iterating on the wrong context converges on the wrong answer. Connected mode pulls it through the Fairmind MCP (`fairmind-context` skill); standalone mode approximates it from the local repo.
- **1 · Agent turn (≈ minutes)** — the harness. The plugin configures it: hooks fire at key lifecycle points, skills put written conventions in front of the agent on every run, and a subagent gives a second opinion — the writer never approves their own work.
- **2 · Task (≈ hours)** — the loop `/fairmind-loop` drives. Exit requires **two independent checks**, both owned by agents other than the maker:
  - the **binary oracle** — the executed gate (`run_gate_checks.py`): acceptance checks pass or fail with no interpretation, and stay valid across refactors because they never look at the implementation. It answers: *does it work?*
  - the **completeness check** — the development journal compared against what was designed (task, story, architecture); catches missing scope and drift. It answers: *is it complete and faithful?*
- **3 · Sprint (≈ days)** — Agile Studio designs the sprint (needs, stories, tasks, acceptance tests) into a Working Session; closing it is the exit.
- **4 · Optimization (every N sprints)** — Conductor's Optimize measures how agent-ready the codebase is and turns its recommendations into next-sprint tasks: the outer loop feeds the inner ones.

Every loop, at every scale, is built from the same six pieces: a **trigger** (the beat), a **worktree** (isolation), **skills** (written context), **MCP connectors** (reach), a **second opinion** (the verifier), and **state on disk** (memory between runs — `loop-state.json`, journals, trace).

## Loop mode

Interactive mode (the default) is human-driven. **Loop mode** (opt-in via `/fairmind-loop`, recorded as `active-context.json.mode = "loop"`) adds an objective, machine-verifiable stop condition and lets an *executed* gate drive the loop:

- **Contract up front.** The Technical Lead classifies each acceptance criterion into one of five **check** types — `functional`, `metric`, `performance`, `static`, `evidence` (plus `custom`) — and specifies descriptors into `${FAIRMIND_BASE}/loop-state.json`. The Technical Lead never writes check code.
- **maker ≠ checker.** Checks are authored RED-first by an agent other than the maker (the QA Engineer for functional/evidence, the Code Reviewer for metric/performance). The engine enforces this structurally.
- **Admission ("verify the verifier").** `admit_check.py` runs mandatory portable gates — maker≠checker, clean-signal, RED-first, determinism probe — before a check can gate. Failures are quarantined and excluded from the stop condition.
- **Executed gate.** The `loop-check` Stop hook runs `run_gate_checks.py` on every stop: not green + budget left → blocks the turn with routed feedback; all green for **K ≥ 3 consecutive** evaluations → `passed_pending_human`.
- **Portable, no sandbox required.** Hermeticity is tiered: Tier A wraps checks in `srt` (network-denied) when present; otherwise Tier B uses a determinism probe and marks checks `hermeticity-unverified`.
- **Two human touchpoints.** The Technical Lead proposes a budget (iterations / consecutive-failure cap / timeout) that the user confirms before the loop arms; at the end a **final human gate** reviews the report — no auto-merge/deploy.

See the `fairmind-gate` skill for the check types and descriptor contract, and `/fairmind-add-check` for custom checks.

## Standalone vs connected

The plugin installs and runs with **zero MCP servers**. In **standalone mode** (the default when no Fairmind workspace is present), the Technical Lead bootstraps a minimal `.fairmind/active-context.json`, asks once whether a Fairmind workspace exists, and drives loop mode entirely from local `.fairmind/` files (contracts, `loop-state.json`, journals) — the executed gate, admission, budget, and human gate all work with nothing but `git`, `python3`, and `bash`. In **connected mode** (`fairmind: "configured"`), the Fairmind MCP adds platform context (projects, stories, requirements, tests, RAG), while Playwright and MongoDB MCP enable the QA/frontend and MongoDB-stack workflows. Every agent degrades gracefully: absent MCP tools mean it reads the local equivalents and operates standalone — absence of Fairmind is a mode, not an error.

## Workspace contract

The plugin assumes a Fairmind session workspace rooted at:

```
.fairmind/
  active-context.json             { base_path, project_id, session_mindstreamId }
  <project-slug>/<session-slug>/
    work_packages/
      ai/      backend/      frontend/      qa/
    journals/
```

The Technical Lead creates this on first run from a Fairmind work package. The other agents read `active-context.json` to resolve `FAIRMIND_BASE` and only write to scoped subpaths — the `validate-fairmind-path` hook will refuse anything outside that scope.

## Prerequisites

Required for standalone (loop mode) use:

- **`python3`**, **`git`**, **`bash`** on `$PATH` — the gate engine, its test suite, and the hooks are stdlib-only, no third-party dependencies.

Optional — only enable **connected mode** and the corresponding workflows:

- **Fairmind MCP** — `mcp__Fairmind__*` — platform context (projects, stories, requirements, tests, RAG). Absent → agents read local `.fairmind/` and operate standalone.
- **Playwright MCP** — the `QA Engineer` and `/fix-frontend-issue` browser workflows.
- **MongoDB MCP** — `Software Engineer` / `Code Reviewer` for NextJS/MongoDB stacks.
- **`gh` CLI** and **`jq`** on `$PATH` — the GitHub commands and the journal hook.
- For `/sonarqube-fix`: `SONAR_TOKEN` env var and a `sonar-project.properties` file in the project root.

An absent optional dependency degrades gracefully to standalone behavior; it is never a hard failure.

## Install

From git:

```text
/plugin marketplace add FairMind-Gen-AI-Studio/claude-plugins
/plugin install fairmind-coding@fairmind-plugins
```

From a local checkout:

```text
/plugin marketplace add /path/to/claude-plugins
/plugin install fairmind-coding@fairmind-plugins
```

Verify:

```text
/agents     # Technical Lead / Architect, Software Engineer, Code Reviewer, QA Engineer, Debugging Specialist, Security Engineer listed
/help       # /fairmind-loop, /fairmind-add-check, /harness-audit, /fix-issue, /sonarqube-fix, /migrate-to-k8s, /gh-* listed
```

## Examples

### Start a Fairmind task

Engage the tech lead first — the Technical Lead pulls the work package from Fairmind and bootstraps `.fairmind/`:

```text
> Technical Lead, prepare the work package for user story FM-1234
```

### Fix an issue with intelligent classification

```text
/fix-issue auth-login-broken
```

The orchestrator inspects the issue file under `./issues/`, classifies it (FE-FE / FE-BE / BE-BE), confirms with you, then dispatches the Software Engineer with the right skill loaded.

### Clean up SonarCloud issues for the current PR

```text
/sonarqube-fix
```

Runs `analyze_sonarqube.py` from `$CLAUDE_PLUGIN_ROOT/scripts/`, fixes issues by severity, runs `poetry run pytest`, and commits the result.

### Migrate a Lambda app to Kubernetes

```text
/migrate-to-k8s payments-api
```

Walks through the seven-phase migration with sub-agents and a manual traffic-shift gate at the end.

## Configuration

The plugin reads project-level configuration from:

- `.fairmind/active-context.json` — created by the Technical Lead, consumed by every other agent and by the hooks
- `./issues/*.md` (and image attachments) — input for `/fix-issue` and `/fix-frontend-issue`
- `./.claude/directive/*.md` — optional project-specific directives consumed by `/fix-frontend-issue` (the `*.example` file is ignored)
- `sonar-project.properties` + `SONAR_TOKEN` — required by `/sonarqube-fix`

## License

MIT
