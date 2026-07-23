---
name: fairmind-context
description: Use when starting any Fairmind development work - gathers full context including project, session, user story, task implementation plan, requirements, test expectations, and relevant documentation
---

# Fairmind Context Gathering

## Overview

Reusable context gathering for any Fairmind development work. This skill is the **foundation** for all other Fairmind skills.

**Announce at start:** "I'm using the fairmind-context skill to gather full context for this work."

## When to Use

Use this skill when:
- Starting any development task linked to Fairmind
- You have a task_id, user_story_id, or project_id
- You need to understand requirements and acceptance criteria
- You're preparing for implementation or code review

## Context Gathering Process

### Step 0: Resolution chain (`active-context.json` → bootstrap)

Context resolves in a fixed order so the plugin runs on any repo, connected or not:

1. Read `.fairmind/active-context.json` at the repo root. It carries `mode` (`interactive` | `loop`), a `project` name, and a **`fairmind`** field recording whether a Fairmind workspace is connected: `"configured"` (use the `mcp__Fairmind__*` tools in the steps below) or `"none"` (standalone — skip the MCP steps and take context from the user or local `.fairmind/` files).
2. If `active-context.json` is **absent**, bootstrap a minimal one — `{"mode":"loop","fairmind":"none","project":"<repo-name>"}` — and ask the user **once** whether a Fairmind workspace exists, recording the answer in `fairmind` and never re-asking while it stands (the Technical Lead owns this in `/fairmind-loop` Phase 0).

**Standalone is a mode, not an error.** When `fairmind` is `"none"` (or the MCP tools are unavailable), the steps below that call `mcp__Fairmind__*` are simply skipped; everything they would supply comes from the user or from local `.fairmind/` files.

### Step 1: Identify Current Project

**If project_id provided:** Skip to Step 2

**If project_id not provided:**
1. Use `mcp__Fairmind__General_list_projects` to list available projects
2. Ask user which project (if ambiguous) or infer from current work context
3. Store project_id for subsequent calls

### Step 2: Identify Active Session

1. Use `mcp__Fairmind__General_list_work_sessions` with the project_id
2. Filter for active sessions (`is_active: true`)
3. If multiple active sessions, ask user to clarify
4. Store session_id for subsequent calls

### Step 3: Gather Task Context (if task_id available)

1. Use `mcp__Fairmind__Studio_get_task` with task_id
2. Extract:
   - Implementation plan
   - Task status
   - Assigned agent role
   - Dependencies

### Step 4: Gather User Story Context (if user_story_id available)

1. Use `mcp__Fairmind__Studio_get_user_story` with user_story_id
2. Extract:
   - Title and description
   - Acceptance criteria
   - Business requirements
3. Use `mcp__Fairmind__Studio_get_related_user_stories` to identify dependencies

### Step 5: Gather Requirements

1. If user_story_id available:
   - Use `mcp__Fairmind__Studio_list_functional_requirements_by_session`
   - Use `mcp__Fairmind__Studio_list_technical_requirements_by_session`
2. Alternative: Use `mcp__Fairmind__Studio_list_requirements_by_project` for project-wide requirements

### Step 6: Gather Test Expectations (if user_story_id available)

1. Use `mcp__Fairmind__Studio_list_tests_by_userstory`
2. Extract expected test coverage and test scenarios

### Step 7: Gather Documentation

1. Use `mcp__Fairmind__General_rag_retrieve_documents` with relevant queries:
   - Similar implementations
   - Architectural patterns
   - Technology-specific best practices
2. Use `mcp__Fairmind__General_get_document_content` for specific documents if needed

### Step 8: Cross-Project Context (if applicable)

If the work involves integration with another project:
1. Repeat Steps 1-7 for the target project
2. Use `mcp__Fairmind__Code_list_repositories` to identify target repositories
3. Use `mcp__Fairmind__Code_search` to understand integration points

## Output Structure

Present gathered context in this format:

```markdown
# Context for {Task/User Story}

## Project
- **ID**: {project_id}
- **Name**: {project_name}

## Session
- **ID**: {session_id}
- **Status**: {active/inactive}

## User Story
- **ID**: {user_story_id}
- **Title**: {title}
- **Description**: {description}
- **Acceptance Criteria**:
  1. {criterion_1}
  2. {criterion_2}
- **Related Stories**: {list of related story IDs}

## Task
- **ID**: {task_id}
- **Status**: {status}
- **Implementation Plan**:
  ```
  {plan details}
  ```

## Requirements
### Functional Requirements
- {FR-1}: {description}
- {FR-2}: {description}

### Technical Requirements
- {TR-1}: {description}
- {TR-2}: {description}

## Test Expectations
- {Test scenario 1}
- {Test scenario 2}
- Expected coverage: {percentage or scope}

## Documentation
- {Document 1}: {summary}
- {Document 2}: {summary}

## Integration Points (if cross-project)
- Target project: {name}
- Repositories: {list}
- API endpoints: {list}
```

## Next Steps

After gathering context:
- **For implementation**: Use `fairmind-tdd` skill
- **For code review**: Use `fairmind-code-review` skill
- **For debugging**: Use gathered context with debugging workflows

## Error Handling

**If project_id not found:**
- Ask user to provide project name or ID
- List available projects for selection

**If session not active:**
- Warn user that session may be ended
- Proceed with project-level context only

**If task_id or user_story_id not found:**
- Gather whatever context is available
- Inform user of missing context elements
