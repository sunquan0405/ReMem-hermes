# Memory Health Check Script

Write a Python script `scripts/memory_health_check.py` that runs a full health check of the Memory OS stack.

## Requirements

### 1. Docker services check
- Check docker-qdrant-1, docker-redis-1, docker-worker-1 are running and healthy
- Use `docker ps --filter name=docker-` or `docker inspect`

### 2. Qdrant check
- Hit `/healthz` endpoint
- Get collection stats from `/collections/knowledge_base` (needs API key from container env)
- Report: points_count, indexed_vectors_count, status
- Warn if indexed < points (index not built)

### 3. Redis check
- Get password from container env
- Send AUTH + PING via raw socket
- Report DBSIZE

### 4. Worker check
- Read last 5 lines of docker logs
- Check for "ARQ worker" and "Connected to Qdrant"

### 5. Data stores
- Connect to ~/.hermes/memory_store.db: count facts, list categories
- Connect to ~/.hermes/state.db: count sessions, messages, fabric entries

### 6. Tool verification
- Use the fact_store and qdrant_search tools to run a quick test query
- Report if results come back

### Output format
Print a clean summary table at the end, like:
```
=== Memory System Health ===
Qdrant:   ✓ healthy (49 pts, 49 indexed, green)
Redis:    ✓ healthy (DBSIZE=1)
Worker:   ✓ healthy (ARQ worker running)
Facts:    49 (26 config, 10 project, 7 pref, 3 skill, 2 fix, 1 workflow)
Sessions: 288 sessions, 28731 messages
Fabric:   22 entries (18 session, 2 resolution, 1 decision, 1 note)
Overall:  ✓ ALL SYSTEMS HEALTHY
```

### Dependencies
- Use only stdlib (subprocess, sqlite3, socket, json, urllib)
- No pip installs needed
- Handle missing containers/services gracefully (report DOWN, don't crash)
