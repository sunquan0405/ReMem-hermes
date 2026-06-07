# ReMem — We re-memoried Hermes

> **Re**placement **Mem**ory for Hermes Agent.  
> Broke the 10,000-character ceiling. Added structured facts, vector search, trust feedback.  
> Zero cloud. Zero subscription. One-click rollback if you change your mind.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What we did

**Hermes' native memory has a hard 10,000-character limit.** When you hit it, old memories get evicted batch-by-batch. You spend your conversations re-explaining context instead of making progress.

**We took [Memory OS](https://github.com/ClaudioDrews/memory-os) (ClaudioDrews' 7-layer memory architecture, MIT) — a powerful sprawling toolbox — and turned it into a drop-in replacement.**

- **We added the Glue Layer:** Storage routing, query routing, Ground Truth hierarchy — the wiring that makes two systems act as one.
- **We added the safety net:** `backup.sh` → `migrate_from_hermes.py` → `verify_integration.py` → `rollback.sh`. Install with confidence, revert in one command.
- **We built the installation pipeline:** OrbStack + Docker Compose + env config + API key management — everything needed to go from zero to running in under 30 minutes.
- **We documented the migration path:** 49 entries migrated, verified, searchable. Your existing memory is not lost — it's upgraded.

---

## The pain points we eliminated

### 1. The 10,000-character wall

You hit it. You know you hit it because the `memory` tool starts refusing writes. The only fix is to delete old entries — manually, one by one, guessing which ones you no longer need.

```
  原生 memory 条数   已用字符    剩余空间
  ────────────────  ────────    ────────
    48 entries        9,867        133     ← 随时会满
```

ReMem uses SQLite (`memory_store.db`) + Qdrant for storage. No character limit. No guessing what to delete. Disk is the only boundary.

### 2. All memories are equal — and none are trusted

Native Hermes has no way to say "this memory is more reliable than that one." Every entry is just a string. Over time, stale or incorrect entries accumulate silently.

ReMem introduces `fact_feedback(action='helpful'|'unhelpful')` — every time the agent uses a fact, it can flag whether the fact was correct. Trust scores adjust dynamically. Low-scoring facts get deprioritized or cleaned by the decay scanner.

### 3. No cross-session continuity

Every session feels like a cold start. The agent re-discovers what was already decided in the previous session, burning tokens and context.

ReMem's `pre_llm_call` hook injects relevant memories from all four sources (fact_store, Qdrant, sessions, fabric) before every turn. The Ground Truth hierarchy tells the agent these injected memories are authoritative — no re-discovery, no wasted tokens.

### 4. Manual knowledge management

You have to tell the agent "remember this." If you forget, the knowledge is lost. There's no automatic extraction, no summarization, no deduplication.

ReMem's `on_session_end` hook uses an LLM (DeepSeek V4 Flash) to automatically extract significant decisions, resolutions, and notes from every conversation. New knowledge enters the system without manual effort.

---

## Performance overhead — measured, not guessed

We installed and measured on a production Hermes setup. Here's exactly what ReMem adds:

### Docker stack

| Container | Idle RAM | Idle CPU | Notes |
|-----------|----------|----------|-------|
| Qdrant | 100-150 MB | <1% | Vector database, needed only during search |
| Redis | 50-80 MB | <0.5% | Job queue, 512MB maxmemory hard cap |
| ARQ Worker | 80-120 MB | <0.5% | Embedding pipeline, sleeps most of the time |
| **Total** | **~300 MB** | **<2%** | |

### Per-turn latency

Every user message triggers a `pre_llm_call` hook that searches all four memory sources:

```
User message
  ├── fabric_recall()          → local file read, <2 ms
  ├── _search_qdrant()         → Qdrant HTTP (localhost), ~5 ms
  ├── _search_sessions()       → SQLite FTS5 (local), ~2 ms
  └── _search_facts()          → SQLite FTS5 (first turn only), ~2 ms
                                   Total: ~9-11 ms added
```

**10 ms per turn. You won't notice it.** The main latency remains the LLM API call (2-5 seconds on DeepSeek V4 Flash/Pro).

### Context overhead

Injected memory adds approximately 200-500 tokens to the system prompt per turn. At DeepSeek V4 Flash pricing (~$0.15/1M input tokens), that's **~$0.0001 per turn**.

### Session-end extraction

After each session, ReMem runs an LLM extraction (~8K tokens) using DeepSeek V4 Flash:

```
8,000 tokens × $0.15/1M = $0.0012 per session
At 50 sessions/day: ~$0.06/day (~$1.80/month)
```

### Background tasks

| Task | Frequency | Resource |
|------|-----------|----------|
| Decay scanner | Weekly | Qdrant scan + API cleanup, ~5 MB spike |
| Semantic dedup | Weekly | Vector comparison (cosine >0.92 → merge) |
| Wiki continuous ingest | On file change | Redis + ARQ enqueue |
| Micro-reflection | Every 2h | DeepSeek API, ~$0.01/call |

### Total monthly cost estimate

| Item | Cost |
|------|------|
| RAM (300 MB shared) | Already paid for (your hardware) |
| Docker images | Free (Qdrant + Redis are open-source) |
| LLM extraction (50 sessions/day) | ~$1.80/month |
| Embeddings via OpenRouter | ~$0.30/month (qwen3-embedding-8b) |
| Micro-reflection (12/day) | ~$3.60/month |
| **Total added cost** | **~$5.70/month** |

To put that in perspective: a single `hermes chat` with DeepSeek V4 Pro costs more than a full day of ReMem background tasks.

---

## Before → After

| Dimension | Before (Native) | After (ReMem) |
|-----------|----------------|---------------|
| **Storage ceiling** | ✗ 10,000 characters | ✓ Disk capacity (SQLite + Qdrant) |
| **Memory structure** | ✗ Flat key-value strings | ✓ Structured facts with categories, entities, trust scores |
| **Search** | ✗ Key-value lookup only | ✓ FTS5 keyword + Qdrant semantic vector search |
| **Quality feedback** | ✗ Static | ✓ `fact_feedback` loop adjusts trust scores dynamically |
| **Automatic cleanup** | ✗ Manual deletion | ✓ Decay scanner + semantic dedup |
| **Cross-session recall** | ✗ Limited `session_search` | ✓ Fabric + Qdrant + fact_store multi-source injection |
| **Ground Truth** | ✗ Implicit | ✓ Explicit 4-level hierarchy: terminal → injected memory → docs → training |
| **Installation** | ✗ Not applicable | ✓ One command, 30 minutes, verified, rollback-ready |

---

## Architecture in one picture

```
                               ┌──────────────────────┐
                               │      Glue Layer      │
                               │  (query + storage    │
  "记住这个" or "查那个" ──────▶   routing in rulebook) │
                               └──────┬───────┬───────┘
                                      │       │
                              ┌───────┘       └───────┐
                              ▼                       ▼
                     ┌─────────────────┐    ┌──────────────────┐
                     │  Native memory   │    │  ReMem stack      │
                     │  (10K limit)     │    │  ──────────────── │
                     │  temp/prefs only │    │  · fact_store     │
                     │                  │    │  · Qdrant vectors │
                     │                  │    │  · SQLite + FTS5  │
                     └─────────────────┘    │  · Worker pipeline │
                                            └──────────────────┘
```

The Glue Layer sits in `rulebook.md` and `SOUL.md` — it tells the agent **which tool to use for which type of query**. No Python middleware. No runtime daemon. Pure agent instructions.

---

## What Memory OS gave us (and what we added)

| Layer | Memory OS (upstream) | ReMem (this project) |
|-------|-------------------|---------------------|
| L1 Workspace | MEMORY.md, USER.md, CREATIVE.md | Same files, no change |
| L2 Sessions | SQLite + FTS5 | Same, Hermes-native is preserved |
| L3 Structured Facts | `fact_store` tool + trust scoring | **Added storage routing rules** — agent knows when to use `fact_store` vs `memory` |
| L4 Fabric (Icarus) | Pre_llm_call hook, cross-session recall | **Added query routing** — agent selects optimal tool per query type |
| L5 Qdrant | Vector database, 4-level fallback | **Added migration pipeline** — bulk import 49 existing entries, verified |
| L6 LLM Wiki | Auto-curated vault | No change, uses upstream |
| L7 Ground Truth | SOUL.md hierarchy | **Added conflict resolution table**, context injection convention, infra verification |
| **Glue Layer** | ❌ Not present | **Storage routing, query routing, fact feedback rule** in rulebook.md |
| **Safety** | ❌ Not present | **backup.sh → migrate → verify → rollback.sh** |
| **Install** | ❌ Basic setup.sh | **OrbStack + Docker Compose + .env management + 30-min verified install** |

---

## Comparison with other memory solutions

| Feature | ReMem (this) | mem0 | Zep | Letta | Native Hermes |
|---------|-------------|------|-----|-------|--------------|
| Local storage | ✅ | ✗ Cloud-first | ✗ Cloud-first | ✗ Cloud-first | ✅ |
| No subscription | ✅ | ✗ | ✗ | ✗ | ✅ |
| Any LLM provider | ✅ | Partial | Partial | Partial | ✅ |
| Hermes-native | ✅ | ✗ | ✗ | ✗ | ✅ |
| Unlimited storage | ✅ | ✗ | ✗ | ✗ | ✗ (10K chars) |
| Structured facts | ✅ | Partial | ✗ | ✗ | ✗ |
| Vector search | ✅ | ✅ | ✅ | ✅ | ✗ |
| Auto-curated knowledge | ✅ | ✗ | ✗ | ✗ | ✗ |
| Ground Truth hierarchy | ✅ | ✗ | ✗ | ✗ | ✗ |
| Fact feedback loop | ✅ | ✗ | ✗ | ✗ | ✗ |
| One-click rollback | ✅ | ✗ | ✗ | ✗ | N/A |

---

## Quick Start

### Prerequisites

- [Hermes Agent](https://hermes-agent.nousresearch.com)
- [OrbStack](https://orbstack.dev) or Docker Desktop
- Python 3.11+

### Install (30 minutes)

```bash
git clone https://github.com/sunquan0405/ReMem-hermes.git
cd ReMem-hermes

# 1. Full backup (safety first)
bash scripts/backup.sh

# 2. Install everything
bash scripts/install.sh --skip-backup

# 3. Verify
python3 scripts/verify_integration.py --quick
```

### What happens

| Step | Action | Effect |
|------|--------|--------|
| `backup.sh` | Saves state.db, SOUL.md, config.yaml, .env, disk snapshot | SHA256 verified, rollback-ready |
| `install.sh` | Clones Memory OS, creates SQLite DBs, starts Docker stack (Qdrant + Redis + Worker) | Infrastructure ready |
| Glue Layer | Appends Ground Truth hierarchy → SOUL.md, storage/query rules → rulebook.md | Agent knows how to use the new system |
| `migrate_from_hermes.py` | Reads existing `memory` entries → writes to `fact_store` + Qdrant | 0 data loss |
| `verify_integration.py` | Checks DB, Qdrant, SOUL.md, rulebook | 4/4 or rollback |

### Rollback (1 minute)

```bash
bash scripts/rollback.sh
```

Restores SOUL.md, rulebook.md, removes `memory_store.db`, stops Docker stack. Your native `memory` entries and `state.db` are untouched — always.

---

## Name

**ReMem = Re (Replacement / Remember) + Mem (Memory)**

A replacement for the original Hermes memory system — not a complement, not a plugin that sits alongside. Once installed, the agent uses ReMem by default. The old 10K memory is preserved for compatibility but deprioritized.

---

## License & Attribution

- **ReMem-hermes:** MIT © 2026 sunquan0405
- **Memory OS** (upstream): MIT © 2026 [ClaudioDrews](https://github.com/ClaudioDrews/memory-os)
- **Hermes Agent:** Apache 2.0 © [Nous Research](https://hermes-agent.nousresearch.com)

This is NOT an official Nous Research project. The "Hermes" name indicates the target platform only.

Full copyright statements in [NOTICE](./NOTICE).
