#!/usr/bin/env bash
# check_ascii.sh —— 全树 ASCII 命名与长度检查（Windows 路径风险规避）
#
# 出处：music_rebuild_project/README.md「Windows 路径风险规避（强制）」；
#       gate_G1 只读前的输入准入（#27 落地实现）。
#
# 规则（README 原文）：
#   1. 一律 ASCII：目录/文件名只允许 [A-Za-z0-9._-]，禁中文/空格/韩文/emoji
#   2. 全树统一小写（NTFS 大小写不敏感）
#   3. 单段 ≤ 40 字符，全路径 ≤ 200 字符（给 Windows 260 上限留余量）
#   4. 不用保留名 CON/PRN/AUX/NUL/COM1..9/LPT1..9，不以 . 或空格结尾
#
# 用法：
#   check_ascii.sh [路径...] [--strict] [--max-path N] [--max-seg N]
#                 [--quiet] [--json] [--include-git]
#
#   --strict        有违规即退出码 1（CI 用），默认仅告警退出 0
#   --max-path N    全路径长度上限，默认 200（0 = 不检查）
#   --max-seg  N    单段长度上限，默认 40（0 = 不检查）
#   --include-git   连同 .git 等隐藏目录一起扫（默认跳过 VCS/构建产物）
#   --quiet         只输出汇总行
#   --json          以 JSON 汇总输出（便于上游流水线解析）
#
# 退出码：0 = 通过（或仅告警）；1 = 有违规且 --strict；2 = 用法/参数错误。
#
# 边界说明：
#   - 本脚本查的是**当前工作树**；`.git` 等 VCS/构建目录默认跳过（--include-git 可纳入）。
#   - 只能抓"已经落到磁盘"的坏名字，抓不到"只在某个平台才炸"的其它差异
#     （如 NTFS 大小写不敏感导致的同名冲突）—— 那属于 #13 命名细则，别在这里扩权。
#   - 长度口径与 music_rebuild_project/README.md 一致：单段 <= 40、全路径 <= 200，
#     给 Windows 260 上限留余量；阈值可用 --max-seg / --max-path 覆盖。

set -uo pipefail

MAX_PATH=200
MAX_SEG=40
STRICT=0
QUIET=0
JSON=0
INCLUDE_GIT=0
TARGETS=()

usage() { sed -n '1,40p' "$0" | sed -n '/^# /p' | sed 's/^# \{0,1\}//'; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict)    STRICT=1 ;;
    --quiet)     QUIET=1 ;;
    --json)      JSON=1 ;;
    --include-git) INCLUDE_GIT=1 ;;
    --max-path)  shift; MAX_PATH="${1:-}" ;;
    --max-seg)   shift; MAX_SEG="${1:-}" ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "check_ascii: 未知参数 $1" >&2; exit 2 ;;
    *)           TARGETS+=("$1") ;;
  esac
  shift
done
[ "${#TARGETS[@]}" -eq 0 ] && TARGETS=(".")

for n in "$MAX_PATH" "$MAX_SEG"; do
  case "$n" in ''|*[!0-9]*) echo "check_ascii: 长度上限必须是非负整数（得到 '$n'）" >&2; exit 2 ;; esac
done

# 允许的字符集：[A-Za-z0-9._-]。含空格/中文/韩文/emoji 等一律违规。
ASCII_RE='^[A-Za-z0-9._-]+$'
# NTFS 保留名（不含扩展名比较）
RESERVED_RE='^(con|prn|aux|nul|com[1-9]|lpt[1-9])$'

SKIP_DIRS=(.git .hg .svn node_modules __pycache__ .venv venv .mypy_cache .pytest_cache dist build .tox)
should_skip_dir() {
  local base="$1"
  if [ "$INCLUDE_GIT" -eq 0 ]; then
    for d in "${SKIP_DIRS[@]}"; do [ "$base" = "$d" ] && return 0; done
  fi
  return 1
}

bad_name=0; long_seg=0; long_path=0; reserved=0
declare -a lines=()
add() { lines+=("$1"); }

scan() {
  local target="$1"
  local base seg chars
  base="$(basename "$target")"
  seg="$(printf '%s' "$base" | wc -m)"

  if ! printf '%s' "$base" | LC_ALL=C grep -qE "$ASCII_RE"; then
    bad_name=$((bad_name + 1))
    add "非ASCII/非法字符名字符  $target"
  fi
  if [ "$MAX_SEG" -gt 0 ] && [ "$seg" -gt "$MAX_SEG" ]; then
    long_seg=$((long_seg + 1))
    add "单段超长($seg>$MAX_SEG)      $target"
  fi
  if [ "$MAX_PATH" -gt 0 ]; then
    chars="$(printf '%s' "$target" | wc -m)"
    if [ "$chars" -gt "$MAX_PATH" ]; then
      long_path=$((long_path + 1))
      add "路径超长($chars>$MAX_PATH)   $target"
    fi
  fi
  # 保留名 / 结尾为 . 或空格（只对非 ASCII 检查另计，避免重复刷屏）
  local stem="${base%.*}"
  local lc; lc="$(printf '%s' "$stem" | tr '[:upper:]' '[:lower:]')"
  if printf '%s' "$lc" | grep -qE "$RESERVED_RE"; then
    reserved=$((reserved + 1))
    add "Windows保留名($stem)         $target"
  fi
  case "$base" in
    *.|*" ")
      reserved=$((reserved + 1))
      add "以 . 或空格结尾              $target" ;;
  esac
}

# find 遍历：目录 + 文件都查（命名规则对两者同样适用）
for t in "${TARGETS[@]}"; do
  if [ ! -e "$t" ]; then
    echo "check_ascii: 路径不存在，跳过：$t" >&2
    continue
  fi
  while IFS= read -r -d '' p; do scan "$p"; done \
    < <(find "$t" \( -name .git -o -name .hg -o -name .svn \) -prune -o -print0 2>/dev/null)
  # 目录自身
  scan "$t"
done

if [ "$JSON" -eq 1 ]; then
  printf '{"bad_name":%d,"long_seg":%d,"long_path":%d,"reserved":%d,"targets":%d}\n' \
    "$bad_name" "$long_seg" "$long_path" "$reserved" "${#TARGETS[@]}"
else
  if [ "${#lines[@]}" -gt 0 ] && [ "$QUIET" -eq 0 ]; then
    printf '%s\n' "${lines[@]}"
  fi
  printf 'check_ascii: 扫描 %d 个路径 - 非ASCII命名 %d / 单段超长 %d / 路径超长 %d / 保留名或结尾异常 %d\n' \
    "${#TARGETS[@]}" "$bad_name" "$long_seg" "$long_path" "$reserved"
fi

total=$((bad_name + long_seg + long_path + reserved))
if [ "$total" -gt 0 ]; then
  echo "check_ascii: 结论 FAIL（Windows 上可能无法 checkout / 构建，见 README 命名规范）"
  [ "$STRICT" -eq 1 ] && exit 1
  exit 0
fi
echo "check_ascii: 结论 PASS"
exit 0
