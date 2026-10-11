#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# FLOW(CCM) 恢复脚本 —— gospel.db 是【主机文件】，直接在主机侧操作
#
# 为什么必须单独写（与音乐平台 restore.sh 同理）：
#   「有备份、没恢复流程」是最常见的自欺生产线。这里把恢复步骤固化下来：
#     · 恢复前**先把当前库也抢救一份**（恢复错了还能滚回去）
#     · 校验待恢复文件的完整性（拒绝把坏文件写进线上库）
#     · 停 backend → 原子替换 → 起 backend → 健康检查通过才算成功
#
# 用法：
#   ./restore.sh                              # 恢复最新一份备份
#   ./restore.sh gospel-20260914-120000.db    # 指定备份文件名（位于 BACKUP_DIR 下）
#   BACKUP_DIR=/data/flow-bak ./restore.sh    # 指定备份目录
#
# ⚠ 破坏性操作：会覆盖正在使用的数据库。脚本二次确认，除非 FORCE=1。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/flow-ccm}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
ENV_FILE="$APP_DIR/.env.production"
DB_FILE="$APP_DIR/gospel.db"
TARGET="${1:-}"
FORCE="${FORCE:-0}"
BACKUP_IMAGE="python:3.11-slim"

info() { echo -e "\033[0;32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
die()  { echo -e "\033[0;31m[FAIL]\033[0m $*" >&2; exit 1; }

cd "$APP_DIR" || die "无法进入 $APP_DIR"
command -v docker >/dev/null 2>&1 || die "未安装 Docker（恢复需要停/起 backend 容器）"

# compose 封装（复用 bootstrap 的 ${VAR:?} 教训：所有子命令必须 --env-file）
fc() { docker compose -f "$APP_DIR/docker-compose.prod.yml" --env-file "$ENV_FILE" "$@"; }

# 确保校验镜像可用
if docker image inspect "$BACKUP_IMAGE" >/dev/null 2>&1; then : ; else
  docker pull "$BACKUP_IMAGE" >/dev/null 2>&1 \
    || die "无法获取校验镜像 $BACKUP_IMAGE"
fi

# ── 1. 选出要恢复的备份 ──────────────────────────────────────────────────────
if [ -z "$TARGET" ]; then
  if [ ! -d "$BACKUP_DIR" ]; then
    die "备份目录 $BACKUP_DIR 不存在 —— 先跑 backup.sh"
  fi
  # 直接在主机侧挑最新的 gospel-*.db（备份是主机文件，无需进容器）
  TARGET="$(ls -1t "$BACKUP_DIR"/gospel-*.db 2>/dev/null | head -1 || true)"
  [ -z "$TARGET" ] && die "在 $BACKUP_DIR 没找到任何 gospel-*.db 备份"
  TARGET="$(basename "$TARGET")"
  info "未指定文件 → 使用最新备份：$TARGET"
fi
SRC="$BACKUP_DIR/$TARGET"

# ── 2. 待恢复文件的存在性与完整性（拒绝把坏文件写进线上库）────────────────────
[ -f "$SRC" ] || die "待恢复文件不存在：$SRC"
info "校验待恢复文件：$SRC"
if ! docker run --rm --entrypoint python3 \
      -v "$SRC:/bk.db:ro" "$BACKUP_IMAGE" - <<'PY'
import sqlite3, sys
p = "/bk.db"
try:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    tables = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    bad = con.execute("pragma integrity_check").fetchone()[0]
    con.close()
except sqlite3.DatabaseError as e:
    print(f"[FAIL] 不是有效的 SQLite 文件：{e}")
    sys.exit(1)
print(f"[OK] 待恢复文件自检：表数={tables} integrity={bad}")
sys.exit(0 if bad == "ok" and tables > 0 else 1)
PY
then
  die "待恢复文件不可用：$SRC（见上方原因）"
fi

# ── 3. 二次确认（除非 FORCE=1）───────────────────────────────────────────────
if [ "$FORCE" != "1" ]; then
  warn "即将用 $SRC **覆盖** $DB_FILE（当前线上库）。"
  printf "确认请输入 yes："
  read -r ANS
  [ "$ANS" = "yes" ] || die "已取消（未输入 yes）"
fi

# ── 4. 先把当前库抢救一份（万一恢复错了还能滚回来）────────────────────────────
if [ -f "$DB_FILE" ]; then
  SAFETY="$(date -u +%Y%m%d-%H%M%S)"
  cp -f "$DB_FILE" "$BACKUP_DIR/gospel-prerestore-${SAFETY}.db" \
    && info "恢复前已抢救当前库 → $BACKUP_DIR/gospel-prerestore-${SAFETY}.db" \
    || warn "当前库抢救失败（权限？），继续 —— 风险自负"
fi

# ── 5. 停服务 → 原子替换 → 起服务 ────────────────────────────────────────────
info "停止 backend（释放 gospel.db 文件句柄）..."
fc stop backend

info "原子替换数据库文件..."
# 先写 .tmp 再 mv，避免中途崩溃留下半截库；清除旧 WAL/SHM，否则会用旧 WAL 覆盖新数据
cp -f "$SRC" "$DB_FILE.tmp"
rm -f "$DB_FILE-wal" "$DB_FILE-shm"
mv -f "$DB_FILE.tmp" "$DB_FILE"
info "已替换 $DB_FILE"

info "启动 backend..."
fc up -d backend

info "等待健康检查 /health ..."
for i in $(seq 1 30); do
  if fc exec -T backend curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo
    info "恢复完成并通过健康检查 ✅ （第 ${i} 次探测）"
    info "请再抽查一条业务数据确认（如登录 / 问答记录）。"
    exit 0
  fi
  sleep 2
done

echo
fc logs --tail=60 backend || true
die "恢复后健康检查未通过 —— 现库可能已损坏，请用第 4 步的 gospel-prerestore-<时间戳>.db 回滚：
     cp $BACKUP_DIR/gospel-prerestore-<时间戳>.db $DB_FILE  &&  fc restart backend"
