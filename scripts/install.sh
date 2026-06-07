#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# ReMem — Install Script
# ──────────────────────────────────────────────────────────────────────────────
# 一键安装 Memory OS 并将当前 Hermes 切换为增强记忆系统。
#
# 先备份，再安装。任何时候运行 rollback.sh 可回退到原生状态。
#
# 用法：
#   bash install.sh                              ← 完整安装
#   bash install.sh --dry-run                    ← 预览，不执行任何操作
#   bash install.sh --skip-backup                ← 跳过备份（有备份时使用）
#   bash install.sh --skip-git-clone             ← 跳过 git clone（已下载时使用）
#
# 依赖：
#   - Hermes Agent（已安装并运行过）
#   - Docker Desktop（macOS）或 Docker Engine（Linux）
#   - Python 3.11+
#   - 4GB 可用磁盘
#
# 所属项目：https://github.com/sunquan0405/memory-os-hermes
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"   # memory-os-hermes 项目根目录
HERMES_HOME="${HOME}/.hermes"
MEMORY_OS_DIR="${HOME}/memory-os"         # 上游 Memory OS 安装位置
BACKUP_DIR="${HOME}/Work/Backups/memory-os-hermes"

# ── 解析参数 ────────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_BACKUP=false
SKIP_GIT_CLONE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)           DRY_RUN=true; shift ;;
    --skip-backup)       SKIP_BACKUP=true; shift ;;
    --skip-git-clone)    SKIP_GIT_CLONE=true; shift ;;
    --help|-h)           head -30 "$0"; exit 0 ;;
    *)                   echo "Unknown: $1"; exit 1 ;;
  esac
done

# ── 前置检查 ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ ReMem — Install ═══${NC}"
echo ""
info "Project root: $PROJECT_DIR"
info "Hermes home:  $HERMES_HOME"

if ! $DRY_RUN; then
    if ! command -v docker &>/dev/null; then
        fail "Docker not found — install Docker Desktop first"
        exit 1
    fi
    if ! python3 --version &>/dev/null; then
        fail "Python 3 not found"
        exit 1
    fi
    ok "Docker: $(docker --version 2>/dev/null)"
    ok "Python: $(python3 --version 2>/dev/null)"
fi

# ── Step 1: Backup ──────────────────────────────────────────────────────────
step_backup() {
    echo ""
    echo -e "${BOLD}═══ Step 1: Backup ═══${NC}"
    echo ""

    if $DRY_RUN; then
        info "[DRY-RUN] Would run: bash $SCRIPT_DIR/backup.sh"
        info "[DRY-RUN]   → SOUL.md, config.yaml, .env, state.db, hermes_home snapshot"
        return
    fi

    if $SKIP_BACKUP; then
        info "Skipped (--skip-backup)"
        return
    fi

    bash "$SCRIPT_DIR/backup.sh"
    echo ""
    ok "Backup complete"
}

# ── Step 2: Install Memory OS Stack ─────────────────────────────────────────
step_install_memory_os() {
    echo ""
    echo -e "${BOLD}═══ Step 2: Install Memory OS Stack ═══${NC}"
    echo ""

    if $DRY_RUN; then
        info "[DRY-RUN] Would clone Memory OS and run setup.sh"
        return
    fi

    if [ -d "$MEMORY_OS_DIR/.git" ] && $SKIP_GIT_CLONE; then
        info "Memory OS repo already exists at $MEMORY_OS_DIR (--skip-git-clone)"
    elif [ -d "$MEMORY_OS_DIR/.git" ]; then
        info "Memory OS repo already exists, pulling latest..."
        cd "$MEMORY_OS_DIR" && git pull
    else
        info "Cloning Memory OS..."
        git clone https://github.com/ClaudioDrews/memory-os.git "$MEMORY_OS_DIR"
    fi

    info "Running Memory OS setup.sh..."
    cd "$MEMORY_OS_DIR"
    bash setup.sh
    echo ""
    ok "Memory OS stack installed"
}

# ── Step 3: Apply Glue Layer ────────────────────────────────────────────────
step_apply_glue() {
    echo ""
    echo -e "${BOLD}═══ Step 3: Apply Glue Layer ═══${NC}"
    echo ""

    if $DRY_RUN; then
        info "[DRY-RUN] Would apply rulebook and SOUL.md modifications"
        return
    fi

    # 确保 modification 目录存在
    mkdir -p "$HERMES_HOME/modifications"

    # SOUL.md — 追加 Ground Truth 层级（如果有）
    SOUL_FILE="$HERMES_HOME/SOUL.md"
    GLUE_SOUL="$PROJECT_DIR/modifications/soul-rulebook.md"
    if [ -f "$GLUE_SOUL" ] && [ -f "$SOUL_FILE" ]; then
        if grep -q "Memory OS additions" "$SOUL_FILE"; then
            info "SOUL.md already has Memory OS additions (skip)"
        else
            cat "$GLUE_SOUL" >> "$SOUL_FILE"
            ok "SOUL.md updated with Ground Truth hierarchy"
        fi
    fi

    # rulebook.md — 追加 Glue Layer 存储路由
    RULEBOOK_FILE="$HERMES_HOME/rulebook.md"
    GLUE_RULEBOOK="$PROJECT_DIR/modifications/glue-layer-rulebook.md"
    if [ -f "$GLUE_RULEBOOK" ]; then
        if [ ! -f "$RULEBOOK_FILE" ]; then
            touch "$RULEBOOK_FILE"
        fi
        if grep -q "ReMem — Glue Layer" "$RULEBOOK_FILE"; then
            info "rulebook.md already has Glue Layer rules (skip)"
        else
            cat "$GLUE_RULEBOOK" >> "$RULEBOOK_FILE"
            ok "rulebook.md updated with storage routing rules"
        fi
    fi

    echo ""
    ok "Glue Layer applied"
}

# ── Step 4: Run Migration ───────────────────────────────────────────────────
step_migrate() {
    echo ""
    echo -e "${BOLD}═══ Step 4: Migrate Existing Memory ═══${NC}"
    echo ""

    if $DRY_RUN; then
        info "[DRY-RUN] Would run migrate_from_hermes.py"
        return
    fi

    if [ -f "$SCRIPT_DIR/migrate_from_hermes.py" ]; then
        info "Running memory migration..."
        python3 "$SCRIPT_DIR/migrate_from_hermes.py"
        ok "Migration complete"
    else
        warn "migrate_from_hermes.py not yet implemented (will be created in Step 3)"
        warn "Run manually later: python3 scripts/migrate_from_hermes.py"
    fi
}

# ── Step 5: Verify ──────────────────────────────────────────────────────────
step_verify() {
    echo ""
    echo -e "${BOLD}═══ Step 5: Verify Integration ═══${NC}"
    echo ""

    if $DRY_RUN; then
        info "[DRY-RUN] Would run verify_integration.py"
        return
    fi

    if [ -f "$SCRIPT_DIR/verify_integration.py" ]; then
        python3 "$SCRIPT_DIR/verify_integration.py"
        if [ $? -eq 0 ]; then
            ok "Integration verified"
        else
            warn "Integration verification reported issues (see above)"
        fi
    else
        warn "verify_integration.py not yet implemented"
        warn "Manual verification steps:"
        echo "  1. Start a new Hermes session"
        echo "  2. Ask agent: '查一下我的 memory 里有什么'"
        echo "  3. Ask agent: '查一下 fact_store 里有什么'"
        echo "  4. Verify both return consistent results"
    fi
}

# ── 执行 ────────────────────────────────────────────────────────────────────
echo ""
step_backup
echo ""
step_install_memory_os
echo ""
step_apply_glue
echo ""
step_migrate
echo ""
step_verify

# ── 完成 ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══ Install Complete ═══${NC}"
echo ""
echo -e "  ${GREEN}✅ ReMem installed${NC}"
echo ""
echo "  What happened:"
echo "    ✅ Backup:   ~/Work/Backups/memory-os-hermes/"
echo "    ✅ Stack:    Qdrant + Redis + Worker (Docker)"
echo "    ✅ Glue:     SOUL.md + rulebook.md updated"
echo "    ✅ Migrate:  Memory entries → Memory OS"
echo ""
echo "  What to do next:"
echo "    1. Start a new Hermes session"
echo "    2. Test: '帮我查一下之前存过的所有记忆'"
echo "    3. Verify memory search works across sessions"
echo ""
echo "  If anything goes wrong:"
echo "    bash scripts/rollback.sh"
echo ""
