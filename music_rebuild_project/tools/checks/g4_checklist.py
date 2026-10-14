#!/usr/bin/env python3
"""g4_checklist.py · G4 最终 QC 检查表（可执行，纯 stdlib）(#61)

把 ``00_docs/gates/gate_G4.md`` 的 G4-1~G4-12 **能纯 stdlib 判定的部分**
逐条跑成 PASS / FAIL / SKIP，输出机器可读报告（JSON 或文本）。

    结论 = 有 FAIL ? FAIL : PASS
    退出码  0 = 无 FAIL（SKIP 不算失败）；1 = 有 FAIL；2 = 用法/读取错误。

## 与既有能力的分工（只 import，不重复实现）

| 依赖 | 复用内容 | 本脚本**不**做的事 |
|---|---|---|
| ``tools/render/manifest.py`` | ``Manifest`` 解析、``sha256_of``、``check_mp3`` | 不重新实现哈希/清单结构 |
| ``tools/render/stems.py`` | ``check_stems`` / ``REQUIRED_STEMS`` / ``TOLERANCE_SECONDS`` | 不重新实现四轨时长与同播逻辑 |
| ``tools/render/wavio.py`` | ``read_wav`` / ``wave_spec`` / ``duration_of`` / ``dbfs`` | 不重新实现 WAV 解码 |
| ``tools/checks/qc_file_check.py`` | 削波/静音**阈值口径**（见下） | 见下方「为什么不直接调 qc_file_check」 |

### 为什么不直接调 ``qc_file_check.py``

``qc_file_check.py``（#60）是**逐文件**的五项 CLI；本脚本（#61）是**整门逐条**的
检查表，输入是 #59 产出的 manifest —— 两者接口不同（单文件 vs 清单驱动）。
削波（G4-3）与静音占比（G4-4）的口径与 #60 保持一致（同一份 dBFS 定义、
同样的逐帧 RMS），但 G4-3 额外收紧到 gate_G4 里写明的「峰值 ≥ -0.5dBFS 视为疑似削波」
（见 ``G4_CLIP_PEAK_DBFS``）。#60 的默认阈值仍是 0dBFS，二者不冲突：
门表要的是「疑似即 FAIL」，不是「顶到 0 才算」。

## 不可纯 stdlib 判定的条目（显式 SKIP，不报错）

G4-1 LUFS / G4-2 dBTP / G4-7 频谱掩蔽 / G4-8 单声道试听 / G4-11 人工试听清单 /
G4-10 中的「真 MP3 位率」—— 需要 EBU R128 滤波器组、心理声学模型或 ffmpeg，
沙箱没有。它们**不会**让结论变 FAIL，但会在报告里留下 ``SKIP(needs ffmpeg)``
与 ``needs`` 字段，指向应补的工具命令。**静默通过才是真的失败。**
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

# --- 复用 #59 已实现的能力（同仓库 import），绝不重写 ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from music_rebuild_project.tools.render import manifest as M  # noqa: E402
from music_rebuild_project.tools.render import stems as S  # noqa: E402
from music_rebuild_project.tools.render import wavio as W  # noqa: E402

VERSION = "1.0.0"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: G4-3「疑似削波」阈值：峰值 ≥ -0.5 dBFS 即视为疑似削波（gate_G4 明文）。
G4_CLIP_PEAK_DBFS = -0.5

#: G4-4 静音占比：整轨 RMS 低于此值视为能量过低。
G4_SILENCE_DBFS = -60.0
#: 静音分析帧长（毫秒），与 #60 ``--silence-frame-ms`` 默认值一致。
G4_SILENCE_FRAME_MS = 50
#: 整轨静音帧占比上限（含首尾淡入淡出，故非 0），与 #60 默认值一致。
G4_SILENCE_MAX_RATIO = 0.05
#: 连续静音达到该秒数才算「异常静音段」，与 #60 默认值一致。
G4_MIN_SILENCE_SEC = 1.0

#: G4-9 时长极差容差 —— 直接引用 stems 的常量，不另立一份。
G4_STEM_TOLERANCE = S.TOLERANCE_SECONDS

#: G4-12 五类交付物 → manifest 里的 role（复用 M.REQUIRED_DELIVERABLES 的键）。
_REQUIRED_ROLES = tuple(M.REQUIRED_DELIVERABLES)

#: 需要外部工具的条目：(gate_id, 说明, 需要的工具)。
_NEEDS_EXTERNAL = {
    "G4-1": ("整体响度 -14 LUFS ±1", "ffmpeg loudnorm / loudgain"),
    "G4-2": ("真峰 ≤ -1 dBTP", "ffmpeg loudnorm / ebur128"),
    "G4-7": ("보컬 2~5kHz 不被掩蔽", "#55 频谱比对工具"),
    "G4-8": ("单声道兼容无抵消", "单声道渲染 + 试听"),
    "G4-11": ("全曲逐段试听清单", "人工勾选（08_qc/qc_trial_list.md）"),
}


# ---------------------------------------------------------------------------
# 报告数据结构
# ---------------------------------------------------------------------------
class Item:
    """G4 检查表里的一条。``state`` ∈ {PASS, FAIL, SKIP}。

    ``evidence`` 是**可复核的数值**（不是「已检查」这种空话）：
    峰值 dBFS、时长极差秒数、位深、缺失项名字…… 报告的价值全在这。
    """

    __slots__ = ("id", "title", "state", "evidence", "needs", "problems")

    def __init__(self, id, title, state, *, evidence=None, needs="", problems=None):
        self.id = id
        self.title = title
        self.state = state
        self.evidence = evidence or {}
        self.needs = needs
        self.problems = list(problems or [])

    def to_dict(self):
        d = {"id": self.id, "title": self.title, "state": self.state}
        if self.evidence:
            d["evidence"] = self.evidence
        if self.needs:
            d["needs"] = self.needs
        if self.problems:
            d["problems"] = list(self.problems)
        return d


class ChecklistReport:
    """G4-1~G4-12 的判定记录。``ok`` 才是唯一通过判据。"""

    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)
        return item

    @property
    def failures(self):
        return [i for i in self.items if i.state == FAIL]

    @property
    def skipped(self):
        return [i for i in self.items if i.state == SKIP]

    @property
    def ok(self):
        return not self.failures

    def to_dict(self):
        return {
            "gate": "G4",
            "script": "g4_checklist.py",
            "version": VERSION,
            "status": PASS if self.ok else FAIL,
            "counts": {
                PASS: sum(1 for i in self.items if i.state == PASS),
                FAIL: len(self.failures),
                SKIP: len(self.skipped),
            },
            "failed": [i.id for i in self.failures],
            "items": [i.to_dict() for i in self.items],
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_text(self):
        """人看的那一版 —— 对齐 gate_G4.md 的表格顺序。"""
        lines = [
            f"# G4 最终 QC 检查表 · v{VERSION}",
            f"# 结论: {PASS if self.ok else FAIL}"
            f"  (PASS {self.to_dict()['counts'][PASS]} / "
            f"FAIL {len(self.failures)} / SKIP {len(self.skipped)})",
            "-" * 74,
        ]
        for it in self.items:
            lines.append(f"[{it.state:4}] {it.id:6} {it.title}")
            if it.evidence:
                lines.append("        evidence: " + json.dumps(it.evidence, ensure_ascii=False))
            for p in it.problems:
                lines.append("        - " + p)
            if it.needs:
                lines.append("        needs: " + it.needs)
        lines.append("-" * 74)
        lines.append("CONCLUSION: " + (PASS if self.ok else FAIL))
        if self.failures:
            lines.append("FAILED: " + ", ".join(i.id for i in self.failures))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# WAV 分析辅助（薄封装在 wavio 之上，阈值口径对齐 #60）
# ---------------------------------------------------------------------------
def _peak_dbfs(path):
    """归一化峰值（dBFS）。空/全零 → -inf（JSON 里写成 None）。"""
    samples = W.read_wav(path)["samples"]
    peak = W.peak_of(samples)
    d = W.dbfs(peak)
    return peak, (None if d == float("-inf") else round(d, 4))


def _root_mean_square(samples):
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))


def _silence_stats(path, *, frame_ms=G4_SILENCE_FRAME_MS, threshold_dbfs=G4_SILENCE_DBFS):
    """逐帧 RMS 静音统计。返回 ``(ratio, longest_sec, total_frames, silent_frames, rms_dbfs)``。

    口径与 #60 ``check_silence`` 一致：帧 RMS(dBFS) < 阈值 即静音帧。
    这里在**内存里**按帧切片（信息已由 read_wav 归一化），不重复解码逻辑。
    """
    info = W.read_wav(path)
    samples = info["samples"]
    rate = info["framerate"] or 1
    nch = info["channels"] or 1
    frame_len = max(1, int(rate * frame_ms / 1000.0) * nch)

    total = silent = run = longest = 0
    for start in range(0, len(samples), frame_len):
        chunk = samples[start:start + frame_len]
        if not chunk:
            continue
        total += 1
        rms = _root_mean_square(chunk)
        d = W.dbfs(rms)
        if d == float("-inf") or d < threshold_dbfs:
            silent += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    ratio = silent / total if total else 0.0
    longest_sec = longest * (frame_ms / 1000.0)
    rms_d = W.dbfs(_root_mean_square(samples))
    return (
        ratio,
        longest_sec,
        total,
        silent,
        (None if rms_d == float("-inf") else round(rms_d, 4)),
    )


# ---------------------------------------------------------------------------
# manifest 读取（复用 M.Manifest 的结构，不重写解析）
# ---------------------------------------------------------------------------
def load_manifest(path):
    """读取 #59 产出的 ``*_manifest.json``（或 .txt 的 JSON 兄弟），返回 dict。

    只接受 JSON —— 文本 manifest 是给人看的，机器可读报告要走 ``.json``。
    缺文件 → OSError；不是 JSON → ValueError（都由 CLI 映射成退出码 2）。
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"manifest 不是 JSON 对象：{path}")
    return data


def _entries_by_role(mdata):
    """``[{role: entry}]`` → ``{role: entry}``（多文件角色会拆成 ``role[0]`` 等）。"""
    out = {}
    for e in mdata.get("deliverables", []):
        role = e.get("role", "")
        out[role] = e
    return out


# ---------------------------------------------------------------------------
# 逐条检查
# ---------------------------------------------------------------------------
def check_g4_1(_ctx):
    return _skip("G4-1")


def check_g4_2(_ctx):
    return _skip("G4-2")


def _skip(gate_id):
    title, needs = _NEEDS_EXTERNAL[gate_id]
    return Item(
        gate_id,
        title,
        SKIP,
        needs=needs,
        problems=[f"SKIP(needs ffmpeg)：纯 stdlib 无法测量，需 {needs}"],
    )


def check_g4_3(ctx, *, clip_peak_dbfs=G4_CLIP_PEAK_DBFS):
    """G4-3 削波计数：master（+各 stem）峰值 ≥ -0.5dBFS 视为疑似削波 → FAIL。

    在**归一化 dBFS 域**判定，与 #60 一致；阈值取 gate_G4 明文的 -0.5dBFS
    （比 #60 默认的 0dBFS 更严，因为门表说的是「疑似」）。
    """
    title = "削波计数（峰值 ≥ %.1fdBFS 视为疑似削波）" % clip_peak_dbfs
    targets = ctx["audio_targets"]()
    if not targets:
        return Item("G4-3", title, SKIP, problems=["SKIP：无可分析的 WAV 交付物"])

    evidence = {}
    problems = []
    for name, path in targets:
        try:
            peak, peak_db = _peak_dbfs(path)
        except W.WavError as exc:
            problems.append(f"{name} 不可读：{type(exc).__name__}: {exc}")
            evidence[name] = {"error": str(exc)}
            continue
        clipped = peak_db is None or peak_db >= clip_peak_dbfs
        evidence[name] = {"peak_dbfs": peak_db, "clipped": clipped}
        if clipped:
            problems.append(
                f"{name} 峰值 {peak_db} dBFS ≥ {clip_peak_dbfs} dBFS（疑似削波）"
            )
    return Item("G4-3", title, FAIL if problems else PASS,
                evidence=evidence, problems=problems)


def check_g4_4(ctx):
    """G4-4 静音占比：有效乐段内异常静音 = 0；整轨能量过低 → FAIL。

    判据与 #60 ``check_silence`` 一致：连续静音段 ≥ 1s 或整轨静音占比 > 5% → FAIL。
    """
    title = "静音占比（异常静音段 = 0，整轨占比 ≤ %.0f%%）" % (G4_SILENCE_MAX_RATIO * 100)
    targets = ctx["audio_targets"]()
    if not targets:
        return Item("G4-4", title, SKIP, problems=["SKIP：无可分析的 WAV 交付物"])

    evidence = {}
    problems = []
    for name, path in targets:
        try:
            ratio, longest, total, silent, rms_db = _silence_stats(path)
        except W.WavError as exc:
            problems.append(f"{name} 不可读：{type(exc).__name__}: {exc}")
            evidence[name] = {"error": str(exc)}
            continue
        evidence[name] = {
            "silence_ratio": round(ratio, 6),
            "longest_silence_sec": round(longest, 4),
            "total_frames": total,
            "silent_frames": silent,
            "rms_dbfs": rms_db,
        }
        if rms_db is None:
            problems.append(f"{name} 整轨 RMS = -inf（全静音，能量过低）")
        elif ratio > G4_SILENCE_MAX_RATIO:
            problems.append(
                f"{name} 静音占比 {ratio * 100:.1f}% > {G4_SILENCE_MAX_RATIO * 100:.0f}%"
            )
        if longest >= G4_MIN_SILENCE_SEC:
            problems.append(f"{name} 最长连续静音 {longest:.3f}s ≥ {G4_MIN_SILENCE_SEC}s")
    return Item("G4-4", title, FAIL if problems else PASS,
                evidence=evidence, problems=problems)


def check_g4_5(ctx, *, expected=None, tol=1.0):
    """G4-5 时长与预期一致：master vs 期望（小节×拍数×BPM）误差 ≤ ±1s。

    ``expected`` 由调用方给出（--duration-expected）；未给出则报时长、判 SKIP
    （「只测量不判定」—— 与 #60 同口径，不假装知道预期值）。
    """
    title = "时长与预期一致（误差 ≤ ±%.1fs）" % tol
    master = ctx["master_path"]()
    if not master:
        return Item("G4-5", title, SKIP, problems=["SKIP：manifest 无 master WAV"])

    try:
        duration = W.duration_of(master)
    except W.WavError as exc:
        return Item("G4-5", title, FAIL,
                    problems=[f"master 不可读：{type(exc).__name__}: {exc}"])

    evidence = {"duration_sec": round(duration, 6), "master": os.path.basename(master)}
    if expected is None:
        evidence["expected_sec"] = None
        return Item("G4-5", title, SKIP, evidence=evidence,
                    problems=[f"SKIP：未提供 --duration-expected，仅测得 {duration:.3f}s"])

    err = abs(duration - expected)
    evidence.update({"expected_sec": expected, "tolerance_sec": tol, "error_sec": round(err, 6)})
    problems = []
    if err > tol:
        problems.append(f"时长误差 {err:.6f}s > 容差 {tol}s")
    return Item("G4-5", title, FAIL if problems else PASS,
                evidence=evidence, problems=problems)


def check_g4_6(_ctx):
    title, needs = "结构可辨（段落密度/动态差）", "段落能量/EQ 对比表 + 人工勾选"
    return Item("G4-6", title, SKIP, needs=needs,
                problems=[f"SKIP(needs ffmpeg)：需 {needs}"])


def check_g4_7(_ctx):
    return _skip("G4-7")


def check_g4_8(_ctx):
    return _skip("G4-8")


def check_g4_9(ctx):
    """G4-9 stem 完整性：四轨齐 + 各自可重开 + 时长极差 ≤ 5ms + 可同播。

    直接调 ``stems.check_stems`` —— 时长/同播逻辑是 #59 的实现，本脚本只报结论。
    无 stem 路径信息时 SKIP（manifest 未声明分轨目录）。
    """
    title = "stem 完整性（四轨齐 + 可重开 + 时长一致 ≤%.0fms）" % (G4_STEM_TOLERANCE * 1000)
    refs = ctx["stem_refs"]()
    if not refs:
        return Item("G4-9", title, SKIP,
                    problems=["SKIP：未提供 --stems-dir，无法核对四轨"])

    try:
        report = S.check_stems(refs)
    except W.WavError as exc:
        return Item("G4-9", title, FAIL,
                    problems=[f"stems 不可读：{type(exc).__name__}: {exc}"])

    durations = report.durations
    spread = None
    if len(durations) >= 2:
        spread = round(max(durations.values()) - min(durations.values()), 6)
    evidence = {
        "stems_present": sorted(durations),
        "duration_spread_sec": spread,
        "tolerance_sec": G4_STEM_TOLERANCE,
        "mix_peak": round(report.mix_peak, 6),
    }
    return Item("G4-9", title, PASS if report.ok else FAIL,
                evidence=evidence, problems=list(report.problems))


def check_g4_10(ctx, *, expect_rate=44100, expect_sampwidth=3):
    """G4-10 输出规格：master 44.1k/24bit WAV；MP3 320kbps 只校验不产。

    - WAV 侧（可判定）：采样率 == 44100 且位深 == 24。
    - MP3 侧：复用 ``manifest.check_mp3``；缺失**不计入** FAIL（它是附加格式），
      但会把 ``unavailable_reason`` 写进证据，位率**不猜**。
    """
    title = "输出规格（master %dHz/%dbit WAV + MP3 320kbps）" % (expect_rate, expect_sampwidth * 8)
    master = ctx["master_path"]()
    problems = []
    evidence = {"expected": {"sample_rate": expect_rate, "bit_depth": expect_sampwidth * 8}}

    if not master:
        problems.append("manifest 无 master WAV，无法核对 G4-10 WAV 规格")
        evidence["wav"] = None
    else:
        try:
            rate, sw, nch = W.wave_spec(master)
        except W.WavError as exc:
            problems.append(f"master 不可读：{type(exc).__name__}: {exc}")
            evidence["wav"] = {"error": str(exc)}
        else:
            evidence["wav"] = {
                "sample_rate": rate,
                "bit_depth": sw * 8,
                "channels": nch,
                "container": "RIFF/WAVE",
            }
            if rate != expect_rate:
                problems.append(f"master 采样率 {rate}Hz ≠ {expect_rate}Hz")
            if sw != expect_sampwidth:
                problems.append(f"master 位深 {sw * 8}bit ≠ {expect_sampwidth * 8}bit")

    # MP3：只校验存在性/命名/文件头；缺失不判 FAIL，但必须留档。
    mp3_path = ctx["mp3_path"]()
    mp3 = M.check_mp3(mp3_path)
    evidence["mp3"] = {
        "present": mp3["present"],
        "path": os.path.basename(mp3["path"]) if mp3["path"] else "",
        "probe": mp3["probe"],
        "available": mp3["available"],
        "expected_spec": mp3["expected_spec"],
        "bitrate_kbps": None,  # stdlib 解不出 MP3 位率 —— 不猜
    }
    if not mp3["available"]:
        evidence["mp3"]["unavailable_reason"] = mp3["unavailable_reason"]

    return Item("G4-10", title, FAIL if problems else PASS,
                evidence=evidence, problems=problems)


def check_g4_11(_ctx):
    return _skip("G4-11")


def check_g4_12(ctx):
    """G4-12 交付物齐全：五类分发物 + 四类记录，缺一即 FAIL。

    直接消费 #59 manifest 的 ``missing_required`` / ``missing_records``
    —— 齐全性判定是 ``manifest.check_deliverables`` 的实现，本脚本只汇总。
    另外校验每条交付物的 ``sha256`` 非空（哈希是「分发件 = 过门件」的唯一凭证）。
    """
    title = "交付物齐全（五类分发物 + 四类记录）"
    mdata = ctx["manifest"]
    if not mdata:
        return Item("G4-12", title, SKIP, problems=["SKIP：未提供 manifest"])

    missing_required = list(mdata.get("missing_required", []))
    missing_records = list(mdata.get("missing_records", []))
    entries = _entries_by_role(mdata)

    problems = []
    if missing_required:
        problems.append("缺交付物：" + "；".join(missing_required))
    if missing_records:
        problems.append("缺记录：" + "；".join(missing_records))

    # 五类交付物每条都要在 entries 里、且 sha256 非空。
    seen_roles = []
    for role in _REQUIRED_ROLES:
        hit = [r for r in entries if r == role or r.startswith(role + "[")]
        if hit:
            seen_roles.extend(hit)
        else:
            # 已在 missing_required 里报过就不重复刷屏，但要保证这条被看见。
            if not any(role in msg for msg in missing_required):
                problems.append(f"缺交付物 role={role}（{M.REQUIRED_DELIVERABLES[role]}）")

    empty_hash = [r for r in seen_roles if not str(entries[r].get("sha256", "")).strip()]
    if empty_hash:
        problems.append("以下交付物 sha256 为空：" + "、".join(sorted(empty_hash)))

    evidence = {
        "deliverable_roles": sorted(seen_roles),
        "required_deliverables": list(_REQUIRED_ROLES),
        "required_records": list(M.REQUIRED_RECORDS),
        "empty_sha256": sorted(empty_hash),
        "manifest_status": mdata.get("status"),
    }
    return Item("G4-12", title, FAIL if problems else PASS,
                evidence=evidence, problems=problems)


#: 检查表顺序 —— 与 gate_G4.md 表格一致。
CHECK_ORDER = [
    ("G4-1", check_g4_1),
    ("G4-2", check_g4_2),
    ("G4-3", check_g4_3),
    ("G4-4", check_g4_4),
    ("G4-5", check_g4_5),
    ("G4-6", check_g4_6),
    ("G4-7", check_g4_7),
    ("G4-8", check_g4_8),
    ("G4-9", check_g4_9),
    ("G4-10", check_g4_10),
    ("G4-11", check_g4_11),
    ("G4-12", check_g4_12),
]


def build_context(*, manifest_path=None, stems_dir=None, mp3_path=None,
                  duration_expected=None):
    """把 CLI 参数收成一个 ctx —— 检查函数都只读 ctx，不直接摸 argv。"""
    mdata = load_manifest(manifest_path) if manifest_path else None
    entries = _entries_by_role(mdata) if mdata else {}

    def master_path():
        e = entries.get("master_wav")
        return e.get("path") if e else None

    def mp3_path_():
        if mp3_path:
            return mp3_path
        e = entries.get("mp3")
        return e.get("path") if e else None

    def stem_refs():
        if stems_dir:
            return stems_dir
        # manifest 里若登记了 stem 角色，就用它；否则交给调用方 --stems-dir。
        stems = {r: entries[r]["path"] for r in entries if r.startswith("stem:")}
        return stems or None

    def audio_targets():
        """G4-3 / G4-4 要查的音频：master + 各 stem（若有）。"""
        out = []
        if master_path():
            out.append(("master", master_path()))
        refs = stem_refs()
        if isinstance(refs, dict):
            out.extend((name, p) for name, p in sorted(refs.items()))
        elif isinstance(refs, str) and os.path.isdir(refs):
            try:
                out.extend(sorted(S.stem_paths(refs).items()))
            except W.WavError:
                pass
        return out

    return {
        "manifest": mdata,
        "manifest_path": manifest_path,
        "master_path": master_path,
        "mp3_path": mp3_path_,
        "stem_refs": stem_refs,
        "audio_targets": audio_targets,
        "duration_expected": duration_expected,
    }


def run_checklist(ctx):
    """按 gate_G4 顺序跑完 12 条，返回 ``ChecklistReport``。"""
    report = ChecklistReport()
    for gate_id, fn in CHECK_ORDER:
        if gate_id == "G4-5":
            report.add(fn(ctx, expected=ctx.get("duration_expected")))
        else:
            report.add(fn(ctx))
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="g4_checklist.py",
        description="G4 最终 QC 检查表（可执行，纯 stdlib）：manifest + stems → PASS/FAIL/SKIP。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--manifest", help="tools/render 产出的 *_manifest.json（G4-12/#59）")
    p.add_argument("--stems-dir", help="分轨 stem 目录（G4-9；不传则从 manifest 推断）")
    p.add_argument("--mp3", help="MP3 交付物路径（G4-10；不传则从 manifest 推断）")
    p.add_argument("--duration-expected", type=float, default=None,
                   help="master 预期时长秒（G4-5；不传则该条 SKIP）")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告（默认文本）")
    p.add_argument("--out", help="把报告写到指定文件（同时仍打印到 stdout）")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ctx = build_context(
            manifest_path=args.manifest,
            stems_dir=args.stems_dir,
            mp3_path=args.mp3,
            duration_expected=args.duration_expected,
        )
    except (OSError, ValueError) as exc:
        print(f"error: 读取失败：{exc}", file=sys.stderr)
        return 2

    report = run_checklist(ctx)
    text = report.to_json() if args.json else report.to_text()
    print(text)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8", newline="\n") as f:
                f.write(report.to_json() + "\n")
        except OSError as exc:
            print(f"error: 写报告失败：{exc}", file=sys.stderr)
            return 2

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
