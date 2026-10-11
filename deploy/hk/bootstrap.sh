#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# FLOW 音乐平台 — 中国香港服务器一键部署（幂等，可重复执行）
#
# 用法（在全新 Ubuntu 22.04/24.04 上，root 或 sudo）：
#   export MUSIC_DOMAIN=music.example.com
#   export MUREKA_API_KEY=sk-xxxx          # 必填，真实密钥
#   sudo -E ./bootstrap.sh
#
# 可选环境变量：
#   APP_DIR=/opt/flow              部署目录（默认 /opt/flow）
#   REPO_URL=...                   仓库地址（默认官方仓库）
#   BRANCH=feature/...             分支（默认当前主开发分支）
#   SKIP_CADDY=1                   跳过反向代理（已有 Nginx/Caddy 时）
#   SKIP_DOCKER_INSTALL=1          跳过 Docker 安装（已装时）
#   SKIP_FRONTEND_BUILD=1          跳过前端构建（沿用已有 out/）
#
# 幂等性：已存在的 .env / JWT_SECRET **不会被覆盖**；重复执行等于"更新并重启"。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/flow}"
REPO_URL="${REPO_URL:-https://github.com/TopRemnantAcademy/ai-music-platform.git}"
BRANCH="${BRANCH:-feature/company-quality-2026-05-26}"
MUSIC_DOMAIN="${MUSIC_DOMAIN:-}"
MUREKA_API_KEY="${MUREKA_API_KEY:-}"
SKIP_CADDY="${SKIP_CADDY:-0}"
SKIP_DOCKER_INSTALL="${SKIP_DOCKER_INSTALL:-0}"
SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GRN}[INFO]${NC} $*"; }
warn() { echo -e "${YLW}[WARN]${NC} $*"; }
die()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# 安全写入 .env 的 KEY=VALUE。
#
# ⚠ 为什么不用 sed（实测踩坑，2026-09-14 mock 测试发现）：
#   sed -i "s|^KEY=.*|KEY=$VAL|" 里，$VAL 若含：
#     · `|`  → 与分隔符冲突 → "unknown option to s" → set -e 直接中止部署
#     · `&`  → 被 sed 解释为"整个匹配串" → **静默写入错误值**（API Key 损坏 → 全曲 401）
#     · `\`  → 转义语义同样会破坏值
#   API Key 由第三方颁发，字符集不受我们控制，因此绝不能用 sed 拼接。
#   改用 Python 逐行精确替换，对任意字符都安全。
set_env_var() {
  KEY="$1" VAL="$2" FILE="$3" python3 - <<'PY'
import os, re

key, val, path = os.environ["KEY"], os.environ["VAL"], os.environ["FILE"]
lines = open(path, encoding="utf-8").read().splitlines()
pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
out, hit = [], False
for ln in lines:
    if pat.match(ln):
        if not hit:
            out.append(f"{key}={val}")
            hit = True
        # 重复键：丢弃后续行，避免"看起来改了实际没生效"
    else:
        out.append(ln)
if not hit:
    out.append(f"{key}={val}")
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
os.replace(tmp, path)
PY
}

# 所有 compose 调用的统一入口 —— **必须显式带 --env-file**。
#
# ⚠ 2026-09-15 真机实测踩坑：compose 里的 `${VAR:?}` 在**每一条**子命令上都会做插值，
#   不只是 up。`docker compose exec ...` 不带 --env-file 时，若当前目录没有 .env
#   （或 .env 缺项），命令会以
#     error while interpolating ... required variable X is missing a value
#   直接失败 —— 而脚本里这些调用都带 `|| true` 或只取退出码，于是**静默降级**：
#   健康检查永远等不到（60s 空转）、出口探测退出码恒为 1（403 探测彻底失效）。
#   显式传 --env-file 后与 cwd 无关，行为确定。
dc() { docker compose --env-file "$APP_DIR/.env" "$@"; }

# 读取 .env 中某个键的当前值（不存在或为空都返回空串）。
# ⚠ 用 `cut -d= -f2-` 而不是 -f2：Fernet 密钥/URL-safe 令牌里本身就含 `=`，
#   只取第 2 段会把密钥截断（静默生成坏密钥，重启后无法解密已有数据）。
env_val() { grep -E "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2-; }

# ── 0. 前置检查 ───────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "请以 root 运行（或 sudo -E ./bootstrap.sh）"

if [ -z "$MUSIC_DOMAIN" ]; then
  die "缺少 MUSIC_DOMAIN。例：export MUSIC_DOMAIN=music.example.com"
fi
if [ -z "$MUREKA_API_KEY" ] || [ "$MUREKA_API_KEY" = "<PASTE_YOUR_MUREKA_API_KEY>" ]; then
  die "缺少真实的 MUREKA_API_KEY。例：export MUREKA_API_KEY=sk-xxxx
      （Mureka 是实际主引擎；留空会导致全部曲目 401 后静默失败）"
fi
info "目标域名 = $MUSIC_DOMAIN / 部署目录 = $APP_DIR"

# ── 1. Docker ─────────────────────────────────────────────────────────────────
if [ "$SKIP_DOCKER_INSTALL" = "1" ]; then
  info "跳过 Docker 安装（SKIP_DOCKER_INSTALL=1）"
else
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    info "Docker 已安装：$(docker --version)"
  else
    info "安装 Docker（官方脚本）..."
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg python3-yaml
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh
    apt-get install -y docker-compose-plugin
    info "Docker 安装完成"
  fi
fi
docker compose version >/dev/null 2>&1 || die "docker compose(v2) 不可用，请手动安装 docker-compose-plugin"

# python3-yaml（部署前校验脚本依赖）
python3 -c "import yaml" 2>/dev/null || {
  info "安装 python3-yaml（validate_compose_env.py 依赖）..."
  apt-get install -y python3-yaml || pip3 install pyyaml || die "无法安装 pyyaml"
}

# ── 2. 代码 ───────────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  info "更新代码..."
  git -C "$APP_DIR" fetch --all --prune
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only || warn "拉取失败（可能有本地改动），继续使用现有代码"
else
  info "克隆代码 $REPO_URL ($BRANCH)..."
  rm -rf "${APP_DIR:?}"/*
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR" \
    || die "克隆失败。若为私有仓库，请配置部署密钥后重试"
fi
cd "$APP_DIR"

# ── 3. .env（幂等：不覆盖已有密钥）─────────────────────────────────────────────
TPL="$APP_DIR/deploy/hk/env.prod.example"
[ -f "$TPL" ] || die "缺少模板 $TPL"

if [ ! -f "$APP_DIR/.env" ]; then
  info "从模板创建 .env"
  cp "$TPL" "$APP_DIR/.env"
fi

# 3-1 JWT_SECRET：为空才生成（64 位十六进制 = 32 字节）
if grep -qE '^JWT_SECRET=\s*$' "$APP_DIR/.env"; then
  NEW_JWT="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  set_env_var JWT_SECRET "$NEW_JWT" "$APP_DIR/.env"
  info "已生成 JWT_SECRET（64 位随机）"
else
  info "JWT_SECRET 已存在，保留不覆盖"
fi

# 3-2 MUREKA_API_KEY：本次显式提供则写入（保证与传入值一致）
set_env_var MUREKA_API_KEY "$MUREKA_API_KEY" "$APP_DIR/.env"
# 回读校验：防止任何写入异常导致"看起来成功、实际是占位符"
WRITTEN="$(grep '^MUREKA_API_KEY=' "$APP_DIR/.env" | cut -d= -f2-)"
[ "$WRITTEN" = "$MUREKA_API_KEY" ] \
  || die "MUREKA_API_KEY 回读校验失败（写入值与传入值不一致），已中止"
info "已写入 MUREKA_API_KEY（回读校验通过）"

# 3-3 CORS_ORIGINS：必须包含生产域名
if grep -q '^CORS_ORIGINS=' "$APP_DIR/.env"; then
  CUR_CORS="$(grep '^CORS_ORIGINS=' "$APP_DIR/.env" | cut -d= -f2-)"
  case "$CUR_CORS" in
    *"$MUSIC_DOMAIN"*) info "CORS_ORIGINS 已含 $MUSIC_DOMAIN" ;;
    *) set_env_var CORS_ORIGINS "https://$MUSIC_DOMAIN" "$APP_DIR/.env"
       warn "CORS_ORIGINS 已改为 https://$MUSIC_DOMAIN（原值未含该域名）" ;;
  esac
else
  echo "CORS_ORIGINS=https://$MUSIC_DOMAIN" >> "$APP_DIR/.env"
fi

# 3-4 ADMIN_TOKEN：为空才生成（管理台无账号体系，只认这把共享密钥）
if [ -z "$(env_val ADMIN_TOKEN "$APP_DIR/.env")" ]; then
  NEW_ADMIN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  set_env_var ADMIN_TOKEN "$NEW_ADMIN" "$APP_DIR/.env"
  info "已生成 ADMIN_TOKEN（管理台共享密钥，请从 .env 复制保存）"
else
  info "ADMIN_TOKEN 已存在，保留不覆盖"
fi

# 3-5 ENCRYPTION_KEY：为空才生成（Fernet = 32 字节 urlsafe-base64，标准库即可造，
#     不依赖 cryptography —— 宿主机上不一定装了它）
if [ -z "$(env_val ENCRYPTION_KEY "$APP_DIR/.env")" ]; then
  NEW_ENC="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  set_env_var ENCRYPTION_KEY "$NEW_ENC" "$APP_DIR/.env"
  info "已生成 ENCRYPTION_KEY（Fernet 32 字节随机）"
else
  info "ENCRYPTION_KEY 已存在，保留不覆盖"
fi

chmod 600 "$APP_DIR/.env"
info ".env 就绪（权限 600）"

# ── 4. 部署前校验（必填项缺失即中止）────────────────────────────────────────────
info "运行部署前校验..."
python3 "$APP_DIR/scripts/validate_compose_env.py" \
  --env "$APP_DIR/.env" --compose "$APP_DIR/docker-compose.yml" \
  || die "存在必填变量缺失，已中止部署（见上方 MISSING）"

# ── 5. 构建并启动 ─────────────────────────────────────────────────────────────
info "构建并启动容器（首次约数分钟）..."
dc up -d --build

info "等待健康检查..."
HEALTHY=0
for i in $(seq 1 30); do
  if dc exec -T backend python -c \
      "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" \
      >/dev/null 2>&1; then
    HEALTHY=1
    info "backend /health 通过（第 ${i} 次探测）"
    break
  fi
  sleep 2
done

# 为什么不让它"过去就算了"：健康检查失败往往就是配置错误（缺必填项、端口冲突、
# 迁移失败），此时后面的出口探测、前端构建、Caddy 全都是给一个**根本没起来的服务**
# 做装饰，最后得到"部署脚本跑完了但站点打不开"，排查成本极高。宁可现在就报清楚。
if [ "$HEALTHY" -ne 1 ]; then
  echo
  dc logs --tail=60 backend || true
  echo
  die "backend 健康检查 60 秒内未通过 —— 已中止后续步骤。
      请先看上面日志定位（最常见：必填变量缺失 / 端口 8000 被占 / 迁移失败）。
      修好后重跑本脚本即可（幂等，不会覆盖已有密钥）。"
fi

# ── 6. 出口探测（P0-CRIT：403 地域封锁判定）───────────────────────────────────
info "探测到 Mureka 的出口（免费端点，不计费）..."
# 镜像刻意不含 scripts/（.dockerignore 排除），按需拷入容器内执行。
# ⚠ 三个已踩过的坑（2026-09-15 真机实测，全部修在此处）：
#   1) **源目录**：probe_mureka.py 在 `backend/scripts/`，不是仓库根的 `scripts/`
#      （根 scripts/ 只有 validate_compose_env.py）。拷错目录 → python 报
#      "can't open file" 并以 **exit 2** 退出 —— 而 2 在本脚本里代表"401 认证失败"，
#      于是被**误诊成密钥错误**，真正的问题（脚本不在）被彻底掩盖。
#   2) **先删目标**：docker cp 在目标目录已存在时会拷成 /app/scripts/scripts。
#   3) **拷完必须校验**：不做存在性校验就会重演坑 1 的静默误诊。
dc exec -T backend rm -rf /app/scripts >/dev/null 2>&1 || true
dc cp "$APP_DIR/backend/scripts" backend:/app/scripts >/dev/null 2>&1 || true

if ! dc exec -T backend test -f /app/scripts/probe_mureka.py >/dev/null 2>&1; then
  warn "探测脚本未成功拷入容器（/app/scripts/probe_mureka.py 不存在）—— 跳过出口探测。
      手工排查：docker compose --env-file $APP_DIR/.env cp $APP_DIR/backend/scripts backend:/app/scripts"
  PROBE_CODE=5    # 5 = 脚本缺失（与 2=401 / 3=403 / 4=网络 区分开，避免误诊）
else
  set +e
  dc exec -T backend python scripts/probe_mureka.py
  PROBE_CODE=$?
  set -e
fi

echo
case "$PROBE_CODE" in
  0) info "出口可用 ✅ 该部署可正常生成" ;;
  2) warn "401 认证失败 → 检查 MUREKA_API_KEY 是否正确" ;;
  3) warn "403 地域封锁 ❌ 该服务器出口不在 Mureka 允许区域。
      处置：更换到中国香港/允许区域的出口，或配置已备案出口代理。
      **重试无效**（代码已判定 403 不可重试）。" ;;
  4) warn "网络/超时 → 检查到 api.mureka.ai 的出站连通性与代理设置" ;;
  5) warn "探测脚本缺失（未拷入容器）—— 出口状态**未知**，请按上方提示手工补跑" ;;
  *) warn "其他（退出码 $PROBE_CODE）→ 看上方 [REPORT]/[ACTION] 行" ;;
esac
echo

# ── 7. 前端静态构建（Next.js export）──────────────────────────────────────────
# 漏了这步 = 打开域名只有 API、没有界面（2026-09-14 mock 自检时发现的缺口）。
# 产物 out/ 由 Caddy 以 file_server 托管（/api/* 与 /health 才走 backend）。
FRONTEND_OUT="$APP_DIR/frontend/out"
FRONTEND_SRC="$APP_DIR/frontend"
FE_API_BASE="https://$MUSIC_DOMAIN"
FE_STAMP="$FRONTEND_OUT/.build-stamp"

# 是否需要重建。三条判据命中任一即重建：
#   ① 没有 index.html
#   ② 记录的 API 基址与本次域名不一致（NEXT_PUBLIC_API_BASE 是**构建期内联**的，
#      换了域名却沿用旧产物 = 前端继续打旧地址，且症状表现为"接口 401/404"很难查）
#   ③ 有源码文件比 index.html 新
# 否则跳过 —— 每次 bootstrap 都重跑 npm ci 会让"更新重启"从 10 秒变成 5 分钟。
frontend_needs_build() {
  [ -f "$FRONTEND_OUT/index.html" ] || return 0
  [ "$(cat "$FE_STAMP" 2>/dev/null)" = "$FE_API_BASE" ] || return 0
  [ -n "$(find "$FRONTEND_SRC" \
            \( -name node_modules -o -name out -o -name .next -o -name .git \) -prune -o \
            -type f -newer "$FRONTEND_OUT/index.html" -print 2>/dev/null | head -1)" ] && return 0
  return 1
}

build_frontend() {
  if [ "$SKIP_FRONTEND_BUILD" = "1" ]; then
    warn "跳过前端构建（SKIP_FRONTEND_BUILD=1）—— 沿用 $FRONTEND_OUT"
    return 0
  fi
  if ! frontend_needs_build; then
    info "前端产物已是最新（源码未变、域名未变）—— 跳过重建"
    return 0
  fi
  info "构建前端静态站点（node:20-alpine 内 npm ci + export，约 3~5 分钟）..."
  # ⚠ NEXT_PUBLIC_API_BASE 是**构建期内联**进客户端包的（lib/api.ts:2），
  #   运行期改 .env 无效 —— 必须在这里传入，否则前端会去连 http://localhost:8000。
  docker run --rm \
    -v "$FRONTEND_SRC:/app" -w /app \
    -e APK_BUILD=1 \
    -e "NEXT_PUBLIC_API_BASE=$FE_API_BASE" \
    -e NEXT_TELEMETRY_DISABLED=1 \
    node:20-alpine \
    sh -c "npm ci --no-audit --no-fund && npm run build" \
    || return 1
  echo "$FE_API_BASE" > "$FE_STAMP" 2>/dev/null || true
  return 0
}

if build_frontend; then
  info "前端构建完成"
else
  warn "前端构建失败（网络/依赖问题？）。可重试：
        docker run --rm -v $APP_DIR/frontend:/app -w /app -e APK_BUILD=1 \\
          -e NEXT_PUBLIC_API_BASE=https://$MUSIC_DOMAIN node:20-alpine \\
          sh -c 'npm ci --no-audit --no-fund && npm run build'"
fi

# 硬校验：没有界面就等于部署不可用，明确失败而不是"能起来但打不开"
if [ ! -f "$FRONTEND_OUT/index.html" ]; then
  die "缺少前端产物 $FRONTEND_OUT/index.html —— 站点将无界面。
      （若已手工放置产物，用 SKIP_FRONTEND_BUILD=1 重跑）"
fi
info "前端产物就绪：$FRONTEND_OUT"

# ── 8. 反向代理 Caddy（自动 HTTPS + 静态托管）─────────────────────────────────
if [ "$SKIP_CADDY" = "1" ]; then
  info "跳过 Caddy（SKIP_CADDY=1）—— 请自行把 80/443 反代到 127.0.0.1:8000"
else
  sed "s|__MUSIC_DOMAIN__|$MUSIC_DOMAIN|g" \
    "$APP_DIR/deploy/hk/Caddyfile.template" > "$APP_DIR/Caddyfile"
  info "启动 Caddy（--network host，故 127.0.0.1 指向宿主机）"
  docker rm -f flow-caddy >/dev/null 2>&1 || true
  docker run -d --name flow-caddy --network host --restart unless-stopped \
    -v "$APP_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v "$FRONTEND_OUT:/srv:ro" \
    -v caddy_data:/data -v caddy_logs:/var/log/caddy \
    caddy:2-alpine
  info "Caddy 已启动，将自动申请 Let's Encrypt 证书"
fi

# 防火墙提示（不擅自改动规则）
if command -v ufw >/dev/null 2>&1; then
  warn "若启用 ufw，请放行 80/443 且**不要**对外暴露 8000：
        ufw allow 80/tcp && ufw allow 443/tcp && ufw reload"
fi

# ── 9. 收尾提示 ───────────────────────────────────────────────────────────────
cat <<EOF

${GRN}===== 部署流程结束 =====${NC}

后续验证（建议逐条确认）：
  1) 启动日志首行应出现出口自检结论：
       docker compose logs backend | grep -i "Mureka 出口自检"
     → 期望 "kind=ok status=200"；若 "kind=region" 即地域封锁（见上方 403 处置）
  2) 管理端点（需管理员密钥）：
       curl -s -H "X-Admin-Token: <token>" https://$MUSIC_DOMAIN/api/admin/mureka/egress
  3) 健康检查：curl -s https://$MUSIC_DOMAIN/health    → 200
  4) 前端界面：浏览器打开 https://$MUSIC_DOMAIN   → 首页可加载、可注册/登录
     （若白屏，先看 Caddy 是否挂上了前端产物：docker exec flow-caddy ls /srv/index.html）
  5) 备份（SQLite）：把 deploy/hk/backup.sh 加入 crontab，见 deploy/hk/crontab.example

密钥与 .env 位于 $APP_DIR/.env（权限 600）。**切勿提交到 git**。
EOF
