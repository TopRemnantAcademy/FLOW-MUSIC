#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# FLOW(CCM) 香港服务器部署引导脚本 —— LLM 凭据缺失即 fail-closed
#
# 硬阻塞：FLOW 主链路（채팅 / 질의 / 설교 생성）需要 LLM 凭据。
#   · LLM_PROVIDER 缺失或非法            → 中止
#   · 所选 provider 的 API Key 缺失      → 中止
#   判读口径与后端 /ready（backend/app/main.py:463）的 llm:key_missing 完全一致：
#   凭据到位后，脚本即可一键拉起 FLOW（qdrant + backend + ui + caddy）。
#
# 复用 FLOW 仓库自带的 docker-compose.prod.yml 与 Caddyfile，本脚本只负责
# 「凭据闸门 + 构建启动 + 健康检查」，不重复维护 compose。
#
# 用法：
#   # 可选：APP_DIR 还没有源码时自动 clone
#   export FLOW_GIT_URL=https://github.com/<you>/flow.git
#   sudo -E ./deploy/hk/flow/bootstrap.sh
#
# ⚠ co-location 注意：本脚本自带 Caddy 监听 :443 / :8501，
#   与同机音乐平台 Caddy(:443) 冲突。共置时二选一：
#   ① 共用一个 Caddy（把 FLOW 的 route 并入音乐 Caddyfile），或
#   ② 本脚本改用非 443 端口 / 独立域名。骨架默认自带 Caddy，按需调整。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# 脚本自身所在目录（模板/脚本与 FLOW 源码分离：脚手架随 ai-music-platform 走，
# FLOW 源码另 clone 到 APP_DIR）。模板从这里取，不依赖 APP_DIR 根恰好有副本。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

APP_DIR="${APP_DIR:-/opt/flow-ccm}"
FLOW_GIT_URL="${FLOW_GIT_URL:-}"
ENV_FILE="$APP_DIR/.env.production"
TEMPLATE="$SCRIPT_DIR/env.prod.example"

# 共置模式：与同机音乐平台共用 Caddy（FLOW 自带 caddy 会抢 443）。
# FLOW_COLOCATE=1 → 加载 docker-compose.colocation.yml（把 ui/backend 暴露给宿主 Caddy），
#                   且只起 backend/ui/qdrant，**不含 caddy**。
FLOW_COLOCATE="${FLOW_COLOCATE:-0}"
COLOC_OVERRIDE="$APP_DIR/docker-compose.colocation.yml"

info() { echo -e "\033[0;32m[INFO]\033[0m $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $*"; }
die()  { echo -e "\033[0;31m[FAIL]\033[0m $*" >&2; exit 1; }

# compose 封装（与音乐套件同理：${VAR:?} 在所有子命令上插值，必须 --env-file）
if [ "$FLOW_COLOCATE" = "1" ]; then
  fc() { docker compose -f "$APP_DIR/docker-compose.prod.yml" -f "$COLOC_OVERRIDE" --env-file "$ENV_FILE" "$@"; }
else
  fc() { docker compose -f "$APP_DIR/docker-compose.prod.yml" --env-file "$ENV_FILE" "$@"; }
fi

# 从 env 文件取首个 KEY= 的值（不 eval；值含 = 也安全）
env_val() {
  [ -f "$ENV_FILE" ] || return 1
  grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-
}

echo "== FLOW(CCM) 部署引导 =="

# 1) 源码就绪
if [ ! -f "$APP_DIR/docker-compose.prod.yml" ]; then
  if [ -n "$FLOW_GIT_URL" ]; then
    info "克隆 FLOW 源码 → $APP_DIR"
    git clone "$FLOW_GIT_URL" "$APP_DIR"
  else
    die "未在 $APP_DIR 找到 docker-compose.prod.yml，也未设置 FLOW_GIT_URL。
先执行： git clone <你的 FLOW 仓库> $APP_DIR"
  fi
fi

# 2) Docker
command -v docker >/dev/null 2>&1 || die "未安装 Docker。先安装 Docker Engine + compose 插件。"

# 3) env 文件
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$TEMPLATE" ]; then
    cp "$TEMPLATE" "$ENV_FILE"
    warn "已从脚手架模板生成 $ENV_FILE —— 请填好 LLM 凭据等必填项后重跑。"
  fi
  die "缺少 $ENV_FILE（LLM 凭据等必填项未配置）。模板位置：$TEMPLATE"
fi

# 4) ⛔ FAIL-CLOSED：LLM 凭据闸门（与后端 /ready 的 llm:key_missing 判读一致）
# ⚠ `|| true` 不可省：env_val 在「整行不存在」时返回 1，而赋值语句的退出码来自命令替换，
#   在 set -e 下会**当场静默退出**，根本走不到下面的友好 die（用户只看到失败、不知道原因）。
PROV="$(env_val LLM_PROVIDER | tr '[:upper:]' '[:lower:]' || true)"
[ -n "$PROV" ] || die "LLM_PROVIDER 未设置。
  在 $ENV_FILE 设置： LLM_PROVIDER=gemini|openai|anthropic|deepseek|tencent|ollama"

case "$PROV" in
  gemini)                  NEED=GOOGLE_API_KEY ;;
  openai)                  NEED=OPENAI_API_KEY ;;
  anthropic|claude)        NEED=ANTHROPIC_API_KEY ;;
  deepseek)                NEED=DEEPSEEK_API_KEY ;;
  tencent)                 NEED=TENCENT_API_KEY ;;
  ollama)                  NEED=OLLAMA_HOST ;;
  *) die "不支持的 LLM_PROVIDER=$PROV（支持：gemini/openai/anthropic/deepseek/tencent/ollama）" ;;
esac

KEY_VAL="$(env_val "$NEED" || true)"       # 同上：必须留给下面的 die，不能被 set -e 静默杀掉
if [ -z "$KEY_VAL" ] || [ "$KEY_VAL" = "change-me" ] || [ "$KEY_VAL" = "dummy" ] \
   || [ "$KEY_VAL" = "your-key" ] || [ "$KEY_VAL" = "<PASTE_KEY>" ]; then
  die "LLM 凭据缺失：$PROV 需要 $NEED。
  在 $ENV_FILE 设置 $NEED=真实密钥后重跑。
  （后端 /ready 会因 llm:key_missing 返回 503，聊天主链路无法工作。）"
fi
info "LLM 闸门通过：LLM_PROVIDER=$PROV / $NEED 已设置"

# 5) 弱默认值告警（非阻塞）
[ "$(env_val ADMIN_API_KEY)" = "change-me" ] \
  && warn "ADMIN_API_KEY 仍是占位符 change-me —— 上线前务必改掉"
[ -z "$(env_val AUTH_SECRET)" ] \
  && warn "AUTH_SECRET 为空 —— 管理会话会随密钥变动整体失效"
[ -z "$(env_val ENCRYPTION_KEY)" ] \
  && warn "ENCRYPTION_KEY 为空 —— PII 字段加密键将从 AUTH_SECRET 派生（运营禁止，见 .env.example 注释）"

# 5.5) ⚠ SQLite 的 bind mount 源必须是【文件】
#   compose 里是 `./gospel.db:/app/gospel.db`。源不存在时 Docker 会把它创建成
#   **目录**，于是容器内 /app/gospel.db 是目录 → SQLite 打不开 → backend 起不来。
#   这是 bind mount 挂载单文件时的经典坑，首次部署必踩，故在此兜底。
if [ -d "$APP_DIR/gospel.db" ]; then
  if [ -z "$(ls -A "$APP_DIR/gospel.db" 2>/dev/null || true)" ]; then
    warn "$APP_DIR/gospel.db 是空目录（bind mount 源缺失时被自动创建）—— 已重建为文件"
    rmdir "$APP_DIR/gospel.db"
  else
    die "$APP_DIR/gospel.db 是非空目录 —— 数据异常，请人工确认后再跑"
  fi
fi
if [ ! -f "$APP_DIR/gospel.db" ]; then
  : > "$APP_DIR/gospel.db"
  info "已创建空 gospel.db（SQLite bind mount 源须先以文件存在）"
fi

# 6) 构建并启动
if [ "$FLOW_COLOCATE" = "1" ]; then
  # 覆盖文件必须在源码就位之后才存在，故在此校验（不能放在脚本开头）
  [ -f "$COLOC_OVERRIDE" ] \
    || die "FLOW_COLOCATE=1 但缺少覆盖文件 $COLOC_OVERRIDE —— 它需随 FLOW 源码一同就位"
  info "共置模式：构建并启动 FLOW（qdrant + backend + ui，**跳过自带 caddy**）..."
  # ⚠ 必须显式列出服务名：不列 caddy 才不会去抢 443。
  #    ui 宿主端口 8502 / backend 8001（由覆盖文件提供），供共用 Caddy 反代。
  fc up -d --build backend ui qdrant
  warn "共置模式未启动 FLOW 自带 Caddy —— 请把 $SCRIPT_DIR/Caddyfile.colocation.snippet 的路由并入音乐平台 Caddyfile 后重载。"
else
  info "构建并启动 FLOW（qdrant + backend + ui + caddy）..."
  fc up -d --build
fi

# 7) 健康检查：先 /health（存活），再 /ready（就绪）
HEALTHY=0
for i in $(seq 1 30); do
  if fc exec -T backend curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    HEALTHY=1; info "backend /health 通过（第 ${i} 次探测）"; break
  fi; sleep 2
done
[ "$HEALTHY" -eq 1 ] || die "backend /health 60s 内未通过 —— 见 'fc logs backend'"

READY=0
for i in $(seq 1 30); do
  if fc exec -T backend curl -sf http://localhost:8000/ready >/dev/null 2>&1; then
    READY=1; info "backend /ready 通过（第 ${i} 次探测）"; break
  else
    PROB="$(fc exec -T backend curl -s http://localhost:8000/ready 2>/dev/null || true)"
    info "等待 /ready ... 当前: $PROB"
  fi; sleep 3
done
if [ "$READY" -ne 1 ]; then
  fc logs --tail=40 backend || true
  die "backend /ready 未通过（多半是 LLM 凭据或 Qdrant 集合为空）。见上方 problems。"
fi

info "✅ FLOW 部署完成并通过 /ready。"
warn "co-location：本 Caddy 监听 :443/:8501，与同机音乐平台 Caddy 冲突时请改用共用 Caddy 或换端口/域名。"
warn "正式域名 + Let's Encrypt：编辑 Caddyfile 把 ':443 {' 换成 '你的域名 {' 并删掉 'auto_https off'。"
