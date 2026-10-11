#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# bootstrap.sh 的 **mock 自检**（不装 Docker / 不联网 / 不碰真实 .env）
#
# 目的：bootstrap.sh 是"一次写对、上线才发现错"的高风险脚本，用 mock 把
#       docker / git / apt-get / id / python3 全部替换后完整跑一遍，验证：
#       守卫条件、.env 写入与特殊字符健壮性、出口探测判读、幂等性、前端构建与反代路由。
#
# 用法：
#   ./deploy/hk/selftest.sh
#   REAL_PYTHON=/path/to/python ./deploy/hk/selftest.sh   # 推荐：真跑 python 片段
#
# 为什么要用 REAL_PYTHON：默认为空时，set_env_var 的 python 片段只被"假装成功"，
#   无法验证真实替换逻辑。传入真实解释器即可**真正执行** bootstrap 里的
#   python 代码（含 | & $ \ 等特殊字符处理）。
#
# ⚠ 为什么 mock 用**导出函数**而不是 $TEST/bin 下的脚本文件：
#   初版把 mock 写成脚本文件放在 PATH 前，实测**每次派生子进程 1~4 秒**
#   （Windows/Git-Bash shim 开销），8 个用例一轮要 9~10 分钟，还常被工具超时误杀。
#   改成 `export -f` 的函数后 mock 少了一层派生，本机实测 8 用例约 6~7 分钟
#   （剩余开销主要在 Windows 版 python.exe 的反复启动）；Linux 上应在数十秒内。
#
# 退出码：0=全部通过  1=存在失败
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
export PATH="/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST="$(mktemp -d "${TMPDIR:-/tmp}/hk-selftest.XXXXXX")"
trap '/bin/rm -rf "$TEST" 2>/dev/null || true' EXIT
mkdir -p "$TEST/app"
export MOCKLOG="$TEST/mock.log"
: > "$MOCKLOG"

# ⚠ 参数匹配不能按位置：`docker compose --env-file X up ...` 的 $2 是 --env-file。
# ⚠ 还必须先 shift 掉目标词，否则目标词自身也在 $@ 里，第一个判断恒为真。
has() { local t="$1"; shift; for a in "$@"; do [ "$a" = "$t" ] && return 0; done; return 1; }

docker() {
  echo "[docker] $*" >> "$MOCKLOG"
  case "$1" in
    compose)
      if   has "version" "$@"; then echo "Docker Compose version v2.20.0-mock"; return 0
      elif has "up"      "$@"; then echo "[mock] compose up -d --build";        return 0
      elif has "cp"      "$@"; then echo "[mock] compose cp $*";                return 0
      elif has "ps"      "$@"; then echo "mockcontainerid";                     return 0
      elif has "exec"    "$@"; then
        # 模拟"探测脚本没拷进去"：bootstrap 必须报 code=5，绝不能当成 401(exit 2)
        if echo "$*" | grep -q "test -f"; then
          [ "${MOCK_PROBE_MISSING:-0}" = "1" ] && return 1 || return 0
        fi
        if echo "$*" | grep -q "probe_mureka"; then
          case "${MOCK_PROBE_CODE:-0}" in
            0) echo "[KIND] ok";     echo "[REPORT] Mureka 出口自检: kind=ok status=200";;
            2) echo "[KIND] auth";   echo "[REPORT] Mureka 出口自检: kind=auth status=401";;
            3) echo "[KIND] region"; echo "[REPORT] Mureka 出口自检: kind=region status=403";;
          esac
          return "${MOCK_PROBE_CODE:-0}"
        fi
        return 0
      fi
      return 0;;
    --version) echo "Docker version 24.0.0-mock";;
    rm)        echo "[mock] docker rm -f flow-caddy";;
    run)
      if echo "$*" | grep -q "node:20-alpine"; then
        echo "[mock] frontend build $*"
        mkdir -p "${MOCK_FRONTEND_OUT:?}" && echo "<html>mock</html>" > "${MOCK_FRONTEND_OUT}/index.html"
      else
        echo "[mock] docker run flow-caddy $*"
      fi;;
  esac
  return 0
}
git()     { echo "[git] $*" >> "$MOCKLOG"; return 0; }
apt-get() { return 0; }
id()      { echo 0; }                       # 本机通常非 root，mock 掉（生产脚本的 root 校验不受影响）
python3() {
  case "$*" in
    *"import yaml"*)        return 0;;      # 跳过 apt 安装分支
    *validate_compose_env*) echo "[mock] validate OK"; return 0;;
    *) if [ -n "${REAL_PYTHON:-}" ] && [ -x "${REAL_PYTHON:-}" ]; then
         "$REAL_PYTHON" "$@"                # 真跑 bootstrap 的 python 片段
         return $?
       fi
       return 0;;
  esac
}
export -f has docker git apt-get id python3

# ── 准备应用目录 ─────────────────────────────────────────────────────────────
cp -r "$REPO/deploy"             "$TEST/app/" 2>/dev/null
cp -r "$REPO/scripts"            "$TEST/app/" 2>/dev/null
mkdir -p "$TEST/app/backend/scripts" "$TEST/app/frontend" "$TEST/app/.git"
cp    "$REPO/docker-compose.yml" "$TEST/app/" 2>/dev/null

export SKIP_DOCKER_INSTALL=1
export APP_DIR="$TEST/app"
export MOCK_FRONTEND_OUT="$TEST/app/frontend/out"

PASS=0; FAIL=0
chk() {
  if [ "$2" = "$3" ]; then echo "  PASS  $1"; PASS=$((PASS+1));
  else echo "  FAIL  $1"; echo "        实际=[$2]"; echo "        期望=[$3]"; FAIL=$((FAIL+1)); fi
}
run() { /bin/bash "$REPO/deploy/hk/bootstrap.sh" > "$TEST/last.log" 2>&1; echo $?; }
envget() { grep "^$1=" "$TEST/app/.env" 2>/dev/null | cut -d= -f2-; }

echo "== 1) 正常部署：API Key 含 | & \$ \\ （sed 经典陷阱）=="
SPECIAL='sk&ab|cd$1\x'
export MUSIC_DOMAIN="music.example.com"; export MUREKA_API_KEY="$SPECIAL"
export MOCK_PROBE_CODE=0 MOCK_PROBE_MISSING=0
RC="$(run)"; echo "  exit=$RC"
chk "退出码 0"                    "$RC" "0"
chk "MUREKA_API_KEY 原样写入"      "$(envget MUREKA_API_KEY)" "$SPECIAL"
chk "JWT_SECRET 为 64 位"          "$(envget JWT_SECRET | wc -c | tr -d ' ')" "65"
chk "CORS_ORIGINS 写入域名"        "$(envget CORS_ORIGINS)" "https://music.example.com"
chk "ADMIN_TOKEN 已生成"          "$([ -n "$(envget ADMIN_TOKEN)" ] && echo yes || echo no)" "yes"
# Fernet = 32 字节 urlsafe-base64 = 44 字符（含 = 填充）。长度不足=被 cut 截断成坏密钥。
chk "ENCRYPTION_KEY 为 44 位"      "$(envget ENCRYPTION_KEY | wc -c | tr -d ' ')" "45"

echo "== 2) 缺少 MUSIC_DOMAIN 必须中止（且原因正确）=="
/bin/rm -f "$TEST/app/.env"; unset MUSIC_DOMAIN; export MUREKA_API_KEY="sk-normal"
RC="$(run)"
chk "非 0 退出"   "$([ "$RC" -ne 0 ] && echo yes || echo no)" "yes"
chk "原因是域名"   "$(grep -c '缺少 MUSIC_DOMAIN' "$TEST/last.log")" "1"

echo "== 3) API Key 为占位符必须中止（且原因正确）=="
export MUSIC_DOMAIN="music.example.com"; export MUREKA_API_KEY="<PASTE_YOUR_MUREKA_API_KEY>"
RC="$(run)"
chk "非 0 退出" "$([ "$RC" -ne 0 ] && echo yes || echo no)" "yes"
chk "原因是 Key" "$(grep -c '缺少真实的 MUREKA_API_KEY' "$TEST/last.log")" "1"

echo "== 4) 普通 Key 应完整通过 =="
/bin/rm -f "$TEST/app/.env"; export MUREKA_API_KEY="sk-plainkey123"
# ⚠ 必须先清掉前端产物以强制重建：frontend_needs_build() 在「index.html 存在 且 域名未变
#   且 源码未更新」时判定无需重建（正确的幂等优化），但那会让下面两条前端断言恒为 0，
#   使用例产生**顺序依赖**（前面用例先构建过就全绿、单独跑就全红）。
/bin/rm -rf "$TEST/app/frontend/out"
RC="$(run)"
chk "退出码 0"         "$RC" "0"
chk "普通 Key 原样写入"  "$(envget MUREKA_API_KEY)" "sk-plainkey123"
chk "调用了 compose up"  "$(grep -c 'compose up' "$TEST/last.log")" "1"
chk "执行了出口探测"     "$(grep -c 'KIND. ok' "$TEST/last.log")" "1"
chk "构建了前端"        "$(grep -c 'frontend build' "$TEST/last.log")" "1"
chk "启动了 Caddy"      "$(grep -c 'docker run flow-caddy' "$TEST/last.log")" "1"
chk "Caddy 挂载前端产物"  "$(grep -c 'out:/srv' "$TEST/last.log")" "1"
chk "Caddyfile 含 /api 路由"  "$(grep -c 'handle /api/\*' "$TEST/app/Caddyfile")" "1"
chk "Caddyfile 含静态托管"    "$(grep -c 'file_server' "$TEST/app/Caddyfile")" "1"
chk "前端 API 基址内联域名"    "$(grep -c "NEXT_PUBLIC_API_BASE=https://music.example.com" "$TEST/last.log")" "1"
# probe_mureka.py 在 backend/scripts/（根 scripts/ 只有 validate_compose_env.py）。
# 拷错目录 → python 报 can't open file 并以 exit 2 退出，而 2=401 → 会被**误诊成密钥错误**。
# ⚠ 查 MOCKLOG 而非 last.log：bootstrap 把 cp 的输出重定向到了 /dev/null。
chk "探测脚本源=backend/scripts" \
    "$([ "$(grep -c 'cp .*backend/scripts backend:/app/scripts' "$MOCKLOG")" -ge 1 ] && echo yes || echo no)" "yes"
# 除 `compose version`（不解析 compose 文件，无需 env-file）外，
# 所有 compose 调用都必须带 --env-file，否则 ${VAR:?} 插值失败 → 静默降级
BAD_COMPOSE="$(grep -a '\[docker\] compose' "$MOCKLOG" | grep -a -v 'compose --env-file' | grep -a -v 'compose version' | wc -l | tr -d ' ')"
chk "缺 --env-file 的 compose 调用" "$BAD_COMPOSE" "0"
# 幂等性（前端）：源码与域名都没变时**不得**重复构建 —— 否则每次重启都要等一次 npm ci(3~5 分钟)。
# 这条同时是上面「前端产物已是最新」分支的回归保护：该分支若被误删，用例不会再红，
# 但用户每次 bootstrap 都会平白多等 5 分钟，属于典型的静默退化。
RC="$(run)"
chk "重复执行仍成功"     "$RC" "0"
chk "未重复构建前端"     "$(grep -c '前端产物已是最新' "$TEST/last.log")" "1"

echo "== 5) 出口 403（地域封锁）不得中断部署，且必须给出处置提示 =="
export MOCK_PROBE_CODE=3
RC="$(run)"
chk "部署仍然成功"        "$RC" "0"
chk "提示地域封锁"        "$(grep -c '403 地域封锁' "$TEST/last.log")" "1"
chk "仍启动 Caddy"       "$(grep -c 'docker run flow-caddy' "$TEST/last.log")" "1"
export MOCK_PROBE_CODE=0

echo "== 6) 幂等性：重复执行不得覆盖 JWT/ADMIN/ENCRYPTION，但要更新 API Key =="
JWT_BEFORE="$(envget JWT_SECRET)"; ADMIN_BEFORE="$(envget ADMIN_TOKEN)"; ENC_BEFORE="$(envget ENCRYPTION_KEY)"
export MUREKA_API_KEY="sk-rotated-999"
RC="$(run)"
chk "退出码 0"               "$RC" "0"
chk "JWT_SECRET 未被覆盖"     "$(envget JWT_SECRET)" "$JWT_BEFORE"
chk "ADMIN_TOKEN 未被覆盖"    "$(envget ADMIN_TOKEN)" "$ADMIN_BEFORE"
chk "ENCRYPTION_KEY 未被覆盖" "$(envget ENCRYPTION_KEY)" "$ENC_BEFORE"
chk "MUREKA_API_KEY 已更新"   "$(envget MUREKA_API_KEY)" "sk-rotated-999"

echo "== 7) SKIP_FRONTEND_BUILD=1 且无产物时必须中止（不能『起来但打不开』）=="
export SKIP_FRONTEND_BUILD=1
/bin/rm -rf "$TEST/app/frontend/out"
RC="$(run)"
chk "非 0 退出"      "$([ "$RC" -ne 0 ] && echo yes || echo no)" "yes"
chk "原因是前端产物"  "$(grep -c '缺少前端产物' "$TEST/last.log")" "1"

echo "== 8) 探测脚本缺失时必须报「未知」(code=5)，不得误诊为 401 =="
export SKIP_FRONTEND_BUILD=0   # 复位用例 7 的开关，否则会在前端守卫处提前中止
export MOCK_PROBE_MISSING=1
RC="$(run)"
chk "部署仍然成功"      "$RC" "0"
chk "报的是脚本缺失"    "$(grep -c '探测脚本缺失' "$TEST/last.log")" "1"
chk "未误报 401"       "$(grep -c '401 认证失败' "$TEST/last.log")" "0"
chk "未误报出口可用"    "$(grep -c 'KIND. ok' "$TEST/last.log")" "0"
export MOCK_PROBE_MISSING=0

echo
echo "══════ PASS=$PASS  FAIL=$FAIL ══════"
if [ "$FAIL" -ne 0 ]; then
  echo "--- 最后一次日志尾部 ---"
  tail -n 20 "$TEST/last.log" 2>/dev/null
fi
[ "$FAIL" -eq 0 ]
