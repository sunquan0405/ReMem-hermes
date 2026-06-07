# ReMem — Glue Layer: Storage & Query Routing Rules

> Append to `~/.hermes/rulebook.md`. Idempotent via `<!-- ReMem — Glue Layer -->` marker.

<!-- ReMem — Glue Layer -->

## Memory Storage Routing

When saving information, apply these rules to determine storage target:

1. **Temporary state** (information that may change this session: current task progress, ephemeral preferences)
   → **`memory` tool only** (fast, no vectorization)

2. **Durable facts** (information that should persist across sessions: project decisions, tech stack, architecture decisions, user long-term preferences)
   → **`memory` + `fact_store` (dual write)**
   - `fact_store(action='add', content='...', entities=[...], tags=[...])` → Memory OS
   - `memory` tool → Hermes native (backward compatibility)
   - Tag: `source="dual:memory+fact"`

3. **Searchable knowledge** (code snippets, technical designs, document summaries)
   → **Also write to Qdrant** via ingestion pipeline
   - `fact_store(action='add', content='...', entities=['knowledge'])`
   - Qdrant indexing is handled automatically by the ARQ worker

4. **When uncertain**
   → **Write to both**, tag `source="dual:unsure"`

## Query Routing

When retrieving knowledge, select the optimal tool based on query type:

| Query Type | Tool | Priority |
|-----------|------|----------|
| "Who is the user / current preferences" | `memory` | Fast lookup |
| "Project decisions / tech stack / architecture" | `fact_store(search)` + `qdrant_search` | fact_store first, qdrant second |
| "What does the knowledge base / wiki say" | `qdrant_search` | Semantic match |
| "What was discussed before" | `session_search` | Full-text |
| "Cross-session patterns" | `fabric_recall` | LLM-extracted summaries |

**Merge rules:**
- When multiple tools return results: merge by Ground Truth hierarchy (see SOUL.md section)
- If `fact_store` and `memory` both return variants of the same info: prefer `fact_store` (structured, newer)
- If `qdrant_search` and `fact_store` both return: prefer `fact_store` (explicit storage), qdrant as supplement

## Fact Feedback Rule

When you retrieve a fact via `fact_store` (probe, search, or reason) and reference it in your response, you MUST call `fact_feedback` in the same turn:

```
fact_feedback(action='helpful', fact_id=...)    # fact was accurate and useful
fact_feedback(action='unhelpful', fact_id=...)  # fact was wrong or outdated
```

Without this rule, trust scores stagnate and fact quality degrades silently over time.

## Migration Note

After installing ReMem, existing Hermes memory entries are migrated to `fact_store` via `migrate_from_hermes.py`. Old `memory` entries are preserved (not deleted) for backward compatibility. The agent will prefer ReMem results during queries.
