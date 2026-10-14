"""命名规范校验（#13）——纯 stdlib，只回答「这个名字合不合规」。

规则的**唯一定义处**是文档，不是本文件：
  - `00_docs/gates/gate_G2.md`「冻结定义」（对齐 #13）
  - `00_docs/gates/gate_G3.md` G3-10、`gate_G4.md` G4-10（`song_name_mix_v001.wav`）
  - `00_docs/glossary.md`（song_id 形态）、`README.md`「Windows 路径风险规避」
本文件只是把它们变成可执行的断言；文档与本文件冲突时以文档为准。

设计口径（三条，都是刻意的）：

1. **返回违规列表，不抛异常、不打印。** 调用方要能一次拿到全部问题去写报告，
   而不是修一个跑一次。唯一的例外是「路径不是相对路径」这类**没法校验**的输入，
   那时抛 `ValueError` —— 静默返回空列表等于假装合规。
2. **ASCII 违规一律 FAIL。** README 说「大小写不敏感 → 全树统一小写」是**规避**建议，
   这里直接把它变成硬规则：`Stems/` 与 `stems/` 在 NTFS 上同目录，
   管这个隐患的唯一可靠办法是根本不允许出现大写。
3. **不写任何数据目录。** 本模块不碰源区与批准区（见 README 的目录表），
   也不替调用方决定文件落在哪 —— 落点是调用方的事（见 `freeze.py`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# 常量：全部指向文档出处，改这里等于改规则，需记理由
# ---------------------------------------------------------------------------

#: `glossary.md`：song_id 「ASCII 定长：`song01`、`song02`…（超 99 用三位）」。
#: 注意**不是** `song1`：定长是为了排序与目视对齐都稳定。
SONG_ID_RE = re.compile(r"^song\d{2,3}$")

#: README「版本号 `vNNN` 从 `v001` 递增」—— 固定 3 位，`v1` / `v0001` 都不算数。
VERSION_SUFFIX_RE = re.compile(r"^v\d{3}$")

#: gate_G4 G4-10 的输出规格命名。G4-10 原文里 `song_name` 是字面占位符；
#: `README.md`「文件命名规范」把它实例化为 `song01_mix_v001.wav`，
#: 而阶段1 e2e 的 `MockDAW`（`mocks.py`）产出的就是 `flow_demo_mix_v001.wav`。
#: 两种写法都放行，**只此两种**；`song_mix_v001.wav`（缺序号）要被抓出来。
MASTER_MIX_RE = re.compile(
    r"^(?:"
    r"[a-z0-9][a-z0-9_-]*"   # <song_id>：glossary 的 songNN 只是举例；#122 实际用 flow-demo-001
    r"|song_name"            # gate_G4 G4-10 的字面占位符形态
    r")_mix_v\d{3}\.wav$"
)

#: README「Windows 路径风险规避」第 3 条：单段 ≤ 40 字符。
MAX_PATH_SEGMENT = 40

#: README 第 4 条：DOS 保留名（不分大小写、不含扩展名）。
RESERVED_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

#: README 第 4 条：Windows 非法字符 + 通配符。
_ILLEGAL_CHARS = '<>:"|?*'

#: 版本号捕获（通用，用于 `next_version` / `derive_next_path` 等版本派生）。
VERSION_IN_NAME_RE = re.compile(r"_v(\d{3})(?=\.|$)")

#: 正式交付物（ext → 要求的命名形态）。`primary=True` 的产物**必须**带版本号。
#: 出处：`00_docs/deliverables.md` 的「交付物 | 路径 / 文件名」列。
CANONICAL_DELIVERABLES: dict[str, dict] = {
    ".musicxml": {
        "primary": True,
        "pattern": re.compile(r"^(?:song\d{2,3}|ground_truth)_.+_v\d{3}\.musicxml$"),
        "note": "deliverables.md：song01_review_v001.musicxml / song01_Audiveris_v001.musicxml",
    },
    ".mid": {
        "primary": True,
        "pattern": re.compile(r"^song\d{2,3}_.+_v\d{3}\.mid$"),
        "note": "deliverables.md：song01_drums_main_v001.mid / song01_bass_v001.mid",
    },
    ".mscz": {
        "primary": True,
        # `song01_approved_v001.mscz` 与 `song01_review_v001.mscz` 同形，一条正则覆盖。
        "pattern": re.compile(r"^song\d{2,3}_.+_v\d{3}\.mscz$"),
        "note": "deliverables.md：song01_review_v001.mscz / song01_approved_v001.mscz",
    },
    ".acep": {
        "primary": True,
        "pattern": re.compile(r"^song\d{2,3}_.+_v\d{3}\.acep$"),
        "note": "deliverables.md：song01_ace_v001.acep",
    },
    ".svp": {
        "primary": True,
        "pattern": re.compile(r"^song\d{2,3}_.+_v\d{3}\.svp$"),
        "note": "deliverables.md：song01_sv2_v001.svp",
    },
    ".tif": {
        "primary": True,
        "pattern": re.compile(r"^song\d{2,3}_source_p\d{3}_v\d{3}\.tif$"),
        "note": "deliverables.md：song01_source_p001_v001.tif（每页一文件）",
    },
    ".pdf": {
        "primary": True,
        "pattern": re.compile(r"^song\d{2,3}_source_v\d{3}\.pdf$"),
        "note": "deliverables.md：song01_source_v001.pdf",
    },
    ".png": {
        "primary": True,
        "pattern": re.compile(r"^song\d{2,3}_[a-z0-9_]*v\d{3}\.png$"),
        "note": "deliverables.md：02_source/<song_id>/preprocessed/song01_p001_v001.png",
    },
    ".wav": {
        "primary": True,
        # 先匹配 master，再匹配普通；`approved_vocal_pilot.wav` 是受保护的**固定名**
        # （gate_G3 G3-10 原文），不带版本号 —— 这是唯一被允许的无版本主产物。
        "pattern": re.compile(
            r"^(?:"
            r"(?:song\d{2,3}|song_name)_mix_v\d{3}\.wav"          # G4-10 master
            r"|song\d{2,3}_.+_v\d{3}\.wav"                        # 普通产物 / stem
            r"|approved_vocal_pilot\.wav"                         # G3-10 固定名
            r")$"
        ),
        "note": "gate_G4 G4-10 命名 / gate_G3 G3-10 固定名",
    },
}

#: 非正式交付物：**不校验形态**，只走通用的字符/长度/保留名规则。
#: `.rpp`/`.ardour`（deliverables.md 里写的是 `song01_mix_v001.rpp`）暂不列，
#: 避免把没把握的规则写死 —— 有把握的才抓。
UNCHECKED_DELIVERABLE_EXTS = {
    ".rpp", ".ardour", ".json", ".csv", ".md", ".txt", ".log",
    ".sha256", ".mxl", ".mp3", ".db", ".zip",
}

#: 冻结件里**没有版本号**的固定名（G2 谱面 + G3 声乐）。
#: `ground_truth.musicxml` 的下一个版本是 `ground_truth_v002.musicxml`（见 freeze.py）。
FIXED_FROZEN_BASENAMES = {"ground_truth.musicxml", "approved_vocal_pilot.wav"}

#: 形如 `<file>.sha256` 的哈希记录 —— 它继承被冻结件的名字，所以**不套版本规则**。
_HASH_RECORD_RE = re.compile(r"^.+\.sha256$")

#: 版本号提取：`_v001` 或 `_v001.ext`（也接受 `.wav` 之类的产物）。
#: 「ground_truth`** 是固定名，`ground_truth_v002.musicxml` 才带版本。
def version_of(name: str) -> int:
    """取出名字里的版本号；没有版本号返回 0。

    `0` 是刻意的：调用方拿它做比较时，「无版本」永远小于 `v001`，
    不需要额外判断 None。
    """
    for m in re.finditer(r"_v(\d{3})(?=[.\-_]|$)", name):
        return int(m.group(1))
    return 0


# ---------------------------------------------------------------------------
# 违规项
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Violation:
    """一条命名违规。

    `code` 是给机器（测试、报告表格的「拦不拦得住」列）用的稳定标识；
    `message` 与 `hint` 是给人的，`hint` 一定指向「怎么改」，不是「你错了」。
    """

    code: str
    path: str
    message: str
    hint: str = ""

    def __str__(self) -> str:  # 便于直接拼进报告
        return f"[{self.code}] {self.path}: {self.message}" + (f"（{self.hint}）" if self.hint else "")


def _v(code: str, path: str, message: str, hint: str = "") -> Violation:
    return Violation(code=code, path=path, message=message, hint=hint)


def _segments(rel: str) -> list[str]:
    return [p for p in PurePosixPath(rel).parts if p not in ("", ".")]


def _first_byte(s: str) -> int:
    return ord(s[0])


# ---------------------------------------------------------------------------
# 通用规则（README「Windows 路径风险规避」1~5）
# ---------------------------------------------------------------------------

def check_path_chars(rel: str) -> list[Violation]:
    """字符层规则：ASCII 小写、无空格、无非法字符、无保留名、长度受限。

    这一层对**目录段与文件名**都生效 —— 目录名带大写同样会在 NTFS 上撞车。
    """
    out: list[Violation] = []
    for seg in _segments(rel):
        stem = seg.split(".", 1)[0]
        if not seg.isascii():
            bad = "".join(sorted({c for c in seg if not c.isascii()}))
            out.append(_v("E_NON_ASCII", rel, f"段 {seg!r} 含非 ASCII 字符 {bad!r}",
                          "README：一律 ASCII，禁中文/韩文/emoji/全角"))
        elif not seg.islower():
            up = "".join(sorted({c for c in seg if c.isupper()}))
            out.append(_v("E_UPPER_CASE", rel, f"段 {seg!r} 含大写字符 {up!r}",
                          "README：NTFS 大小写不敏感，全树统一小写"))
        if " " in seg:
            out.append(_v("E_SPACE", rel, f"段 {seg!r} 含空格", "用下划线 _ 连接"))
        bad_chars = sorted({c for c in seg if c in _ILLEGAL_CHARS or _first_byte(c) < 32})
        if bad_chars:
            out.append(_v("E_ILLEGAL_CHAR", rel, f"段 {seg!r} 含非法字符 {bad_chars!r}",
                          'Windows 保留 <>:"|?* 与控制字符'))
        if stem.lower() in RESERVED_STEMS:
            out.append(_v("E_RESERVED_NAME", rel, f"段 {seg!r} 是 DOS 保留名",
                          "重命名，例如 song01_con_notes"))
        if seg.endswith((".", " ")):
            out.append(_v("E_TRAILING_DOT_OR_SPACE", rel, f"段 {seg!r} 以点或空格结尾",
                          "Windows 会静默吞掉尾部的点/空格，改掉"))
        if len(seg) > MAX_PATH_SEGMENT:
            out.append(_v("E_SEGMENT_TOO_LONG", rel, f"段 {seg!r} 长度 {len(seg)} > {MAX_PATH_SEGMENT}",
                          "缩短到 40 字符内，Windows 260 上限要留余量"))
    return out


def validate_song_id(song_id: str) -> list[Violation]:
    """`glossary.md`：song_id 是 ASCII 定长 `songNN`（超 99 用三位）。"""
    out = check_path_chars(song_id) if not SONG_ID_RE.match(song_id) else []
    if not SONG_ID_RE.match(song_id):
        out.insert(0, _v("E_SONG_ID_FORM", song_id,
                         "song_id 必须是 songNN / songNNN",
                         "如 song01；不要用 song1、song-01、曲名拼音"))
    return out


def validate_version_suffix(name: str) -> list[Violation]:
    """README：版本号 `vNNN` 从 `v001` 递增，**新版本 = 新文件**。

    只对「看起来在表达版本」的名字发难：形如 `..._v1.wav` / `..._v0001.wav`
    这类写法按坏消息处理（附 `vNNN` 违规），其余没有 `_v` 片段的名字交给
    `validate_deliverable` 的形态规则去判「缺版本号」。
    """
    out: list[Violation] = []
    for m in re.finditer(r"_v([0-9]+)(?=[.\-_]|$)", name):
        if not VERSION_SUFFIX_RE.match("v" + m.group(1)):
            out.append(_v("E_VERSION_FORM", name, f"版本号 'v{m.group(1)}' 不是 vNNN 形态",
                          "定长三位：v001 / v002"))
    return out


def validate_deliverable(name: str, *, primary: bool = True) -> list[Violation]:
    """按 `deliverables.md` 的交付物形态校验**文件名**（不含目录）。

    `primary=False` 表示这是非正式产物（如 `*.log`）—— 只查通用字符规则，
    不查形态。理由：没把握的规则不写死，否则工具会拦下合法的中间文件，
    然后大家学会「加个 --force 跳过校验」，规则就废了。
    """
    out = check_path_chars(name)
    out.extend(validate_version_suffix(name))

    if _HASH_RECORD_RE.match(name):
        return out  # `<file>.sha256` 继承被冻结件名，不套版本规则

    ext = PurePosixPath(name).suffix.lower()
    spec = CANONICAL_DELIVERABLES.get(ext)
    if spec is not None and primary:
        if name in FIXED_FROZEN_BASENAMES:
            return out
        if not spec["pattern"].match(name):
            code = "E_NO_VERSION" if not VERSION_IN_NAME_RE.search(name) else "E_NAME_FORM"
            hint = spec["note"]
            out.append(_v(code, name, f"不符交付物命名形态（期望 {spec['pattern'].pattern}）", hint))
    return out


def validate_master_mix(name: str) -> list[Violation]:
    """gate_G4 **G4-10**：master 命名 `song_name_mix_v001.wav`（硬规格）。"""
    out = check_path_chars(name)
    if not MASTER_MIX_RE.match(name):
        out.append(_v("E_MASTER_MIX_NAME", name,
                      "master 必须命名为 <song_id>_mix_vNNN.wav（G4-10）",
                      "如 song01_mix_v001.wav / flow_demo_mix_v001.wav"))
    elif version_of(name) < 1:
        out.append(_v("E_VERSION_FORM", name, "版本号必须从 v001 起", "不能是 v000"))
    return out


def validate_relative_path(rel: str) -> list[Violation]:
    """相对路径整体校验：绝对路径与 `..` 一律拒绝。

    为什么单列：`07_mix/README.md` 说「工程内禁绝对路径，保证可搬迁复现」，
    绝对路径在搬迁后必然断链，而断链的文件同样会「看起来存在」。
    """
    if not rel or rel in (".", ".."):
        raise ValueError(f"不是可校验的相对路径：{rel!r}")
    if rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        raise ValueError(f"只接受仓库内相对路径（POSIX），收到：{rel!r}")
    out = check_path_chars(rel)
    if ".." in _segments(rel):
        out.append(_v("E_PATH_ESCAPE", rel, "路径含 ..", "用仓库内相对路径，别往上爬"))
    return out


# ---------------------------------------------------------------------------
# 目录约定（deliverables.md 的落位）
# ---------------------------------------------------------------------------

#: 目录 → 该目录下必须遵守的形态类别。键是 `rel` 的前两段。
_DIR_KINDS = {
    "02_source": "source",
    "03_omr": "deliverable",
    "04_approved": "frozen",
    "05_vocals": "deliverable",
    "06_arrangement": "deliverable",
    "07_mix": "deliverable",
}


def check_repo_path(rel: str, *, primary: bool = True) -> list[Violation]:
    """从仓库根算起的相对路径校验（含目录约定）。

    目录约定只做**能确证的**两条：
      1. `02_source/<song_id>/...` 里，`<song_id>` 必须是合法 song_id；
      2. `07_mix/<song_id>/masters/` 下的 `.wav` 必须符合 G4-10 命名。
    其余目录不发明规则。
    """
    out = validate_relative_path(rel)
    segs = _segments(rel)
    if not segs:
        return out

    root = segs[0]
    kind = _DIR_KINDS.get(root)
    if kind is not None and len(segs) >= 2:
        out.extend(validate_song_id(segs[1]))

    name = segs[-1]
    if root == "07_mix" and "masters" in segs and name.lower().endswith(".wav"):
        out.extend(validate_master_mix(name))
    elif kind == "frozen":
        out.extend(validate_frozen_name(name))
    else:
        out.extend(validate_deliverable(name, primary=primary))
    return out


def validate_frozen_name(name: str) -> list[Violation]:
    """`04_approved/<song_id>/` 下的**冻结件**命名。

    冻结件有两种形态，都必须认得：
      - 固定名（G2 定义）：`ground_truth.musicxml` + `ground_truth.musicxml.sha256`
      - 版本派生（#13「此后任何修改生成 v002」）：`ground_truth_v002.musicxml`
    名字里带版本号就算版本派生，不许再叫 `ground_truth.musicxml` 的旧名 ——
    那样哈希记录会指向另一个文件，而**冻结的全部意义**就是名字 ↔ 内容一一对应。
    """
    out = check_path_chars(name)
    out.extend(validate_version_suffix(name))

    if _HASH_RECORD_RE.match(name):
        target = name[: -len(".sha256")]
        # 版本派生的哈希记录：`ground_truth.musicxml.sha256` → `ground_truth_v002.musicxml.sha256`
        if not (target in FIXED_FROZEN_BASENAMES or VERSION_IN_NAME_RE.search(target)):
            out.append(_v("E_HASH_RECORD_TARGET", name,
                          f"哈希记录 {name} 指向的名字既不是固定冻结名，也不带 vNNN",
                          f"应为 {' 或 '.join(sorted(FIXED_FROZEN_BASENAMES))}（或它们的 _vNNN 形态）+ .sha256"))
        return out

    if name in FIXED_FROZEN_BASENAMES:
        return out  # 首次冻结，合规

    # 版本派生：`ground_truth_v002.musicxml` —— 把 `_vNNN` 摘掉后应当回到固定名',
    # 或回到一个形态合规的交付物名（`song01_approved_v001.mscz`）。
    if VERSION_IN_NAME_RE.search(name):
        deversioned = VERSION_IN_NAME_RE.sub("", name)
        if deversioned in FIXED_FROZEN_BASENAMES:
            return out
        if CANONICAL_DELIVERABLES.get(PurePosixPath(deversioned).suffix.lower(), {}).get("pattern", re.compile(r"$^")).match(deversioned):
            return out

    ext = PurePosixPath(name).suffix.lower()
    spec = CANONICAL_DELIVERABLES.get(ext)
    if spec is not None and spec["pattern"].match(name):
        return out  # 版本派生，形态合规

    out.append(_v("E_FROZEN_NAME", name,
                  "04_approved/ 下的冻结件必须是 ground_truth.musicxml（首次）"
                  "或其 _vNNN 版本派生，外加对应 *.sha256",
                  "改动用新版本：ground_truth_v002.musicxml，不得原地覆盖"))
    return out


# ---------------------------------------------------------------------------
# 版本派生（#13：「此后任何修改生成 v002，不得原地覆盖」）
# ---------------------------------------------------------------------------

def next_version(name: str) -> str:
    """给名字派生下一个版本号（`..._v001.wav` → `..._v002.wav`）。

    没有版本号的固定名按前缀派生出**带版本**的新名：
    `ground_truth.musicxml` → `ground_truth_v002.musicxml`
    （从 v002 起，因为 v001 就是那个没版本号的原始冻结件本身 —— 它的「版本 1」
    已经被 `ground_truth.musicxml` 占用，再叫 v001 会得到两个 v001）。

    ⚠ 本函数**只算名字，不碰文件系统**。真要生成新版本请用 `freeze.derive_next_path`，
    它顺带校验并给你看「上一个版本是被谁冻结的」。
    """
    m = VERSION_IN_NAME_RE.search(name)
    if m:
        return name[: m.start()] + f"_v{int(m.group(1)) + 1:03d}" + name[m.end():]
    if name in FIXED_FROZEN_BASENAMES:
        stem, _, ext = name.partition(".")
        return f"{stem}_v002.{ext}"
    raise ValueError(f"{name!r} 既无可递增的 vNNN，也不是已知固定冻结名，无法派生下一版本")


def derive_next_path(path_or_name: str) -> str:
    """`next_version` 的路径版：只替换最后一段，目录原样保留。"""
    p = PurePosixPath(path_or_name)
    return str(p.parent / next_version(p.name)) if str(p.parent) != "." else next_version(p.name)


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------

def validate(name_or_relpath: str, *, kind: str = "deliverable", primary: bool = True) -> list[Violation]:
    """按类别校验，返回违规列表（空列表 = 合规）。

    `kind`：
      - `"song_id"`：只校验 song_id 形态
      - `"deliverable"`：交付物文件名（默认）
      - `"master_mix"`：G4-10 master 命名
      - `"frozen"`：`04_approved/` 冻结件
      - `"repo_path"`：仓库内相对路径（含目录约定）
    """
    if kind == "song_id":
        return validate_song_id(name_or_relpath)
    if kind == "deliverable":
        return validate_deliverable(name_or_relpath, primary=primary)
    if kind == "master_mix":
        return validate_master_mix(name_or_relpath)
    if kind == "frozen":
        return validate_frozen_name(name_or_relpath)
    if kind == "repo_path":
        return check_repo_path(name_or_relpath, primary=primary)
    raise ValueError(f"未知校验类别 {kind!r}")


def is_valid(name_or_relpath: str, **kw) -> bool:
    """布尔版（`validate` 的空列表判定），方便单行断言。"""
    return not validate(name_or_relpath, **kw)


def format_violations(violations: list[Violation]) -> str:
    """把违规列表排成报告可用的文本；空列表给一句明确的话，不给空字符串。"""
    if not violations:
        return "合规：无违规"
    return "\n".join(f"- {v}" for v in violations)
