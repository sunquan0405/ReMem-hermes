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
