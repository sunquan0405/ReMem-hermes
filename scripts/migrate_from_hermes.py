#!/usr/bin/env python3
"""
ReMem — Memory Migration Script

读取从 Hermes 导出的 memory 条目 JSON 文件，写入 Memory OS 的：
1. memory_store.db（facts 表，SQLite + FTS5）
2. Qdrant knowledge_base（向量集合，如果可用）

用法：
    python3 scripts/migrate_from_hermes.py                         # 从最新备份读取
    python3 scripts/migrate_from_hermes.py --input export.json     # 指定文件
    python3 scripts/migrate_from_hermes.py --dry-run               # 预览，不写入
    python3 scripts/migrate_from_hermes.py --skip-qdrant           # 跳过 Qdrant
"""

import json
import os
import sqlite3
import sys
import re
from datetime import datetime
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────────────────
DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_MEMORY_DB = DEFAULT_HERMES_HOME / "memory_store.db"
DEFAULT_BACKUP_DIR = Path.home() / "Work" / "Backups" / "memory-os-hermes"

# ── 颜色 ──────────────────────────────────────────────────────────────────
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
NC = "\033[0m"
ok = lambda s: print(f"  {GREEN}✅{NC} {s}")
warn = lambda s: print(f"  {YELLOW}⚠️{NC}  {s}")
fail = lambda s: print(f"  {RED}❌{NC} {s}")
info = lambda s: print(f"  📘 {s}")

# ── Tag → Category 映射 ───────────────────────────────────────────────────
TAG_TO_CATEGORY = {
    "config": "config",
    "proj": "project",
    "project": "project",
    "pref": "preference",
    "skill": "skill",
    "workflow": "workflow",
    "fix": "fix",
}

def infer_category(tag: str) -> str:
    return TAG_TO_CATEGORY.get(tag, "general")

def infer_entities(content: str) -> list:
    """从 memory 条目中提取实体名"""
    entities = []
    # 从 [tag:xxx] 格式提取
    m = re.search(r'\[tag:(\w+)\]', content)
    if m:
        entities.append(m.group(1))
    # 从 [type:xxx] 格式提取
    m = re.search(r'\[type:(\w+)\]', content)
    if m:
        entities.append(m.group(1))
    # 从 [path:...] 格式提取
    m = re.search(r'\[path:([^\]]+)\]', content)
    if m:
        path_name = Path(m.group(1)).name
        entities.append(path_name)
    # 从 [name:xxx] 格式提取
    m = re.search(r'\[([\w-]+)\]', content)
    if m:
        entities.append(m.group(1))
    return entities if entities else ["general"]


def find_latest_backup() -> Path:
    """找最新的备份目录中的 export_memory.json"""
    if not DEFAULT_BACKUP_DIR.exists():
        return None
    dirs = sorted(DEFAULT_BACKUP_DIR.iterdir(), reverse=True)
    for d in dirs:
        if d.is_dir():
            f = d / "export_memory.json"
            if f.exists():
                return f
    return None


def read_memory_entries(input_path: Path) -> list:
    """读取 memory 导出 JSON，过滤掉 debug 标记"""
    with open(input_path, "r") as f:
        entries = json.load(f)
    # 过滤 debug 标记和空条目
    filtered = []
    for e in entries:
        content = e.get("content", "").strip()
        tag = e.get("tag", "")
        if not content:
            continue
        if tag == "debug" or content.startswith("Hermes memory export marker"):
            continue
        filtered.append(e)
    return filtered


def migrate_to_sqlite(entries: list, db_path: Path, dry_run: bool = False) -> int:
    """写入 memory_store.db facts 表"""
    if dry_run:
        info(f"[DRY-RUN] Would insert {len(entries)} facts into {db_path}")
        return len(entries), 0

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 确保表存在
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'general',
            tags TEXT DEFAULT '',
            trust_score REAL DEFAULT 0.5,
            retrieval_count INTEGER DEFAULT 0,
            helpful_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hrr_vector BLOB
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
            USING fts5(content, tags, content=facts, content_rowid=fact_id);
        CREATE TABLE IF NOT EXISTS entities (
            entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            entity_type TEXT DEFAULT 'unknown',
            aliases TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS fact_entities (
            fact_id INTEGER REFERENCES facts(fact_id),
            entity_id INTEGER REFERENCES entities(entity_id),
            PRIMARY KEY (fact_id, entity_id)
        );
    """)

    inserted = 0
    skipped = 0
    now = datetime.utcnow().isoformat()

    for entry in entries:
        content = entry.get("content", "").strip()
        tag = entry.get("tag", "general")
        category = infer_category(tag)
        entities = infer_entities(content)

        try:
            cur.execute(
                """INSERT OR IGNORE INTO facts (content, category, tags, trust_score, created_at, updated_at)
                   VALUES (?, ?, ?, 1.0, ?, ?)""",
                (content, category, tag, now, now)
            )
            if cur.rowcount > 0:
                fact_id = cur.lastrowid
                # 关联实体
                for ename in entities:
                    cur.execute(
                        "INSERT OR IGNORE INTO entities (name, entity_type) VALUES (?, ?)",
                        (ename, category)
                    )
                    cur.execute("SELECT entity_id FROM entities WHERE name = ?", (ename,))
                    eid_row = cur.fetchone()
                    if eid_row:
                        cur.execute(
                            "INSERT OR IGNORE INTO fact_entities (fact_id, entity_id) VALUES (?, ?)",
                            (fact_id, eid_row[0])
                        )
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as e:
            warn(f"SQLite error: {e}")

    conn.commit()

    # 重建 FTS5 索引
    try:
        cur.executescript("""
            INSERT INTO facts_fts(facts_fts) VALUES('rebuild');
        """)
    except sqlite3.Error:
        pass

    conn.close()
    return inserted, skipped


def migrate_to_qdrant(entries: list, dry_run: bool = False) -> int:
    """写入 Qdrant knowledge_base（可选）"""
    if dry_run:
        info(f"[DRY-RUN] Would insert {len(entries)} points into Qdrant")
        return len(entries)

    # 检查 Qdrant 是否可用
    try:
        import httpx
        qdrant_url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
        qdrant_key = os.environ.get("QDRANT_API_KEY", "")

        r = httpx.get(f"{qdrant_url}/healthz", timeout=3)
        if r.status_code != 200:
            warn(f"Qdrant not available ({r.status_code}) — skipping vector migration")
            return 0
    except Exception as e:
        warn(f"Qdrant unreachable ({e}) — skipping vector migration. "
             "Qdrant will index content automatically via the ARQ worker.")
        return 0

    info("Qdrant healthy, inserting points...")

    # 准备批量写入
    points = []
    for i, entry in enumerate(entries):
        content = entry.get("content", "").strip()
        tag = entry.get("tag", "general")
        category = infer_category(tag)
        now = int(datetime.utcnow().timestamp())
        points.append({
            "id": i + 1,
            "vector": {
                "dense": None,  # 让 worker 后续计算
                "sparse": None,  # 或通过 embedding API 即时计算
            },
            "payload": {
                "content": content,
                "category": category,
                "tag": tag,
                "source": "hermes_memory_migration",
                "created_at": now,
            }
        })

    try:
        headers = {"Content-Type": "application/json"}
        if qdrant_key:
            headers["api-key"] = qdrant_key

        # 先检查 collection 是否存在
        r = httpx.get(
            f"{qdrant_url}/collections/knowledge_base",
            headers=headers,
            timeout=5
        )
        if r.status_code == 404:
            info("Creating knowledge_base collection...")
            create_payload = {
                "vectors": {
                    "dense": {"size": 4096, "distance": "Cosine"},
                },
                "sparse_vectors": {"sparse": {}}
            }
            r2 = httpx.put(
                f"{qdrant_url}/collections/knowledge_base",
                headers=headers,
                json=create_payload,
                timeout=10
            )
            if r2.status_code not in (200, 201):
                warn(f"Failed to create collection: {r2.text}")
                return 0
            ok("Collection created")

        # 批量 upsert
        upsert_payload = {"points": points}
        r = httpx.put(
            f"{qdrant_url}/collections/knowledge_base/points",
            headers=headers,
            json=upsert_payload,
            timeout=30
        )
        if r.status_code in (200, 201):
            inserted = len(points)
            ok(f"Qdrant upserted {inserted} points")
            return inserted
        else:
            warn(f"Qdrant upsert failed: {r.status_code} {r.text[:200]}")
            return 0

    except Exception as e:
        warn(f"Qdrant error: {e}")
        return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate Hermes memory to Memory OS")
    parser.add_argument("--input", help="Path to export_memory.json (default: latest backup)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--skip-qdrant", action="store_true", help="Skip Qdrant migration")
    args = parser.parse_args()

    # 确定输入文件
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = find_latest_backup()

    if not input_path or not input_path.exists():
        fail(f"Export file not found. Either specify --input or run backup.sh first")
        sys.exit(1)

    info(f"Reading memory export: {input_path}")
    entries = read_memory_entries(input_path)
    info(f"Found {len(entries)} memory entries (excluding debug markers)")

    if len(entries) == 0:
        warn("No memory entries to migrate")
        return

    # 按 tag 统计
    tag_counts = {}
    for e in entries:
        t = e.get("tag", "unknown")
        tag_counts[t] = tag_counts.get(t, 0) + 1
    info("Entry breakdown:")
    for tag, count in sorted(tag_counts.items()):
        info(f"  {tag}: {count}")

    # ── 写入 SQLite ──
    db_path = Path(os.environ.get("MEMORY_STORE_PATH", str(DEFAULT_MEMORY_DB)))
    inserted, skipped = migrate_to_sqlite(entries, db_path, args.dry_run)

    if not args.dry_run:
        ok(f"SQLite: {inserted} inserted, {skipped} skipped (duplicates)")
    else:
        info(f"[DRY-RUN] SQLite would insert {inserted} facts")

    # ── 写入 Qdrant ──
    if not args.skip_qdrant:
        qdrant_count = migrate_to_qdrant(entries, args.dry_run)
    else:
        qdrant_count = 0

    # ── 汇总 ──
    print()
    print("═══ Migration Summary ═══")
    print(f"  Total entries:     {len(entries)}")
    print(f"  SQLite inserted:   {inserted}")
    print(f"  SQLite skipped:    {skipped} (already exist)")
    print(f"  Qdrant inserted:   {qdrant_count}")
    print()

    if args.dry_run:
        print("Dry-run complete. Run without --dry-run to execute.")
    else:
        print("Migration complete. Start a new Hermes session to test:")
        print("  '查一下我存过的记忆'")
        print("  '查一下 fact_store 里有没有我的配置信息'")


if __name__ == "__main__":
    main()
