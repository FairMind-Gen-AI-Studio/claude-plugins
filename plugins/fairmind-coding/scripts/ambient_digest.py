#!/usr/bin/env python3
"""PL-A1b — the ambient-telemetry DIGESTER's pure transform.

This module holds the whole PL-A1b `digest()` contract: turn one or more parsed
transcript record lists (a main session transcript plus zero+ subagent SIDECAR
transcripts) into a privacy-scrubbed, per-(session,model) token/skill rollup.
It is a peer of PL-A0's `_usage_dedup.py` and PL-A1a's `_insights_session.py`,
and is invoked from `_insights_session.run_sweep` (the SessionStart-driven
lifecycle sweep) — never on the request/response hot path.

Design, matching the frozen PL-A1b interface contract:

  * `digest(record_sets, meta) -> dict` is a PURE function: no filesystem, no
    clock, no env reads beyond the one-time sibling `plugin.json` version probe.
    It never raises on malformed input — a schema-drifted record degrades the
    result (`parserDegraded: True`, no fabricated numbers) rather than crashing
    the caller (a SessionStart-spawned background sweep must never wedge).
  * Dedup is delegated ENTIRELY to `_usage_dedup.deduped_usage_totals` (PL-A0) —
    this module never re-sums usage itself, so the two can't drift apart.
  * Privacy: a rollup carries only {model, the 4 token ints} (round 2: skills/
    entry_source moved to the row level, see below). No raw cwd/gitBranch/
    message content/id ever reaches the returned dict.

PL-A2a extended this SAME contract (converging the spool row onto the shipped
project-context wire schema, see `ambient_outbox.build_wire_payload`) with
session-LEVEL fields on the returned dict, alongside the pre-existing
`session_id`/`tenancy`/`pluginVersion`/`parserDegraded`/`rollups`:

  * `started_at` / `ended_at`: carried through VERBATIM from `meta` (the
    caller — `_insights_session._digest_one_session` — sources them from the
    session's own registry row and copies them into `meta`; this module never
    re-derives or reformats them, it only echoes what it was handed, exactly
    like it already does for `session_id`/`tenancy`/`entry_source`).
  * `entry_source`: HOISTED to this top (row) level — one value per session.
  * `skills`: HOISTED to this top (row) level too (PL-A2a round 2, D1) — the
    sorted union of every `attributionSkill`/`Skill` tool_use seen across the
    whole session. Round 1 stamped this into every per-MODEL rollup instead,
    which silently lost the skill entirely for a session whose only
    Skill-invoking record carried no `usage` block (such a record never
    produces a rollup at all, since rollups are keyed off usage-bearing
    records — see `groups.setdefault` below). `tool_counts` has never had
    this problem (it was already row-level, independent of `usage`), so
    `skills` now follows the SAME altitude — one writer per fact.
  * `schema`: an explicit, versioned stamp (`SCHEMA_VERSION`, PL-A2a round 2
    D4) on every row this function produces — the outbox
    (`ambient_outbox._unsendable_reason`) uses this to detect an
    unrecognized/future row VERSION rather than sniffing for the mere
    absence of `started_at`/`ended_at`, which a row with a different, still
    incompatible field set could otherwise slip past.
  * `tool_counts`: `{tool_name: count}`, aggregated across EVERY record in
    EVERY record_set (main transcript + subagent sidecars alike) via the SAME
    single content-block walk `_record_skills` already performs (no second
    walk) — see `_record_tool_names` below. A `<synthetic>` record's own
    tool_use blocks are excluded, mirroring the token-accounting exclusion of
    that model exactly (`model != _SYNTHETIC_MODEL`).

A rollup itself now carries ONLY `{model, the 4 token ints}` — a CLOSED set
(see test_ambient_digest.py's own pinned key-set assertion). `skills` and
`entry_source` are no longer per-rollup fields; a rollup is a per-MODEL token
aggregate, and neither fact is a per-model fact, so each has exactly ONE
writer at the row level instead of a copy re-stamped into every rollup.

stdlib only.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _usage_dedup  # noqa: E402  (PL-A0 shared dedup helper — the single sum oracle)

_SYNTHETIC_MODEL = "<synthetic>"

# PL-A2a round 2 (D4): the explicit, versioned schema stamp `digest()` puts on
# every row it produces. A single, PUBLIC (no leading underscore) constant so
# `ambient_outbox._unsendable_reason` can import and compare against the SAME
# value rather than duplicating the literal — one source of truth for
# producer (this module) and validator (ambient_outbox.py) alike, the same
# idiom `insights_flush_payload.py`'s `*_CONTRACT_VERSION` constants use.
SCHEMA_VERSION = "fm-ambient.session/1"


def _plugin_version():
    """Best-effort read of the sibling ../plugin.json version (this module lives
    in <plugin>/scripts/, mirroring `_insights_session.plugin_version()`).
    Returns None on any failure — never raises."""
    try:
        with open(os.path.join(_HERE, "..", "plugin.json"), encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except Exception:
        return None


def _is_drifted_assistant(rec):
    """True iff `rec` is a schema-drifted assistant record: `type == "assistant"`
    and either `message` is not a dict, or `message` carries a `usage` key whose
    value is present but not a dict. A record with no `usage` key at all is
    normal (not every assistant record carries usage), not drift."""
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return False
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return True
    if "usage" in msg and not isinstance(msg.get("usage"), dict):
        return True
    return False


def _record_signals(rec):
    """Both content-derived signals this ONE record contributes, from ONE walk
    over `message.content`: `(skills, tool_names)`.

    - `skills` (a set) comes from BOTH sources — a non-empty top-level
      `attributionSkill`, and any `Skill` tool_use block's `input.skill`.
    - `tool_names` (a LIST, not a set) is every `tool_use` block's `name`: the
      same record can invoke one tool repeatedly and each invocation is its own
      count.

    A `Skill` block contributes to both, which is exactly why this is one
    function: two helpers walking the same content list drifted from their own
    docstring, which claimed a single walk while performing two."""
    skills = set()
    names = []
    if not isinstance(rec, dict):
        return skills, names
    top = rec.get("attributionSkill")
    if isinstance(top, str) and top:
        skills.add(top)
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return skills, names
    content = msg.get("content")
    if not isinstance(content, list):
        return skills, names
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str) and name:
            names.append(name)
        if name != "Skill":
            continue
        inp = block.get("input")
        if not isinstance(inp, dict):
            continue
        skill = inp.get("skill")
        if isinstance(skill, str) and skill:
            skills.add(skill)
    return skills, names


def digest(record_sets, meta):
    """Turn `record_sets` (record_sets[0] = main transcript, record_sets[1:] =
    zero+ subagent sidecars) into the PL-A1b/PL-A2a rollup dict. See module
    docstring and the PL-A1b/PL-A2a dispatches for the exact contract. Never
    raises."""
    session_id = meta.get("session_id") if isinstance(meta, dict) else None
    tenancy = meta.get("tenancy") if isinstance(meta, dict) else None
    entry_source = meta.get("entry_source") if isinstance(meta, dict) else None
    started_at = meta.get("started_at") if isinstance(meta, dict) else None
    ended_at = meta.get("ended_at") if isinstance(meta, dict) else None

    parser_degraded = False
    skills = set()
    tool_counts = {}
    groups = {}  # model -> [usage-bearing, non-synthetic records]

    for record_set in record_sets or []:
        if not isinstance(record_set, list):
            continue
        for rec in record_set:
            if _is_drifted_assistant(rec):
                parser_degraded = True
            rec_skills, rec_tool_names = _record_signals(rec)
            skills |= rec_skills
            if not isinstance(rec, dict):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            model = msg.get("model")
            # PL-A2a tool_counts: the SAME synthetic-model exclusion token
            # accounting already applies below (`isinstance(model, str) and
            # model and model != _SYNTHETIC_MODEL`) — a <synthetic> record's
            # own tool_use blocks must never be counted, even though this
            # gate runs independently of whether `usage` is present (a
            # tool_use record with no usage block would otherwise contribute
            # no tokens but should still contribute its tool call).
            if isinstance(model, str) and model and model != _SYNTHETIC_MODEL:
                for name in rec_tool_names:
                    tool_counts[name] = tool_counts.get(name, 0) + 1
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                # No usage block (or a drifted non-dict one) -> this record
                # contributes no tokens; never fabricate a rollup for it.
                continue
            if model == _SYNTHETIC_MODEL:
                continue  # excluded from accounting entirely, even non-zero usage
            if not isinstance(model, str) or not model:
                continue
            groups.setdefault(model, []).append(rec)

    # PL-A2a round 2 (D1): `skills` is a ROW-level fact (one writer), not a
    # per-rollup copy — a rollup only exists for a model with a usage-bearing
    # record, but a Skill invocation's own record may carry NO usage block at
    # all (see `_record_skills`/`tool_counts` above, which is unconditional).
    # Stamping skills into every rollup means a session with zero rollups
    # (e.g. exactly this no-usage case) would silently lose every skill name;
    # hoisting it here, alongside `tool_counts`, fixes that at the source.
    sorted_skills = sorted(skills)
    rollups = []
    for model in sorted(groups):
        totals = _usage_dedup.deduped_usage_totals(groups[model])
        rollup = {"model": model}
        rollup.update(totals)
        rollups.append(rollup)

    return {
        "session_id": session_id,
        "tenancy": tenancy,
        "pluginVersion": _plugin_version(),
        "parserDegraded": parser_degraded,
        "started_at": started_at,
        "ended_at": ended_at,
        "entry_source": entry_source,
        "schema": SCHEMA_VERSION,
        "skills": sorted_skills,
        "tool_counts": tool_counts,
        "rollups": rollups,
    }


def _read_jsonl(path):
    """Parsed records from a JSONL file at `path`, skipping blank/unparseable
    lines. A missing file yields an empty list (never raises) — the sweep's
    common "session ended, transcript not yet flushed" case must degrade to an
    empty transcript, not crash."""
    if not path or not os.path.isfile(path):
        return []
    records = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return []
    return records


def digest_transcript_file(main_path, meta, sidecar_paths=()):
    """Read `main_path` (+ any `sidecar_paths`) as JSONL, build the `record_sets`
    `digest()` expects, and return its result. A missing/unreadable file (main or
    sidecar) degrades to an empty record_set rather than raising."""
    record_sets = [_read_jsonl(main_path)]
    for sidecar_path in sidecar_paths:
        record_sets.append(_read_jsonl(sidecar_path))
    return digest(record_sets, meta)
