---
description: Flush any unflushed Agentic Insights payloads (loop stats, agent decisions, harness-audit runs) to project-context, on demand and independent of a loop close
allowed-tools: Bash(python3 "$CLAUDE_PLUGIN_ROOT"/scripts/insights_flush_payload.py:*), mcp__Fairmind__Insights_record_harness_audit, mcp__Fairmind__Insights_record_loop_stats, mcp__Fairmind__Insights_record_agent_decisions
---

# fairmind-sync-insights

`/fairmind-loop`'s Exit gate and `/harness-audit`'s own final step both flush what they
just produced to Agentic Insights automatically — but either one leaves its payload
**unflushed on disk** when the Fairmind MCP was not connected at that moment, or when a
send was attempted and failed partway. `/fairmind-sync-insights` is the catch-up verb:
run it any time (standalone, on a schedule, or right after reconnecting Fairmind) to pick
up everything still pending across **all three** categories — loop stats, agent
decisions, and harness-audit runs — and send it now.

## Usage

```bash
/fairmind-sync-insights
```

No arguments: it always reads whatever is on disk for the current repo and flushes
whatever is pending.

## Resolving the script path

`$CLAUDE_PLUGIN_ROOT` is **empty in your (orchestrator) shell** — Claude Code substitutes
it in the `allowed-tools` frontmatter above and sets it inside the plugin's own hooks, but
it is not exported to the Bash calls you issue from this command body, so a literal
`python3 "$CLAUDE_PLUGIN_ROOT"/scripts/…` runs as `/scripts/…` and errors. Resolve the
install path once, before step 1, and substitute it for `$CLAUDE_PLUGIN_ROOT` below:

```bash
python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['fairmind-coding@fairmind-plugins'][0]['installPath'])"
```

That absolute path is exactly what Claude Code expands `$CLAUDE_PLUGIN_ROOT` to in
`allowed-tools`, so using it keeps every call inside the granted permissions. (When
self-hosting inside the plugin repo itself, the repo copy
`plugins/fairmind-coding/scripts/…` also works.)

## What it does

1. **Emit everything pending, in one call**, with `$CLAUDE_PLUGIN_ROOT` replaced by the
   path you resolved above:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT"/scripts/insights_flush_payload.py --emit all
   ```
   This prints `{"loop": ..., "decisions": ..., "audit": ...}` on stdout. Each key is
   either the pending payload for that category or JSON `null` when there is nothing new
   to send (already flushed, or the category's source files are missing/degraded — a
   missing `.fairmind/audit/run-meta.json`, for instance, quietly emits `audit: null`
   under this `--emit all` call, with no advisory: a repo that never ran `/harness-audit`
   is the normal case here, so nothing is printed to stderr. The "run /harness-audit
   first" advisory only appears on an explicit `--emit audit` request, not on this
   catch-up command's `--emit all`).

2. **Send each non-null category through its own MCP tool** — never batch them into one
   call, since a partial failure must be attributable to exactly one category:
   - `loop` → `mcp__Fairmind__Insights_record_loop_stats`
   - `decisions` → `mcp__Fairmind__Insights_record_agent_decisions`
   - `audit` → `mcp__Fairmind__Insights_record_harness_audit`

3. **Commit only after that category's send succeeds** — the cursor advance and the MCP
   call are never bundled:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT"/scripts/insights_flush_payload.py --commit <category>
   ```
   Call `--commit loop`, `--commit decisions`, `--commit audit` individually, one **only
   after** its own MCP call above has returned success — never speculatively, never for a
   category whose send errored or did not complete. A failed or partial send leaves that
   category **uncommitted**: it stays on disk exactly as it was and this same command
   picks it up again next run, so nothing is ever silently dropped and nothing is ever
   marked sent that was not.

4. **Report per-category counts.** Tell the user, per category, how many records were
   flushed this run (a loop close, a decisions batch of `<n>` rows, an audit run) versus
   how many stayed pending because their send failed or the category was already `null`.
   Do not report a single aggregate number — the per-category breakdown is what lets a
   partial failure be diagnosed and re-run.

## Backfill semantics

The cursor (`.fairmind/insights-sync.json`) is the only thing standing between "already
sent" and "pending" — it is safe to delete. **Deleting** the cursor file makes every
category pending again (a full **backfill**): the next `--emit all` re-emits everything
currently on disk, and the next `--commit` re-marks it flushed. This is safe because the
server-side `Insights_record_*` tools **upsert** — re-sending an already-recorded loop
close, decision, or audit run is idempotent, never a duplicate. Use this when the cursor
itself is suspect (corrupted, or you want to re-verify what actually landed server-side)
rather than trying to hand-edit it.

## Notes

- `insights_flush_payload.py` is stdlib Python 3 only — no install step, no network
  calls; it never calls the network itself, only reads/writes local JSON.
- When the Fairmind MCP is not connected at all, this command has nothing useful to do —
  report that plainly and stop; nothing on disk is touched (no `--commit` without a real
  send), so a later run with Fairmind connected picks up exactly the same pending set.
- This command complements, it does not replace, the terminal flush `/fairmind-loop`
  already runs on close and the flush `/harness-audit` already runs on a successful run —
  those stay the primary path. `/fairmind-sync-insights` exists for what those two leave
  behind: a disconnected MCP at the time, or a send that failed partway.
