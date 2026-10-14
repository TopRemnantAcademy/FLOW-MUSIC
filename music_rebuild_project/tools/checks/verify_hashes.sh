#!/usr/bin/env bash
# verify_hashes.sh —— 按 *.sha256 校验冻结件
#
# 出处：
#   gate_G1 G1-9 原始文件只读 —— `sha256sum -c source_manifest.txt`
#   gate_G2 G2-9 冻结完整性 —— `04_approved/<song_id>/ground_truth.musicxml.sha256`
#   gate_G3 G3-10 冻结完整性 —— `05_vocals/<song_id>/vocal_wav/approved_vocal_pilot.sha256`
#   （#27 把"哈希校验"从约定变成可跑命令）
#
# 它做什么：
#   1. 发现目标树下的 `*.sha256` / `*.sha256sum` 文件，逐个以 sha256sum -c 校验
#   2. 把 `.sha256` 所在目录视为"冻结件目录"，检查其中是否有**冻结件缺哈希记录**
#      （默认按冻结件扩展名：musicxml / mscz / wav / mid / midi / xml；--ext 可覆盖）
#   3. 汇总 通过 / 失败 / 缺失哈希，非 0 退出表示 FAIL
#
# 用法：
#   verify_hashes.sh [路径...] [--strict-missing] [--ext "musicxml,wav"]
#                    [--quiet] [--json] [--list]
#
#   --strict-missing  冻结件缺哈希记录也判 FAIL（默认仅 WARN）
#   --ext "a,b"       冻结件扩展名白名单（默认见上）
#   --list            通过项也逐条打印（默认只打印失败与缺失）
#   --quiet / --json  输出形态
#
# 退出码：0 = 全部校验通过（可能有 WARN）；1 = 有校验失败（或 --strict-missing 下有缺失）；2 = 用法错误。
#
# 注：空目录 / 无 *.sha256 时不算错误，打印"无目标文件，跳过"并退出 0（仓库里这些目录多为占位）。
#
# ============================ 诚实边界 ============================
#   哈希校验是**内容级**证据，能证明"文件没被改"，但不能证明"文件不能被改"。
#   - Git 只跟踪可执行位，不跟踪只读位：`chmod -w` 不进提交，新 clone 一律可写。
#     所以"只读"这件事在 git 里是**存不住**的，必须由部署侧加固（见 check_ro.sh）。
#   - 哈希清单自身若被一起改写，本脚本看不出来（无签名/无时间锚）。
#     真正的防篡改需要 签名清单 / 只读挂载 / 对象存储 WORM 或外部留存。
#   - 因此本脚本给出的是**验收证据**（G1-9 / G2-9 / G3-10 的"可跑命令"），
#     不是安全边界；配合 check_ro.sh 使用，二者互补。
# =================================================================

set -uo pipefail

STRICT_MISSING=0
QUIET=0
JSON=0
LIST=0
EXTS="musicxml,mscz,wav,mid,midi,xml"
TARGETS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict-missing) STRICT_MISSING=1 ;;
    --quiet)  QUIET=1 ;;
    --json)   JSON=1 ;;
    --list)   LIST=1 ;;
    --ext)    shift; EXTS="${1:-}" ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)       echo "verify_hashes: 未知参数 $1" >&2; exit 2 ;;
    *)        TARGETS+=("$1") ;;
  esac
  shift
done
[ "${#TARGETS[@]}" -eq 0 ] && TARGETS=(".")

command -v sha256sum >/dev/null 2>&1 || { echo "verify_hashes: 需要 sha256sum（coreutils），当前环境缺失" >&2; exit 2; }

SKIP_DIRS=(.git .hg .svn node_modules __pycache__ .venv venv)

pass=0; fail=0; missing=0; hashfiles=0
declare -a lines=()

# 冻结件白名单正则：<name>.<ext>
ext_re=""
if [ -n "$EXTS" ]; then
  old_ifs="$IFS"; IFS=','
  for e in $EXTS; do
    [ -z "$e" ] && continue
    ext_re="${ext_re:+$ext_re|}\.${e}"
  done
  IFS="$old_ifs"
fi

# 冻结件是否已有对应哈希记录：<f>.sha256 / <f>.sha256sum / <stem>.sha256
has_record() {
  local f="$1" stem="${1%.*}"
  [ -f "$f.sha256" ] || [ -f "$f.sha256sum" ] || [ -f "$stem.sha256" ] || [ -f "$stem.sha256sum" ]
}

for t in "${TARGETS[@]}"; do
  if [ ! -e "$t" ]; then
    echo "verify_hashes: 路径不存在，跳过：$t" >&2
    continue
  fi

  # 1) 逐个 .sha256 校验
  while IFS= read -r -d '' hf; do
    hashfiles=$((hashfiles + 1))
    d="$(dirname "$hf")"
    out="$(cd "$d" && sha256sum -c "$(basename "$hf")" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      pass=$((pass + 1))
      [ "$LIST" -eq 1 ] && lines+=("OK    $hf")
    else
      fail=$((fail + 1))
      lines+=("FAIL  $hf")
      while IFS= read -r l; do [ -n "$l" ] && lines+=("      $l"); done <<< "$out"
    fi
  done < <(find "$t" \( -name .git -o -name .hg -o -name .svn \) -prune -o \
                -type f \( -name '*.sha256' -o -name '*.sha256sum' \) -print0 2>/dev/null)

  # 2) 冻结件缺哈希记录检查（同目录内）
  if [ -n "$ext_re" ]; then
    while IFS= read -r -d '' f; do
      case "$(basename "$f")" in *.sha256|*.sha256sum) continue ;; esac
      printf '%s' "$f" | grep -qE "(${ext_re})$" || continue
      if ! has_record "$f"; then
        missing=$((missing + 1))
        lines+=("MISS  $f  （冻结件缺 *.sha256 记录）")
      fi
    done < <(find "$t" \( -name .git -o -name .hg -o -name .svn \) -prune -o -type f -print0 2>/dev/null)
  fi
done

if [ "$JSON" -eq 1 ]; then
  printf '{"hashfiles":%d,"pass":%d,"fail":%d,"missing":%d}\n' "$hashfiles" "$pass" "$fail" "$missing"
else
  if [ "$QUIET" -eq 0 ] && [ "${#lines[@]}" -gt 0 ]; then printf '%s\n' "${lines[@]}"; fi
  if [ "$hashfiles" -eq 0 ]; then
    echo "verify_hashes: 无目标文件（未发现 *.sha256），跳过"
  else
    printf 'verify_hashes: %d 个哈希清单 - 通过 %d / 失败 %d / 缺失记录 %d\n' "$hashfiles" "$pass" "$fail" "$missing"
  fi
  if [ "$missing" -gt 0 ]; then
    echo "verify_hashes: WARN 有冻结件没有 SHA-256 记录，冻结完整性（G2-9 / G3-10）无法背书"
  fi
fi

if [ "$fail" -gt 0 ]; then
  echo "verify_hashes: 结论 FAIL（冻结件已被改动，禁止原地覆盖，应生成新版本 vNNN）"
  exit 1
fi
if [ "$STRICT_MISSING" -eq 1 ] && [ "$missing" -gt 0 ]; then
  echo "verify_hashes: 结论 FAIL（--strict-missing：冻结件缺哈希记录）"
  exit 1
fi
[ "$hashfiles" -eq 0 ] || echo "verify_hashes: 结论 PASS"
exit 0
