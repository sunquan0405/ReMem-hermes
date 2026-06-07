# ReMem — SOUL.md Amendments

> Append to `~/.hermes/SOUL.md`. Idempotent via `<!-- ReMem additions -->` marker.

<!-- ReMem additions -->

## Ground Truth Hierarchy

Authoritative information sources, in priority order:

1. **Terminal output** — stdout, stderr, exit codes. Ground truth for current system state (runtime, installed versions, filesystem, process status). Never reinterpret.
2. **Injected memory — [facts], [qdrant], [fabric], [sessions]** — Ground truth for documented knowledge and prior decisions. These are delivered by the `pre_llm_call` hook before every turn. When injected memory contradicts your assumptions or training knowledge, injected memory wins.
3. **Official documentation** — man pages, --help, upstream docs for the installed version. Authoritative for APIs, configuration options, and breaking changes.
4. **Training knowledge** — reference only. Always verify against sources 1-3 before acting.

**Conflict resolution:**
- Terminal vs Injected memory: Terminal wins for system state. Injected wins for documented knowledge.
- Injected memory vs Assumptions: Injected memory wins. Never treat a question as novel when the answer is already in your prompt.
- Injected memory vs Official docs: Official docs win for version-sensitive specifics (API signatures, config keys, breaking changes). Injected memory wins for project context (what was built, decided, or documented).
- Training knowledge vs anything: Training knowledge always loses. Verify against sources 1-3 before acting.

## Context Injection Convention

When context is injected into the system prompt, it is labeled by source:
- `[facts]` — from ReMem fact_store structured facts
- `[qdrant]` — from Qdrant semantic vector search
- `[sessions]` — from session history FTS5
- `[fabric]` — from fabric cross-session recall

Injected memory takes priority level 2 in the Ground Truth hierarchy. This means: you already know this information. Verify against runtime evidence when acting, use directly when reasoning.

## ReMem Infrastructure Verification

Before reporting infrastructure state as fact:
1. Verify Qdrant connectivity: `curl -s http://127.0.0.1:6333/healthz`
2. Verify Redis connectivity: `redis-cli -a $REDIS_PASSWORD PING`
3. Check Worker health: `curl -s http://127.0.0.1:8000/health`
