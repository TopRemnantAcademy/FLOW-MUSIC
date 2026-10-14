#!/usr/bin/env bash
# check_ro.sh —— 02_source/ 与 04_approved/ 只读位检查
#
# 出处：
#   gate_G1 G1-9 原始文件只读 —— 02_source/ 未被改动
#   gate_G2 G2-9 冻结完整性 —— 04_approved/<song_id>/ 冻结即只读不可变
#   02_source/README.md「强只读（L0，append-only）」/ 04_approved/README.md「强只读（L2，冻结）」
#   （#27 把"只读"从约定变成可跑检查）
#
# 用法：
#   check_ro.sh [根路径...] [--dirs "pathA,pathB"] [--strict-missing] [--quiet] [--json]
#
#   --dirs "a,b"   要检查的受保护目录（默认 02_source,04_approved，相对每个根路径解析）
#   --strict-missing 目标目录不存在也判 FAIL（默认仅提示"不存在，跳过"）
#   --quiet/--json 输出形态
#
# 退出码：0 = 无可写项（或目录为空/不存在，跳过）；1 = 发现可写文件/目录；2 = 用法错误。
#
# ============================ 诚实边界（必须读） ============================
#   1) **Git 不跟踪文件权限位**：git 只记录可执行位（100644 / 100755），
#      `chmod -w` / `chmod +w` 不会进入提交，也无法从提交里复原。
#      => 本脚本在 CI / 新 clone 上是**没有意义的**（clone 出来的文件都可写）。
#         它只在**本地已加固的工作树**上有意义，且加固动作必须由部署脚本 /
#         运维工单（#27 的落地侧）执行，不能靠 git。
#   2) **Windows 上只读位语义不同**：NTFS 的只读属性（FILE_ATTRIBUTE_READONLY）
#      与 POSIX 的写位不是一回事，也不阻止删除 / 重命名；Git for Windows 更会
#      直接忽略权限差异。=> 本脚本在 Windows 上只能当作提示，不等价于"不可变"。
#   3) 因此本脚本的定位是**防手滑（accident guard）**，不是安全边界。
#      真正的不可变需要 文件系统 ACL / 只读挂载 / 对象存储 WORM / 外部备份，
#      见 #27 后续。gate_log 里的哈希校验（verify_hashes.sh）才是权威证据。
# ==========================================================================

set -uo pipefail

TARGETS=()
DIRS="02_source,04_approved"
STRICT_MISSING=0
QUIET=0
JSON=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dirs)  shift; DIRS="${1:-}" ;;
    --strict-missing) STRICT_MISSING=1 ;;
    --quiet) QUIET=1 ;;
    --json)  JSON=1 ;;
    -h|--help) sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)      echo "check_ro: 未知参数 $1" >&2; exit 2 ;;
    *)       TARGETS+=("$1") ;;
  esac
  shift
done
[ "${#TARGETS[@]}" -eq 0 ] && TARGETS=(".")

# 这里不检测 Windows/POSIX 平台差异并"自动降级"：因为语义不同**无法可靠探测**。
# 只做事实陈述 —— 在非 Linux/macOS 上额外提示一次。
case "$(uname -s 2>/dev/null || echo unknown)" in
  Linux|Darwin) ;;
  *) echo "check_ro: 提示 — 当前平台 $(uname -s) 的只读位语义与 POSIX 不同，本检查仅供参考，不等价于不可变" ;;
esac

# 可写判定：-w 对 root 恒真，故优先用权限位（POSIX）。Windows/Git-Bash 下退化为 -w。
perm_writable() {
  # 只看 owner 写位，不用 [ -w ]：root 下 -w 恒真、只读挂载也测不准。
  # 判定 owner 写位 = mode & 0o200，等价于 mode & 2 == 2。
  # 不用 `case "$m" in 2|3|6|7)`/(单个字面量轮换) 之类写法：
  # 本仓库沙箱的 bash 在这些形式上判定不可靠，注释里记一笔免得后人踩。
  local p="$1" mode
  mode="$(stat -c '%a' "$p" 2>/dev/null || true)"
  if [ -z "$mode" ]; then
    # 非 GNU stat（如 Windows / Git-Bash）退化判定，只能当提示
    [ -w "$p" ]
    return $?
  fi
  if [ ${#mode} -gt 0 ] && [ $(( 0$mode & 0200 )) -ne 0 ]; then
    return 0
  fi
  return 1
}

checked=0; writable=0; missing_dirs=0
declare -a lines=()

IFS=',' read -r -a dir_arr <<< "$DIRS"
for t in "${TARGETS[@]}"; do
  if [ ! -d "$t" ]; then
    echo "check_ro: 根路径不存在，跳过：$t" >&2
    continue
  fi
  for d in "${dir_arr[@]}"; do
    [ -z "$d" ] && continue
    base="$t/$d"
    if [ ! -d "$base" ]; then
      missing_dirs=$((missing_dirs + 1))
      lines+=("SKIP  $base  （受保护目录不存在，跳过）")
      continue
    fi
    # 目录本身 + 内容（跳过 .git）
    while IFS= read -r -d '' p; do
      checked=$((checked + 1))
      if perm_writable "$p"; then
        writable=$((writable + 1))
        lines+=("WRITABLE  $p")
      fi
    done < <(find "$base" -name .git -prune -o -print0 2>/dev/null)
  done
done

if [ "$JSON" -eq 1 ]; then
  printf '{"checked":%d,"writable":%d,"missing_dirs":%d}\n' "$checked" "$writable" "$missing_dirs"
else
  if [ "$QUIET" -eq 0 ] && [ "${#lines[@]}" -gt 0 ]; then printf '%s\n' "${lines[@]}"; fi
  if [ "$checked" -eq 0 ]; then
    echo "check_ro: 无目标文件（受保护目录为空或不存在），跳过"
  else
    printf 'check_ro: 检查 %d 项 - 可写 %d / 目录缺失 %d\n' "$checked" "$writable" "$missing_dirs"
  fi
  if [ "$writable" -gt 0 ]; then
    echo "check_ro: 提示 — 只读位不是不可变：Git 不跟踪权限位，Windows 只读语义也不同，真冻结需 ACL/只读挂载"
    echo "check_ro: 加固命令示例 — chmod -R a-w 02_source 04_approved（部署脚本执行，勿写回仓库）"
  fi
fi

if [ "$writable" -gt 0 ]; then
  echo "check_ro: 结论 FAIL（存在可写项；02_source 为 L0 append-only、04_approved 为 L2 冻结）"
  exit 1
fi
if [ "$STRICT_MISSING" -eq 1 ] && [ "$missing_dirs" -gt 0 ]; then
  echo "check_ro: 结论 FAIL（--strict-missing：受保护目录缺失）"
  exit 1
fi
[ "$checked" -eq 0 ] || echo "check_ro: 结论 PASS"
exit 0
