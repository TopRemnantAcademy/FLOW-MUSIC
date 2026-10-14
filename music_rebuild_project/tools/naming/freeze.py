"""批准本冻结规则（#13 / gate_G2「冻结定义」）——纯 stdlib。

**规则出处**（本文件只把它变成可执行的函数，冲突以文档为准）：

    gate_G2.md「冻结定义（对齐 #13）」
      G2 PASS 时把 `04_approved/<song_id>/ground_truth.musicxml` 置为只读不可变；
      此后任何修改生成 `v002`，不得原地覆盖；下游一律以哈希校验读取。
      哈希记录格式（逐字段固定）：
        <sha256>  <file>  <utc_time>  <approver>

    gate_G3.md G3-10：`approved_vocal_pilot.wav` 固定 + SHA-256 记录。

哈希记录是**四段**，`<file>` 是**被冻结件自身的文件名**（不是路径）——
`04_approved/README.md` 与阶段1 e2e（`orchestrator.py` 里那段写入）都这么用：

    f"{gt_sha}  ground_truth.musicxml  2026-10-13T00:00:00Z  mock-approver\\n"

所以这里写成一行四段、**不加任何额外字段**。想记「原文件名 / 尺寸 / 引擎」的，
请另开 sidecar，别把四段格式撑成五段 —— 下游 `verify_frozen` 与
`tools/checks/verify_hashes.sh`（#27）都按四段解析。

三条硬边界：

1. **只提供函数，不替调用方决定落点。** 没有任何目录被硬编码进本模块，
   也不会「顺手」写 `02_source/` `04_approved/`。
2. **拒绝原地覆盖。** `freeze_file` 发现目标已有冻结件时直接抛
   `AlreadyFrozenError`，并告诉你该用哪个新版本名 —— 不提供 `force=True`。
   一个能关掉的冻结开关等于没有开关。
3. **不做强不可变。** `chmod 0444` 挡得住手滑，挡不住 `os.remove`；
   Git 侧的回滚保护只在 `core.hooksPath` 指向本仓 hooks 时生效
   （见 `hooks/pre-commit` 与 README「诚实的边界」）。哈希校验是**检测**，
   不是**阻止** —— 它保证「改了会被发现」，不保证「改不了」。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
from dataclasses import dataclass

from naming import FIXED_FROZEN_BASENAMES, VERSION_IN_NAME_RE, validate_frozen_name

#: 块读取大小。与 `sha256sum` 同口径（内容哈希，不含任何元数据）。
_CHUNK = 65536


def sha256_file(path: str | os.PathLike) -> str:
    """文件内容的 SHA-256（纯内容哈希：改 mtime / 改权限都不影响结果）。

    用 hexdigest 小写 —— `sha256sum` 默认输出小写，比对时大小写不一致是
    最常见的假阳性来源，这里统一成小写就少一类坑。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 哈希记录：写入 / 解析
# ---------------------------------------------------------------------------

def utc_now() -> str:
    """UTC 时间戳，形如 `2026-10-13T00:00:00Z`。

    格式刻意写成「秒精度 + 字面 Z」而不是 `datetime.isoformat()`：
    后者会给出 `+00:00` 且带微秒，与 gate_G2 示例（`2026-10-13T00:00:00Z`）
    不是同一个字符串，逐字段比对就会挂。冻结记录是**判据**，不是日志，
    它的格式必须能被人拿眼睛逐字符核对。
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_hash_record(sha256: str, file_name: str, utc_time: str, approver: str) -> str:
    """把四段拼成哈希记录的**整行内容**（含结尾换行）。

    分隔符是两个空格 —— 与 `sha256sum` 的输出一致，也与 gate_G2 原文一致。
    用 `f"{sha}  {name}  {time}  {approver}"` 显式拼接而不是 `"  ".join(...)`：
    后者在字段意外为空时会静默塌掉一列，四段变三段，而下游按段解析就会错位。
    """
    for field, value in (("sha256", sha256), ("file_name", file_name),
                         ("utc_time", utc_time), ("approver", approver)):
        if not value or not isinstance(value, str):
            raise ValueError(f"哈希记录字段 {field} 不能为空：{value!r}")
    if "\n" in (file_name + approver):
        raise ValueError("file_name / approver 不得含换行（记录是行式格式）")
    return f"{sha256}  {file_name}  {utc_time}  {approver}\n"


def hash_record_path(path: str | os.PathLike) -> str:
    """哈希记录路径 = 被冻结件路径 + `.sha256`（同目录、同前缀）。

    gate_G2 原文：
        `04_approved/<song_id>/ground_truth.musicxml.sha256`
    同目录的理由：记录与被保护对象一起搬迁、一起归档，不会一个走了一个留下。
    """
    return f"{path}.sha256"


def parse_hash_record(text: str) -> dict:
    """解析哈希记录，返回四段字段（非法格式抛 `ValueError`）。

    解析**严格**：列数必须 4、sha256 必须 64 位小写十六进制、时间必须
    `YYYY-MM-DDTHH:MM:SSZ`。宽松解析在这里没有好处 —— 一个被截断的记录
    如果「勉强能读」，它保护的就不是文件内容，而是一个误会。
    """
    line = text.strip()
    if not line:
        raise ValueError("哈希记录为空")
    parts = line.split("  ")
    if len(parts) != 4:
        raise ValueError(f"哈希记录必须是 4 段（sha256/file/utc_time/approver），收到 {len(parts)} 段：{line!r}")
    sha256, file_name, utc_time, approver = (p.strip() for p in parts)
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError(f"sha256 段不是 64 位小写十六进制：{sha256!r}")
    try:
        _dt.datetime.strptime(utc_time, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError(f"utc_time 段不是 YYYY-MM-DDTHH:MM:SSZ：{utc_time!r}") from exc
    if not file_name or not approver:
        raise ValueError(f"file_name / approver 不得为空：{line!r}")
    return {"sha256": sha256, "file": file_name, "utc_time": utc_time, "approver": approver}


def read_hash_record(path: str | os.PathLike) -> dict:
    """读 `<path>.sha256` 并解析（记录不存在 → `FileNotFoundError`）。"""
    record = hash_record_path(path)
    with open(record, encoding="utf-8") as f:
        return parse_hash_record(f.read())


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class FreezeError(Exception):
    """冻结相关错误的基类。"""


class AlreadyFrozenError(FreezeError):
    """目标已被冻结 —— 拒绝原地覆盖。

    带 `next_path` 属性：调用方不用自己拼新版本名，直接拿它去建 v002。
    """

    def __init__(self, path: str, record: dict | None, next_path: str):
        self.path = path
        self.record = record
        self.next_path = next_path
        who = f"（{record['approver']} @ {record['utc_time']}）" if record else ""
        super().__init__(
            f"已冻结，拒绝原地覆盖：{path}{who}；改动必须生成新版本 → {next_path}"
        )


# ---------------------------------------------------------------------------
# 冻结
# ---------------------------------------------------------------------------

def derive_next_path(path: str | os.PathLike) -> str:
    """给定**已冻结件**的路径，给出它的下一个版本路径。

    `04_approved/song01/ground_truth.musicxml` → `.../ground_truth_v002.musicxml`
    `.../approved_vocal_pilot.wav`               → `.../approved_vocal_pilot_v002.wav`

    为什么固定名从 `v002` 起：那个没版本号的文件**就是** v001，
    再叫 `v001` 会得到两个 v001，哈希记录里 `file` 段就分不清谁是谁。
    """
    p = os.fspath(path)
    directory, name = os.path.split(p)
    base = os.path.basename(name)
    m = VERSION_IN_NAME_RE.search(base)
    if m:
        new_name = base[: m.start()] + f"_v{int(m.group(1)) + 1:03d}" + base[m.end():]
    else:
        stem, dot, ext = base.partition(".")
        new_name = f"{stem}_v002{dot}{ext}"
    return os.path.join(directory, new_name) if directory else new_name


def is_frozen(path: str | os.PathLike) -> bool:
    """是否已有冻结记录（只看记录存在性，不比对哈希 —— 那是 `verify_frozen` 的事）。"""
    return os.path.exists(hash_record_path(path))


def freeze_file(
    path: str | os.PathLike,
    approver: str,
    *,
    utc_time: str | None = None,
    record: str | os.PathLike | None = None,
    read_only: bool = True,
) -> dict:
    """冻结一个文件：写 `<file>.sha256`，并把文件置为只读位。

    返回记录字段字典（含 `record_path` / `sha256`），方便调用方直接回填报告。

    行为约定：
      - **目标文件必须已存在**（冻结的是内容，不是承诺）；不存在 → `FileNotFoundError`。
      - **已有记录即拒绝**（`AlreadyFrozenError`，附 `next_path`），绝不静默覆盖。
      - **文件名必须是合规的冻结件名**（`naming.validate_frozen_name`）；
        名字合规性放在这里而不是调用方，是因为「冻结一个乱名的文件」是必须
        在写记录**之前**就拦住的事。
      - `utc_time` 可注入 —— 测试要断言「逐字段一致」就得能钉住时间，
        否则只能断言「大概是现在」，那等于没断言。
      - `read_only=True` 只设**只读位**；不设 `0o000`（那是拒绝自己读，
        会连校验都做不了）。它拦手滑，不拦恶意，见模块 docstring 第 3 条。
    """
    path = os.fspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"冻结目标不存在，不能冻结一个还没写出来的东西：{path}")

    name = os.path.basename(path)
    name_violations = validate_frozen_name(name)
    if name_violations:
        raise ValueError(
            f"文件名不合冻结规范，先在命名上改对再冻结：{path}\n"
            + "\n".join(f"  - {v}" for v in name_violations)
        )

    record_path = os.fspath(record) if record is not None else hash_record_path(path)
    if os.path.exists(record_path):
        existing = None
        try:
            existing = read_hash_record(path)
        except (OSError, ValueError):
            pass  # 记录存在但读不动/解析不了：一样拒绝覆盖，只是给不出「谁冻的」
        raise AlreadyFrozenError(path, existing, derive_next_path(path))

    sha = sha256_file(path)
    if read_only:
        os.chmod(path, 0o444)
    content = format_hash_record(sha, name, utc_time or utc_now(), approver)
    with open(record_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    return {"sha256": sha, "file": name, "utc_time": parse_hash_record(content)["utc_time"],
            "approver": approver, "record_path": record_path, "path": path}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerifyResult:
    """一次校验的结论。

    `ok` 之外还要 `status`，因为「失败」有四种成因，处置完全不同：
    记录丢了要去找记录、文件没了要去找文件、哈希不符是**被篡改**、
    记录本身非法说明流程漏了。把它们混成一个 False，报告里就只剩「失败」两个字。
    """

    path: str
    status: str          # ok | missing_file | missing_record | hash_mismatch | bad_record
    message: str
    expected: str = ""
    actual: str = ""
    record: dict | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __bool__(self) -> bool:
        return self.ok

    def to_dict(self) -> dict:
        return {"path": self.path, "status": self.status, "ok": self.ok,
                "expected": self.expected, "actual": self.actual, "message": self.message}

    def __str__(self) -> str:
        extra = f" expected={self.expected[:16]}… actual={self.actual[:16]}…" if self.actual else ""
        return f"[{self.status}] {self.path}{extra} {self.message}"


#: 状态 → 人话（报告与评论里直接用）。
STATUS_MEANING = {
    "ok": "哈希与记录一致，文件未被改动",
    "missing_file": "冻结件不见了 —— 记录还在，文件没了",
    "missing_record": "缺少 *.sha256 记录 —— 无法证明这个文件是谁冻的、冻的是哪一版",
    "hash_mismatch": "当前文件哈希 != 记录哈希 —— 内容被改动（就地覆盖）",
    "bad_record": "记录文件存在但格式非法 —— 按 4 段格式重写，别猜",
}


def verify_frozen(path: str | os.PathLike) -> VerifyResult:
    """比对当前文件哈希与冻结记录。**被篡改即失败**（返回 `ok=False`，不抛异常）。

    返回对象而不是抛异常，是因为「校验不通过」是**预期结果**之一 ——
    门禁脚本要把它写进报告并继续扫剩下的文件，而不是在第一个坏文件上崩掉。

    ⚠ 只做**检测**：它能证明「被改过」，不能阻止「被改」。
    真正的阻止靠文件系统只读位（#27）+ Git hooks，而两者都有绕过方式。
    """
    path = os.fspath(path)
    record_path = hash_record_path(path)
    if not os.path.exists(record_path):
        return VerifyResult(path, "missing_record", STATUS_MEANING["missing_record"])
    try:
        rec = read_hash_record(path)
    except (OSError, ValueError) as exc:
        return VerifyResult(path, "bad_record", f"{STATUS_MEANING['bad_record']}：{exc}")
    if not os.path.isfile(path):
        return VerifyResult(path, "missing_file", STATUS_MEANING["missing_file"], expected=rec["sha256"], record=rec)

    actual = sha256_file(path)
    if actual != rec["sha256"]:
        return VerifyResult(path, "hash_mismatch",
                            f"{STATUS_MEANING['hash_mismatch']}（改动者：{rec['approver']} 冻结于 {rec['utc_time']}）",
                            expected=rec["sha256"], actual=actual, record=rec)
    return VerifyResult(path, "ok", STATUS_MEANING["ok"], expected=rec["sha256"], actual=actual, record=rec)


def verify_all(paths) -> list[VerifyResult]:
    """批量校验，逐项返回（不短路）。`all(r.ok for r in verify_all(...))` 即总结论。"""
    return [verify_frozen(p) for p in paths]


# ---------------------------------------------------------------------------
# 只读位（#13「置为只读不可变」的可执行部分）
# ---------------------------------------------------------------------------

def set_read_only(path: str | os.PathLike, read_only: bool = True) -> str:
    """设置/清除只读位，返回最终权限字符串。

    只改**拥有者写位**，其余权限原样保留：`chmod 0444` 会把原本 0644 的
    文件变成人人都不可写，在共享目录里会连合法流程一起卡死。
    用 `stat.S_IWUSR` 单点位就够表达「这份文件已冻结」。
    """
    import stat  # 局部 import：只用在这里，不污染模块顶部

    st = os.stat(path)
    mode = (st.st_mode & ~stat.S_IWUSR) if read_only else (st.st_mode | stat.S_IWUSR)
    os.chmod(path, mode)
    return f"{stat.filemode(mode)}"


def is_read_only(path: str | os.PathLike) -> bool:
    """是否已无拥有者写位。Windows 上这个判断不可靠（见 README）。"""
    import stat

    return not (os.stat(path).st_mode & stat.S_IWUSR)


# ---------------------------------------------------------------------------
# 冻结后的追加校验（#13「不得原地覆盖」的检测面）
# ---------------------------------------------------------------------------

def check_no_overwrite(original: str | os.PathLike, candidate: str | os.PathLike) -> None:
    """确认 `candidate` 不是对已冻结件 `original` 的原地覆盖。

    给调用方一个显式断言点：写 v002 之前先问一句「我是不是正在覆盖冻结件」。
    覆盖就抛 `AlreadyFrozenError`，并给出应该用的路径。
    """
    if os.path.abspath(original) == os.path.abspath(candidate) and is_frozen(original):
        raise AlreadyFrozenError(os.fspath(original), None, derive_next_path(original))


def frozen_names_are_consistent(path: str | os.PathLike) -> bool:
    """记录里的 `<file>` 段是否等于被冻结件的文件名。

    单列一个函数是因为这件事很容易漏：记录指向 `ground_truth.musicxml`，
    而你在校验 `ground_truth_v002.musicxml`，两个哈希都对得上就已经错位了。
    """
    try:
        return read_hash_record(path)["file"] == os.path.basename(os.fspath(path))
    except (OSError, ValueError):
        return False


__all__ = [
    "AlreadyFrozenError",
    "FIXED_FROZEN_BASENAMES",
    "FreezeError",
    "STATUS_MEANING",
    "VerifyResult",
    "check_no_overwrite",
    "derive_next_path",
    "format_hash_record",
    "freeze_file",
    "frozen_names_are_consistent",
    "hash_record_path",
    "is_frozen",
    "is_read_only",
    "parse_hash_record",
    "read_hash_record",
    "set_read_only",
    "sha256_file",
    "utc_now",
    "verify_all",
    "verify_frozen",
]
