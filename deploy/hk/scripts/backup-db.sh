#!/usr/bin/env bash
# =========================================================
# FLOW SQLite 일일 백업 (단일 노드 배포 — 백업 없으면 데이터 손실)
#
# 왜 필요한가:
#   이 배포는 SQLite 단일 파일(/app/data/gospel.db) 에 구독자·대화이력·결제를
#   모두 저장한다. 디스크 장애나 오삭제 시 복구 수단이 없으므로 일일 스냅샷이 필수.
#
# 안전한 복사:
#   단순 `cp` 는 쓰기 중인 DB 를 복사해 깨질 수 있다 → sqlite3 의 `.backup`
#   (온라인 백업 API) 을 우선 사용하고, sqlite3 CLI 가 없으면 cp 로 폴백.
#
# 보관: KEEP_DAYS(기본 14일) 초과분 자동 삭제.
#
# 설치(cron, 매일 03:00):
#   sudo install -m 755 deploy/hk/scripts/backup-db.sh /opt/flow/scripts/backup-db.sh
#   (crontab -l 2>/dev/null; echo "0 3 * * * /opt/flow/scripts/backup-db.sh >> /opt/flow/logs/backup.log 2>&1") | crontab -
# =========================================================
set -euo pipefail

SRC_DIR="${SRC_DIR:-/opt/flow/deploy/hk/data}"
BAK_DIR="${BAK_DIR:-/opt/flow/backups}"
LOG_DIR="${LOG_DIR:-/opt/flow/logs}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BAK_DIR" "$LOG_DIR"

# 중단/실패 시 .partial 이나간-stage 를 즉시 치운다(백업 디렉터리 오염 방지).
_stage=""
trap '[ -n "$_stage" ] && rm -f "$_stage" "$_stage.gz" 2>/dev/null || true' EXIT
TS="$(date +%Y%m%d-%H%M%S)"
found=0

for db in "$SRC_DIR"/*.db; do
    [ -e "$db" ] || continue
    found=1
    name="$(basename "$db")"
    out="$BAK_DIR/${name%.db}-$TS.db"
    # [2026-10-04] 실패해도 backups/ 가 오염되지 않도록:
    #   임시(.partial)에 쓰고 → 성공 시에만 최종 이름으로 mv, 실패/중단 시 trap 이 지운다.
    stage="$out.partial"
    _stage="$stage"

    # 2026-10-04 수정: WAL 안전 백업.
    #   이전 cp 분기는 journal_mode=wal DB 에서 **-wal 을 복사하지 않아**
    #   체크포인트되지 않은 행이 통째로 유실됐다(실측: gospel.db 주 DB activity_event=0
    #   인데 실제 값은 62 → 4/4 백업이 0으로 기록돼 있었다).
    #   원인은 서버에 sqlite3 CLI 가 없어 cp 로 폴백했기 때문.
    #   이제 python3 의 온라인 백업 API(= sqlite3 .backup 와 동등)를 2순위로 쓴다.
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "$db" ".backup '$stage'"
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$db" "$stage" <<'PYBACKUP'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src, timeout=30)
d = sqlite3.connect(dst)
try:
    s.backup(d, pages=256)   # 분할 배치 → 락 보유 시간 짧음, WAL 자동 포함
finally:
    d.close(); s.close()
PYBACKUP
    else
        # python3 도 없으면 조용히 약한 백업을 만들지 않고 **실패시킨다**(2026-10-04).
        # 이전처럼 cp 로 넘어가면 "백업 성공" 로그만 남고 데이터는 유실된다.
        echo "[$(date -Is)] ERROR 백업 도구 없음(sqlite3/python3 모두 미설치): $db" >&2
        exit 1
    fi
    gzip -f "$stage"
    mv "$stage.gz" "$out.gz"
    _stage=""
    echo "[$(date -Is)] backup $(basename "$out").gz"
done

if [ "$found" -eq 0 ]; then
    echo "[$(date -Is)] WARN 백업할 .db 없음 (SRC_DIR=$SRC_DIR)"
fi

find "$BAK_DIR" -name '*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "[$(date -Is)] 보관 정리 완료 (KEEP_DAYS=$KEEP_DAYS)"
