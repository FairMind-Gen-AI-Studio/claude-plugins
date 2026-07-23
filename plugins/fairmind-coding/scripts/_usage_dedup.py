#!/usr/bin/env python3
"""Single source of truth for per-message.id token-usage dedup (PCF-15).

A streamed assistant message repeats its `usage` block once per content block,
every repeat carrying the SAME `message.id`, so a naive per-record sum
over-counts (~2.7x seen in the wild). Deduping by message.id before summing is
the fix.

Both the SubagentStop capture hook (`hooks/scripts/capture-subagent-tokens.sh`)
and PL-A1's Python digester import THIS helper, so the two can never drift — a
bash-only inline dedup could not be imported, which would make that "can't
drift" guarantee a fiction.

stdlib only: importable with no third-party dependency.
"""

# The four canonical Anthropic usage fields we sum. A missing field counts as 0.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def deduped_usage_totals(records):
    """Sum token usage across transcript records, deduped by message.id.

    `records` is an iterable of parsed transcript record dicts, each shaped like
    a real Claude Code transcript line: usage at ``record["message"]["usage"]``,
    the message id at ``record["message"]["id"]``.

    Rules:
      - records that share a NON-EMPTY STRING message.id contribute their usage
        ONCE — the first usage-bearing occurrence wins; the streamed-block repeats
        are dropped;
      - records with no id, an empty id, or a NON-STRING id (list/dict/int/bool)
        EACH contribute (never deduped): a non-string id is untrustworthy as a
        dedup key — it can be unhashable (list/dict raise on the set lookup) or
        collide across distinct values (1 == True), which would suppress a real
        usage row — so such records are treated id-less;
      - records without a usage block are skipped, and an id-only record does
        NOT mark that id as seen — a later usage-bearing sibling of the same id
        still counts once;
      - a missing/None token field counts as 0.

    Returns a dict of the four TOKEN_FIELDS summed. Empty input -> all zero.
    """
    totals = {f: 0 for f in TOKEN_FIELDS}
    seen = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        # Dedup ONLY on a non-empty STRING id: a non-string id (list/dict raises
        # on the set lookup; 1/True collide) is untrustworthy, so treat it id-less
        # (count once, never suppress). Only a usage-bearing record marks its id
        # seen, so an id-only record never suppresses that id's real usage row.
        mid = msg.get("id")
        if isinstance(mid, str) and mid:
            if mid in seen:
                continue
            seen.add(mid)
        for f in TOKEN_FIELDS:
            v = usage.get(f, 0) or 0
            try:
                totals[f] += int(v)
            except (TypeError, ValueError):
                pass
    return totals


if __name__ == "__main__":
    # Tiny CLI: read a transcript (JSONL) from argv[1] or stdin, print deduped
    # totals as JSON. Handy for manual inspection; the hook imports the function.
    import json
    import sys

    src = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    recs = []
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    print(json.dumps(deduped_usage_totals(recs)))
