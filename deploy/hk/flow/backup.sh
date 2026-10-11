#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# FLOW(CCM) 备份脚本 —— 主机视角（gospel.db 是主机 bind mount 文件）
#
# 与音乐平台 deploy/hk/backup.sh 的关键差异：
#   · 音乐平台 app.db 在容器内 /app/data/app.db（bind mount 目录）→ 备份在容器内跑；
#   · FLOW 的 gospel.db 是【主机文件】$APP_DIR/gospel.db
#     （compose 里 ./gospel.db:/app/gospel.db），故本脚本直接在【主机侧】用一次性
#     容器（挂主机文件 ro + 备份目录 rw）做在线一致性备份。
#     → 不依赖 backend 是否在跑，cron 更稳；也不需要往 compose 里塞额外 volume。
#
# 为什么用 sqlite3.backup()（在线一致性备份）而不是直接 cp：
#   直接复制正在写入的 SQLite 文件可能得到损坏/不一致快照。backup() 是在线一致性
#   备份（等价 .backup 命令），服务无需停机。python:3.11-slim 自带 sqlite3 模块。
#
# 备份内容：
#   1. gospel.db  —— SQLite 在线一致性备份，备份后立刻 integrity_check 自检
#   2. .env.production —— 默认【跳过】（避免明文密钥落盘）；
#                        设 BACKUP_SECRETS=1 + BACKUP_PASSPHRASE 才 AES-256-CBC 加密备份
#   3. Qdrant    —— 默认【跳过】；BACKUP_QDRANT=1 且使用本地 qdrant 时才快照。
#                        云端 QDRANT_URL 由厂商备份，无需本地处理。
#
# 用法：
#   ./backup.sh                                    # 备份 gospel.db + 校验 + 清理过期
#   KEEP_DAYS=30 ./backup.sh                       # 保留 30 天（默认 14）
#   BACKUP_SECRETS=1 BACKUP_PASSPHRASE='***' ./backup.sh   # 同时加密备份密钥
#   HOST_BACKUP_DIR=/mnt/nas/flow ./backup.sh      # 备份后同步到异地
#   BACKUP_DIR=/data/flow-bak ./backup.sh          # 自定义备份目录
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/flow-ccm}"
KEEP_DAYS="${KEEP_DAYS:-14}"
BACKUP_SUBDIR="${BACKUP_SUBDIR:-backups}"            # 仅当 BACKUP_DIR 未显式指定时生效
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/$BACKUP_SUBDIR}"  # 默认 $APP_DIR/backups（独立于 data/）
BACKUP_SECRETS="${BACKUP_SECRETS:-0}"
BACKUP_QDRANT="${BACKUP_QDRANT:-0}"
HOST_BACKUP_DIR="${HOST_BACKUP_DIR:-}"
ENV_FILE="$APP_DIR/.env.production"

# gospel.db 实际文件名解析（部署修复后应用写入绑定挂载的 gospel.db；
# 本地/未注入 DATABASE_URL 时 db.py 默认 ROOT/.gospel.db）：
#   优先选已存在的；都不存在则按 compose 挂载名(gospel.db) 占位（脚本会 warn 跳过）。
if [ -f "$APP_DIR/gospel.db" ]; then
  DB_FILE="$APP_DIR/gospel.db"
elif [ -f "$APP_DIR/.gospel.db" ]; then
  DB_FILE="$APP_DIR/.gospel.db"
else
  DB_FILE="$APP_DIR/gospel.db"
fi

# 备份运行时镜像：固定用 python:3.11-slim（自带 sqlite3，且镜像不随业务代码变动）
BACKUP_IMAGE="python:3.11-slim"

info() { echo -e "\033[0;32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
die()  { echo -e "\033[0;31m[FAIL]\033[0m $*" >&2; exit 1; }

cd "$APP_DIR" || die "无法进入 $APP_DIR"
command -v docker >/dev/null 2>&1 || die "未安装 Docker —— 备份需要 Docker 运行一次性 Python 容器"

# 确保备份镜像可用（已存在则跳过拉取；否则尝试拉取一次）
if docker image inspect "$BACKUP_IMAGE" >/dev/null 2>&1; then
  :
else
  info "首次运行：拉取备份运行时镜像 $BACKUP_IMAGE ..."
  docker pull "$BACKUP_IMAGE" >/dev/null 2>&1 \
    || die "无法获取备份运行时镜像 $BACKUP_IMAGE（请先手动 docker pull $BACKUP_IMAGE）"
fi

mkdir -p "$BACKUP_DIR"

# ── 1. gospel.db 在线一致性备份 + 完整性校验 + 保留清理（一次性容器内完成）──────
if [ ! -f "$DB_FILE" ]; then
  warn "未找到 $DB_FILE —— 跳过 DB 备份（是否还没产生数据 / 首次部署前？）"
else
  STAMP="$(date -u +%Y%m%d-%H%M%S)"
  export STAMP KEEP_DAYS
  info "开始备份 gospel.db（在线一致性备份）..."
  # 注意：用 <<'PY'（引号）阻止 bash 展开；STAMP/KEEP_DAYS 经 -e 注入容器环境。
  if ! docker run --rm --entrypoint python3 \
        -e STAMP -e KEEP_DAYS \
        -v "$DB_FILE:/app/gospel.db:ro" \
        -v "$BACKUP_DIR:/backups" \
        "$BACKUP_IMAGE" - <<'PY'
import sqlite3, os, sys, time

src = "/app/gospel.db"
dst = f"/backups/gospel-{os.environ['STAMP']}.db"
keep = int(os.environ["KEEP_DAYS"])

# 在线一致性备份（源以只读事务打开，避免读到半截写入）
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
b = sqlite3.connect(dst)
try:
    with b:
        s.backup(b)
finally:
    b.close(); s.close()

# 备份完立刻自检完整性 —— 不然很可能到真要恢复那天才发现备份一直是坏的
c = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
try:
    tables = c.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
    bad = c.execute("pragma integrity_check").fetchone()[0]
finally:
    c.close()
print(f"[OK] 备份={dst} 表数={tables} integrity={bad}")
if not (bad == "ok" and tables > 0):
    sys.exit(1)

# 保留策略：删除超过 KEEP_DAYS 天的备份
cutoff = time.time() - keep * 86400
removed = 0
for n in os.listdir("/backups"):
    p = os.path.join("/backups", n)
    if n.startswith("gospel-") and n.endswith(".db") and os.path.isfile(p) \
       and os.path.getmtime(p) < cutoff:
        os.remove(p); removed += 1
print(f"[OK] 清理过期备份 {removed} 个（保留 {keep} 天）")
PY
  then
    die "gospel.db 备份/校验失败 —— **该备份不可用**，请立即排查（磁盘满 / 权限 / DB 被锁）"
  fi
  info "gospel.db 备份完成并校验通过。"
fi

# ── 2. 密钥备份（默认跳过，避免明文密钥落盘）──────────────────────────────────
if [ "$BACKUP_SECRETS" = "1" ]; then
  [ -z "${BACKUP_PASSPHRASE:-}" ] \
    && die "BACKUP_SECRETS=1 但 BACKUP_PASSPHRASE 为空 —— 拒绝以明文备份密钥"
  if [ -f "$ENV_FILE" ]; then
    ENC="$BACKUP_DIR/.env.production.enc"
    openssl enc -aes-256-cbc -salt -pbkdf2 \
      -pass "env:BACKUP_PASSPHRASE" -in "$ENV_FILE" -out "$ENC" \
      && info "密钥已加密备份：$ENC" \
      && info "恢复：openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in $ENC -out .env.production" \
      || warn "密钥加密备份失败（openssl 可用？）"
  else
    warn "$ENV_FILE 不存在 —— 跳过密钥备份"
  fi
else
  warn "未备份 .env.production（含明文密钥）。设 BACKUP_SECRETS=1 + BACKUP_PASSPHRASE 可加密备份；否则请从主保险库(.env.secrets)恢复。"
fi

# ── 3. Qdrant 备份（默认跳过；仅本地 qdrant 时有意义）────────────────────────
if [ "$BACKUP_QDRANT" = "1" ]; then
  # 复用 compose 封装（与 bootstrap 同理：${VAR:?} 在所有子命令插值，必须 --env-file）
  fc() { docker compose -f "$APP_DIR/docker-compose.prod.yml" --env-file "$ENV_FILE" "$@"; }
  QURL="$(grep -E '^QDRANT_URL=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  if [ -n "$QURL" ]; then
    warn "检测到 QDRANT_URL=$QURL（云端）→ 向量库由厂商备份，跳过本地快照"
  else
    info "本地 qdrant 快照中（best-effort）..."
    fc exec -T qdrant qdrant snapshot create 2>/dev/null || warn "qdrant 快照创建失败（容器未运行？）"
    CID="$(fc ps -q qdrant 2>/dev/null || true)"
    if [ -n "$CID" ]; then
      docker cp "$CID:/qdrant/storage/snapshots/." "$BACKUP_DIR/" 2>/dev/null \
        && info "qdrant 快照已导出到 $BACKUP_DIR" || warn "qdrant 快照导出失败"
    else
      warn "qdrant 容器未在运行 —— 跳过快照导出"
    fi
  fi
else
  info "跳过 Qdrant 备份（默认）。云端 QDRANT_URL 由厂商备份；本地 qdrant 可设 BACKUP_QDRANT=1 快照。"
fi

# ── 4. 异地同步（可选）──────────────────────────────────────────────────────
if [ -n "$HOST_BACKUP_DIR" ]; then
  mkdir -p "$HOST_BACKUP_DIR"
  cp -r "$BACKUP_DIR/." "$HOST_BACKUP_DIR/" \
    && info "已同步到异地 $HOST_BACKUP_DIR" || warn "异地同步失败"
fi

info "✅ FLOW 备份完成（gospel.db → $BACKUP_DIR）。"
