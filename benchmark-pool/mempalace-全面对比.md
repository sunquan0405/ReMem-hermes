# MemPalace vs ReMem Hermes Memory System — 全面对比分析

> 来源：https://github.com/MemPalace/mempalace — 55k stars, MIT, v3.4.0, Python
> 分析时间：2026-06-09
> 状态：深度分析（替换 benchmark-pool 中 18 行的占位文件）

---

## 0. 一句话定位

| 系统 | 哲学 |
|------|------|
| **MemPalace** | **Keep the original words. Just find them.** — 不 Summarize、不 Extract、不 Paraphrase。存原文，纯语义搜索。96.6% R@5 raw，零 API 调用。 |
| **ReMem (你的系统)** | **Multi-layer structured memory with routing.** — 7 层架构分拆记忆职责（facts/vectors/sessions/fabric），Glue Layer 告诉 agent 什么时候用什么工具。 |

---

## 1. 架构对比

### 1.1 整体架构

```
MemPalace                      ReMem (你的系统)
─────────────                  ─────────────────
┌──────────────────┐           ┌──────────────────┐
│    Palace         │           │   Glue Layer      │
│  ┌────────────┐   │           │  (agent routing)  │
│  │ Wings       │   │           └──────┬───────┬───┘
│  │  (人/项目)  │   │                  │       │
│  │  └ Rooms    │   │          ┌───────┘       └───────┐
│  │    (主题)   │   │          ▼                       ▼
│  └────────────┘   │   ┌──────────────┐      ┌──────────────┐
│  ┌────────────┐   │   │ Hermes Native│      │ ReMem Stack  │
│  │ Drawers     │   │   │ (10K limit)  │      │ fact_store   │
│  │ (原文块)    │   │   │              │      │ Qdrant       │
│  └────────────┘   │   │              │      │ SQLite FTS5  │
│  ┌────────────┐   │   └──────────────┘      │ Fabric       │
│  │ Knowledge   │   │                        │ Worker       │
│  │ Graph       │   │                        └──────────────┘
│  │ (SQLite)    │   │
│  └────────────┘   │
│  ┌────────────┐   │
│  │ Agent Wings│   │
│  │ + Diaries  │   │
│  └────────────┘   │
└──────────────────┘
```

### 1.2 存储后端对比

| 维度 | MemPalace | ReMem |
|------|-----------|-------|
| **默认后端** | ChromaDB (384-dim, all-MiniLM-L6-v2) | Qdrant (1024-dim, text-embedding-v4) |
| **可选后端** | sqlite_exact, pgvector | Qdrant only (单后端) |
| **可插拔性** | ✅ BaseBackend ABC，注册制 registry | ❌ 硬编码 Qdrant HTTP 调用 |
| **本地模式** | ✅ 完全离线，零 API | ⚠️ 需 Worker + 外部 embedding API |
| **embedding 维度** | 384 (Matryoshka truncatable) | 1024 |
| **embedding 模型** | EmbeddingGemma-300m (ONNX, 本地) | Qwen text-embedding-v4 (API) |
| **稀疏搜索** | ❌ 纯稠密（hybrid 是后处理） | ❌ 纯稠密 |
| **硬件加速** | CUDA/CoreML/DirectML/CPU | 不适用（API 调用） |
| **离线诉求** | ✅ 核心卖点 | ❌ 依赖外部 API |

### 1.3 数据结构对比

| 概念 | MemPalace | ReMem |
|------|-----------|-------|
| **顶层单元** | Wings（人/项目） | 无显式顶层 — Qdrant collection 平铺 |
| **子单元** | Rooms（主题） | tags / categories（metadata 字段） |
| **原文存储** | Drawers（verbatim 文本块） | 不支持 — 只有结构化的 facts 和 vectors |
| **总结层** | Closets（可选，指向原文） | Fabric entries（LLM 提取的摘要） |
| **关系图** | Knowledge Graph（SQLite, 时间窗口） | 无 |
| **实体** | 人物/项目/决策 (带 validity window) | 无显式实体模型 |
| **跨单元连接** | Tunnels（room 作为跨 wing 桥梁） | 无 — 仅平面 vector search |
| **分级记忆** | 单层（原文检索为主） | 4 层: temporary/facts/sessions/knowledge |

### 1.4 关键架构差异

**MemPalace 的核心发现**（来自 BENCHMARKS.md）：
> "Every competitive memory system uses an LLM to manage memory. MemPalace just stores the actual words and searches them with ChromaDB's default embeddings. No extraction. No summarization. No AI deciding what matters. And it scores 96.6% on LongMemEval."

**你的系统反方向走**：七层架构 + LLM 提取 + 结构化 facts + 多工具路由。更复杂但更可控。

**这不是谁对谁错的问题**，两种哲学各有适用场景：

| 场景 | MemPalace 更优 | ReMem 更优 |
|------|---------------|-----------|
| 对话历史完整回忆 | ✅ 存原文，零损失 | ❌ 只有 LLM 提取的摘要 |
| 技术决策精确回溯 | ✅ "why PostgreSQL not MySQL" 原文可查 | ⚠️ fact_store 存结论，丢了"为什么" |
| 结构化知识管理 | ❌ 只能搜原文，不能按 category 查询 | ✅ facts 有 category/entity/trust 字段 |
| 长周期知识衰减 | ❌ 原文永不过期 | ✅ 置信度衰减 + 自动清理 |
| 多源异构数据 | ❌ 单一 palace | ✅ 4 种存储各司其职 |
| 完全离线运行 | ✅ 零 API | ❌ DeepSeek + Qwen API 依赖 |

---

## 2. 检索性能对比

MemPalace 的 benchmark 数据非常硬核，是 55k star 的根基：

| Benchmark | MemPalace 分数 | MemPalace 模式 | 你的系统可对标 |
|-----------|---------------|---------------|--------------|
| LongMemEval R@5 | **96.6% raw** | 纯 ChromaDB，无 LLM | 未测试 |
| LongMemEval R@5 | **98.4% hybrid v4** | 仅 hybrid 后处理 | 未测试 |
| LongMemEval R@5 | **100%** | hybrid + LLM rerank | 未测试 |
| LoCoMo R@10 | **88.9%** | hybrid v5 | 未测试 |
| ConvoMem | **92.9%** avg recall | 所有 category | 未测试 |
| MemBench (8,500 items) | **80.3% R@5** | 所有 category | 未测试 |

**关键分析（来自他们的诚实声明）：**
- 96.6% raw 是**产品故事**：免费、私有、零依赖、零 API
- 100% 是**竞争故事**：通过调最后 3 条错题达到，held-out 450q 是 98.4%
- 99.2% R@5 / 100% R@10 的 LLM rerank 是**模型无关的**（Claude Haiku / minimax-m2.7 都行）

**你的系统未做过此类 benchmark。** 这是体系化测试的盲区。

---

## 3. 可借鉴的设计

### 借鉴点 1：Verbatim Storage（P1 — 高价值，低风险）

**MemPalace 的核心理念：不要丢掉原文，只靠语义搜索召回。**

你的系统目前的问题：
- `fact_store` 存的是 LLM 提取的**二次加工结论**——丢失了原文的"为什么"和上下文
- 当 agent 需要精确回溯某个决策的推理过程时，它找不到原始对话
- `session_search` 虽然能搜会话历史，但它不在 pre_llm_call hook 的自动注入中

**建议：在 ReMem 中加一个 "drawers" 层**

```python
# memory_store.db 新增原文存储表
CREATE TABLE drawers (
    id          TEXT PRIMARY KEY,
    wing        TEXT NOT NULL,           -- e.g. "project:ai-platform"
    room        TEXT NOT NULL,           -- e.g. "model-provider-decision"
    content     TEXT NOT NULL,           -- verbatim 原文
    source      TEXT NOT NULL,           -- e.g. "session:2026-06-08"
    created_at  TEXT NOT NULL,
    expires_at  TEXT                     -- 可选过期时间，存原始对话不用设
);

-- 全文索引，辅助 vector search
CREATE VIRTUAL TABLE drawers_fts USING fts5(content, wing, room);
```

- 当 session-end hook 提取到重要决策时，**不只写 fact_store**，也把相关对话原文写入 drawers
- 初始化时，对历史 session 做一次批量 backfill（提取所有含 "决定/选择了/采用了" 等关键字的片段）
- 检索时优先返回原文（drawers）而非摘要（facts）

**为什么这不同于已有的 session_search：**
- `session_search` 搜整条会话——噪声大，精确定位难
- `drawers` 只存被标记为"重要"的片段——精度高，量可控
- `drawers` 进入 pre_llm_call 的自动注入路径，`session_search` 不走

### 借鉴点 2：Knowledge Graph 实体关联（P1 — 中等风险）

MemPalace 的 KG 是一个**带时间窗口的实体-关系图**，SQLite 本地存储。支持：
- 实体添加/查询/过期/时间线
- 关系定向（subject → relation → object）
- validity window（实体只在某个时间段内有效）
- 跨 wing 实体连接

**你的系统当前：** 实体关系完全隐式——semantic search 是你唯一找联系的手段。没有显式的 entity 表，没有关系追踪。

**建议：加一个轻量 KG**

```python
CREATE TABLE entities (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,       -- "DeepSeek V4 Flash"
    type        TEXT NOT NULL,              -- "model" | "company" | "project" | "person"
    description TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE entity_relations (
    subject_id   TEXT NOT NULL REFERENCES entities(id),
    relation     TEXT NOT NULL,              -- "used_by" | "part_of" | "depends_on"
    object_id    TEXT NOT NULL REFERENCES entities(id),
    valid_from   TEXT,
    valid_until  TEXT,                       -- 空 = 持续有效
    confidence   REAL DEFAULT 0.5,
    source       TEXT,                       -- 哪个 session/fact 提取的
    PRIMARY KEY (subject_id, relation, object_id)
);

CREATE INDEX idx_er_subject ON entity_relations(subject_id);
CREATE INDEX idx_er_object  ON entity_relations(object_id);
```

**场景示例：**
- agent 发现 "ai-platform" 用了 "React Flow" → 写入 KG
- 下次问 "我们的技术栈依赖关系" → KG 返回结构化的依赖图
- "DeepSeek V4 Flash" 已弃用 → 实体标记 expired，agent 下次不会被误导
- 对比 `fact_store`：fact_store 存 "ai-platform uses React Flow"，KG 存的是可查询、可遍历的关系图

### 借鉴点 3：Pluggable Backend 接口（P2 — 长期架构升级）

MemPalace 的 `BaseBackend` / `BaseCollection` ABC + `registry` 注册制是一个设计良好的可插拔模式：

```python
# MemPalace 的模式
from .base import BaseBackend, BaseCollection, register, get_backend

@register("my_custom")
class MyBackend(BaseBackend):
    ...

# 调用方只需要知道 backend name
backend = get_backend("my_custom", palace_ref)
```

**你的系统当前：** Qdrant 调用硬编码在 embedding.py / context_enhancer.py 里。没有抽象层，换后端（比如从 Qdrant 换到 Milvus 或 Chroma）要改多个文件。

**建议：加一个薄抽象层**

```python
# memory-os/services/vector_store.py
from abc import ABC, abstractmethod

class VectorStore(ABC):
    @abstractmethod
    def search(self, query_vector, top_k, filter=None): ...
    @abstractmethod
    def upsert(self, points): ...
    @abstractmethod
    def delete(self, ids): ...
    @abstractmethod
    def health(self) -> bool: ...

class QdrantStore(VectorStore):
    """当前实现，封装现有 HTTP 调用"""
    ...

# 换后端只需新增一个实现
class ChromaStore(VectorStore):
    ...
```

**为什么值得做：** 当前你的 embedding 是 API-based，如果未来想离线运行（像 MemPalace 的 EmbeddingGemma），换个本地 embedding + Chroma 后端即可，不需要重写 Qdrant 适配逻辑。

### 借鉴点 4：Halls 概念——记忆的 5 种心智分类（P3 — 理念借鉴）

MemPalace 的 5 种 Halls 是在语义检索之上的认知分类：

| Hall | 用途 | 对应你的系统 |
|------|------|-------------|
| `hall_facts` | 决定、锁定的事项 | fact_store |
| `hall_events` | 会话、里程碑、调试过程 | session + 部分 fabric |
| `hall_discoveries` | 突破性发现、新洞察 | fabric（部分） |
| `hall_preferences` | 习惯、好恶、观点 | Hermes native memory |
| `hall_advice` | 推荐、解决方案 | 无对应 |

**建议：fact_store 加 hall type 字段**
- 不需要新工具，只需要在已有 fact 的 category 上增加一个 `hall` 粒度的标注
- 当 agent 搜索时，可以指定 `hall="hall_discoveries"` 来找到"突破性洞察"而非"已锁定决策"
- 实现成本极低（metadata 字段 + 路由提示词）

### 借鉴点 5：MCP 工具集（信息性参考）

MemPalace 提供 29 个 MCP tools 覆盖 palace 读写、KG 操作、agent diary、跨 wing 导航。你的系统也可以走 MCP 路线，但这不是当前优先事项（你的 agent 通过 Hermes tool call 直接调 memory 工具，不需要额外的 MCP server）。

---

## 4. 你的系统中已更好或不需要借鉴的

| 方面 | MemPalace 做法 | 你的系统 | 结论 |
|------|--------------|---------|------|
| **信任机制** | 无 | ✅ fact_feedback + 置信度衰减 + decay scanner | 你更好 |
| **自动提取** | 无 | ✅ on_session_end LLM 提取 | 你更好 |
| **Ground Truth** | 无 | ✅ SOUL.md 4 级层级 + 冲突解决 | 你更好 |
| **多工具路由** | 单一 palace search | ✅ Glue Layer 路由到 4 种源 | 你更好 |
| **回滚能力** | 无 | ✅ backup.sh/rollback.sh 完整链 | 你更好 |
| **Wiki 集成** | 无 | ✅ Wiki → Qdrant continuous ingest | 你更好 |
| **跨会话注入** | 仅 Claude Code hooks | ✅ pre_llm_call 每轮自动注入 | 你更好 |
| **Agent 隔离** | ✅ 每个 agent 独立 wing + diary | ❌ 所有 agent 共用一个 memory | MemPalace 好 |

**Agent 隔离值得关注**：MemPalace 的 multi-agent wing 设计——每个 agent 有自己的 wing 和 diary，agent 之间的 memory 天然隔离。你如果用多个 agent profile（OpenCode Worker / Hermes / cron 等），可以考虑加 agent_namespace 字段做隔离，但这不是高优先级。

---

## 5. 优先推荐的三件事（按价值/风险排序）

| 优先级 | 借鉴点 | 估值 | 成本 | 对你的系统的影响 |
|--------|--------|------|------|---------------|
| **P1** | Verbatim Storage（原文 Drawers） | 高 — 解决"丢了为什么"的结构性缺陷 | 低 — 新表 + batch backfill + pre_llm_call 注入 | 事实完整性的提升。不再只有结论，还有推理过程 |
| **P1** | Knowledge Graph 实体关联 | 高 — 技术栈/项目依赖可查询 | 中 — 新表 + session-end 提取 + KG 路由 | 从"平面搜索"到"图遍历"的能力飞跃 |
| **P2** | Pluggable Backend 接口 | 中 — 长期架构灵活性 | 低 — 薄抽象层，不改现有逻辑 | 未来切换存储后端时零代价 |
| **P3** | Halls 心智分类 | 低 — 纯 metadata 改进 | 极低 — fact_store 加一个字段 | 检索精度的小幅提升 |

---

## 6. MemPalace 弱项（你的系统可直接避免）

1. **无去重** — 原文可能被多次存储，冗余高。你的 decay scanner 已经解决
2. **无信任机制** — 所有 drawer 平等，没有"这个记忆被验证过"的标记
3. **纯离线意味着纯 ChromaDB** — 384-dim 的检索质量 vs 你的 1024-dim Qwen embedding（理论上你的语义密度更高）
4. **无备份/回滚** — 官方文档说 "beware of impostor sites" 但没解决数据安全问题
5. **仅英文** — all-MiniLM-L6-v2 不适合中文。EmbeddingGemma-300m 支持多语言但 300MB 模型体积对纯 Python 环境不小

---

## 7. 总结

| 维度 | MemPalace | ReMem |
|------|-----------|-------|
| Star | 55,000 | 1 (私有) |
| 核心卖点 | 零 API、纯本地、96.6% raw | 结构化、多源、信任机制、全生命周期 |
| 哲学 | Keep original, find with vectors | Extract structure, route by type |
| 开源质量 | ✅ 高 — ABC + registry + typed results + benchmarks | ✅ 中 — 功能完整但缺少抽象层 |
| 可直接借鉴 | Verbatim storage, KG, Plug backend | — |
| 已更好或无关 | — | trust feedback, auto-extract, backup/rollback |
| 适合场景 | 对话历史完整回忆、个人的 AI memory | 工程决策管理、知识工程系统 |
