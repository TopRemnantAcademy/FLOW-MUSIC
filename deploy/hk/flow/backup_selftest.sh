#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# flow/backup.sh 的 mock 自检（不装 Docker / 不联网 / 不碰真实库）
#
# 把 docker / openssl 换成 mock，覆盖关键分支：
#   · 正常备份：构造出正确的 `docker run --entrypoint python3` + ro/rw 挂载
#   · 缺 gospel.db：warn + 跳过 + 不调 docker run
#   · 密钥默认跳过 / BACKUP_SECRETS=1 加密 / 缺口令即 die
#   · Qdrant 默认跳过 / BACKUP_QDRANT=1 触发快照
#   · HOST_BACKUP_DIR 异地同步
#
# 退出码 0 = 全通过。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SH="$SCRIPT_DIR/backup.sh"

PASS=0; FAIL=0
ok()   { echo -e "\033[0;32m[PASS]\033[0m $1"; PASS=$((PASS+1)); }
bad()  { echo -e "\033[0;31m[FAIL]\033[0m $1"; FAIL=$((FAIL+1)); }

# ── mock 基础设施（docker / openssl），MOCKLOG 必须 export 给子进程 ──────────────
MOCKDIR="$(mktemp -d)"
MOCKLOG="$(mktemp)"
export MOCKDIR MOCKLOG
export PATH="$MOCKDIR:$PATH"
trap 'rm -rf "$MOCKDIR" "$MOCKLOG" "${WORKS[@]}"' EXIT
WORKS=()

write_mocks() {
  cat > "$MOCKDIR/docker" <<'SH'
#!/usr/bin/env bash
echo "DOCKER $*" >> "$MOCKLOG"
case "$1" in
  image) exit 0 ;;                                       # image inspect → 存在，不拉取
  pull)  exit 0 ;;
  compose)
    for a in "$@"; do [ "$a" = "ps" ] && echo "fakecid"; done   # ps -q qdrant → cid（让 cp 分支跑）
    exit 0 ;;
  cp)    echo "DOCKER cp $*" >> "$MOCKLOG"; exit 0 ;;
  run)
    bdir=""
    for a in "$@"; do case "$a" in *:/backups) bdir="${a%%:/backups}";; esac; done
    if [ -n "$bdir" ]; then
      mkdir -p "$bdir"
      ts="$(date -u +%Y%m%d-%H%M%S)"
      touch "$bdir/gospel-${ts}.db"
      echo "[OK] 备份=$bdir/gospel-${ts}.db 表数=5 integrity=ok"
      echo "[OK] 清理过期备份 0 个（保留 14 天）"
    fi
    exit 0 ;;
  *) exit 0 ;;
esac
SH
  cat > "$MOCKDIR/openssl" <<'SH'
#!/usr/bin/env bash
echo "OPENSSL $*" >> "$MOCKLOG"
prev=""; out=""
for a in "$@"; do [ "$prev" = "-out" ] && out="$a"; prev="$a"; done
[ -n "$out" ] && : > "$out"
exit 0
SH
  chmod +x "$MOCKDIR/docker" "$MOCKDIR/openssl"
}

# 运行一次 backup.sh；$1=appdir，后续为环境变量覆盖
# ⚠ APP_DIR 用字面量赋值（在 env 之前），其余覆盖变量经 env 传入，
#    否则 "$@" 展开在命令行中部会关闭 bash 的「赋值区」→ 变量被当成命令（command not found）。
run_bak() {
  local appdir="$1"; shift
  mkdir -p "$appdir"
  WORKS+=("$appdir")
  APP_DIR="$appdir" env "$@" bash "$BACKUP_SH" > "$appdir/out.log" 2>&1
  echo $? > "$appdir/rc"
}

# ── 场景 1：正常备份（gospel.db 存在）─────────────────────────────────────────
W="$(mktemp -d)"; write_mocks; : > "$MOCKLOG"
: > "$W/gospel.db"
run_bak "$W"
RC="$(cat "$W/rc")"
[ "$RC" = "0" ] && ok "S1 正常备份退出码=0" || bad "S1 正常备份退出码=$RC"
grep -q "DOCKER run.*--entrypoint python3" "$MOCKLOG" && ok "S1 调用 docker run --entrypoint python3" || bad "S1 未构造 docker run --entrypoint python3"
grep -q ":/app/gospel.db:ro" "$MOCKLOG" && ok "S1 源以 ro 挂载" || bad "S1 缺少 :/app/gospel.db:ro 挂载"
grep -q ":/backups" "$MOCKLOG" && ok "S1 备份目录以 rw 挂载" || bad "S1 缺少 :/backups 挂载"
ls "$W"/backups/gospel-*.db >/dev/null 2>&1 && ok "S1 产出 gospel-*.db 备份文件" || bad "S1 未产出备份文件"
grep -q "跳过 Qdrant" "$W/out.log" && ok "S1 默认跳过 Qdrant" || bad "S1 未跳过 Qdrant"
grep -q "未备份 .env.production" "$W/out.log" && ok "S1 默认跳过密钥备份（明文保护）" || bad "S1 不应默认备份密钥"
! grep -q "OPENSSL" "$MOCKLOG" && ok "S1 未触发 openssl（默认不加密）" || bad "S1 默认不应调用 openssl"

# ── 场景 2：缺 gospel.db ───────────────────────────────────────────────────────
W="$(mktemp -d)"; write_mocks; : > "$MOCKLOG"
run_bak "$W"
RC="$(cat "$W/rc")"
[ "$RC" = "0" ] && ok "S2 缺 gospel.db 退出码=0（优雅跳过）" || bad "S2 退出码=$RC"
! grep -q "DOCKER run" "$MOCKLOG" && ok "S2 未调用 docker run（无库可备）" || bad "S2 不应调用 docker run"
grep -q "未找到.*gospel.db" "$W/out.log" && ok "S2 打印跳过提示" || bad "S2 缺跳过提示"

# ── 场景 3：BACKUP_SECRETS=1 + 口令 → 加密 ─────────────────────────────────────
W="$(mktemp -d)"; write_mocks; : > "$MOCKLOG"
: > "$W/gospel.db"; : > "$W/.env.production"
run_bak "$W" BACKUP_SECRETS=1 BACKUP_PASSPHRASE="s3cr3t"
RC="$(cat "$W/rc")"
[ "$RC" = "0" ] && ok "S3 密钥加密备份退出码=0" || bad "S3 退出码=$RC"
grep -q "OPENSSL" "$MOCKLOG" && ok "S3 调用 openssl enc" || bad "S3 未调用 openssl"
[ -f "$W/backups/.env.production.enc" ] && ok "S3 产出 .env.production.enc" || bad "S3 未产出加密密钥文件"
! grep -q "未备份 .env.production" "$W/out.log" && ok "S3 不再打印默认跳过提示" || bad "S3 仍打印跳过提示"

# ── 场景 4：BACKUP_SECRETS=1 缺口令 → die ─────────────────────────────────────
W="$(mktemp -d)"; write_mocks; : > "$MOCKLOG"
: > "$W/gospel.db"
run_bak "$W" BACKUP_SECRETS=1
RC="$(cat "$W/rc")"
[ "$RC" != "0" ] && ok "S4 缺口令时 die（非零退出）" || bad "S4 缺口令却退出 0（应拒绝明文备份）"

# ── 场景 5：HOST_BACKUP_DIR 异地同步 ───────────────────────────────────────────
W="$(mktemp -d)"; SYNC="$(mktemp -d)"; WORKS+=("$SYNC"); write_mocks; : > "$MOCKLOG"
: > "$W/gospel.db"
run_bak "$W" HOST_BACKUP_DIR="$SYNC"
RC="$(cat "$W/rc")"
[ "$RC" = "0" ] && ok "S5 异地同步退出码=0" || bad "S5 退出码=$RC"
ls "$SYNC"/gospel-*.db >/dev/null 2>&1 && ok "S5 备份已同步到 HOST_BACKUP_DIR" || bad "S5 未同步到异地"

# ── 场景 6：BACKUP_QDRANT=1 本地快照（云端 URL 未设）──────────────────────────
W="$(mktemp -d)"; write_mocks; : > "$MOCKLOG"
: > "$W/gospel.db"; printf 'QDRANT_API_KEY=xxx\n' > "$W/.env.production"
run_bak "$W" BACKUP_QDRANT=1
RC="$(cat "$W/rc")"
[ "$RC" = "0" ] && ok "S6 本地 qdrant 快照退出码=0" || bad "S6 退出码=$RC"
grep -q "qdrant snapshot" "$MOCKLOG" && ok "S6 触发 qdrant snapshot create" || bad "S6 未触发 qdrant 快照"
grep -q "DOCKER cp" "$MOCKLOG" && ok "S6 导出快照到备份目录" || bad "S6 未导出快照"

# ── 场景 7：BACKUP_QDRANT=1 但设了云端 QDRANT_URL → 跳过 ───────────────────────
W="$(mktemp -d)"; write_mocks; : > "$MOCKLOG"
: > "$W/gospel.db"; printf 'QDRANT_URL=https://qdrant.example.com\n' > "$W/.env.production"
run_bak "$W" BACKUP_QDRANT=1
RC="$(cat "$W/rc")"
[ "$RC" = "0" ] && ok "S7 云端 QDRANT_URL 跳过本地快照退出码=0" || bad "S7 退出码=$RC"
! grep -q "qdrant snapshot" "$MOCKLOG" && ok "S7 检测到云端 URL → 不调本地快照" || bad "S7 云端 URL 仍触发本地快照"
grep -q "由厂商备份" "$W/out.log" && ok "S7 打印厂商备份提示" || bad "S7 缺厂商备份提示"

echo
echo "══════════════════════════════════════════════"
echo " flow/backup_selftest  通过=$PASS  失败=$FAIL"
echo "══════════════════════════════════════════════"
[ "$FAIL" = "0" ] && exit 0 || exit 1
