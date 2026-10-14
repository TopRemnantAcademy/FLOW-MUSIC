"""分轨 stem 组织与 G4-9 校验（纯 stdlib）。

gate_G4.md · **G4-9 stem 完整性**（硬）：
    鼓 / 贝斯 / 和声 / 보컬 stem 各自可重开、可同播、**时长一致**。

本模块把那条「硬」拆成可执行的四件事：

1. **四轨齐**（`REQUIRED_STEMS`）—— 少一条就 FAIL，并指名缺哪条。
2. **可重开** —— 每条都能被 `wave.open` 打开且位深合法（`WavError` 家族）。
3. **时长一致** —— 全长轨之间 `max - min ≤ TOLERANCE_SECONDS`。
   注意方向：这里**不用** G4-5 的 ±1s。G4-5 是「master vs 小节×拍数×BPM 的预期」，
   允许 ±1s；G4-9 是「四条 stem 互相比」，同一次导出的分轨误差应在毫秒级，
   ±1s 会把「50ms 错位」这种真缺陷放过去，所以收紧到 5ms，且**可传参覆盖**。
4. **可同播** —— 用 `wavio.mix_down` 真做一次逐采样相加：长度不同会直接抛错，
   这就是「能同播」最诚实的验证方式（不是靠「看起来一样长」）。

**보컬轨从 `05_vocals/` 冻结件来**（`approved_vocal_pilot.wav`，过 G3 只读）：
用 `StemRef("vocal", <冻结件路径>, origin="frozen")` 显式接进来 —— 复制一份到
`stems/` 会让同一份声乐出现两个「真相」，而 G4-7 的频谱关系必须查批准件本身。

一切失败都变成 `StemReport.problems` 里的**具名条目**，绝不静默 return True。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import wavio

#: G4-9 点名的四轨（顺序即 mix 顺序，便于断言稳定）。
REQUIRED_STEMS = ("drums", "bass", "harmony", "vocal")

#: 轨道别名 → 规范名。보컬 二字在文档里是韩文，文件系统里一律 ASCII `vocal`
#: （README「Windows 路径风险规避」：禁中文/韩文/空格）。
ALIASES = {
    "drum": "drums",
    "drums": "drums",
    "bass": "bass",
    "bassline": "bass",
    "chord": "harmony",
    "chords": "harmony",
    "harmony": "harmony",
    "pad": "harmony",
    "vocal": "vocal",
    "vocals": "vocal",
    "voice": "vocal",
}

#: 全长轨互比的时长容差（秒）。根因见模块 docstring：G4-9 ≠ G4-5。
TOLERANCE_SECONDS = 0.005

#: 期望输出规格（G4-10）：44.1k / 24bit / 单声道 stem。可传 `expect` 覆盖。
DEFAULT_STEM_SPEC = wavio.Spec(44100, 3, 1)


@dataclass(frozen=True)
class StemRef:
    """一条 stem 的落位与角色。

    `origin` 只有两个合法值：
      - `"stem"`：DAW 导出的分轨，落 `07_mix/<song_id>/stems/`；
      - `"frozen"`：从上游冻结件直接引用（보컬 = `05_vocals/.../approved_vocal_pilot.wav`），
        **不复制**，避免与批准件产生两份真相。
    """

    name: str
    path: str
    origin: str = "stem"

    def __post_init__(self):
        canon = canonical_name(self.name)
        if canon != self.name:
            object.__setattr__(self, "name", canon)
        if self.origin not in ("stem", "frozen"):
            raise ValueError(f"origin 只能是 stem/frozen，收到 {self.origin!r}")


@dataclass
class StemCheck:
    """单条 stem 的检查结果（可直接进 `08_qc/verify_report.json`）。"""

    name: str
    path: str
    present: bool
    duration: float = 0.0
    bit_depth: int = 0
    framerate: int = 0
    channels: int = 0
    bytes_: int = 0
    peak: float = 0.0
    error: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "path": self.path,
            "present": self.present,
            "duration": round(self.duration, 6),
            "bit_depth": self.bit_depth,
            "framerate": self.framerate,
            "channels": self.channels,
            "bytes": self.bytes_,
            "peak": round(self.peak, 6),
            "error": self.error,
        }


@dataclass
class StemReport:
    """G4-9 的判定记录。`ok` 是唯一的通过判据，`problems` 说清**为什么**不通过。"""

    checks: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    mix_peak: float = 0.0
    tolerance: float = TOLERANCE_SECONDS

    @property
    def ok(self):
        return not self.problems

    @property
    def durations(self):
        return {c.name: c.duration for c in self.checks if c.present}

    def to_dict(self):
        return {
            "gate": "G4-9",
            "status": "PASS" if self.ok else "FAIL",
            "tolerance_s": self.tolerance,
            "mix_peak": round(self.mix_peak, 6),
            "problems": list(self.problems),
            "stems": [c.to_dict() for c in self.checks],
        }


def canonical_name(name):
    """把别名收敛到 G4-9 的四个名字；未知名字原样返回（由 `check_stems` 报错）。"""
    return ALIASES.get(str(name).strip().lower(), str(name).strip().lower())


#: 文件名里「可被当成轨道名」的词 —— 收集成集合，避免按位置瞎猜。
#: `song01_drums_v001.wav` 与 `drums.wav` 都取得到 `drums`，
#: 而 `song01_drums_main_v001.wav` 取的是第一个命中词（drums），不是 `v001`。
_KNOWN_TOKENS = set(ALIASES)


def name_from_filename(filename):
    """从文件名推断轨道名；推断不出返回 None。

    为什么按「已知词表」扫而不用固定位置：目录里实际会出现
    `song01_drums_v001.wav`（#13 规范）和 `drums.wav`（手工素材）两种，
    位置法必然在其中一种上取到 `v001` 这种垃圾 —— 那会让「缺哪轨」
    的报错指向一个不存在的名字，排查成本反而更高。
    """
    stem = os.path.splitext(os.path.basename(filename))[0].lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", stem) if t]
    for tok in tokens:
        if tok in _KNOWN_TOKENS:
            return canonical_name(tok)
    # 全文兜底：`drums-raw`、`bassstem` 这类粘连写法
    for known in sorted(_KNOWN_TOKENS, key=len, reverse=True):
        if known in stem:
            return canonical_name(known)
    return None


def stem_paths(stems_dir, *, ext=".wav", on_unknown="ignore"):
    """扫 `stems/` 目录，按规范名返回 `{name: path}`。

    只在**目录层面**做发现（DAW 导出后不会自带清单）。目录不存在 → `MissingFileError`：
    空目录和不存在的目录是两种病，不该给同一种沉默。

    认不出名字的文件默认**跳过，不报错** —— 目录里可能有 `README.md`、DAW 的
    `.rpp`、`song01_drums_raw.wav` 这类旁证文件；把它们当「非法轨道」会把
    一个正常目录判成 FAIL（假阳性比漏报更消耗信任）。真正要管的「多出来的轨」
    是**能认出名字但不在 G4-9 四轨里**的那些（如 `strings.wav`）。
    """
    if not os.path.isdir(stems_dir):
        raise wavio.MissingFileError(f"stems 目录不存在：{stems_dir}")
    found = {}
    for fn in sorted(os.listdir(stems_dir)):
        base, e = os.path.splitext(fn)
        if e.lower() != ext:
            continue
        name = name_from_filename(fn)
        if name is None:
            if on_unknown == "error":
                raise wavio.WavError(f"认不出轨道名，无法归位：{fn}")
            continue
        found[name] = os.path.join(stems_dir, fn)
    return found


def check_stems(refs, *, tolerance=TOLERANCE_SECONDS, expect=DEFAULT_STEM_SPEC):
    """按 G4-9 校验分轨，返回 `StemReport`。

    `refs` 可以是：
      - `StemRef` 列表；
      - `{name: path}` 字典（顺序无关，名字按 `canonical_name` 收敛）；
      - 一个 `stems/` 目录路径（走 `stem_paths` 发现）。

    `expect=None` 可跳过位深/采样率核对 —— 当上游只要求「时长一致」时用。
    """
    if isinstance(refs, (str, os.PathLike)):
        items = [StemRef(n, p) for n, p in sorted(stem_paths(refs).items())]
    elif isinstance(refs, dict):
        items = [StemRef(n, p) for n, p in sorted(refs.items())]
    else:
        items = list(refs)

    report = StemReport(tolerance=tolerance)
    seen = set()
    dupes = []
    for ref in items:
        if ref.name in seen:
            dupes.append(ref.name)
        seen.add(ref.name)

    if dupes:
        report.problems.append(f"同一轨重复出现：{sorted(set(dupes))}（G4-9 要求一轨一份）")
    missing = [n for n in REQUIRED_STEMS if n not in seen]
    if missing:
        report.problems.append(
            f"缺 stem：{missing}（G4-9 要求鼓/贝斯/和声/보컬 四轨齐全）"
        )
    extra = sorted(n for n in seen if n not in REQUIRED_STEMS)
    if extra:
        report.problems.append(f"出现 G4-9 未定义的轨：{extra}（改名或明确其门位置）")

    samples_by_name = {}
    for ref in items:
        check = StemCheck(name=ref.name, path=ref.path, present=False)
        try:
            info = wavio.read_wav(ref.path, expect_spec=expect)
        except wavio.WavError as exc:
            check.error = f"{type(exc).__name__}: {exc}"
            report.problems.append(f"{ref.name} 不可重开：{check.error}")
        else:
            check.present = True
            check.duration = info["duration"]
            check.bit_depth = info["bit_depth"]
            check.framerate = info["framerate"]
            check.channels = info["channels"]
            check.bytes_ = os.path.getsize(ref.path)
            check.peak = wavio.peak_of(info["samples"])
            samples_by_name[ref.name] = info["samples"]
            if check.bytes_ == 0:
                report.problems.append(f"{ref.name} 是 0 字节空文件：{ref.path}")
        report.checks.append(check)

    # ---- 静音轨：一条 stem 全零不是「安静」，是断线 ----
    silent = [c.name for c in report.checks if c.present and c.peak <= 0]
    if silent:
        report.problems.append(
            f"stem 全静音（峰值 = 0）：{sorted(silent)} —— "
            f"G4-9 要求各 stem 可同播，静音轨意味着导出或接线断了"
        )

    # ---- 时长一致（G4-9 的核心）----
    lengths = {c.name: c.duration for c in report.checks if c.present}
    if len(lengths) >= 2:
        lo, hi = min(lengths.values()), max(lengths.values())
        spread = hi - lo
        if spread > tolerance:
            detail = "、".join(f"{k}={v:.6f}s" for k, v in sorted(lengths.items()))
            report.problems.append(
                f"stem 时长不一致：极差 {spread:.6f}s > 容差 {tolerance}s（{detail}）"
            )

    # ---- 可同播：真做一次逐采样相加 ----
    # 容差内的微小帧数差（如 44100 vs 44102）不构成「不能同播」，按最短长度对齐；
    # 但**极差一旦超过容差，前面已经报过 FAIL**，这里再报一次就是在同一个病上
    # 叠加两条问题，会让报告里最该被看见的那条被淹掉。
    if all(n in samples_by_name for n in REQUIRED_STEMS) and len(lengths) == len(REQUIRED_STEMS):
        lengths_frames = {n: len(samples_by_name[n]) for n in REQUIRED_STEMS}
        spread = (max(lengths_frames.values()) - min(lengths_frames.values())) / 44100.0
        if spread <= tolerance:
            shortest = min(lengths_frames.values())
            tracks = [samples_by_name[n][:shortest] for n in REQUIRED_STEMS]
            mixed, peak = wavio.mix_down(tracks)
            report.mix_peak = peak
            if peak <= 0 and not silent:
                report.problems.append("四轨同播后全静音：素材或接线有问题")

    return report


def mix_to_master(refs, out_path, *, tolerance=TOLERANCE_SECONDS,
                  expect=DEFAULT_STEM_SPEC, headroom=1.0, sampwidth=3,
                  framerate=44100):
    """四轨 → master WAV（G4-9 混音的落盘版）。

    先校验再混：**校验不通过就不产出 master** —— 与其生成一个「时长对不上」的
    母带再让人去猜，不如在源头 `StemError` 报出问题清单。

    返回 `(report, master_info)`；`master_info` 来自 `wavio.read_wav`，
    直接可喂 `manifest.py`。
    """
    report = check_stems(refs, tolerance=tolerance, expect=expect)
    if not report.ok:
        raise StemError(
            "分轨未过 G4-9，拒绝出 master：\n  - " + "\n  - ".join(report.problems)
        )

    tracks = []
    for name in REQUIRED_STEMS:
        path = _path_of(refs, name)
        tracks.append(wavio.read_wav(path)["samples"])

    mixed, peak = wavio.mix_down(tracks, headroom=headroom)
    wavio.write_wav(out_path, mixed, framerate=framerate, sampwidth=sampwidth)
    info = wavio.read_wav(out_path, expect_spec=wavio.Spec(framerate, sampwidth, 1))
    info["peak_before_clip"] = peak
    info["accumulated_peak"] = peak
    return report, info


class StemError(wavio.WavError):
    """分轨组织层面的失败（G4-9 不通过）。"""


def _path_of(refs, name):
    if isinstance(refs, (str, os.PathLike)):
        return stem_paths(refs)[name]
    if isinstance(refs, dict):
        for k, v in refs.items():
            if canonical_name(k) == name:
                return v
    for ref in refs:
        if ref.name == name:
            return ref.path
    raise StemError(f"找不到 stem：{name}")
