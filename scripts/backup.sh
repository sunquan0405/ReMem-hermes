#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# ReMem — Backup Script
# ──────────────────────────────────────────────────────────────────────────────
# 先备份，再安装。无备份不操作。
#
# 备份内容：
#   1. Hermes memory 条目（通过 memory 工具导出为 JSON）
#   2. Hermes state.db（会话历史、FTS5 索引）
#   3. SOUL.md（agent 身份文件）
#   4. rulebook.md（行为规则）
#   5. ~/.hermes/ 目录的快照
#   6. Memory OS 当前状态（memory_store.db, 如果已存在）
#   7. 环境变量
#
# 用法：
#   bash scripts/backup.sh                        ← 默认备份
#   bash scripts/backup.sh --output /path/to/dir  ← 指定备份目录
#   bash scripts/backup.sh --dry-run              ← 预览，不执行
#   bash scripts/backup.sh --verify-only          ← 仅验证已有备份
#
# 输出：
#   ~/Work/Backups/memory-os-hermes/YYYYMMDD_HHMMSS/
#   ├── export_memory.json        ← Hermes memory 条目（核心资产）
#   ├── state.db                  ← Hermes session DB
#   ├── SOUL.md                   ← 身份文件
#   ├── rulebook.md               ← 规则手册
#   ├── hermes_home_snapshot.tar.gz ← ~/.hermes/ 快照
#   ├── memory_store.db           ← Memory OS fact DB（如果存在）
#   ├── env.txt                   ← 环境变量备份
#   ├── manifest.md               ← 备份清单
#   └── integrity.sha256          ← 完整性校验
#
# 回退时使用：
#   bash scripts/rollback.sh --from /path/to/backup
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
DEFAULT_OUTPUT="${HOME}/Work/Backups/memory-os-hermes"
MEMORY_OS_REPO="${HOME}/Work/Projects/memory-os"

# ── 解析参数 ────────────────────────────────────────────────────────────────
OUTPUT_DIR="$DEFAULT_OUTPUT"
DRY_RUN=false
VERIFY_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)     OUTPUT_DIR="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    --verify-only) VERIFY_ONLY=true; shift ;;
    --help|-h)    head -30 "$0"; exit 0 ;;
    *)            echo "Unknown: $1"; exit 1 ;;
  esac
done

# ── 验证模式 ────────────────────────────────────────────────────────────────
if $VERIFY_ONLY; then
    if [ ! -f "$OUTPUT_DIR/manifest.md" ]; then
        fail "No backup found at $OUTPUT_DIR"
        exit 1
    fi
    echo "🔍 Verifying backup at $OUTPUT_DIR..."
    cd "$OUTPUT_DIR"
    if sha256sum -c integrity.sha256 2>/dev/null; then
        ok "Backup integrity verified"
        cat manifest.md
    else
        fail "Backup corrupted!"
        exit 1
    fi
    exit 0
fi

# ── 备份目录 ────────────────────────────────────────────────────────────────
TIMESTAMP=$(date "+%Y%m%d_%H%M%S")
BACKUP_DIR="${OUTPUT_DIR}/${TIMESTAMP}"

# ── Dry-run ──────────────────────────────────────────────────────────────────
if $DRY_RUN; then
    echo ""
    echo -e "${BOLD}═══ ReMem — Backup (DRY RUN) ═══${NC}"
    echo ""
    echo "Will backup to:     $BACKUP_DIR"
    echo "Will export memory: from Hermes native memory tool"
    echo "Will copy:          state.db, SOUL.md, rulebook.md"
    echo "Will snapshot:      $HERMES_HOME/"
    echo "Will copy:          memory_store.db (if exists)"
    echo "Will verify with:   sha256sum"
    echo ""
    echo "Files to backup:"
    echo "  $HERMES_HOME/state.db"
    echo "  $HERMES_HOME/SOUL.md"
    echo "  $HERMES_HOME/rulebook.md"
    echo "  $HERMES_HOME/memory_store.db (if exists)"
    echo ""
    echo -e "${GREEN}Dry-run complete. Run without --dry-run to execute.${NC}"
    exit 0
fi

# ── 开始备份 ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ ReMem — Backup ═══${NC}"
echo ""
info "Backup directory: $BACKUP_DIR"

mkdir -p "$BACKUP_DIR"

PASS=0
FAIL=0
WARN=0

# ── 1. Hermes 配置备份 ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Phase 1: Configuration ──${NC}"
echo ""

info "Hermes memory 条目由 agent runtime 管理，不在文件中存储"
info "将在迁移阶段（Step 3）通过 migrate_from_hermes.py 导出"

# SOUL.md
SOUL_FILE="${HERMES_HOME}/SOUL.md"
if [ -f "$SOUL_FILE" ]; then
    cp "$SOUL_FILE" "$BACKUP_DIR/SOUL.md"
    SIZE=$(du -h "$SOUL_FILE" | cut -f1)
    ok "SOUL.md ($SIZE)"
    PASS=$((PASS + 1))
else
    warn "SOUL.md not found"
    WARN=$((WARN + 1))
fi

# config.yaml
CONFIG_FILE="${HERMES_HOME}/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "$BACKUP_DIR/config.yaml"
    SIZE=$(du -h "$CONFIG_FILE" | cut -f1)
    ok "config.yaml ($SIZE)"
    PASS=$((PASS + 1))
else
    warn "config.yaml not found"
    WARN=$((WARN + 1))
fi

# .env
ENV_FILE_SRC="${HERMES_HOME}/.env"
if [ -f "$ENV_FILE_SRC" ]; then
    cp "$ENV_FILE_SRC" "$BACKUP_DIR/env_hermes.env"
    SIZE=$(du -h "$ENV_FILE_SRC" | cut -f1)
    ok ".env ($SIZE)"
    PASS=$((PASS + 1))
else
    warn ".env not found"
    WARN=$((WARN + 1))
fi

# ── 2. state.db ────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Phase 2: Database ──${NC}"
echo ""

STATE_DB="${HERMES_HOME}/state.db"
if [ -f "$STATE_DB" ]; then
    cp "$STATE_DB" "$BACKUP_DIR/state.db"
    SIZE=$(du -h "$STATE_DB" | cut -f1)
    ok "state.db ($SIZE)"
    PASS=$((PASS + 1))
else
    warn "state.db not found"
    WARN=$((WARN + 1))
fi

# rulebook.md（如果存在）
RULEBOOK="${HERMES_HOME}/rulebook.md"
if [ -f "$RULEBOOK" ]; then
    cp "$RULEBOOK" "$BACKUP_DIR/rulebook.md"
    SIZE=$(du -h "$RULEBOOK" | cut -f1)
    ok "rulebook.md ($SIZE)"
    PASS=$((PASS + 1))
fi

# ── 3. ~/.hermes 快照 ──────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Phase 3: Hermes Home Snapshot ──${NC}"
echo ""

SNAPSHOT="$BACKUP_DIR/hermes_home_snapshot.tar.gz"
if tar czf "$SNAPSHOT" \
    --exclude="state.db" \
    --exclude="state.db-wal" \
    --exclude="state.db-shm" \
    --exclude="*.db-wal" \
    --exclude="*.db-shm" \
    --exclude="cache" \
    --exclude="__pycache__" \
    --exclude="node_modules" \
    --exclude="venv" \
    --exclude="hermes-setup" \
    -C "$(dirname "$HERMES_HOME")" "$(basename "$HERMES_HOME")" 2>/dev/null; then
    SIZE=$(du -h "$SNAPSHOT" | cut -f1)
    ok "Hermes home snapshot ($SIZE)"
    PASS=$((PASS + 1))
else
    warn "Hermes home snapshot failed (permissions or disk)"
    WARN=$((WARN + 1))
fi

# ── 6. Memory OS state（如果已存在） ──────────────────────────────────────
echo ""
echo -e "${BOLD}── Phase 4: Memory OS State ──${NC}"
echo ""

MEMORY_OS_DB="${HERMES_HOME}/memory_store.db"
if [ -f "$MEMORY_OS_DB" ]; then
    cp "$MEMORY_OS_DB" "$BACKUP_DIR/memory_store.db"
    SIZE=$(du -h "$MEMORY_OS_DB" | cut -f1)
    ok "memory_store.db ($SIZE)"
    PASS=$((PASS + 1))
else
    info "memory_store.db not yet installed (skip)"
fi

# ── 7. 环境变量 ────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Phase 6: Environment ──${NC}"
echo ""

ENV_FILE="$BACKUP_DIR/env.txt"
{
    echo "# ReMem — Environment Backup"
    echo "# Generated: $(date)"
    echo "# From: $(hostname)"
    echo ""
    echo "## Hermes"
    echo "HERMES_HOME=$HERMES_HOME"
    echo ""
    echo "## DeepSeek"
    echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:+present}"
    echo ""
    echo "## OpenRouter"
    echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:+present}"
    echo ""
    echo "## Memory OS"
    echo "QDRANT_API_KEY=${QDRANT_API_KEY:+present}"
    echo "REDIS_PASSWORD=${REDIS_PASSWORD:+present}"
} > "$ENV_FILE"
ok "Environment variables logged (keys redacted)"
PASS=$((PASS + 1))

# ── 8. 清单 + 完整性校验 ──────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Phase 7: Manifest & Integrity ──${NC}"
echo ""

MANIFEST="$BACKUP_DIR/manifest.md"
{
    echo "# Backup Manifest"
    echo ""
    echo "**Date:** $(date)"
    echo "**Host:** $(hostname)"
    echo "**Directory:** $BACKUP_DIR"
    echo ""
    echo "## Files"
    echo ""
    echo "| File | Size | Status |"
    echo "|------|------|--------|"
} > "$MANIFEST"

cd "$BACKUP_DIR"
for f in *; do
    if [ -f "$f" ]; then
        SIZE=$(du -h "$f" | cut -f1)
        echo "| $f | $SIZE | ✅ |" >> "$MANIFEST"
    fi
done

# sha256sum
if command -v sha256sum &>/dev/null; then
    cd "$BACKUP_DIR"
    sha256sum * > integrity.sha256 2>/dev/null
    ok "Integrity checksums generated (sha256)"
    PASS=$((PASS + 1))
elif command -v shasum &>/dev/null; then
    cd "$BACKUP_DIR"
    shasum -a 256 * > integrity.sha256 2>/dev/null
    ok "Integrity checksums generated (shasum)"
    PASS=$((PASS + 1))
else
    warn "No sha256sum/shasum available — integrity not verifiable"
    WARN=$((WARN + 1))
fi

# ── 汇总 ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ Summary ═══${NC}"
echo ""
echo "  ✅ Pass: $PASS"
echo "  ⚠️  Warn: $WARN"
echo "  ❌ Fail: $FAIL"
echo ""
echo "  Backup path: $BACKUP_DIR"

if [ -f "$MANIFEST" ]; then
    echo ""
    echo -e "${BOLD}Backed up files:${NC}"
    cat "$MANIFEST"
fi

echo ""
if [ $FAIL -eq 0 ]; then
    echo -e "  ${GREEN}✅ Backup complete.${NC}"
    echo ""
    echo "  Next step:"
    echo "    bash scripts/install_memory_os.sh"
    echo ""
    echo "  To verify backup later:"
    echo "    bash scripts/backup.sh --verify-only --output $BACKUP_DIR"
else
    echo -e "  ${RED}❌ Some backups failed. Check errors above.${NC}"
    exit 1
fi
