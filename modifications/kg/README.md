# Knowledge Graph — 实体关系记忆层

> 版本: v1.0 | 集成时间: 2026-06-09

## 概述

在 ReMem 现有的 4 层记忆注入（fabric + Qdrant + sessions + facts）基础上新增第 5 层：**Knowledge Graph（知识图谱）**。将实体之间的显式关系（`Hermes --[uses]--> DeepSeek`）以 SQLite 边的形式存储，通过 `pre_llm_call` hook 自动注入对话上下文。

## 解决的问题

| 之前 | 之后 |
|------|------|
| 实体关系只能靠语义搜索隐式推断 | 实体关系以 SQL 行显式存储，精确查询 |
| 问"我的项目用了什么"需要 LLM 读全文总结 | 直接查 entity_relations 表返回结构化的关系图 |
| 没有时间维度的实体生命周期 | validity window 支持实体过期 (valid_until) |

## 改动文件

| 文件 | 位置 (安装后) | 说明 |
|------|-------------|------|
| `setup_db.py` | `~/memory-os/setup/setup_db.py` | 新增 `entity_relations` 表 DDL |
| `knowledge_graph.py` | `~/memory-os/services/knowledge_graph.py` | 新建，KG 查询/写入服务 |
| `hooks.py` | `~/memory-os/icarus/hooks.py` | pre_llm_call 第 5 路注入 + session-end 实体提取 |
| `backfill_kg.py` | `~/memory-os/scripts/backfill_kg.py` | 新建，历史数据回填脚本 |

## 安装步骤

```bash
# 1. 将文件复制到 memory-os 对应目录
cp modifications/kg/knowledge_graph.py ~/memory-os/services/knowledge_graph.py
cp modifications/kg/setup_db.py ~/memory-os/setup/setup_db.py
cp modifications/kg/hooks.py ~/memory-os/icarus/hooks.py
cp scripts/backfill_kg.py ~/memory-os/scripts/backfill_kg.py

# 2. 初始化数据库表（如果 memory_store.db 已存在）
python3 -c "
import sqlite3
from pathlib import Path
db = Path.home() / '.hermes' / 'memory_store.db'
conn = sqlite3.connect(str(db))
conn.execute('''CREATE TABLE IF NOT EXISTS entity_relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES entities(entity_id),
    relation TEXT NOT NULL,
    object_id INTEGER NOT NULL REFERENCES entities(entity_id),
    valid_from TEXT, valid_until TEXT,
    confidence REAL DEFAULT 0.5, source TEXT,
    created_at TEXT DEFAULT (datetime(\"now\"))
)''')
conn.execute('CREATE INDEX IF NOT EXISTS idx_er_subject ON entity_relations(subject_id)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_er_object ON entity_relations(object_id)')
conn.execute('CREATE INDEX IF NOT EXISTS idx_er_relation ON entity_relations(relation)')
conn.commit(); conn.close()
print('entity_relations table created')
"

# 3. 回填历史数据
cd ~/memory-os && python3 scripts/backfill_kg.py
```

## 用法

安装后自动生效，无需额外配置：

- **读取**：每轮对话 pre_llm_call 自动匹配实体并注入关系（标记为 `[KG]`）
- **写入**：session-end 时 LLM 自动提取实体+关系写入 KG
- **手动查询**：`python3 services/knowledge_graph.py related "Hermes"`
- **数据回填**：`python3 scripts/backfill_kg.py`

## 架构

```sql
entity_relations (
    relation_id   INTEGER PRIMARY KEY,
    subject_id    INTEGER → entities(entity_id),
    relation      TEXT,       -- 'uses' | 'depends_on' | 'replaces' | 'part_of' | 'mentioned_in'
    object_id     INTEGER → entities(entity_id),
    valid_from    TEXT,       -- ISO 8601, null = always valid
    valid_until   TEXT,       -- null = still valid
    confidence    REAL DEFAULT 0.5,
    source        TEXT,       -- 'session:xxx' | 'fact:xxx' | 'backfill'
    created_at    TEXT
)
```

5 路记忆注入（pre_llm_call）:

```
用户消息
  ├── fabric_recall()     → 跨会话摘要
  ├── qdrant_search()     → 语义搜索
  ├── session_search()    → 会话历史 FTS5
  ├── fact_store()        → 结构化事实（首轮）
  └── knowledge_graph()   → 实体关系（新增）
```
