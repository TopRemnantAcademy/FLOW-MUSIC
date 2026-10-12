#!/usr/bin/env bash
# =========================================================
# deploy/hk/sync-code.sh — 把 git ref 里的 backend/ 同步到服务器 APP_DIR
#
# 为什么需要它（ISSUE #107 坑 1）：
#   旧做法 `git archive <ref> backend | tar -x --strip-components=1` 是
#   **覆盖式、不删除**语义。main 上被**重命名**的迁移文件会在服务器留下旧副本：
#       migrations/versions/c3d4e5f6a7b8_add_song_extend_type.py   ← main 已改名为 c3d4e5f7a8b9
#       migrations/versions/c3d4e5f6a7b8_fix_index_unique_drift.py ← main 已改名为 c3d4e5f6a7b9
#   后果是容器内 `alembic heads` 出现 revision id 撞号
#   （`Revision ... is present more than once`），迁移链路直接不可用。
#   所以同步必须是 **镜像语义**：目标端多出来的文件要删掉。
#
# 为什么不用 rsync 作为唯一路径：
#   目标服务器（Ubuntu 最小化安装）**未必有 rsync**。本脚本优先用 rsync --delete
#   （最快、原子性好）；没有 rsync 时退化为「按 git 清单清理 + tar 解包」两步法，
#   两者最终状态一致，且都会跑同一套「git ls-tree vs 实际文件」自检。
#
# 用法：
#   ./deploy/hk/sync-code.sh [git-ref] [--dry-run]
#     git-ref     默认 origin/main（脚本会先 fetch）
#     --dry-run   只打印将要更新/删除的文件与差异，不落盘
#
# 环境变量：
#   APP_DIR     服务器上的部署目录（默认 /opt/flow）
#   REPO_DIR    本地/服务器上的 git 仓库路径（默认取脚本所在仓库根）
# =========================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
APP_DIR="${APP_DIR:-/opt/flow}"
SUBDIR="backend"
BACKUP_DIR="$SCRIPT_DIR/backups"

DRY_RUN=0
REF=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) echo "未知参数: $arg" >&2; exit 2 ;;
        *) REF="$arg" ;;
    esac
done

# 忽略白名单：运行时产物 / 数据 / 密钥 / 日志，不参与「多余文件」判定，
# 也不参与清理——它们本就不在 git 里，删掉等于删数据。
# 用 bash 内置 case glob 过滤而非 grep/sed：脚本在服务器上可能被 sh 调用，
# 且 grep -q 早退会让上游 git 收 SIGPIPE（再叠加 pipefail 就是假失败）。
is_ignored() {
    case "$1" in
        */__pycache__/*|__pycache__/*) return 0 ;;
        */data/*|data/*)               return 0 ;;
        */.cache/*|.cache/*)           return 0 ;;
        *.log)                         return 0 ;;
        *.pyc)                         return 0 ;;
        */.pytest_cache/*)             return 0 ;;
        .env|.env.*|*/.env|*/.env.*)   return 0 ;;
        *)                             return 1 ;;
    esac
}
# 按白名单过滤标准输入 → 标准输出（仅在 bash 下运行；脚本 shebang 是 bash）
filter_ignored() {
    local line
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        if ! is_ignored "$line"; then printf '%s\n' "$line"; fi
    done | sort
}

log()  { printf '[sync-code] %s\n' "$*"; }
die()  { printf '[sync-code] ERROR %s\n' "$*" >&2; exit 1; }
step() { printf '\n===== %s =====\n' "$*"; }

[ -d "$REPO_DIR/.git" ] || die "REPO_DIR 不是 git 仓库: $REPO_DIR"

# ---- 0. 确定 ref ----------------------------------------------------------
if [ -z "$REF" ]; then
    REF="origin/main"
fi
if [ -n "$(git -C "$REPO_DIR" remote 2>/dev/null || true)" ]; then
    log "fetch origin（失败则沿用本地已有 ref）"
    git -C "$REPO_DIR" fetch --prune origin >/dev/null 2>&1 || log "WARN fetch 失败，使用本地缓存 ref"
fi
git -C "$REPO_DIR" rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
    || die "无法解析 ref: $REF"
COMMIT="$(git -C "$REPO_DIR" rev-parse --short "$REF")"
log "ref=$REF commit=$COMMIT"
log "APP_DIR=$APP_DIR  DRY_RUN=$DRY_RUN"

[ -d "$APP_DIR" ] || die "APP_DIR 不存在: $APP_DIR"
TARGET="$APP_DIR/$SUBDIR"

# ---- 1. 生成 git 侧清单（唯一事实来源） -----------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git -C "$REPO_DIR" ls-tree -r --name-only "$REF" -- "$SUBDIR" \
    | while IFS= read -r p; do printf '%s\n' "${p#"$SUBDIR"/}"; done \
    | filter_ignored >"$WORK/expected.txt"
[ -s "$WORK/expected.txt" ] || die "git ls-tree 结果为空，ref=$REF 是否包含 $SUBDIR/ ？"
EXPECTED_N="$(wc -l <"$WORK/expected.txt")"
log "git 侧清单: $EXPECTED_N 个文件"

# ---- 2. 生成目标端实际清单 ------------------------------------------------
ACTUAL_N=0
if [ -d "$TARGET" ]; then
    (cd "$TARGET" && find . -type f) \
        | while IFS= read -r p; do printf '%s\n' "${p#./}"; done \
        | filter_ignored >"$WORK/actual.txt"
    ACTUAL_N="$(wc -l <"$WORK/actual.txt")"
fi
log "目标端实际: $ACTUAL_N 个文件（$TARGET）"

# ---- 3. 计算差异 ----------------------------------------------------------
# 仅在指定了 --list-only 的旧 rsync 没有的场合退化为 comm；有 rsync 时仍以此差异
# 作为交付判据（rsync 的 itemize 输出不易机器比对）。
comm -23 "$WORK/actual.txt" "$WORK/expected.txt" >"$WORK/stale.txt"   # 多了（幽灵文件）
comm -13 "$WORK/actual.txt" "$WORK/expected.txt" >"$WORK/missing.txt" # 少了
STALE_N="$(wc -l <"$WORK/stale.txt")"
# 「少了」在同步前是正常态（本次就是要补上），故只计数不判定；
# 判定统一放在第 5 步「同步后自检」。
MISSING_N="$(wc -l <"$WORK/missing.txt")"
log "同步前差异: 多余=$STALE_N  缺失=$MISSING_N"

if [ "$DRY_RUN" -eq 1 ]; then
    step "DRY-RUN 计划（不落盘）"
    echo "--- 将被【删除】的幽灵文件 ($STALE_N) ---"
    sed 's|^|  - |' "$WORK/stale.txt" | head -100
    [ "$STALE_N" -gt 100 ] && echo "  ... 省略 $((STALE_N - 100)) 项"
    echo "--- 将被【写入/更新】的文件 ($EXPECTED_N) ---"
    sed 's|^|  + |' "$WORK/expected.txt" | head -40
    [ "$EXPECTED_N" -gt 40 ] && echo "  ... 省略 $((EXPECTED_N - 40)) 项"
    echo
    echo "--- 同步方式 ---"
    if command -v rsync >/dev/null 2>&1; then
        echo "  rsync -a --delete（+ 白名单保护）"
    else
        echo "  服务器无 rsync → 退化路径: 清理幽灵文件 + tar 解包覆盖"
    fi
    echo
    log "DRY-RUN 结束，未做任何修改（退出码 0）"
    exit 0
fi

# ---- 4. 执行同步 ----------------------------------------------------------
if command -v rsync >/dev/null 2>&1; then
    step "rsync --delete 同步"
    mkdir -p "$WORK/stage"
    git -C "$REPO_DIR" archive "$REF" "$SUBDIR" | tar -x -C "$WORK/stage" --strip-components=1
    rsync -a --delete --itemize-changes \
        --exclude='__pycache__/' --exclude='data/' --exclude='.cache/' \
        --exclude='.env*' --exclude='*.log' --exclude='*.pyc' \
        "$WORK/stage/" "$TARGET/"
else
    step "无 rsync → 退化路径（先清幽灵文件，再解包覆盖）"
    cat <<'NOTE'
[sync-code] 选择『解包 + 按 git 清单删多余』而非『清目录再解包』：
[sync-code]   清目录会连带删掉 data/、.env 等非 git 资产；按清单删只动 git 管辖范围。
NOTE
    if [ "$STALE_N" -gt 0 ]; then
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            rm -f -- "$TARGET/$f"
            echo "  removed $f"
        done <"$WORK/stale.txt"
    fi
    # 清理删除后留下的空目录（__pycache__ 等被忽略目录保留）
    find "$TARGET" -mindepth 1 -type d -empty -delete 2>/dev/null || true
    git -C "$REPO_DIR" archive "$REF" "$SUBDIR" | tar -x -C "$TARGET" --strip-components=1
fi

# ---- 5. 同步后自检（核心交付） --------------------------------------------
step "自检: git ls-tree 与实际文件比对"
(cd "$TARGET" && find . -type f) \
    | while IFS= read -r p; do printf '%s\n' "${p#./}"; done \
    | filter_ignored >"$WORK/actual2.txt"
comm -23 "$WORK/actual2.txt" "$WORK/expected.txt" >"$WORK/stale2.txt"
comm -13 "$WORK/actual2.txt" "$WORK/expected.txt" >"$WORK/missing2.txt"
S2="$(wc -l <"$WORK/stale2.txt")"
M2="$(wc -l <"$WORK/missing2.txt")"

log "git=$EXPECTED_N  实际=$(wc -l <"$WORK/actual2.txt")  多余=$S2  缺失=$M2"

if [ "$S2" -gt 0 ] || [ "$M2" -gt 0 ]; then
    echo
    echo "❌ 同步后仍存在差异 —— 这正是导致 alembic revision 撞号的原因，必须修掉。" >&2
    if [ "$S2" -gt 0 ]; then
        echo "--- 多余（不在 $REF 中，疑似重命名残留/幽灵文件）---" >&2
        sed 's|^|  ! |' "$WORK/stale2.txt" >&2
    fi
    if [ "$M2" -gt 0 ]; then
        echo "--- 缺失（$REF 里有但目标端没有）---" >&2
        sed 's|^|  ! |' "$WORK/missing2.txt" >&2
    fi
    echo >&2
    echo "恢复指引：备份在 $BACKUP_DIR（如存在）；重跑本脚本前先确认目标端无未提交手工改动。" >&2
    exit 1
fi

# ---- 6. alembic 快检（有容器时） ------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    step "alembic heads 快检"
    (cd "$APP_DIR" && docker compose run --rm --no-deps --entrypoint alembic music heads) \
        || log "WARN alembic heads 执行失败（可能镜像未构建/DB 未就绪），不阻断同步"
fi

log "✅ 同步完成，文件集与 $REF 完全一致（$EXPECTED_N 个文件）"
