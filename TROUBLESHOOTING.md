# ReMem Troubleshooting Guide

Quick diagnosis and fix for the most common issues. For the complete 7-layer health check, see the `memory-os-integration` skill's `references/health-check.md`.

---

## Symptom: `qdrant_search` returns empty results

### Check

```bash
QKEY=$(docker exec docker-qdrant-1 printenv QDRANT__SERVICE__API_KEY)
curl -s -H "api-key: $QKEY" http://127.0.0.1:6333/collections/knowledge_base \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['result']; \
     print(f'points={d[\"points_count\"]} indexed={d[\"indexed_vectors_count\"]}')"
```

If `indexed=0` but `points>0` — the HNSW index was never built.

### Root cause

Qdrant's default `indexing_threshold=10000`. With fewer than 10,000 vectors, no HNSW index is built. Vector similarity search silently returns nothing.

### Fix (one-time, persists across restarts)

```bash
QKEY=$(docker exec docker-qdrant-1 printenv QDRANT__SERVICE__API_KEY)
curl -s -H "api-key: $QKEY" -H "Content-Type: application/json" \
  http://127.0.0.1:6333/collections/knowledge_base -X PATCH \
  -d '{"optimizers_config":{"indexing_threshold":20}}'
```

Verify: `indexed_vectors_count` should equal `points_count`.

---

## Symptom: "Worker unhealthy" / `curl localhost:8000` fails

### Root cause

The Worker is an ARQ (Async Redis Queue) job processor, NOT an HTTP server. It has no HTTP endpoint — `curl :8000` will always fail.

### Correct verification

```bash
# Container health (Docker's built-in health check pings Redis)
docker inspect docker-worker-1 --format '{{.State.Health.Status}}'
# Expected: healthy

# Recent logs — confirms ARQ started and connected
docker logs docker-worker-1 --tail 10
# Expected: "Starting ARQ worker" + "Connected to Qdrant"
```

---

## Symptom: `curl http://127.0.0.1:6333` returns 401

### Root cause

Qdrant has API key authentication enabled. Requests without `api-key` header return 401.

### Fix

```bash
QKEY=$(docker exec docker-qdrant-1 printenv QDRANT__SERVICE__API_KEY)
curl -s -H "api-key: $QKEY" http://127.0.0.1:6333/collections
```

---

## Symptom: `curl http://127.0.0.1:*` returns 502 Bad Gateway

### Root cause

Clash proxy (127.0.0.1:7897) intercepts localhost traffic when `http_proxy` is set. The proxy doesn't know how to route internal services.

### Fix

Add to `~/.hermes/.env`:
```bash
no_proxy=localhost,127.0.0.1
NO_PROXY=localhost,127.0.0.1
```

Temporary: `unset http_proxy https_proxy` before curl commands.

---

## Symptom: Redis connection refused / AUTH error

### Root cause

Redis requires password authentication. Direct `redis-cli` or raw socket PING without AUTH fails.

### Fix

```bash
# Get password from running container
REDIS_PW=$(docker exec docker-redis-1 printenv REDIS_PASSWORD)

# Test with auth
docker exec docker-redis-1 redis-cli -a "$REDIS_PW" PING
# Expected: PONG
```

---

## Symptom: EMBEDDING_DIMS mismatch warning

### Root cause

`docker-compose.yml` defaults `EMBEDDING_DIMS=4096`, but the actual Qdrant collection was created with 1024-dim vectors (Qwen text-embedding-v4). This is harmless while the collection exists — `ensure_collection` skips creation. But if the collection is ever deleted and recreated, 1024-dim embeddings would fail to insert into a 4096-dim collection.

### Fix

In `~/memory-os/docker/docker-compose.yml`, change:
```yaml
- EMBEDDING_DIMS: "${EMBEDDING_DIMS:-4096}"
+ EMBEDDING_DIMS: "${EMBEDDING_DIMS:-1024}"
```

Verify match:
```bash
COLL_DIMS=$(curl -s -H "api-key: $QKEY" http://127.0.0.1:6333/collections/knowledge_base \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['config']['params']['vectors']['dense']['size'])")
echo "Collection dims: $COLL_DIMS"
```

---

## Symptom: Embedding API returns errors or wrong dimensions

### Root cause

The embedding model MUST be an embedding model, not a chat model. Common mistakes:

- `deepseek-v4-flash` — CHAT model, has NO embedding endpoint
- `deepseek-chat` — CHAT model
- Switching providers without updating the model name

### Correct embedding models

| Provider | Model | Dims | API Base |
|----------|-------|------|----------|
| Qwen DashScope (recommended) | `text-embedding-v4` | 1024 | `dashscope.aliyuncs.com/compatible-mode/v1` |
| OpenRouter | `qwen/qwen3-embedding-8b` | 4096 | `openrouter.ai/api/v1` |

### Check current config

```bash
grep -E 'EMBEDDING_(MODEL|API_KEY|BASE_URL)' ~/.hermes/.env
```

Expected (Qwen DashScope direct):
```
EMBEDDING_API_KEY=sk-...
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4
```

### Verify embedding works

```bash
python3 -c "
import requests, os, json
key = os.environ.get('EMBEDDING_API_KEY', '')
base = os.environ.get('EMBEDDING_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
model = os.environ.get('EMBEDDING_MODEL', 'text-embedding-v4')
r = requests.post(f'{base}/embeddings',
    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    json={'model': model, 'input': 'test'},
    timeout=30)
dims = len(r.json()['data'][0]['embedding'])
print(f'{model}: {r.status_code}, {dims} dims')
"
# Expected: text-embedding-v4: 200, 1024 dims
```

### ⚠️ DeepSeek has NO embedding models

All DeepSeek models (v4-flash, v4-pro, chat) are **chat-only**. If `EMBEDDING_MODEL` is set to any DeepSeek model, the API returns `400 Bad Request` or hangs. Use Qwen DashScope or OpenRouter for embeddings.

### ⚠️ Dimension must match Qdrant collection

If you switch from Qwen (1024-dim) to OpenRouter (4096-dim), you MUST recreate the Qdrant collection — existing 1024-dim vectors cannot be searched against 4096-dim queries. The mismatch causes `qdrant_search` to fail silently.

### ⚠️ Python code defaults silently override docker-compose env

**The trap:** Setting `EMBEDDING_API_BASE=dashscope...` in docker-compose `.env` or `docker-compose.yml` is NOT enough. Five Python files hardcode OpenRouter as the fallback default via `os.environ.get("KEY", "openrouter-fallback")`. If the env var is ever missing (config drift, docker restart without .env sourcing), the system silently falls back to OpenRouter — no warning, no error.

**Files that hardcode OpenRouter defaults (all in `~/memory-os/`):**

| File | Hardcoded Default |
|------|-------------------|
| `docker/worker/services/embedding.py` | `EMBEDDING_API_BASE` → `openrouter.ai/api/v1`<br>`EMBEDDING_MODEL` → `qwen/qwen3-embedding-8b`<br>`EMBEDDING_DIMS` → `4096`<br>Auth: `if "openrouter" in base` branch |
| `docker/worker/services/local_qdrant.py` | `EMBEDDING_DIMS` → `4096` |
| `scripts/context_enhancer.py` | `EMBEDDING_URL` → `openrouter.ai/api` + `/v1/embeddings`<br>`EMBEDDING_MODEL` → `qwen/qwen3-embedding-8b`<br>(Hermes uses this via symlink at `~/.hermes/plugins/icarus/scripts/`) |
| `scripts/bulk_wiki_ingest.py` | `EMBEDDING_MODEL` → `qwen/qwen3-embedding-8b` |
| `scripts/pre_validator.py` | `EMBEDDING_MODEL` → `qwen/qwen3-embedding-8b` (hardcoded, no env var) |

**Permanent fix — change the Python defaults themselves:**

```bash
# Change all defaults from OpenRouter → Qwen DashScope
# EMBEDDING_API_BASE: openrouter.ai/api/v1 → dashscope.aliyuncs.com/compatible-mode/v1
# EMBEDDING_MODEL: qwen/qwen3-embedding-8b → text-embedding-v4
# EMBEDDING_DIMS: 4096 → 1024
# Auth: "if openrouter" branch → EMBEDDING_API_KEY first, OpenRouter fallback
```

Then rebuild the Worker image:
```bash
cd ~/memory-os/docker && docker compose up -d --force-recreate --build worker
```

**Verify defaults are correct:**
```bash
docker exec docker-worker-1 python3 -c "
from services.embedding import EMBEDDING_API_BASE, EMBEDDING_MODEL, EMBEDDING_DIMS
print(f'BASE: {EMBEDDING_API_BASE}')
print(f'MODEL: {EMBEDDING_MODEL}')
print(f'DIMS: {EMBEDDING_DIMS}')
"
# Expected: dashscope.aliyuncs.com/compatible-mode/v1, text-embedding-v4, 1024
```

**Also check the Hermes-side context enhancer** (symlinked from `~/memory-os/scripts/`):
```bash
grep -E 'EMBEDDING_URL|EMBEDDING_MODEL' ~/.hermes/plugins/icarus/scripts/context_enhancer.py
# Expected: dashscope.aliyuncs.com, text-embedding-v4
```

**Why this keeps happening:** docker-compose env vars are a surface-level fix. Code defaults are the source of truth. Always fix both layers.

---

---

## Symptom: Wiki files not searchable in Qdrant (only memory entries indexed)

### Root cause

The wiki continuous ingestion pipeline (`wiki_continuous_ingest.py`) exists but is **configured for the wrong path by default**:
- `WIKI_ROOT` defaults to `~/Vault/wiki` (Karpathy convention) — doesn't exist
- Docker mount `${MEMORY_OS_WIKI_PATH:-./wiki}` resolves to `~/memory-os/docker/wiki/` — empty directory
- 300+ `.md` files in `~/wiki` never reach Qdrant

### Fix

```bash
# 1. Set correct wiki path in docker-compose .env
echo "MEMORY_OS_WIKI_PATH=/Users/$(whoami)/wiki" >> ~/memory-os/docker/.env

# 2. Restart worker to remount
cd ~/memory-os/docker && docker compose up -d --force-recreate worker

# 3. Clear stale state file, run full ingest
rm -f ~/.hermes/wiki_ingest_state.json
WIKI_ROOT=~/wiki REDIS_PASSWORD=$(docker exec docker-redis-1 printenv REDIS_PASSWORD) \
  python3 ~/memory-os/scripts/wiki_continuous_ingest.py
```

### Verify

```bash
QKEY=$(docker exec docker-qdrant-1 printenv QDRANT__SERVICE__API_KEY)
curl -s -H "api-key: $QKEY" http://127.0.0.1:6333/collections/knowledge_base \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['result']; \
     print(f'points={d[\"points_count\"]}')"
# Before fix: ~49 (memory migration only)
# After fix: ~350 (memory + wiki)
```

### Set up periodic incremental sync

```bash
# Add to cron (runs every 30 min)
WIKI_ROOT=~/wiki REDIS_PASSWORD=$(docker exec docker-redis-1 printenv REDIS_PASSWORD) \
  python3 ~/memory-os/scripts/wiki_continuous_ingest.py
```

---

## Symptom: Worker embedding calls fail with ConnectError despite correct env

### Root cause

Docker daemon injects host proxy settings (`http_proxy=http://127.0.0.1:7897`) into containers. The container tries to route DashScope API calls through `127.0.0.1:7897`, where no Clash proxy exists — connection refused.

### Fix

In `~/memory-os/docker/docker-compose.yml`, explicitly clear proxy vars in the worker section:

```yaml
environment:
  http_proxy: ""
  https_proxy: ""
  HTTP_PROXY: ""
  HTTPS_PROXY: ""
  no_proxy: "qdrant,redis,localhost,127.0.0.1,dashscope.aliyuncs.com"
  NO_PROXY: "qdrant,redis,localhost,127.0.0.1,dashscope.aliyuncs.com"
```

Then rebuild: `docker compose up -d --force-recreate --build worker`

---

## All Services Health Check (one command)

```bash
echo "=== Docker ===" && docker ps --filter name=docker- --format "table {{.Names}}\t{{.Status}}"
echo "=== Qdrant ===" && curl -s http://127.0.0.1:6333/healthz
echo "=== Redis ===" && docker exec docker-redis-1 redis-cli -a "$(docker exec docker-redis-1 printenv REDIS_PASSWORD)" PING
echo "=== Worker ===" && docker logs docker-worker-1 --tail 3 | grep -E "ARQ|reflection|health"
```

---

## Disk cleanup: redundant session dumps

`~/.hermes/sessions/` contains 200+ JSON dump files (~87MB) that duplicate data already in `state.db`. Safe to remove:

```bash
du -sh ~/.hermes/sessions/
# rm ~/.hermes/sessions/*.json   # uncomment to execute
```

---

## Memory usage tracking

```bash
# Hermes native memory usage
sqlite3 ~/.hermes/memory_store.db "SELECT category, count(*) FROM facts GROUP BY category;"

# Fabric entries
sqlite3 ~/.hermes/state.db "SELECT type, status, count(*) FROM fabric_index GROUP BY type, status;"

# Session stats
sqlite3 ~/.hermes/state.db "SELECT count(DISTINCT session_id) as sessions, count(*) as messages FROM messages;"
```

---

## Symptom: BM25 sparse model re-downloads on every container rebuild

### Root cause

FastEmbed caches models to `/tmp/fastembed_cache/` inside the container. This path is on the ephemeral container filesystem — lost on `--force-recreate` or Mac reboot.

### Fix

Add a named Docker volume to persist the cache:

```yaml
# docker-compose.yml — worker volumes section
volumes:
  - hf_cache:/tmp/fastembed_cache

# docker-compose.yml — volumes declaration
volumes:
  hf_cache:
```

Also pre-create the cache directory with correct ownership in the Dockerfile (required because named volumes inherit permissions from the image):

```dockerfile
RUN mkdir -p /tmp/fastembed_cache && chown appuser:appuser /tmp/fastembed_cache
```

### Verify

```bash
# Rebuild with volume
docker compose up -d --force-recreate --build worker

# Check model is cached
docker exec docker-worker-1 du -sh /tmp/fastembed_cache/
# Expected: ~160K, 21 files

# Force-recreate again to verify persistence
docker compose up -d --force-recreate worker
docker logs docker-worker-1 | grep "BM25"
# Expected: "BM25 sparse model pre-warmed" — instant, no download
```
