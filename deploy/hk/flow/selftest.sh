#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# flow/bootstrap.sh 的 **mock 自检**（不装 Docker / 不联网 / 不 clone 真实仓库）
#
# 目的：flow/bootstrap.sh 是"一次写对、上线才发现错"的高风险脚本。用 mock 替换
#       docker / git / sleep 后完整跑一遍，重点验证 FAIL-CLOSED LLM 闸门**真的会拦**，
#       而且**给出可读提示** —— 而不是被 set -e 静默杀掉（曾经的真实缺陷）。
#
# 用法： ./deploy/hk/flow/selftest.sh
# 退出码：0=全部通过  1=存在失败
#
# ⚠ mock 用 `export -f` 导出函数而非 $TEST/bin 脚本文件：本机每次派生子进程 1~4 秒
#   （Windows/Git-Bash shim 开销），写成脚本文件会让用例数倍变慢并触发工具超时。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
export PATH="/usr/bin:/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOT="$SCRIPT_DIR/bootstrap.sh"
TEST="$(mktemp -d "${TMPDIR:-/tmp}/flow-selftest.XXXXXX")"
trap '/bin/rm -rf "$TEST" 2>/dev/null || true' EXIT
export MOCKLOG="$TEST/mock.log"
: > "$MOCKLOG"

# ⚠ 参数匹配不能按位置：`compose -f X --env-file Y up ...` 的 $2 是 -f。
# ⚠ 还必须先 shift 掉目标词，否则目标词自身也在 $@ 里，第一个判断恒为真。
has() { local t="$1"; shift; for a in "$@"; do [ "$a" = "$t" ] && return 0; done; return 1; }

docker() {
  echo "[docker] $*" >> "$MOCKLOG"
  case "$1" in
    compose)
      if   has "up"   "$@"; then echo "[mock] compose up -d --build"; return 0
      elif has "logs" "$@"; then echo "[mock] compose logs --tail=40 backend"; return 0
      elif has "exec" "$@"; then
        if echo "$*" | grep -q "/health"; then
          [ "${MOCK_HEALTH:-1}" = "1" ] && return 0 || return 1
        fi
        if echo "$*" | grep -q "/ready"; then
          if [ "${MOCK_READY:-1}" = "1" ]; then echo '{"status":"ready"}'; return 0; fi
          echo '{"status":"not_ready","problems":["llm:key_missing(gemini)"]}'; return 1
        fi
        return 0
      fi
      return 0;;
  esac
  return 0
}
git() {
  echo "[git] $*" >> "$MOCKLOG"
  # 模拟克隆成功：真的落一个 compose 文件，否则后续步骤无法继续
  if [ "$1" = "clone" ] && [ "${MOCK_CLONE_OK:-1}" = "1" ]; then
    mkdir -p "$3"; : > "$3/docker-compose.prod.yml"
  fi
  return 0
}
# 把健康检查的重试等待压成 0；否则负用例要真等 30×(2s|3s)=60s/90s 才退出
sleep() { return 0; }
export -f has docker git sleep

export APP_DIR="$TEST/app"
ENV_FILE="$APP_DIR/.env.production"
mkdir -p "$APP_DIR"

write_env() { printf '%s\n' "$@" > "$ENV_FILE"; }
VALID_ENV=( "LLM_PROVIDER=gemini" "GOOGLE_API_KEY=sk-real-key-123" )
# 复位每个用例的相互影响变量（不清 ENV_FILE，由各用例按需 write_env）
reset() {
  unset FLOW_GIT_URL
  export MOCK_HEALTH=1 MOCK_READY=1 MOCK_CLONE_OK=1
  mkdir -p "$APP_DIR"; : > "$APP_DIR/docker-compose.prod.yml"
}

PASS=0; FAIL=0
chk() {
  if [ "$2" = "$3" ]; then echo "  PASS  $1"; PASS=$((PASS+1));
  else echo "  FAIL  $1"; echo "        实际=[$2]"; echo "        期望=[$3]"; FAIL=$((FAIL+1)); fi
}
run()  { /bin/bash "$BOOT" > "$TEST/last.log" 2>&1; echo $?; }
gc()   { grep -c -- "$1" "$TEST/last.log" 2>/dev/null || true; }
gcl()  { grep -c -- "$1" "$MOCKLOG"        2>/dev/null || true; }
nonzero() { [ "$1" -ne 0 ] && echo yes || echo no; }

echo "== 1) 正常部署（合法 provider + 真实 key）=="
reset; write_env "${VALID_ENV[@]}"
RC="$(run)"
chk "退出码 0"                 "$RC" "0"
chk "闸门通过提示"             "$(gc 'LLM 闸门通过')" "1"
chk "调用了 compose up"        "$(gc 'compose up')" "1"
chk "/health 通过"             "$(gc '/health 通过')" "1"
chk "/ready 通过"              "$(gc '/ready 通过')" "1"
chk "部署完成提示"             "$(gc 'FLOW 部署完成并通过')" "1"
chk "提示 co-location 冲突"    "$(gc 'co-location')" "1"

echo "== 2) 无源码且未设 FLOW_GIT_URL 必须中止（并告知如何 clone）=="
reset; /bin/rm -f "$APP_DIR/docker-compose.prod.yml"; write_env "${VALID_ENV[@]}"
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示需设置 FLOW_GIT_URL"  "$(gc '也未设置 FLOW_GIT_URL')" "1"
chk "未走到 compose up"        "$(gc 'compose up')" "0"

echo "== 3) 无源码但设了 FLOW_GIT_URL → 克隆后继续 =="
reset; /bin/rm -f "$APP_DIR/docker-compose.prod.yml"; export FLOW_GIT_URL="https://example/flow.git"
write_env "${VALID_ENV[@]}"
RC="$(run)"
chk "退出码 0"                 "$RC" "0"
chk "执行了 git clone"         "$(gcl 'clone')" "1"

echo "== 4) 缺 .env.production → 从**脚手架**模板生成后中止（回归 SCRIPT_DIR 修复）=="
reset; /bin/rm -f "$ENV_FILE"
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示已生成模板"           "$(gc '已从脚手架模板生成')" "1"
chk "模板已落到 .env.production" "$([ -f "$ENV_FILE" ] && echo yes || echo no)" "yes"
chk "来自脚手架(模板里有 LLM_PROVIDER)" "$(grep -c '^LLM_PROVIDER=gemini' "$ENV_FILE")" "1"
chk "未走到 compose up"        "$(gc 'compose up')" "0"

echo "== 5) ⛔ LLM_PROVIDER 行完全缺失 → 必须给可读提示（不得被 set -e 静默杀死）=="
reset; write_env "GOOGLE_API_KEY=sk-real-key-123"
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示 LLM_PROVIDER 未设置" "$(gc 'LLM_PROVIDER 未设置')" "1"

echo "== 6) provider 非法 → 明确拒绝 =="
reset; write_env "LLM_PROVIDER=bogus" "GOOGLE_API_KEY=sk-real"
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示不支持该 provider"    "$(gc '不支持的 LLM_PROVIDER=bogus')" "1"

echo "== 7) key 为占位符 change-me → fail-closed 中止 =="
reset; write_env "LLM_PROVIDER=gemini" "GOOGLE_API_KEY=change-me"
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示凭据缺失"             "$(gc 'LLM 凭据缺失')" "1"
chk "未走到 compose up"        "$(gc 'compose up')" "0"

echo "== 8) key 行为空串（模板首跑状态）→ fail-closed 中止 =="
reset; write_env "LLM_PROVIDER=gemini" "GOOGLE_API_KEY="
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示凭据缺失"             "$(gc 'LLM 凭据缺失')" "1"

echo "== 9) ⛔ 对应 key 行整行缺失 → 必须给可读提示（不得被 set -e 静默杀死）=="
reset; write_env "LLM_PROVIDER=gemini"
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示凭据缺失"             "$(gc 'LLM 凭据缺失')" "1"

echo "== 10) provider→key 映射表（6 种）=="
for pair in "openai:OPENAI_API_KEY" "anthropic:ANTHROPIC_API_KEY" "claude:ANTHROPIC_API_KEY" \
            "deepseek:DEEPSEEK_API_KEY" "tencent:TENCENT_API_KEY" "ollama:OLLAMA_HOST"; do
  prov="${pair%%:*}"; var="${pair##*:}"
  reset; write_env "LLM_PROVIDER=$prov" "$var=http://ovh"
  RC="$(run)"
  chk "$prov → $var"           "$RC" "0"
done

echo "== 11) provider 大小写不敏感（GEMINI → GOOGLE_API_KEY）=="
reset; write_env "LLM_PROVIDER=GEMINI" "GOOGLE_API_KEY=sk-real"
RC="$(run)"
chk "退出码 0"                 "$RC" "0"
chk "归一化后通过闸门"         "$(gc 'LLM_PROVIDER=gemini / GOOGLE_API_KEY')" "1"

echo "== 12) 弱密钥只告警、不阻塞部署 =="
reset; write_env "LLM_PROVIDER=gemini" "GOOGLE_API_KEY=sk-real" "ADMIN_API_KEY=change-me"
RC="$(run)"
chk "退出码 0"                 "$RC" "0"
chk "告警 ADMIN_API_KEY"       "$(gc 'ADMIN_API_KEY')" "1"
# ⚠ 不能用 'AUTH_SECRET' 做关键词：ENCRYPTION_KEY 那条的告警文案里也提到 AUTH_SECRET（会数到 2）
chk "告警 AUTH_SECRET"         "$(gc 'AUTH_SECRET 为空')" "1"
chk "告警 ENCRYPTION_KEY"      "$(gc 'ENCRYPTION_KEY')" "1"

echo "== 13) /health 始终不通 → 中止且不进入 /ready =="
reset; write_env "${VALID_ENV[@]}"; export MOCK_HEALTH=0
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示 /health 未通过"      "$(gc '/health 60s 内未通过')" "1"
chk "未进入 /ready"            "$(gc '/ready 通过')" "0"

echo "== 14) /ready 始终不通 → 打印后端 problems 并拉日志后中止 =="
reset; write_env "${VALID_ENV[@]}"; export MOCK_READY=0
RC="$(run)"
chk "非 0 退出"                "$(nonzero "$RC")" "yes"
chk "提示 /ready 未通过"       "$(gc '/ready 未通过')" "1"
# 重试 30 次会打印 30 遍同一份 problems，所以断言「至少出现过」而非「恰好 1 次」
chk "透出后端 problems"        "$([ "$(gc 'llm:key_missing')" -ge 1 ] && echo yes || echo no)" "yes"
chk "调用了 compose logs"      "$(gcl 'logs')" "1"

echo "== 15) 共置模式 FLOW_COLOCATE=1：跳过自带 caddy + 加载覆盖文件 =="
reset; write_env "${VALID_ENV[@]}"
: > "$APP_DIR/docker-compose.colocation.yml"
export FLOW_COLOCATE=1
: > "$MOCKLOG"
RC="$(run)"
chk "退出码 0"                    "$RC" "0"
chk "up 显式列出服务名(不含caddy)" "$(gcl 'up -d --build backend ui qdrant')" "1"
chk "up 调用里没有 caddy"          "$(grep -a 'compose.*up -d' "$MOCKLOG" | grep -ac 'caddy' || true)" "0"
# 覆盖文件名会在每次 compose 调用里出现（up / exec health / exec ready），故断言「至少 1 次」
chk "加载了 colocation 覆盖文件"   "$([ "$(gcl 'docker-compose.colocation.yml')" -ge 1 ] && echo yes || echo no)" "yes"
unset FLOW_COLOCATE

echo "== 16) 共置模式但覆盖文件缺失 → 明确报错且不得启动 =="
reset; write_env "${VALID_ENV[@]}"
/bin/rm -f "$APP_DIR/docker-compose.colocation.yml"
export FLOW_COLOCATE=1
: > "$MOCKLOG"
RC="$(run)"
chk "非 0 退出"           "$(nonzero "$RC")" "yes"
chk "提示缺少覆盖文件"     "$(gc '缺少覆盖文件')" "1"
chk "未走到 compose up"   "$(gcl 'up -d')" "0"
unset FLOW_COLOCATE

echo "== 17) SQLite bind mount：gospel.db 必须预先以【文件】存在 =="
# 背景：compose 是 ./gospel.db:/app/gospel.db。源不存在时 Docker 创建成目录
#       → 容器内 /app/gospel.db 是目录 → SQLite 打不开 → backend 起不来。
reset; write_env "${VALID_ENV[@]}"
/bin/rm -rf "$APP_DIR/gospel.db"
RC="$(run)"
chk "缺失时退出码仍为 0"        "$RC" "0"
chk "被补全为普通文件"          "$([ -f "$APP_DIR/gospel.db" ] && echo yes || echo no)" "yes"

reset; write_env "${VALID_ENV[@]}"
/bin/rm -rf "$APP_DIR/gospel.db"; mkdir -p "$APP_DIR/gospel.db"   # 模拟 Docker 自动建目录
RC="$(run)"
chk "空目录被自动纠正"          "$([ -f "$APP_DIR/gospel.db" ] && echo yes || echo no)" "yes"

reset; write_env "${VALID_ENV[@]}"
/bin/rm -rf "$APP_DIR/gospel.db"; mkdir -p "$APP_DIR/gospel.db"; : > "$APP_DIR/gospel.db/keep"
RC="$(run)"
chk "非空目录非 0 退出"         "$(nonzero "$RC")" "yes"
chk "提示 gospel.db 是非空目录" "$(gc 'gospel.db 是非空目录')" "1"

echo "== 18) 所有 compose 调用都必须带 --env-file（否则 \${VAR:?} 插值失败→静默降级）=="
BAD="$(grep -a '\[docker\] compose' "$MOCKLOG" | grep -a -v -- '--env-file' | wc -l | tr -d ' ')"
chk "缺 --env-file 的 compose 调用" "$BAD" "0"

echo
echo "══════ PASS=$PASS  FAIL=$FAIL ══════"
if [ "$FAIL" -ne 0 ]; then
  echo "--- 最后一次日志尾部 ---"
  tail -n 15 "$TEST/last.log" 2>/dev/null
fi
[ "$FAIL" -eq 0 ]
