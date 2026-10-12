#!/usr/bin/env bash
# =========================================================
# deploy/hk/schema-cutover.sh — 把 prod 库切回「迁移管辖」，一次性重建 schema
#
# 为什么是这个顺序（ISSUE #107 坑 2，实测踩过）：
#   正确顺序 = stop 应用 → 备份 → DROP SCHEMA → alembic upgrade head → start 应用 → 校验
#   反例（错误做法）：应用还在跑时就 DROP SCHEMA + 跑 alembic。应用 lifespan 里的
#   `Base.metadata.create_all`（SCHEMA_CREATE_ALL=1）会**抢建** 13 张表，随后 alembic
#   撞上「表已存在」失败；PG 的 DDL 是**事务性**的 → 整个 upgrade 回滚，
#   `alembic_version` 根本没建成，而日志里**没有任何显式报错**。
#   所以：**先停应用**，并把 SCHEMA_CREATE_ALL 置 0，再让 alembic 独占建表。
#
# 用法：
#   ./deploy/hk/schema-cutover.sh [--dry-run] [--force]
#     --dry-run   打印每一步将执行的命令与前置校验结果，不连服务器、不落盘
#     --force     允许在 users/songs 已有真实数据时执行（默认拒绝）
#
# 环境变量：
#   APP_DIR      部署目录（默认 /opt/flow）
#   COMPOSE_FILE compose 文件（默认 $APP_DIR/docker-compose.yml）
#   SERVICE      应用服务名（默认 music）
#   PG_SERVICE   PG 容器服务名（默认 flow-hk-music-postgres）
#   DB_NAME/PG_USER  默认 flow_music / flow_music
#   PUBLIC_HEALTH_URL  公网健康检查地址（默认从 MUSIC_DOMAIN 推导）
# =========================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/opt/flow}"
COMPOSE_FILE="${COMPOSE_FILE:-$APP_DIR/docker-compose.yml}"
SERVICE="${SERVICE:-music}"
PG_SERVICE="${PG_SERVICE:-flow-hk-music-postgres}"
DB_NAME="${DB_NAME:-flow_music}"
PG_USER="${PG_USER:-flow_music}"
BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"
PUBLIC_HEALTH_URL="${PUBLIC_HEALTH_URL:-}"
PUBLIC_PROBE="${PUBLIC_PROBE:-1}"

# 种子表：DROP SCHEMA 后必须由迁移补回，行数为 0 即视为 cutover 未完成。
SEED_TABLES=(engine_configs pricing_tiers routing_rules)
# 业务数据表：非空即代表这是生产库，未经 --force 不允许 DROP。
COMPOSE_ARGS=()

DRY_RUN=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1; PUBLIC_PROBE=0 ;;
        --force)   FORCE=1 ;;
        -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '[cutover] %s\n' "$*"; }
warn() { printf '[cutover] WARN %s\n' "$*" >&2; }
die()  { printf '[cutover] ERROR %s\n' "$*" >&2; exit 1; }
step() { printf '\n===== %s =====\n' "$*"; }

# 在 dry-run 下只回显，在真实模式下才执行 —— 保证「演示不连服务器」。
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  $ %s\n' "$*"
    else
        "$@"
    fi
}
# 只读查询：dry-run 下返回假数据，绝不触网。
psql_val() {
    local sql="$1" fake="${2:-0}"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '%s\n' "$fake"
    else
        docker compose "${COMPOSE_ARGS[@]}" exec -T "$PG_SERVICE" \
            psql -U "$PG_USER" -d "$DB_NAME" -tAc "$sql"
    fi
}
psql_exec() {
    local sql="$1"
    run docker compose "${COMPOSE_ARGS[@]}" exec -T "$PG_SERVICE" \
        psql -U "$PG_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "$sql"
}

# ---- 0. 前置：compose 配置可用 -------------------------------------------
if [ -n "${COMPOSE_FILE_OVERRIDES:-}" ]; then
    # 允许额外 -f 覆盖文件（共置部署时用 docker-compose.colocation.yml）
    # shellcheck disable=SC2206
    COMPOSE_ARGS+=(-f "$COMPOSE_FILE" ${COMPOSE_FILE_OVERRIDES})
else
    COMPOSE_ARGS=(-f "$COMPOSE_FILE")
fi
# 明确列出生效文件，避免「静默用了别的 compose」这类事故。
log "compose 文件: ${COMPOSE_ARGS[*]}"
log "服务=$SERVICE  PG服务=$PG_SERVICE  库=$DB_NAME  DRY_RUN=$DRY_RUN  FORCE=$FORCE"

if [ "$DRY_RUN" -eq 0 ]; then
    [ -f "$COMPOSE_FILE" ] || die "compose 文件不存在: $COMPOSE_FILE"
    command -v docker >/dev/null 2>&1 || die "未安装 docker"
    docker compose version >/dev/null 2>&1 || die "docker compose 不可用"
    (cd "$APP_DIR" && docker compose "${COMPOSE_ARGS[@]}" config >/dev/null) \
        || die "compose 配置无效（\`docker compose config\` 失败）—— 请先修配置，勿带病上线"
fi

mkdir -p "$BACKUP_DIR"

# ---- 1. 数据保护闸门（默认拒绝覆盖生产数据） ------------------------------
step "1/8 数据保护闸门"
GUARD_SQL="SELECT table_name || '=' || (xpath('/row/c/text()', query_to_xml('SELECT count(*) AS c FROM public.' || quote_ident(table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','songs') ORDER BY table_name;"
TOTAL_ROWS=0
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  $ docker compose ${COMPOSE_ARGS[*]} exec -T $PG_SERVICE psql -U $PG_USER -d $DB_NAME ...（守卫查询）"
    echo "  <演示> 假设 users=0 songs=0 → 闸门通过"
elif [ "$FORCE" -eq 1 ]; then
    warn "--force 已指定：跳过 users/songs 非空闸门（危险操作已由操作者显式确认）"
else
    GUARD_OUT="$(psql_val "$GUARD_SQL" "")"
    printf '%s\n' "${GUARD_OUT//$'\n'/$'\n  '}" | sed 's/^/  /'
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        n="${line##*=}"
        case "$n" in ''|*[!0-9]*) n=0 ;; esac
        TOTAL_ROWS=$((TOTAL_ROWS + n))
    done <<<"$GUARD_OUT"
    if [ "$TOTAL_ROWS" -gt 0 ]; then
        cat >&2 <<EOF
❌ 检测到 users/songs 有真实数据（合计 $TOTAL_ROWS 行）。
   本脚本会 DROP SCHEMA public CASCADE，继续执行将**不可逆地删除生产数据**。
   如确认要重建：先手工备份并核对 $BACKUP_DIR，再加 --force 重跑。
EOF
        exit 1
    fi
    log "闸门通过：users/songs 均空（合计 0 行）"
fi

# ---- 2. pg_dump 备份（带时间戳） -----------------------------------------
step "2/8 pg_dump 备份"
TS="$(date +%Y%m%d-%H%M%S)"
DUMP="$BACKUP_DIR/${DB_NAME}-schema-cutover-$TS.sql"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  $ docker compose ${COMPOSE_ARGS[*]} exec -T $PG_SERVICE pg_dump -U $PG_USER -d $DB_NAME --clean --if-exists"
    echo "  > $DUMP   （未落盘）"
else
    docker compose "${COMPOSE_ARGS[@]}" exec -T "$PG_SERVICE" \
        pg_dump -U "$PG_USER" -d "$DB_NAME" --clean --if-exists >"$DUMP" \
        || die "pg_dump 失败 —— 中途中止，库未改动。请检查 PG 日志与磁盘空间。"
    [ -s "$DUMP" ] || die "备份文件为空: $DUMP —— 已中止（空备份等于没有备份）"
    log "备份完成: $DUMP ($(wc -c <"$DUMP") bytes)"
    log "回滚命令: docker compose ${COMPOSE_ARGS[*]} exec -T $PG_SERVICE psql -U $PG_USER -d $DB_NAME < $DUMP"
fi

# ---- 3. stop 应用（必须早于 DROP SCHEMA） ---------------------------------
step "3/8 停止应用（防止 create_all 抢建表）"
run docker compose "${COMPOSE_ARGS[@]}" stop "$SERVICE"

# ---- 4. DROP + CREATE SCHEMA --------------------------------------------
step "4/8 重建 schema（DROP SCHEMA public CASCADE; CREATE SCHEMA public;）"
psql_exec "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql_exec "GRANT ALL ON SCHEMA public TO $PG_USER;"

# ---- 5. alembic upgrade head（一次性容器，不依赖已停的应用） --------------
step "5/8 alembic upgrade head（一次性容器）"
# SCHEMA_CREATE_ALL=0：让建表权**唯一**归 alembic，杜绝 create_all 抢建。
run docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps \
    -e SCHEMA_CREATE_ALL=0 -e DATABASE_URL="postgresql+psycopg://$PG_USER@$PG_SERVICE:5432/$DB_NAME" \
    --entrypoint alembic "$SERVICE" upgrade head

# ---- 6. 重启应用 ---------------------------------------------------------
step "6/8 启动应用"
run docker compose "${COMPOSE_ARGS[@]}" up -d "$SERVICE"

# ---- 7. 校验 -------------------------------------------------------------
step "7/8 校验"
FAIL=0
check() { # check <描述> <实际> <期望>
    local desc="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
        printf '  ✅ %-28s %s\n' "$desc" "$got"
    else
        printf '  ❌ %-28s got=%s want=%s\n' "$desc" "$got" "$want" >&2
        FAIL=1
    fi
}

HEAD_SQL="SELECT version_num FROM alembic_version LIMIT 1;"
TABLES_SQL="SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "  <演示> alembic_version     = (被校验: 存在且 == alembic heads)"
    for t in "${SEED_TABLES[@]}"; do
        echo "  <演示> $t 行数   = (被校验，要求 > 0)"
    done
    echo "  <演示> 表数量              = (被校验)"
    echo "  <演示> alembic current     = (被校验)"
    echo "  <演示> 公网 /health        = (被校验，要求 {\"status\":\"ok\"})"
else
    # 7a. alembic_version 存在且等于当前 head
    HEAD_LOCAL="$(cd "$APP_DIR" && docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps \
        -e SCHEMA_CREATE_ALL=0 --entrypoint alembic "$SERVICE" heads 2>/dev/null \
        | awk '{print $1}' | head -1)"
    DB_VER="$(psql_val "$HEAD_SQL" "")"
    DB_VER="$(printf '%s' "$DB_VER" | tr -d '[:space:]')"
    [ -n "$HEAD_LOCAL" ] || { warn "无法取到 alembic heads"; FAIL=1; }
    [ -n "$DB_VER" ] || { warn "alembic_version 无记录 —— 迁移很可能整体回滚（ISSUE #107 坑 2）"; FAIL=1; }
    check "alembic_version == head" "$DB_VER" "$HEAD_LOCAL"

    # 7b. alembic current 输出
    echo "  --- alembic current ---"
    (cd "$APP_DIR" && docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps \
        -e SCHEMA_CREATE_ALL=0 --entrypoint alembic "$SERVICE" current) | sed 's/^/  /'

    # 7c. 表数量（>0 且不应只有 alembic_version 一张）
    TABLES_N="$(psql_val "$TABLES_SQL" "0")"
    TABLES_N="$(printf '%s' "$TABLES_N" | tr -d '[:space:]')"
    if [ "${TABLES_N:-0}" -gt 1 ]; then
        printf '  ✅ %-28s %s\n' "表数量" "$TABLES_N"
    else
        printf '  ❌ %-28s got=%s want>1\n' "表数量" "${TABLES_N:-0}" >&2
        FAIL=1
    fi

    # 7d. 种子行数
    for t in "${SEED_TABLES[@]}"; do
        n="$(psql_val "SELECT count(*) FROM $t;" "0")"
        n="$(printf '%s' "$n" | tr -d '[:space:]')"
        if [ "${n:-0}" -gt 0 ]; then
            printf '  ✅ %-28s %s\n' "$t 行数" "$n"
        else
            printf '  ❌ %-28s got=%s want>0（种子未补回）\n' "$t 行数" "${n:-0}" >&2
            FAIL=1
        fi
    done

    # 7e. 公网 /health
    if [ "$PUBLIC_PROBE" -eq 1 ]; then
        URL="$PUBLIC_HEALTH_URL"
        [ -n "$URL" ] || die "PUBLIC_HEALTH_URL 未设置（或用 PUBLIC_PROBE=0 跳过公网探测）"
        BODY="$(curl -fsS --max-time 10 "$URL" 2>/dev/null || true)"
        check "公网 /health" "$(printf '%s' "$BODY" | tr -d '[:space:]')" '{"status":"ok"}'
    else
        log "PUBLIC_PROBE=0，跳过公网 /health 探测"
    fi
fi

# ---- 8. 结果 -------------------------------------------------------------
step "8/8 结果"
if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN 结束：以上为将执行的全部命令，未连服务器、未落盘。"
    exit 0
fi
if [ "$FAIL" -ne 0 ]; then
    cat >&2 <<EOF
❌ cutover 校验未通过。**不要**就此收工。
回滚方式（二选一）：
  A. 从备份恢复:
     docker compose ${COMPOSE_ARGS[*]} stop $SERVICE
     docker compose ${COMPOSE_ARGS[*]} exec -T $PG_SERVICE psql -U $PG_USER -d $DB_NAME < $DUMP
     docker compose ${COMPOSE_ARGS[*]} up -d $SERVICE
  B. 退回 SQLite（临时保命）:
     改 .env 的 MUSIC_DATABASE_URL/DATABASE_URL 为 sqlite:////app/data/app.db，
     SCHEMA_CREATE_ALL=1，然后 docker compose up -d --force-recreate $SERVICE
EOF
    exit 1
fi
log "✅ cutover 完成：schema 由 alembic 唯一管辖，种子与 /health 校验通过。"
