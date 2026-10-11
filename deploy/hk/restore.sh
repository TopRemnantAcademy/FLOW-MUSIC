#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SQLite 恢复（backup.sh 的逆操作）
#
# 为什么必须单独写这个文件：
#   「有备份、没恢复流程」是最常见的自欺生产线 —— 备份脚本每天跑得欢，
#   真出事那天没人知道怎么用、要停什么、会不会覆盖现网库。这里把恢复步骤固化下来：
#     · 恢复前**先把当前库也备份一份**（恢复错了还能滚回去）
#     · 校验待恢复文件的完整性（拒绝恢复一个坏文件，而不是把坏数据写进线上库）
#     · 停 backend → 原子替换 → 起 backend → 健康检查通过才算成功
#
# 用法：
#   ./deploy/hk/restore.sh                     # 恢复最新一份备份
#   ./deploy/hk/restore.sh app-20260914-120000.db   # 指定容器内 backups/ 下的文件名
#   BACKUP_SUBDIR=backups ./deploy/hk/restore.sh    # 指定子目录（默认 backups）
#
# ⚠ 这是**破坏性操作**：会覆盖正在使用的数据库。脚本会二次确认，除非设置 FORCE=1。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/flow}"
BACKUP_SUBDIR="${BACKUP_SUBDIR:-backups}"
TARGET="${1:-}"
FORCE="${FORCE:-0}"

cd "$APP_DIR"
dc() { docker compose --env-file "$APP_DIR/.env" "$@"; }

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GRN}[INFO]${NC} $*"; }
warn() { echo -e "${YLW}[WARN]${NC} $*"; }
die()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

DB="/app/data/app.db"
OUTDIR="/app/data/${BACKUP_SUBDIR}"

# ── 1. 选出要恢复的备份 ───────────────────────────────────────────────────────
if [ -z "$TARGET" ]; then
  TARGET="$(dc exec -T -e "BACKUP_SUBDIR=$BACKUP_SUBDIR" backend python - <<'PY'
import os
outdir = "/app/data/" + os.environ.get("BACKUP_SUBDIR", "backups")
if not os.path.isdir(outdir):
    print("NO_BACKUP")
else:
    cands = sorted(n for n in os.listdir(outdir) if n.startswith("app-") and n.endswith(".db"))
    print(cands[-1] if cands else "NO_BACKUP")
PY
)"
  [ "$TARGET" = "NO_BACKUP" ] && die "容器内没找到任何备份（$OUTDIR）—— 先跑 backup.sh"
  info "未指定文件 → 使用最新备份：$TARGET"
fi
SRC="$OUTDIR/$TARGET"

# ── 2. 待恢复文件的存在性与完整性（拒绝把坏文件写进线上库）────────────────────
dc exec -T backend python - "$SRC" <<'PY' || die "待恢复文件不可用：$SRC（看上方原因）"
import os, sqlite3, sys

path = sys.argv[1]
if not os.path.isfile(path):
    print(f"[FAIL] 文件不存在：{path}")
    sys.exit(1)
try:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    tables = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    bad = con.execute("pragma integrity_check").fetchone()[0]
    con.close()
except sqlite3.DatabaseError as e:
    print(f"[FAIL] 不是有效的 SQLite 文件：{e}")
    sys.exit(1)
print(f"[OK] 待恢复文件自检：表数={tables} integrity={bad}")
sys.exit(0 if bad == "ok" and tables > 0 else 1)
PY

# ── 3. 二次确认（除非 FORCE=1）────────────────────────────────────────────────
if [ "$FORCE" != "1" ]; then
  warn "即将用 $SRC **覆盖** $DB（当前线上库）。"
  printf "确认请输入 yes："
  read -r ANS
  [ "$ANS" = "yes" ] || die "已取消（未输入 yes）"
fi

# ── 4. 先把当前库抢救一份（万一恢复错了还能滚回来）────────────────────────────
info "恢复前先备份当前库..."
./deploy/hk/backup.sh >/dev/null 2>&1 && info "当前库已另存一份" \
  || warn "当前库备份失败（服务可能已停），继续 —— 风险自负"

# ── 5. 停服务 → 原子替换 → 起服务 ─────────────────────────────────────────────
info "停止 backend（避免写入中的句柄干扰替换）..."
dc stop backend

info "替换数据库文件..."
dc run --rm --entrypoint sh -e "BACKUP_SUBDIR=$BACKUP_SUBDIR" backend -c '
set -e
src="$1"; db="/app/data/app.db"
cp -f "$src" "$db.tmp"
# SQLite 的 -wal/-shm 必须与主库匹配；换了主库就必须清掉旧的 WAL/SHM 文件，
# 否则会用旧 WAL 覆盖新数据（这是恢复场景最容易踩的坑）。
rm -f "$db-wal" "$db-shm"
mv -f "$db.tmp" "$db"
echo "[OK] 已替换 $db"
' _ "$SRC"

info "启动 backend..."
dc up -d backend

info "等待健康检查..."
for i in $(seq 1 30); do
  if dc exec -T backend python -c \
      "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" \
      >/dev/null 2>&1; then
    echo
    info "恢复完成并通过健康检查 ✅ （第 ${i} 次探测）"
    info "请再抽查一条业务数据确认（如登录 / 曲目列表）"
    exit 0
  fi
  sleep 2
done

echo
dc logs --tail=60 backend || true
die "恢复后健康检查未通过 —— 服务可能起不来，现库已被替换，请立刻用第 4 步生成的备份回滚：
     ls /app/data/${BACKUP_SUBDIR}   # 选一份刚才的app-<时间戳>.db 再跑一次 restore.sh"