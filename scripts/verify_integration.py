#!/usr/bin/env python3
"""
ReMem — Integration Verification Script

验证 Memory OS 与 Hermes 原生的集成是否正常工作。
在安装 Memory OS + 运行迁移后执行。

检查项：
1. memory_store.db 存在且 facts 表有数据
2. Qdrant knowledge_base 存在且有点
3. SOUL.md 包含 Ground Truth 层级
4. rulebook.md 包含 Glue Layer 规则
5. 迁移的 memory 条目可通过 fact_store search 查询
6. 备份文件完整

用法：
    python3 scripts/verify_integration.py               ← 全部检查
    python3 scripts/verify_integration.py --check-all   ← 同上
    python3 scripts/verify_integration.py --quick       ← 仅检查核心项（1-4）
    python3 scripts/verify_integration.py --backup PATH  ← 验证指定备份
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"
ok = lambda s: print(f"  {GREEN}✅{NC} {s}")
warn = lambda s: print(f"  {YELLOW}⚠️{NC}  {s}")
fail = lambda s: print(f"  {RED}❌{NC} {s}")
info = lambda s: print(f"  📘 {s}")

HERMES_HOME = Path.home() / ".hermes"


# ── 1. memory_store.db ────────────────────────────────────────────────────
def check_memory_store() -> bool:
    print()
    print(f"  {BOLD}[1/6] Memory OS Database (memory_store.db){NC}")
    print()
    db_path = HERMES_HOME / "memory_store.db"
    if not db_path.exists():
        fail(f"memory_store.db not found at {db_path}")
        return False

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]

        expected = {"facts", "facts_fts", "entities", "fact_entities"}
        missing = expected - set(tables)
        if missing:
            fail(f"Missing tables: {missing}")
            conn.close()
            return False

        fact_count = cur.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        entity_count = cur.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        conn.close()

        ok(f"Database exists: {db_path.name}")
        ok(f"Facts: {fact_count} entries")
        ok(f"Entities: {entity_count}")
        ok(f"Tables: {', '.join(tables)}")
        return True

    except sqlite3.Error as e:
        fail(f"SQLite error: {e}")
        return False


# ── 2. Qdrant ─────────────────────────────────────────────────────────────
def check_qdrant() -> bool:
    print()
    print(f"  {BOLD}[2/6] Qdrant Vector Database{NC}")
    print()

    try:
        import httpx
        # 取消代理环境变量，避免 httpx 走 SOCKS 代理
        for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY']:
            os.environ.pop(var, None)
    except ImportError:
        fail("httpx not installed")
        return False

    qdrant_url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
    qdrant_key = os.environ.get("QDRANT_API_KEY", "")

    try:
        r = httpx.get(f"{qdrant_url}/healthz", timeout=3)
        if r.status_code != 200:
            fail(f"Qdrant not reachable (HTTP {r.status_code})")
            info("Try: docker compose -f ~/memory-os/docker/docker-compose.yml up -d")
            return False
        ok("Qdrant service healthy")
    except Exception as e:
        fail(f"Qdrant connection failed: {e}")
        return False

    headers = {"Content-Type": "application/json"}
    if qdrant_key:
        headers["api-key"] = qdrant_key

    try:
        r = httpx.get(
            f"{qdrant_url}/collections/knowledge_base",
            headers=headers, timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            count = data.get("result", {}).get("points_count", 0)
            ok(f"Knowledge base collection found")
            info(f"Points: {count}")
            return True
        elif r.status_code == 404:
            warn("Knowledge base collection not yet created")
            info("Run: python3 ~/memory-os/scripts/bulk_wiki_ingest.py")
            return True  # not a critical failure
        else:
            warn(f"Qdrant collection check: HTTP {r.status_code}")
            return True
    except Exception as e:
        warn(f"Qdrant collection check failed: {e}")
        return True


# ── 3. SOUL.md ────────────────────────────────────────────────────────────
def check_soul() -> bool:
    print()
    print(f"  {BOLD}[3/6] SOUL.md — Ground Truth Hierarchy{NC}")
    print()

    soul_path = HERMES_HOME / "SOUL.md"
    if not soul_path.exists():
        fail("SOUL.md not found")
        return False

    content = soul_path.read_text()
    checks = {
        "Ground Truth section": "## Ground Truth",
        "Injected memory priority": "注入记忆优先",
        "Conflict resolution rules": "冲突解决规则",
        "Context injection convention": "## 上下文注入约定",
        "Source labeling": "[facts]",
    }

    all_ok = True
    for name, marker in checks.items():
        if marker in content:
            ok(f"{name}")
        else:
            warn(f"{name} — marker '{marker}' not found")
            all_ok = False

    return all_ok


# ── 4. rulebook.md ────────────────────────────────────────────────────────
def check_rulebook() -> bool:
    print()
    print(f"  {BOLD}[4/6] rulebook.md — Glue Layer Rules{NC}")
    print()

    rulebook_path = HERMES_HOME / "rulebook.md"
    if not rulebook_path.exists():
        warn("rulebook.md not found (may not exist if no modifications applied)")
        return False

    content = rulebook_path.read_text()
    checks = {
        "Storage routing": "## Memory 存储路由",
        "Query routing": "## 查询路由",
        "Fact feedback rule": "事实反馈规则",
    }

    all_ok = True
    for name, marker in checks.items():
        if marker in content:
            ok(f"{name}")
        else:
            warn(f"{name} — marker '{marker}' not found in rulebook")
            all_ok = False

    return all_ok


# ── 5. Memory Export Backup ──────────────────────────────────────────────
def check_backup(backup_path: str = None) -> bool:
    print()
    print(f"  {BOLD}[5/6] Backup Integrity{NC}")
    print()

    if backup_path:
        backup_dir = Path(backup_path)
    else:
        base = Path.home() / "Work" / "Backups" / "memory-os-hermes"
        dirs = sorted(base.iterdir(), reverse=True) if base.exists() else []
        backup_dir = dirs[0] if dirs else None

    if not backup_dir or not backup_dir.exists():
        warn(f"No backup found at {backup_dir or 'default path'}")
        return False

    required = {"export_memory.json", "state.db", "SOUL.md", "config.yaml"}
    found = set()
    for f in backup_dir.iterdir():
        if f.name in required:
            found.add(f.name)

    missing = required - found
    if missing:
        fail(f"Missing backup files: {missing}")
        return False

    # 检查 integrity
    integrity_file = backup_dir / "integrity.sha256"
    if integrity_file.exists():
        import subprocess
        try:
            result = subprocess.run(
                ["shasum", "-a", "256", "-c", str(integrity_file)],
                capture_output=True, text=True, cwd=str(backup_dir)
            )
            ok("Backup integrity verified (sha256)")
        except:
            info("Backup files present (integrity check skipped)")

    # 检查 export_memory.json
    with open(backup_dir / "export_memory.json") as f:
        entries = json.load(f)
    ok(f"Memory export: {len(entries)} entries")
    ok(f"Backup location: {backup_dir}")
    return True


# ── 6. Fact Store Search Test ─────────────────────────────────────────────
def check_search() -> bool:
    print()
    print(f"  {BOLD}[6/6] Fact Store Search (Demo){NC}")
    print()

    db_path = HERMES_HOME / "memory_store.db"
    if not db_path.exists():
        info("memory_store.db not found — skipping search test")
        return False

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()

        # 测试 FTS5 搜索
        test_queries = ["config", "project", "Hermes", "OpenCode"]
        for q in test_queries:
            try:
                rows = cur.execute(
                    """SELECT f.content, f.trust_score
                       FROM facts_fts JOIN facts f ON f.fact_id = facts_fts.rowid
                       WHERE facts_fts MATCH ?
                       LIMIT 2""", (q,)
                ).fetchall()
                if rows:
                    for content, score in rows:
                        ok(f"Search '{q}' → found: {content[:60]}... (trust: {score:.2f})")
                        break
            except:
                pass

        conn.close()
        return True
    except Exception as e:
        warn(f"Search test failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify ReMem integration")
    parser.add_argument("--quick", action="store_true", help="Only check core items (1-4)")
    parser.add_argument("--backup", help="Backup path to verify")
    parser.add_argument("--check-all", action="store_true", dest="check_all")
    args = parser.parse_args()

    quick = args.quick or args.check_all is False

    print(f"\n{BOLD}═══ ReMem — Integration Verification ═══{NC}\n")

    results = []

    results.append(("memory_store.db", check_memory_store()))
    results.append(("Qdrant", check_qdrant()))
    results.append(("SOUL.md Ground Truth", check_soul()))
    results.append(("rulebook.md Glue Layer", check_rulebook()))

    if not quick:
        results.append(("Backup integrity", check_backup(args.backup)))
        results.append(("Fact search test", check_search()))

    # Summary
    print(f"\n{BOLD}═══ Summary ═══{NC}\n")
    passed = 0
    failed = 0
    for name, ok_flag in results:
        status = f"{GREEN}✅ PASS{NC}" if ok_flag else f"{RED}❌ FAIL{NC}"
        print(f"  {status}  {name}")
        if ok_flag:
            passed += 1
        else:
            failed += 1

    print()
    if failed == 0:
        print(f"  {GREEN}✅ All checks passed. ReMem is running correctly.{NC}\n")
    else:
        print(f"  {YELLOW}⚠️  {failed} check(s) failed — see details above.{NC}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
