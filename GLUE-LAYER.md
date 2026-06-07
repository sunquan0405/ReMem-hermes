# ReMem — Glue Layer Design

## Goal

A unified enhanced memory system that integrates Hermes native memory with the 7-layer Memory OS architecture.  
No overlap. No conflict. No data loss. With guaranteed rollback.

## Components

```
ReMem-hermes/
├── GLUE-LAYER.md              ← This file: architecture design
├── modifications/
│   └── glue-layer-rulebook.md ← Storage routing rules (appended to rulebook.md)
│   └── soul-rulebook.md       ← Ground Truth hierarchy (appended to SOUL.md)
├── scripts/
│   ├── backup.sh               ← Full Hermes state backup + integrity check
│   ├── migrate_from_hermes.py  ← One-shot migration: memory → fact_store + Qdrant
│   ├── verify_integration.py  ← 6-point integration verification
│   ├── rollback.sh            ← One-click rollback to native memory
│   └── install.sh             ← Orchestrator: backup → install → migrate → verify
```

## Layer Responsibilities

| Memory Type | Storage | Query Tool | Characteristics |
|-------------|---------|------------|-----------------|
| Temporary state (current task, today's prefs) | Hermes `memory` | `memory` | Fast write, high churn, short-lived |
| Durable facts (project decisions, tech stack, user prefs) | ReMem `fact_store` + Qdrant | `fact_store` / `qdrant_search` | Structured, trust-scored, vector searchable, long-lived |
| Conversation history | Hermes `state.db` (FTS5) | `session_search` | Full-text searchable |
| Cross-session fabric | ReMem L4 Fabric | `fabric_recall` | LLM-extracted summaries |
| Knowledge base | ReMem L6 Vault | `qdrant_search` | Auto-curated wiki |

## Query Routing (Read)

```
User question → agent determines memory type:
  "who is user / current preferences"     → memory (native, fast)
  "project decision / tech stack / arch"  → fact_store + qdrant_search
  "what did we discuss before"            → session_search
  "what does the knowledge base say"      → qdrant_search
  "cross-session patterns"                → fabric_recall
```

## Storage Routing (Write)

When saving information:

- **Temporary** (may change this session) → `memory` only
- **Permanent** (project decisions, tech selection, user preferences) → `fact_store` + `memory` (dual write)
- **Knowledge content** (code snippets, document summaries) → also Qdrant
- **Uncertain** → write to both, tag `source="dual:unsure"`

## Migration Strategy

### Backup (MANDATORY before any changes)

```
bash scripts/backup.sh
  1. Export all Hermes memory entries to JSON
  2. Backup state.db, SOUL.md, rulebook.md, config.yaml, .env
  3. Full ~/.hermes/ snapshot (tar.gz)
  4. SHA256 integrity verification
```

### Migration

```
1. Read memory entries from JSON export
2. Classify by type (config / project / preference / skill / general)
3. Write to memory_store.db facts table (SQLite + FTS5)
4. Write to Qdrant knowledge_base (vector search)
5. Append Ground Truth hierarchy to SOUL.md
6. Append storage/query routing rules to rulebook.md
```

### Rollback

```
bash scripts/rollback.sh
  1. Restore SOUL.md from backup
  2. Restore/clean rulebook.md
  3. Delete memory_store.db
  4. Delete Qdrant collection
  5. Stop Docker containers
  6. Restore .env from backup
```

## Verification Plan

| Test | Method | Pass Criteria |
|------|--------|---------------|
| Backup integrity | SHA256 checksum | 100% match |
| Migration accuracy | Search fact_store vs original memory | No omissions, no corruption |
| Query routing | 10 test queries across all types | Correct tool routing |
| Storage routing | Save new fact → check memory + fact_store | Dual write works |
| Rollback | Run rollback → check original state restored | Exact match with backup |
