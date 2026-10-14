#!/usr/bin/env bash
# =========================================================
# deploy/hk/schema-cutover.sh — 把 prod 库切回「迁移管辖」，一次性重建 schema
#
# live 真实模型（ISSUE #125 按磁盘复核；改动前请以 live 实测为准）：
#   * `music` 是**镜像构建**服务（build.context = /opt/flow/ai-music-platform/backend），
#     代码在**构建期**进镜像 —— 只同步文件不重建镜像，代码变更不会生效。
#   * 该构建上下文目录**不是 git 仓库**（本仓的 backend/ 只是它的源），
#     所以不存在「在 APP_DIR 里 git pull」这条路。
#   * live 容器名是 `flow-hk-*` → compose 必须带 **-p flow-hk**，
#     否则操作的是默认项目（空栈），命令"成功"但什么也没做。
#   * `music` 服务被 compose **profile** 门控 → 每条子命令都要 **--profile music**。
#   * `music` 的变量来自 **/opt/flow/deploy/hk/.env.music** → 每条子命令都要
#     **--env-file**。漏掉时 compose 只解析 docker-compose.yml 里的字面默认值，
#     env_file 不参与插值（file provider 不支持插值）→ DATABASE_URL 退回
#     SQLite 空库（历史 502 / 变量覆盖事故）。
#
# 为什么是这个顺序（ISSUE #107 坑 2，实测踩过）：
#   正确顺序 = stop 应用 → 备份 → DROP SCHEMA → alembic upgrade head → start 应用 → 校验
#   反例（错误做法）：应用还在跑时就 DROP SCHEMA + 跑 alembic。应用 lifespan 里的
#   `Base.metadata.create_all`（SCHEMA_CREATE_ALL=1）会**抢建** 13 张表，随后 alembic
#   撞上「表已存在」失败；PG 的 DDL 是**事务性**的 → 整个 upgrade 回滚，
#   `alembic_version_music` 根本没建成，而日志里**没有任何显式报错**。
#   所以：**先停应用**，并把 SCHEMA_CREATE_ALL 置 0，再让 alembic 独占建表。
#
# 为什么版本表是 `alembic_version_music`（ISSUE #125 问题 ①，致命）：
#   X3 把本服务的版本表与 FLOW 分开（backend/migrations/env.py 的 `version_table=`，
#   离线/在线两处）。查 `alembic_version` **恒为空** → 脚本会误判"迁移未应用"，
#   进而触发错误的 DROP / 重建动作。
#
# 用法：
#   ./deploy/hk/schema-cutover.sh [--dry-run] [--force] [--yes] [--ref <git-ref>]
#     --dry-run   打印每一步将执行的命令与前置校验结果，不连服务器、不落盘
#     --force     允许在 users/songs 已有真实数据时执行（默认拒绝）
#     --yes       跳过第 0 步的交互确认（仅给非交互调用方用；默认必须确认）
#     --ref       同步代码用的 git ref（默认 origin/main；仅用于打印同步命令，不自动同步）
#
# 环境变量：
#   APP_DIR      部署目录（默认 /opt/flow）
#   COMPOSE_FILE compose 文件（默认 $APP_DIR/docker-compose.yml）
#   COMPOSE_PROJECT_NAME  compose 项目名（默认 flow-hk → live 容器 flow-hk-*）
#   ENV_FILE     变量文件（默认 $APP_DIR/deploy/hk/.env.music）
#   PROFILES     profile 名（默认 music）
#   SERVICE      应用服务名（默认 music）
#   BUILD_CONTEXT  镜像构建上下文（默认 /opt/flow/ai-music-platform/backend）
#   REPO_DIR     本仓工作副本（同步脚本所在仓库根，默认取脚本上级两级）
#   SYNC_REF     同步的 git ref（默认 origin/main）
#   PG_SERVICE   PG 容器服务名（默认 flow-hk-music-postgres）
#   PG_NETWORK   一次性迁移容器接入的网络（默认 ${COMPOSE_PROJECT_NAME}_flow-hk）
#   DB_NAME/PG_USER  默认 flow_music / flow_music
#   BACKUP_DIR   备份落盘目录（默认 /opt/flow/backups，与 live 存量备份同处）
#   PUBLIC_HEALTH_URL  公网健康检查地址（默认 https://music.anistar.studio/health）
#   PUBLIC_PROBE 1=做公网探测，0=跳过
# =========================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/opt/flow}"
COMPOSE_FILE="${COMPOSE_FILE:-$APP_DIR/docker-compose.yml}"
# live 容器名前缀 = compose 项目名；不传 -p 时 docker 用目录名当项目名 → 操作到空栈。
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-flow-hk}"
# music 服务由 compose profile 门控；不带 --profile 时该服务不在生效配置里。
PROFILES="${PROFILES:-music}"
# 变量文件：prod **必须**显式传。以 live 实测为准（历史上就是这个路径）。
ENV_FILE="${ENV_FILE:-$APP_DIR/deploy/hk/.env.music}"
SERVICE="${SERVICE:-music}"
# 构建上下文目录【不是 git 仓库】（以 live 实测为准）：本仓 backend/ 是它的源。
BUILD_CONTEXT="${BUILD_CONTEXT:-$APP_DIR/ai-music-platform/backend}"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
SYNC_REF="${SYNC_REF:-origin/main}"
PG_SERVICE="${PG_SERVICE:-flow-hk-music-postgres}"
# 一次性迁移容器必须接进 compose 网络，否则解析不到 flow-hk-music-postgres。
# 默认网络名 <项目名>_flow-hk —— 以 live `docker network ls` 实测为准。
PG_NETWORK="${PG_NETWORK:-${COMPOSE_PROJECT_NAME}_flow-hk}"
DB_NAME="${DB_NAME:-flow_music}"
PG_USER="${PG_USER:-flow_music}"
# 备份目录：与 live 存量备份同处（$APP_DIR/backups），不是脚本目录。
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
# prod 的 /openapi.json 已关闭（404），不能当探针；/health 走 Caddy。
PUBLIC_HEALTH_URL="${PUBLIC_HEALTH_URL:-https://music.anistar.studio/health}"
PUBLIC_PROBE="${PUBLIC_PROBE:-1}"

# alembic 版本表：X3 与本服务隔离后的名字。查错名字 = 恒为空 = 误判。
VERSION_TABLE="${VERSION_TABLE:-alembic_version_music}"
# 种子表：DROP SCHEMA 后必须由迁移补回，行数为 0 即视为 cutover 未完成。
SEED_TABLES=(engine_configs pricing_tiers routing_rules)

DRY_RUN=0
FORCE=0
ASSUME_YES=0
PREV_ARG=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1; PUBLIC_PROBE=0 ;;
        --force)   FORCE=1 ;;
        --yes)     ASSUME_YES=1 ;;
        --ref)     : ;;                                  # 值由下一轮循环取值
        -h|--help) sed -n '2,75p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*)        echo "未知参数: $arg" >&2; exit 2 ;;
    esac
    # --ref 是唯一带值的参数：上一轮是 --ref 时，本轮的值就是它。
    if [ "$PREV_ARG" = "--ref" ]; then
        case "$arg" in -*) echo "--ref 缺少参数值" >&2; exit 2 ;; esac
        SYNC_REF="$arg"
    fi
    PREV_ARG="$arg"
done
[ "$PREV_ARG" != "--ref" ] || { echo "--ref 需要一个 git ref 参数" >&2; exit 2; }

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

# ---- 0. compose 生效参数（-p / --env-file / --profile 一个都不能少） -------
# 三条都是 live 实测踩出来的：漏 -p → 空栈；漏 --env-file → SQLite 空库；
# 漏 --profile → 服务不在生效配置。组装成数组，后续所有 compose 调用共用。
COMPOSE_ARGS=(--project-name "$COMPOSE_PROJECT_NAME" --env-file "$ENV_FILE")
[ -n "$PROFILES" ] && COMPOSE_ARGS+=(--profile "$PROFILES")
if [ -n "${COMPOSE_FILE_OVERRIDES:-}" ]; then
    # 允许额外 -f 覆盖文件（共置部署时用 docker-compose.colocation.yml）
    # shellcheck disable=SC2206
    COMPOSE_ARGS+=(-f "$COMPOSE_FILE" ${COMPOSE_FILE_OVERRIDES})
else
    COMPOSE_ARGS+=(-f "$COMPOSE_FILE")
fi

# compose 子命令的短写（在 APP_DIR 下执行，保证相对 build.context 能解析）
dc() { (cd "$APP_DIR" && docker compose "${COMPOSE_ARGS[@]}" "$@"); }

# 只读查询：dry-run 下返回假数据，绝不触网。
psql_val() {
    local sql="$1" fake="${2:-0}"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '%s\n' "$fake"
    else
        dc exec -T "$PG_SERVICE" psql -U "$PG_USER" -d "$DB_NAME" -tAc "$sql"
    fi
}
psql_exec() {
    local sql="$1"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '  $ docker compose %s exec -T %s psql -U %s -d %s -c %s\n' \
            "${COMPOSE_ARGS[*]}" "$PG_SERVICE" "$PG_USER" "$DB_NAME" "$sql"
    else
        dc exec -T "$PG_SERVICE" psql -U "$PG_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "$sql"
    fi
}

log "compose 文件: ${COMPOSE_ARGS[*]}"
log "项目=$COMPOSE_PROJECT_NAME profile=$PROFILES env_file=$ENV_FILE"
log "服务=$SERVICE  构建上下文=$BUILD_CONTEXT  库=$DB_NAME  版本表=$VERSION_TABLE"
log "PG服务=$PG_SERVICE  网络=$PG_NETWORK  DRY_RUN=$DRY_RUN  FORCE=$FORCE"

# 打印给人看的一行 compose 命令（用于提示「如何手动跑」）
dc_hint() { printf 'cd %s && docker compose %s %s' "$APP_DIR" "${COMPOSE_ARGS[*]}" "$*"; }

if [ "$DRY_RUN" -eq 0 ]; then
    [ -f "$COMPOSE_FILE" ] || die "compose 文件不存在: $COMPOSE_FILE"
    [ -f "$ENV_FILE" ] || die "变量文件不存在: $ENV_FILE —— 没有它 music 的 DATABASE_URL 会退回 SQLite 空库，拒绝继续"
    command -v docker >/dev/null 2>&1 || die "未安装 docker"
    docker compose version >/dev/null 2>&1 || die "docker compose 不可用"
    dc config >/dev/null \
        || die "compose 配置无效（\`docker compose config\` 失败）—— 请先修配置，勿带病上线"
    # 确认 profile 真的把 music 拉进来了（漏 --profile 时这里会失败，胜过上线才发现）
    dc config --services 2>/dev/null | grep -qx "$SERVICE" \
        || die "生效配置里没有服务 '$SERVICE'（--profile $PROFILES 是否正确？以 live 实测为准）"
else
    log "DRY-RUN：跳过 docker / 文件存在性校验（不触网、不落盘）"
fi

# ---- 0b. 计划 + 显式确认（脚本不自动执行上线动作） -------------------------
step "0/9 执行计划（确认后才动生产）"
cat <<PLAN
  1) 代码同步   $REPO_DIR 的 $SYNC_REF → 构建上下文 $BUILD_CONTEXT（打印命令，需人工/前置完成）
  2) 镜像重建   docker compose --project-name $COMPOSE_PROJECT_NAME --env-file $ENV_FILE --profile $PROFILES build $SERVICE
  3) 数据保护闸门  users/songs 非空且无 --force → 拒绝
  4) 备份前置   docker exec $PG_SERVICE pg_dump -U $PG_USER $DB_NAME > $BACKUP_DIR/${DB_NAME}-pre-<ts>.sql（空备份即中止）
  5) 停服务     stop $SERVICE（必须早于 DROP SCHEMA）
  6) 重建 schema DROP SCHEMA public CASCADE; CREATE SCHEMA public;
  7) 迁移       一次性容器（挂仓库根 + -w /repo/backend + --network $PG_NETWORK）跑 alembic upgrade head
  8) 起服务     up -d --force-recreate $SERVICE
  9) 校验       $VERSION_TABLE == alembic heads / 表数 / 种子行数 / $PUBLIC_HEALTH_URL
PLAN
# 注意：代码同步**不由本脚本自动完成** —— 同步是一次独立的操作（sync-code.sh），
# 失败/半完成的状态不应混进 cutover 事务里。这里只做「计划打印 + 显式确认」。
if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN：跳过确认"
elif [ "$ASSUME_YES" -eq 1 ]; then
    warn "--yes 已指定：跳过交互确认（调用方已显式确认）"
else
    printf '\n本脚本会**停止并重建** %s 服务、DROP 生产 schema。输入 yes 继续: ' "$SERVICE"
    read -r _answer </dev/tty || die "无法读取交互输入（非交互场景请显式传 --yes）"
    [ "$_answer" = "yes" ] || die "未确认（输入 '$%s' ≠ yes），已中止，未做任何改动"
fi

mkdir -p "$BACKUP_DIR"

# ---- 1. 代码同步（镜像式；构建上下文非 git 仓库） --------------------------
# 为什么**不**在这里自动调 sync-code.sh：
#   `sync-code.sh` 的同步目标是硬编码的 `$APP_DIR/backend`（= 本仓在服务器上的副本），
#   而 music 真正吃的是 **构建上下文** `$BUILD_CONTEXT`（另一个目录、且不是 git 仓库）。
#   参数化 sync-code.sh 要动它的 CLI，超出 ISSUE #125 的范围（本次只改本脚本）。
#   这里给的是**可直接复制的两步命令**：git archive → 暂存目录 → rsync -a --delete。
#   ⚠ 本步只覆盖「本仓副本 + 构建上下文在同一台机上」的场合；跨机请自行 ssh，脚本不连远端。
step "1/9 代码同步到构建上下文（镜像式，本步不自动执行）"
if [ -d "$BUILD_CONTEXT/.git" ]; then
    warn "构建上下文 $BUILD_CONTEXT **是** git 仓库 —— 与 ISSUE #125 记录的 live 现状（非 git）不符，"
    warn "以 live 实测为准：请人工确认该目录如何更新代码，本脚本不替它做决定。"
elif [ -d "$BUILD_CONTEXT" ]; then
    log "构建上下文存在且非 git 仓库（与 live 一致）→ 必须用镜像式同步，不能用 git pull"
else
    warn "构建上下文不存在: $BUILD_CONTEXT（以 live 实测为准；构建前请确认路径）"
fi
# 为什么必须「镜像式」而不是 `tar -x` 覆盖：重命名过的迁移文件会在目标端留下旧副本，
# 容器内 `alembic heads` 会报 "Revision ... is present more than once"，迁移链直接不可用。
cat <<SYNCNOTE
  在【构建所在的那台机】上按真实模型同步（本机无 rsync，故先用 tar 落地到暂存目录）：
    1) cd $REPO_DIR && git fetch origin && \
       rm -rf /tmp/flow-sync && mkdir -p /tmp/flow-sync && \
       git archive $SYNC_REF backend | tar -x -C /tmp/flow-sync --strip-components=1
    2) rsync -a --delete --exclude='__pycache__/' --exclude='data/' \
         --exclude='.cache/' --exclude='.env*' --exclude='*.log' --exclude='*.pyc' \
         /tmp/flow-sync/ $BUILD_CONTEXT/
  无 rsync 主机：用 ./deploy/hk/sync-code.sh 的退化语义（按 git ls-tree 清单删幽灵文件 + tar 覆盖），
  但注意 sync-code.sh 的目标是 \$APP_DIR/backend，需要自己把它换成 $BUILD_CONTEXT。
  未同步就开始 build = 重建出来的镜像里是旧代码，本次 cutover 白做（构建期进镜像）。
SYNCNOTE
# ---- 2. 镜像重建（music 是镜像构建服务，代码在构建期进镜像） --------------
step "2/9 重建镜像（$SERVICE ← $BUILD_CONTEXT）"
run dc build "$SERVICE"
# 构建后确认镜像时间戳比之前新——不依赖镜像仓库，直接看本地 image 元数据。
if [ "$DRY_RUN" -eq 0 ]; then
    IMG="$(dc config --images 2>/dev/null | grep -x ".*-$SERVICE-.*\|$COMPOSE_PROJECT_NAME[-_]*$SERVICE.*" | head -1 || true)"
    if [ -n "$IMG" ]; then
        docker image inspect -f '{{.Created}}' "$IMG" >/dev/null 2>&1 \
            && log "镜像已就绪: $IMG ($(docker image inspect -f '{{.Created}}' "$IMG"))"
    else
        log "镜像名未能从 compose 推出（以 live \`docker images\` 实测为准），跳过镜像时间戳确认"
    fi
fi

# ---- 3. 数据保护闸门（默认拒绝覆盖生产数据） ------------------------------
step "3/9 数据保护闸门"
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
        cat >&2 <<'GUARD_EOF'
❌ 检测到 users/songs 有真实数据，继续执行将不可逆地删除生产数据。
GUARD_EOF
        printf '\n   合计 %s 行。本脚本会 DROP SCHEMA public CASCADE。\n' "$TOTAL_ROWS" >&2
        printf '   如确认要重建：先手工备份并核对 %s，再加 --force 重跑。\n\n' "$BACKUP_DIR" >&2
        exit 1
    fi
    log "闸门通过：users/songs 均空（合计 0 行）"
fi

# ---- 4. pg_dump 备份（前置，无备份不继续） --------------------------------
step "4/9 pg_dump 备份（前置，无备份不继续）"
TS="$(date +%Y%m%d-%H%M%S)"
DUMP="$BACKUP_DIR/${DB_NAME}-pre-$TS.sql"
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  $ docker exec $PG_SERVICE pg_dump -U $PG_USER $DB_NAME > $DUMP   （未落盘）"
else
    # 直接 docker exec 到 live 容器（不依赖 compose 能否解析 pg 服务，与 live 真实命令一致）。
    docker exec "$PG_SERVICE" pg_dump -U "$PG_USER" "$DB_NAME" >"$DUMP" \
        || die "pg_dump 失败 —— 中途中止，库未改动。请检查 PG 日志与磁盘空间。"
    [ -s "$DUMP" ] || die "备份文件为空: $DUMP —— 已中止（空备份等于没有备份）"
    # 空库 pg_dump 也会产出报头；再确认文件里确实有报头，避免拿到半个备份。
    grep -q "PostgreSQL database dump" "$DUMP" \
        || die "备份文件不像 pg_dump 输出（缺报头）: $DUMP —— 已中止"
    log "备份完成: $DUMP ($(wc -c <"$DUMP") bytes)"
    log "回滚命令: docker exec -i $PG_SERVICE psql -U $PG_USER -d $DB_NAME < $DUMP"
fi

# ---- 5. stop 应用（必须早于 DROP SCHEMA） ---------------------------------
step "5/9 停止应用（防止 create_all 抢建表）"
run dc stop "$SERVICE"

# ---- 6. DROP + CREATE SCHEMA --------------------------------------------
step "6/9 重建 schema（DROP SCHEMA public CASCADE; CREATE SCHEMA public;）"
psql_exec "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql_exec "GRANT ALL ON SCHEMA public TO $PG_USER;"

# ---- 7. alembic upgrade head（一次性容器，不依赖已停的应用） --------------
step "7/9 alembic upgrade head（一次性容器）"
# 为什么挂仓库根 + -w /repo/backend：
#   迁移上下文是仓库根的 backend/（alembic.ini 与 migrations/ 都在 backend/ 下，
#   而 env.py 需要 `app` 包在 import path 上），只挂 backend/ 会让 `import app` 失败。
# 为什么 --network：一次性容器默认在 compose 默认网络里，解析不到 flow-hk-music-postgres。
# 为什么 DATABASE_URL 由 env_file 提供：secret 一律 env_file 委托，脚本不插值、不打印。
if [ "$DRY_RUN" -eq 1 ]; then
    printf '  $ docker compose %s run --rm --no-deps \\\n' "${COMPOSE_ARGS[*]}"
    printf '      --network %s \\\n' "$PG_NETWORK"
    printf '      -v %s:/repo -w /repo/backend \\\n' "$REPO_DIR"
    printf '      -e SCHEMA_CREATE_ALL=0 --entrypoint alembic %s upgrade head\n' "$SERVICE"
    printf '  （变量由 --env-file %s 提供，脚本不插值、不打印）\n' "$ENV_FILE"
else
    # 不在这里传 DATABASE_URL：传了等于把 secret 写进命令行/进程列表，
    # 还可能覆盖 live 的库地址（历史 502 事故就是这个方向）。
    dc run --rm --no-deps \
        --network "$PG_NETWORK" \
        -v "$REPO_DIR:/repo" -w /repo/backend \
        -e SCHEMA_CREATE_ALL=0 \
        --entrypoint alembic "$SERVICE" upgrade head
fi

# ---- 8. 重启应用 ---------------------------------------------------------
step "8/9 启动应用（--force-recreate：确保吃到新镜像）"
# 只重建镜像的话 up -d 不会换容器；必须 --force-recreate。
run dc up -d --force-recreate "$SERVICE"

# ---- 9. 校验 -------------------------------------------------------------
step "9/9 校验"
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

HEAD_SQL="SELECT version_num FROM $VERSION_TABLE LIMIT 1;"
TABLES_SQL="SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE';"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "  <演示> $VERSION_TABLE = (被校验: 存在且 == alembic heads)"
    for t in "${SEED_TABLES[@]}"; do
        echo "  <演示> $t 行数   = (被校验，要求 > 0)"
    done
    echo "  <演示> 表数量              = (被校验)"
    echo "  <演示> alembic current     = (被校验)"
    echo "  <演示> 公网 $PUBLIC_HEALTH_URL = (被校验，要求 status ok)"
    echo "  <演示> admin/*             = 预期 503（无 admin_token，环境门控，非 bug）"
else
    # 9a. 版本表存在且等于当前 head
    HEAD_LOCAL="$(dc run --rm --no-deps -e SCHEMA_CREATE_ALL=0 \
        --entrypoint alembic "$SERVICE" heads 2>/dev/null | awk '{print $1}' | head -1)"
    DB_VER="$(psql_val "$HEAD_SQL" "")"
    DB_VER="$(printf '%s' "$DB_VER" | tr -d '[:space:]')"
    [ -n "$HEAD_LOCAL" ] || { warn "无法取到 alembic heads"; FAIL=1; }
    [ -n "$DB_VER" ] || { warn "$VERSION_TABLE 无记录 —— 迁移很可能整体回滚（ISSUE #107 坑 2）；查错表名会恒为空，故本脚本查 $VERSION_TABLE"; FAIL=1; }
    check "$VERSION_TABLE == head" "$DB_VER" "$HEAD_LOCAL"

    # 9b. alembic current 输出
    echo "  --- alembic current ---"
    dc run --rm --no-deps -e SCHEMA_CREATE_ALL=0 --entrypoint alembic "$SERVICE" current | sed 's/^/  /'

    # 9c. 表数量（>0 且不应只有版本表一张）
    TABLES_N="$(psql_val "$TABLES_SQL" "0")"
    TABLES_N="$(printf '%s' "$TABLES_N" | tr -d '[:space:]')"
    if [ "${TABLES_N:-0}" -gt 1 ]; then
        printf '  ✅ %-28s %s\n' "表数量" "$TABLES_N"
    else
        printf '  ❌ %-28s got=%s want>1\n' "表数量" "${TABLES_N:-0}" >&2
        FAIL=1
    fi

    # 9d. 种子行数
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

    # 9e. 公网 /health（走 Caddy；prod 的 openapi.json 已关闭 404，别拿它当探针）
    if [ "$PUBLIC_PROBE" -eq 1 ]; then
        URL="$PUBLIC_HEALTH_URL"
        [ -n "$URL" ] || die "PUBLIC_HEALTH_URL 未设置（或用 PUBLIC_PROBE=0 跳过公网探测）"
        BODY="$(curl -fsS --max-time 10 "$URL" 2>/dev/null || true)"
        check "公网 /health" "$(printf '%s' "$BODY" | tr -d '[:space:]')" '{"status":"ok"}'
        log "admin/* 路由预期 503（无 admin_token 的环境门控，非 bug）"
    else
        log "PUBLIC_PROBE=0，跳过公网 /health 探测"
    fi
fi

# ---- 10. 结果 -----------------------------------------------------------
step "结果"
if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY-RUN 结束：以上为将执行的全部命令，未连服务器、未落盘。"
    exit 0
fi
if [ "$FAIL" -ne 0 ]; then
    cat >&2 <<'ROLLBACK_EOF'
❌ cutover 校验未通过。**不要**就此收工。回滚方式（二选一）：
ROLLBACK_EOF
    {
        printf '  A. 从备份恢复:\n'
        printf '     %s\n' "$(dc_hint "stop $SERVICE")"
        printf '     docker exec -i %s psql -U %s -d %s < %s\n' "$PG_SERVICE" "$PG_USER" "$DB_NAME" "$DUMP"
        printf '     %s\n' "$(dc_hint "up -d $SERVICE")"
        printf '  B. 退回 SQLite（临时保命）:\n'
        printf '     改 %s 的 DATABASE_URL 为 sqlite:////app/data/app.db，SCHEMA_CREATE_ALL=1，\n' "$ENV_FILE"
        printf '     然后 %s\n' "$(dc_hint "up -d --force-recreate $SERVICE")"
    } >&2
    exit 1
fi
log "✅ cutover 完成：schema 由 alembic 唯一管辖（版本表 $VERSION_TABLE），种子与 /health 校验通过。"
