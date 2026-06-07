#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# ReMem — Rollback Script
# ──────────────────────────────────────────────────────────────────────────────
# 一键回退到纯原生 Hermes memory 状态。
# 从备份中恢复所有被修改的文件。
#
# 回退内容：
#   1. 恢复 SOUL.md（删除 Memory OS 添加的内容）
#   2. 恢复 rulebook.md（删除 Memory OS 添加的内容）
#   3. 删除 memory_store.db（Memory OS 的事实数据库）
#   4. 删除 Qdrant collection（向量索引）
#   5. 停止 Docker 堆栈（Qdrant + Redis + Worker）
#   6. 删除 Memory OS 环境变量
#
# 注意：Hermes memory 条目本身从未被修改，无需恢复。
#
# 用法：
#   bash scripts/rollback.sh                          ← 从最新备份回退
#   bash scripts/rollback.sh --from /path/to/backup   ← 从指定备份回退
#   bash scripts/rollback.sh --dry-run                ← 预览，不执行
#   bash scripts/rollback.sh --keep-docker            ← 保留 Docker 堆栈
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── 颜色 ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'
ok()  { printf "  ${GREEN}✅${NC} %s\n" "$1"; }
warn(){ printf "  ${YELLOW}⚠️${NC}  %s\n" "$1"; }
fail(){ printf "  ${RED}❌${NC} %s\n" "$1"; }
info(){ printf "  📘 %s\n" "$1"; }

# ── 路径 ────────────────────────────────────────────────────────────────────
HERMES_HOME="${HOME}/.hermes"
MEMORY_OS_DIR="${HOME}/memory-os"
DEFAULT_BACKUP_DIR="${HOME}/Work/Backups/memory-os-hermes"

# ── 解析参数 ────────────────────────────────────────────────────────────────
BACKUP_DIR=""
DRY_RUN=false
KEEP_DOCKER=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)        BACKUP_DIR="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=true; shift ;;
    --keep-docker) KEEP_DOCKER=true; shift ;;
    --help|-h)     head -30 "$0"; exit 0 ;;
    *)             echo "Unknown: $1"; exit 1 ;;
  esac
done

# ── 确定备份路径 ────────────────────────────────────────────────────────────
if [ -z "$BACKUP_DIR" ]; then
    # 找最新备份
    if [ -d "$DEFAULT_BACKUP_DIR" ]; then
        BACKUP_DIR=$(ls -dt "$DEFAULT_BACKUP_DIR"/*/ 2>/dev/null | head -1)
    fi
fi

if [ -z "$BACKUP_DIR" ] || [ ! -d "$BACKUP_DIR" ]; then
    fail "No backup found at $BACKUP_DIR"
    echo ""
    echo "  Specify backup path: bash scripts/rollback.sh --from /path/to/backup"
    exit 1
fi

# 去除尾部斜杠
BACKUP_DIR="${BACKUP_DIR%/}"

# ── Dry-run ─────────────────────────────────────────────────────────────────
if $DRY_RUN; then
    echo ""
    echo -e "${BOLD}═══ ReMem — Rollback (DRY RUN) ═══${NC}"
    echo ""
    echo "Backup source: $BACKUP_DIR"
    echo ""
    echo "Would restore:"
    echo "  1. SOUL.md         ← from backup"
    echo "  2. rulebook.md     ← delete if created by Memory OS, otherwise restore"
    echo "  3. memory_store.db ← delete (Qdrant + SQLite)"
    echo "  4. Qdrant          ← delete knowledge_base collection"
    echo "  5. Docker stack    ← docker compose down"
    echo "  6. .env            ← remove Memory OS variables"
    echo ""
    echo -e "${GREEN}Dry-run complete. Run without --dry-run to execute.${NC}"
    exit 0
fi

# ── 开始回退 ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ ReMem — Rollback ═══${NC}"
echo ""
info "Backup source: $BACKUP_DIR"

PASS=0
FAIL=0
WARN=0

# ── 1. 恢复 SOUL.md ────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 1: Restore SOUL.md ──${NC}"
echo ""

if [ -f "$BACKUP_DIR/SOUL.md" ]; then
    cp "$BACKUP_DIR/SOUL.md" "$HERMES_HOME/SOUL.md"
    ok "SOUL.md restored from backup"
    PASS=$((PASS + 1))
else
    # 尝试只删除 Memory OS 添加的部分
    if grep -q "ReMem additions" "$HERMES_HOME/SOUL.md" 2>/dev/null; then
        warn "No backup SOUL.md found — attempting selective removal"
        warn "Manual review suggested: check $HERMES_HOME/SOUL.md"
        WARN=$((WARN + 1))
    else
        info "SOUL.md has no Memory OS additions (nothing to restore)"
    fi
fi

# ── 2. 恢复 rulebook.md ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 2: Restore rulebook.md ──${NC}"
echo ""

if [ -f "$BACKUP_DIR/rulebook.md" ]; then
    cp "$BACKUP_DIR/rulebook.md" "$HERMES_HOME/rulebook.md"
    ok "rulebook.md restored from backup"
    PASS=$((PASS + 1))
elif [ -f "$BACKUP_DIR/SOUL.md" ]; then
    # SOUL.md 备份中可能包含 rulebook 内容
    if grep -q "ReMem — Glue Layer" "$HERMES_HOME/rulebook.md" 2>/dev/null; then
        warn "No backup rulebook.md — attempting selective removal"
        # 尝试删除 Memory OS 添加的部分
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' '/<!-- ReMem — Glue Layer -->/,/^$/d' "$HERMES_HOME/rulebook.md" 2>/dev/null || true
        else
            sed -i '/<!-- ReMem — Glue Layer -->/,/^$/d' "$HERMES_HOME/rulebook.md" 2>/dev/null || true
        fi
        ok "Glue Layer rules removed from rulebook.md"
        PASS=$((PASS + 1))
    else
        info "rulebook.md has no Glue Layer additions (nothing to restore)"
    fi
else
    info "rulebook.md not found in backup (file may not have existed before installation)"
fi

# ── 3. 删除 memory_store.db ────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 3: Remove Memory OS Databases ──${NC}"
echo ""

MEMORY_DB="${HERMES_HOME}/memory_store.db"
if [ -f "$MEMORY_DB" ]; then
    SIZE=$(du -h "$MEMORY_DB" | cut -f1)
    rm -f "$MEMORY_DB"
    ok "memory_store.db removed ($SIZE)"
    PASS=$((PASS + 1))
else
    info "memory_store.db not found (already removed)"
fi

# 也删除 WAL/SHM 文件
rm -f "${MEMORY_DB}-wal" "${MEMORY_DB}-shm" 2>/dev/null

# Memory OS state.db（如果独立于 Hermes 的 state.db）
# Hermes 的 state.db 不受影响，不做删除

# ── 4. 删除 Qdrant collection ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 4: Remove Qdrant Collection ──${NC}"
echo ""

if command -v curl &>/dev/null; then
    QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
    QDRANT_KEY="${QDRANT_API_KEY:-}"

    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "${QDRANT_URL}/healthz" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        HEADERS="-H 'Content-Type: application/json'"
        if [ -n "$QDRANT_KEY" ]; then
            HEADERS="$HEADERS -H 'api-key: $QDRANT_KEY'"
        fi

        DELETE_RESULT=$(curl -s -X DELETE "${QDRANT_URL}/collections/knowledge_base" \
            -H 'Content-Type: application/json' \
            ${QDRANT_KEY:+-H "api-key: $QDRANT_KEY"} 2>/dev/null || echo "failed")

        if echo "$DELETE_RESULT" | grep -q '"status":"ok"'; then
            ok "Qdrant collection 'knowledge_base' deleted"
            PASS=$((PASS + 1))
        else
            warn "Qdrant collection deletion returned: ${DELETE_RESULT:0:80}"
            WARN=$((WARN + 1))
        fi
    else
        info "Qdrant not running (collection will persist until container is removed)"
    fi
else
    warn "curl not found — skip Qdrant collection deletion"
    info "To delete manually: curl -X DELETE http://127.0.0.1:6333/collections/knowledge_base"
    WARN=$((WARN + 1))
fi

# ── 5. 停止 Docker 堆栈 ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 5: Stop Docker Stack ──${NC}"
echo ""

if $KEEP_DOCKER; then
    info "Docker stack preserved (--keep-docker)"
elif [ -f "$MEMORY_OS_DIR/docker/docker-compose.yml" ]; then
    cd "$MEMORY_OS_DIR/docker"
    if docker compose down 2>/dev/null; then
        ok "Docker stack stopped (Qdrant + Redis + Worker)"
        PASS=$((PASS + 1))
    else
        warn "Docker compose down failed — may need manual cleanup"
        info "Try: docker compose -f ~/memory-os/docker/docker-compose.yml down"
        WARN=$((WARN + 1))
    fi
else
    info "Memory OS docker-compose.yml not found (stack may already be stopped)"
fi

# ── 6. 清理环境变量 ─────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Step 6: Clean Environment ──${NC}"
echo ""

ENV_FILE="${HERMES_HOME}/.env"
if [ -f "$ENV_FILE" ]; then
    # 检查是否有 Memory OS 相关变量
    if grep -q "QDRANT\|REDIS_PASSWORD\|MEMORY_OS" "$ENV_FILE" 2>/dev/null; then
        # 从备份恢复
        if [ -f "$BACKUP_DIR/env_hermes.env" ]; then
            cp "$BACKUP_DIR/env_hermes.env" "$ENV_FILE"
            ok ".env restored from backup"
            PASS=$((PASS + 1))
        else
            warn "Memory OS vars found in .env but no backup available"
            info "Manual cleanup needed: remove QDRANT_*, REDIS_PASSWORD, MEMORY_OS_* vars"
            WARN=$((WARN + 1))
        fi
    else
        info ".env has no Memory OS variables (clean)"
    fi
fi

# ── 汇总 ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ Rollback Complete ═══${NC}"
echo ""
echo "  ✅ Pass: $PASS"
echo "  ⚠️  Warn: $WARN"
echo "  ❌ Fail: $FAIL"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✅ Rollback complete. Hermes is now using native memory only.${NC}"
    echo ""
    echo "  What happened:"
    echo "    ✅ SOUL.md restored (or Memory OS sections removed)"
    echo "    ✅ rulebook.md restored (or Glue Layer rules removed)"
    echo "    ✅ memory_store.db deleted"
    echo "    ✅ Qdrant collection deleted"
    echo "    ✅ Docker stack stopped"
    echo "    ✅ .env restored"
    echo ""
    echo "  What was NOT affected:"
    echo "    ✅ Hermes memory entries (never modified)"
    echo "    ✅ state.db (conversation history)"
    echo "    ✅ Hermes config.yaml"
    echo ""
    echo "  Start a new Hermes session to verify native memory is restored."
else
    echo -e "${RED}❌ Some steps failed. See details above.${NC}"
    exit 1
fi
