#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SQLite 在线备份（安全一致性 + 保留策略）
#
# 为什么用 Python 的 sqlite3 backup API 而不是直接 cp 文件：
#   直接复制**正在写入**的 SQLite 文件可能得到损坏/不一致的快照。
#   backup API 是在线一致性备份（相当于 .backup 命令），服务无需停机。
#   python:3.11-slim 自带 sqlite3 模块，容器内可直接用。
#
# 用法：
#   /opt/flow/deploy/hk/backup.sh            # 备份并清理过期文件
#   KEEP_DAYS=30 ./backup.sh                 # 自定义保留天数（默认 14）
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/flow}"
KEEP_DAYS="${KEEP_DAYS:-14}"
BACKUP_SUBDIR="${BACKUP_SUBDIR:-backups}"

cd "$APP_DIR"

# ⚠ 与 bootstrap.sh 同理：compose 的 ${VAR:?} 在 exec/ps 上同样插值，
#   必须显式 --env-file，否则备份会莫名其妙地“执行了但没结果”。
dc() { docker compose --env-file "$APP_DIR/.env" "$@"; }

# 容器内执行在线备份（写到同一 volume 的 backups/ 下）
DST="$(dc exec -T -e "BACKUP_SUBDIR=$BACKUP_SUBDIR" backend python - <<'PY'
import datetime, os, sqlite3

src = "/app/data/app.db"
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
outdir = "/app/data/" + os.environ.get("BACKUP_SUBDIR", "backups")
os.makedirs(outdir, exist_ok=True)
dst = os.path.join(outdir, f"app-{stamp}.db")

if not os.path.exists(src):
    print("NO_DB")
else:
    s = sqlite3.connect(src)
    b = sqlite3.connect(dst)
    with b:
        s.backup(b)          # 在线一致性备份
    b.close()
    s.close()
    print(dst)
PY
)"

if [ "$DST" = "NO_DB" ] || [ -z "$DST" ]; then
  echo "[WARN] 未找到 /app/data/app.db —— 跳过备份（是否还没产生数据？）"
  exit 0
fi

echo "[OK] 备份完成：$DST"

# 备份完必须**立刻自检完整性** —— 不然很可能到真要恢复那天才发现备份一直是坏的
# （磁盘满、WAL 异常、容器内权限问题都会产出能 ls 到但打不开的 .db）。
if ! dc exec -T backend python - "$DST" <<'PY'
import sqlite3, sys

path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
tables = con.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
bad = con.execute("pragma integrity_check").fetchone()[0]
con.close()
print(f"[OK] 备份自检：{path} 表数={tables} integrity={bad}")
sys.exit(0 if bad == "ok" and tables > 0 else 1)
PY
then
  echo "[FAIL] 备份完整性自检未通过 —— **该备份不可用于恢复**，请立即排查（磁盘/权限/WAL）"
  exit 1
fi

# 保留策略：删除超过 KEEP_DAYS 天的备份
dc exec -T -e "BACKUP_SUBDIR=$BACKUP_SUBDIR" backend python - "$KEEP_DAYS" <<'PY'
import os, sys, time

days = int(sys.argv[1])
outdir = "/app/data/" + os.environ.get("BACKUP_SUBDIR", "backups")
if not os.path.isdir(outdir):
    sys.exit(0)
cutoff = time.time() - days * 86400
removed = 0
for name in os.listdir(outdir):
    p = os.path.join(outdir, name)
    if os.path.isfile(p) and name.startswith("app-") and name.endswith(".db"):
        if os.path.getmtime(p) < cutoff:
            os.remove(p)
            removed += 1
print(f"[OK] 清理过期备份 {removed} 个（保留 {days} 天）")
PY

# 可选：同步到宿主机目录，便于再异地拷贝
if [ -n "${HOST_BACKUP_DIR:-}" ]; then
  mkdir -p "$HOST_BACKUP_DIR"
  CID="$(dc ps -q backend)"
  docker cp "$CID:/app/data/${BACKUP_SUBDIR}/." "$HOST_BACKUP_DIR/" \
    && echo "[OK] 已同步到宿主机 $HOST_BACKUP_DIR"
fi
