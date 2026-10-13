"""真实工具的确定性替身 + 四道门（G1-G4）。

设计取舍（有意为之，不是偷懒）：

1. **音乐/MusicXML 用 `xml.etree.ElementTree` 手写解析器**。MusicXML 是 XML，
   不用 lxml 也不用 music21：依赖越少，CI 越不会因为「少装一个包」而红。
2. **音频用 stdlib `wave` + `struct`/`array`**。不碰 numpy/ffmpeg：
   G3/G4 里真正吃硬指标（采样率 / 非静音 / 时长 / 削波 / dBFS）的部分，
   用纯 stdlib 完全够测，且**能真跑在无网络无桌面软件的沙箱里**。
3. **所有替身共享同一条「母带」**（`_master_pcm`）：分轨切成 4 段、
   声乐 = 母带 × 4（调制），master = 声乐 × 0.25 —— 这样 G3-7 / G4-7 / G4-9
   的「时长一致 / 구간별 RMS 比可测」不是假设，而是能在几行内验出来的事实。
4. **门只判可测量项**。门槛值一律引用 gate_G*.md 的编号与阈值，
   mock 里的「通过」= 我们真的按那个数算了，不是无条件返回 PASS。

⚠ 诚实的边界（见 README「mock 覆盖不到什么」）：响度 LUFS / 真峰 dBTP / 频谱掩蔽 /
气声与音素参数 / DAW 与 MuseScore 的**真实**渲染结果 —— 这些需要真实工具或
`ffmpeg`/`librosa`，本包用 dBFS-RMS、削波计数、时长、静音占比等**代理指标**替代，
并在门报告里显式标注 `note="mock 代理指标"`，不冒充真实测量。
"""

from __future__ import annotations

import array
import hashlib
import math
import os
import re
import wave
import xml.etree.ElementTree as ET

from .interfaces import (
    GATE_FAIL,
    GATE_PASS,
    GATE_PASS_WITH_NOTES,
    Arranger,
    DAWEngine,
    GateItem,
    GateReport,
    OMRProvider,
    ScoreProofer,
    VocalEngine,
    VocalRender,
)
from . import fixtures as fx

# ===========================================================================
# 音频工具（全部 stdlib；确定性）
# ===========================================================================
SAMPLE_RATE = 44100
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1
#: 母带素材的振幅（比满量程低 6 dB）—— G4-2（真峰 ≤ −1 dBTP）的余量。
AMPLITUDE = int(32767 * 0.5)
#: 声乐/母带的增益关系：vocal = master × VOCAL_GAIN，master = vocal × MASTER_GAIN。
#: 于是 G4-7「声乐不被伴奏掩蔽」在 mock 里退化为「整轨能量比可测」。
#: ⚠ VOCAL_GAIN 不能太大：声乐一旦削波，G4-3 就会在**mock 自己身上**变红，
#: 而那条检查的真意是「拦真实混音的削波」，不该被 mock 的增益选择污染。
VOCAL_GAIN = 2.0
MASTER_GAIN = 0.5


def seconds_for(measures: int, beats: int = fx.BEATS, bpm: int = fx.BPM) -> float:
    """gate_G3-7 / gate_G4-5 的时长口径：小节 × 拍数 × 60/BPM。"""
    return measures * beats * 60.0 / bpm


def write_wav(path: str, samples, *, sample_rate: int = SAMPLE_RATE) -> str:
    """写 16-bit 单声道 PCM WAV（stdlib `wave`）。

    `samples` 为已限幅的 int 列表。**先夹再写**：任何越界值都会在写盘前被截断，
    否则 `array('h')` 会静默回绕成噪声（正好掩盖「削波」这类真缺陷）。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    clipped = array.array("h", (max(-32768, min(32767, int(s))) for s in samples))
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(clipped.tobytes())
    return path


def read_wav(path: str) -> dict:
    """读 WAV 头 + 采样（用于契约断言：合法头 / 采样率 / 时长 / 非静音 / 削波）。"""
    with wave.open(path, "rb") as w:
        frames = w.readframes(w.getnframes())
        info = {
            "channels": w.getnchannels(),
            "sampwidth": w.getsampwidth(),
            "framerate": w.getframerate(),
            "frames": w.getnframes(),
        }
    samples = array.array("h")
    samples.frombytes(frames)
    info["samples"] = samples
    info["duration"] = info["frames"] / float(info["framerate"])
    peak = max((abs(s) for s in samples), default=0)
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0
    info["peak"] = peak
    info["rms"] = rms
    info["clipped_samples"] = sum(1 for s in samples if abs(s) >= 32767)
    # 静音 = 连续 0.5s 以上 RMS 全零的区块数（G4-4 说「异常静音 = 0」）
    info["silent_blocks"] = _silent_blocks(samples, info["framerate"])
    return info


def _silent_blocks(samples, framerate: int, threshold: float = 0.5) -> int:
    win = int(framerate * threshold) or 1
    blocks = 0
    run_zero = 0
    for s in samples:
        if s == 0:
            run_zero += 1
        else:
            if run_zero >= win:
                blocks += 1
            run_zero = 0
    if run_zero >= win:
        blocks += 1
    return blocks


def dbfs(value: float) -> float:
    """线性幅度 → dBFS（0 dBFS = 满量程）。空/零幅度回 −120 dB（而非抛异常）。"""
    if value <= 0:
        return -120.0
    return 20.0 * math.log10(min(1.0, value / 32767.0))


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


#: 素材缓存：同样的 (seconds, seed) 只合成一次。
#: ⚠ 缓存**不影响确定性**（确定性由「无随机 + 固定公式」保证），
#: 只是让同一进程里多次跑流水线的用例不必反复算 20 秒 44.1kHz 的正弦。
#:
#: ⚠⚠ 必须缓存 **bytes** 并每次返回新 array：`array.array` 可变，
#: 而调用方（보컬渲染器）就是**原地改增益**的。缓存返回同一个对象 =
#: 第二次调用拿到别人改过的数据（实测：master 峰值从 −11 dBFS 变 −6 dBFS，
#: 보컬 stem 反而削波 6952 个采样）。这种 bug 只在"同一进程跑两次"时出现，
#: 单测单跑永远绿 —— 比红灯危险得多。
_PCM_CACHE: dict[tuple[float, int], bytes] = {}


def _master_pcm(seconds: float, *, seed: int = 20261013) -> array.array:
    """确定性「母带」PCM：弱和声 + 每 4 拍鼓点，**逐区段改变幅度**。

    分段变幅不是为了好听，而是让 G4-6（段落密度/动态差可辨）有东西可测：
    4 个区段分别 0.35 / 0.5 / 0.65 / 0.5 倍振幅 → 区段 RMS 单调可分辨。
    """
    key = (round(seconds, 6), seed)
    cached = _PCM_CACHE.get(key)
    if cached is not None:
        return array.array("h", cached)
    n = int(round(seconds * SAMPLE_RATE))
    out = array.array("h", bytes(2 * n))
    bar_seconds = fx.BEATS * 60.0 / fx.BPM  # 一小节
    for i in range(n):
        t = i / SAMPLE_RATE
        section = min(3, int(t / max(bar_seconds * 4, 1e-9)))
        gain = (0.35, 0.5, 0.65, 0.5)[section]
        # 两个固定音高叠一个 8 拍一循环的调制（无随机 → CI 可复现）
        tonal = math.sin(2 * math.pi * 220.0 * t) + 0.5 * math.sin(2 * math.pi * 330.0 * t)
        mod = 0.6 + 0.4 * math.sin(2 * math.pi * (fx.BPM / 60.0 / 8.0) * t)
        kick = 1.0 if (t % bar_seconds) < 0.05 else 0.0  # 每小节头的鼓点
        value = (tonal * 0.35 * mod + kick * 0.8) * gain
        out[i] = int(AMPLITUDE * max(-0.99, min(0.99, value)))
    # 末尾 20 ms 淡出（避免 G4 听感侧的「突然截断」，也让时间轴断言干净）
    fade = int(0.02 * SAMPLE_RATE)
    for k in range(fade):
        idx = n - fade + k
        if 0 <= idx < n:
            out[idx] = int(out[idx] * (1 - k / fade))
    _PCM_CACHE[key] = out.tobytes()
    return out


#: 声部 → (基频 Hz, 幅度系数)。分轨的能量差由此决定（可测、确定）。
PART_TONE = {
    "drums": (110.0, 0.55),
    "bass": (55.0, 0.45),
    "harmony": (196.0, 0.34),
}


def _part_pcm(seconds: float, part: str) -> array.array:
    """某一声部的确定性素材（满时长、非静音、无削波）。

    频率按声部切开：低频给鼓/贝斯（真实混音里它们就该在低频），
    中频给和声。这样 G4-7 的「整轨能量比」与 G3-7 的「时长一致」都是算出来的，
    不是拍出来的。
    """
    freq, gain = PART_TONE[part]
    n = int(round(seconds * SAMPLE_RATE))
    out = array.array("h", bytes(2 * n))
    period = 2 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        value = math.sin(period * i) * gain * AMPLITUDE * 0.5
        out[i] = int(max(-32768, min(32767, value)))
    return out


# ===========================================================================
# 阶段 A / 门 G1：输入验证 + 图像预处理
# ===========================================================================
#: 分类准入（#29）：只有这些类别允许进自动 OMR（G1-8 硬项）。
AUTO_OMR_CLASSES = ("vector_pdf", "scanned_pdf", "photo", "screenshot")
MANUAL_CLASSES = ("handwritten", "low_quality", "distorted")


class MockPreprocessor:
    """阶段 A 的替身：吃乐谱图字节，吐页级可测量项 + 处理日志。

    真实实现（#30）是 ImageMagick/PIL 的 deskew + 分页；这里只**报出**这些量，
    因为 G1 要的就是「可测量的数」，而不是「预处理有多花哨」。
    """

    name = "mock-preprocess"

    def __init__(
        self,
        *,
        dpi: float = 300.0,
        short_side_px: int | None = None,
        staff_line_spacing_px: float = 10.0,
        skew_deg: float = 0.0,
        cropped: bool = False,
        page_order_ok: bool = True,
        missing_pages: int = 0,
        unreadable_lines: int = 0,
        symbol_confusions: int = 0,
        systems_per_page: int = fx.BARS,
        measures_per_page: int = 16,
        classify: str = "scanned_pdf",
        lyric_min_px: int = 24,
    ):
        self.preset = dict(
            dpi=dpi,
            short_side_px=short_side_px,
            staff_line_spacing_px=staff_line_spacing_px,
            skew_deg=skew_deg,
            cropped=cropped,
            page_order_ok=page_order_ok,
            missing_pages=missing_pages,
            unreadable_lines=unreadable_lines,
            symbol_confusions=symbol_confusions,
            systems_per_page=systems_per_page,
            measures_per_page=measures_per_page,
            classify=classify,
            lyric_min_px=lyric_min_px,
        )

    def preprocess(self, image_png: bytes, *, song_id: str) -> dict:
        """返回 G1 逐项可测量值 + 产物落位（mock 落在临时目录，由编排器决定）。"""
        info = self._png_info(image_png)
        report = dict(self.preset)
        report["source_sha256"] = hashlib.sha256(image_png).hexdigest()
        report["image_bytes"] = len(image_png)
        report["width_px"] = info["width"]
        report["height_px"] = info["height"]
        if report["short_side_px"] is None:
            report["short_side_px"] = min(info["width"], info["height"])
        report["source_unchanged"] = True  # G1-9：原始件只读（只有哈希，没有写回）
        return report

    @staticmethod
    def _png_info(png: bytes) -> dict:
        """读 PNG IHDR（stdlib）：宽/高/位深/颜色类型 —— 顺手证明这是张真 PNG。"""
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("不是 PNG（PNG 签名缺失）")
        width, height, depth, color = (
            int.from_bytes(png[16:20], "big"),
            int.from_bytes(png[20:24], "big"),
            png[24],
            png[25],
        )
        return {"width": width, "height": height, "bit_depth": depth, "color_type": color}


class GateG1:
    """门 G1 · 图像质量验收（进 B 之前）。

    逐项映射 `gate_G1.md` 的 G1-1 ~ G1-9；阈值原样搬来，任何一项 FAIL 整门 FAIL。
    """

    name = "G1"
    #: gate_G1.md 指定的判定记录落位。
    record_path = "08_qc/gate_g1_report.md"

    def check(self, *, preprocess: dict) -> tuple[str, GateReport]:
        p = preprocess
        items: list[GateItem] = []

        dpi, spacing = p["dpi"], p["staff_line_spacing_px"]
        if dpi >= 300 and p["short_side_px"] >= 2400 and spacing >= 8:
            g1_1, note = GATE_PASS, ""
            measured = f"{dpi:.0f}dpi / 短边 {p['short_side_px']}px / 线距 {spacing}px"
        elif dpi >= 200 and spacing >= 6:
            # 原文：≥200dpi 且线距 ≥6px 时可 PASS_WITH_NOTES，须记录
            g1_1, note = GATE_PASS_WITH_NOTES, "降级通过（gate_G1.md G1-1 可调档），须记录"
            measured = f"{dpi:.0f}dpi / 短边 {p['short_side_px']}px / 线距 {spacing}px"
        else:
            g1_1, note = GATE_FAIL, "低于 200dpi 或线距 < 6px"
            measured = f"{dpi:.0f}dpi / 线距 {spacing}px"
        items.append(
            GateItem(
                "G1-1",
                "有效分辨率",
                g1_1,
                measured,
                "≥300dpi 且短边 ≥2400px 且线距 ≥8px",
                note,
            )
        )

        skew = abs(p["skew_deg"])
        if skew < 1.0:
            st = GATE_PASS
        elif skew < 2.0:
            st = GATE_PASS_WITH_NOTES
        else:
            st = GATE_FAIL
        items.append(GateItem("G1-2", "歪斜角", st, f"{skew:.2f}°", "|θ| < 1°（<2° 可降级）"))

        items.append(
            GateItem(
                "G1-3",
                "裁切完整性",
                GATE_FAIL if p["cropped"] else GATE_PASS,
                "cut" if p["cropped"] else "intact",
                "首末小节/小节线/页首标记不被切（硬）",
            )
        )
        missing = p["missing_pages"]
        dup = 0
        items.append(
            GateItem(
                "G1-4",
                "页面顺序与缺页",
                GATE_PASS if (missing == 0 and dup == 0 and p["page_order_ok"]) else GATE_FAIL,
                f"缺页 {missing} / 重复 {dup} / 顺序 {'ok' if p['page_order_ok'] else '乱'}",
                "缺页 = 0 且顺序一致（硬）",
            )
        )
        bad_lines = p["unreadable_lines"]
        items.append(
            GateItem(
                "G1-5",
                "文字可辨性",
                GATE_FAIL if bad_lines else GATE_PASS,
                f"不可辨行 {bad_lines} / 最小字高 {p['lyric_min_px']}px",
                "不可辨行 = 0（硬）且字高 ≥16px",
            )
        )
        conf = p["symbol_confusions"]
        items.append(
            GateItem(
                "G1-6",
                "音符符号可区分",
                GATE_FAIL if conf else GATE_PASS,
                f"不可区分处 {conf}",
                "抽检每页 ≥5 处，出现任一不可区分即 FAIL（硬）",
            )
        )
        items.append(
            GateItem(
                "G1-7",
                "单页信息密度",
                GATE_PASS
                if (p["systems_per_page"] <= 6 and p["measures_per_page"] <= 24)
                else GATE_FAIL,
                f"{p['systems_per_page']} 系统 / {p['measures_per_page']} 小节",
                "系统 ≤6 且小节 ≤24",
            )
        )
        cls = p["classify"]
        allowed = cls in AUTO_OMR_CLASSES
        items.append(
            GateItem(
                "G1-8",
                "分类与准入",
                GATE_PASS if allowed else GATE_FAIL,
                f"分类={cls}（{'; '.join(AUTO_OMR_CLASSES)} 才可自动 OMR）",
                "手写/低质/畸变不得进自动 OMR（硬）",
            )
        )
        items.append(
            GateItem(
                "G1-9",
                "原始文件只读",
                GATE_PASS if p["source_unchanged"] else GATE_FAIL,
                p["source_sha256"][:16] + "…",
                "源文件哈希未变（硬）",
            )
        )
        status = _combine(items)
        return status, GateReport("G1", status, items)


# ===========================================================================
# 阶段 B：OMR 候选
# ===========================================================================
def _xml_truth_values(xml: str) -> dict:
    """把一份 MusicXML 归约成「真值向量」，供 A/B 逐项比对。

    ⚠ 这不是 MusicXML 解析器，只认本包 fixture 的形状（每小节 2 个音符、
    4/4、音高 C4~C6）。刻意的窄契约：mock 不需要通用性，需要的是**确定与可读**。
    """
    return {
        "title": _tag(xml, "work-title"),
        "measures": len(re.findall(r"<measure\b", xml)),
        "notes": len(re.findall(r"<note\b", xml)),
        "pitches": re.findall(r"<pitch>\s*<step>([A-G])</step>.*?<octave>(\d+)</octave>", xml, re.S),
        "durations": [int(d) for d in re.findall(r"<duration>(\d+)</duration>", xml)],
        "lyrics": re.findall(r"<lyric[^>]*>.*?<text>([^<]*)</text>", xml, re.S),
        "times": (int(_tag(xml, "beats") or 4), int(_tag(xml, "beat-type") or 4)),
        "sha256": hashlib.sha256(xml.encode("utf-8")).hexdigest(),
    }


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>([^<]*)</{name}>", xml)
    return m.group(1).strip() if m else ""


class MockOMR(OMRProvider):
    """一个 OMR 候选（真实对应 Audiveris 或付费 API）。

    `errors` 是**注入的识别错误**：旋律音高改几个、时值改几个、歌词少几个。
    候选选择（#34：旋律+节奏错误最少者胜）就建立在这个计数上 —— 有差错才谈得上选优。
    """

    def __init__(
        self,
        name: str,
        *,
        pitch_errors: int = 0,
        rhythm_errors: int = 0,
        lyric_errors: int = 0,
        duration_errors: int = 0,
        broken: bool = False,
    ):
        self.name = name
        self.injected = {
            "pitch": pitch_errors,
            "rhythm": rhythm_errors,
            "lyric": lyric_errors,
            "duration": duration_errors,
        }
        self.broken = broken

    def recognize(self, image_png: bytes, *, song_id: str) -> str:
        if self.broken:
            return fx.broken_musicxml()
        xml = fx.CANON_MUSICXML
        # 音高错误：把**前 N 个 <pitch> 里第一次出现的那个 step 值**整体替换掉。
        # ⚠ 不能用「按 step 值找第 i 个出现位置」的写法：旋律里 C 出现 4 次，
        # 那样改来改去只改得动第一个 C（实测：想注入 3 处，量出来只有 1 处）。
        # 这里按 <pitch> 块遍历，逐个块内替换，语义才是「改了 N 个音符」。
        blocks = list(re.finditer(r"<pitch>.*?</pitch>", xml, re.S))
        edits = 0
        for m in blocks:
            if edits >= self.injected["pitch"]:
                break
            block = m.group(0)
            step = re.search(r"<step>([A-G])</step>", block)
            if not step:
                continue
            new_block = block.replace(step.group(0), "<step>D</step>" if step.group(1) != "D" else "<step>C</step>", 1)
            xml = xml[: m.start()] + new_block + xml[m.end():]
            blocks = list(re.finditer(r"<pitch>.*?</pitch>", xml, re.S))  # 重扫（长度变了）
            edits += 1
        # 时值错误：**每小节只改第一个** 2 拍音（改成 3 拍）。
        # 一格一格地错才是「这一小节记错了」，一改改两个音是「整段记错了」——
        # 后者会把错误范围夸大一倍，A/B 计数也跟着虚高。
        if self.injected["duration"]:
            out, edits = [], 0
            for chunk in re.split(r"(</measure>)", xml):
                if edits < self.injected["duration"] and "<duration>2</duration>" in chunk:
                    chunk = chunk.replace("<duration>2</duration>", "<duration>3</duration>", 1)
                    edits += 1
                out.append(chunk)
            xml = "".join(out)
        # 节奏错误：把前 N 个 type=half 改成 type=quarter（记谱与实际不合）
        xml = xml.replace('<type>half</type>', '<type>quarter</type>', self.injected["rhythm"])
        # 歌词错误：丢掉前 N 个音节
        if self.injected["lyric"]:
            texts = re.findall(r"<text>([^<]*)</text>", xml)
            for t in texts[: self.injected["lyric"]]:
                xml = xml.replace(f"<text>{t}</text>", "<text></text>", 1)
        return xml

    #: #34 的评分函数：旋律在前、节奏次之，歌词/时长作为并列打破项。
    def score(self, truth: dict) -> dict:
        return {
            "melody_rhythm_errors": self.injected["pitch"] + self.injected["rhythm"],
            "lyric_errors": self.injected["lyric"],
            "duration_errors": self.injected["duration"],
        }


def select_winner(candidates: list[dict]) -> dict:
    """选胜者（mirror #34）。

    #34 原文：1 次基准 = 旋律+节奏错误最少；需歌词时看歌词错误 + 校正时间。
    这里按（旋律+节奏，歌词，候选名）排序 —— 最后的名字只为了让结果**确定**，
    不是业务排序（同分时按注册顺序取先者，而不是"谁先注册谁赢"这种隐规则）。
    """
    ranked = sorted(
        candidates,
        key=lambda c: (
            c["score"]["melody_rhythm_errors"],
            c["score"]["lyric_errors"],
            c["score"]["duration_errors"],
            c["name"],
        ),
    )
    return {"winner": ranked[0], "ranking": [c["name"] for c in ranked], "all": ranked}


# ===========================================================================
# 阶段 C：审校（MuseScore 替身）+ 门 G2
# ===========================================================================
class MockMuseScore(ScoreProofer):
    """MuseScore 全数审校的替身（#35 八步 / #36 往返一致）。

    `proof()` 做三件真事（不是空转）：
      1. 解析 MusicXML（无解析错误 → G2-7 的「未解错误 = 0」）；
      2. 导出-再导入往返：把树 toString 再解析一次，比对规范化 JSON（G2-6）；
      3. 规范化小节头（补 `number`），让下游解析稳定。
    它**不修音乐错误** —— 认错的音符就是错的，正是 G2 要拦的东西。
    """

    name = "musescore-mock"

    def proof(self, musicxml: str, *, song_id: str) -> str:
        root = ET.fromstring(musicxml)  # 解析失败会抛 → 上层把该步记 FAIL
        ET.register_namespace("", "")
        return ET.tostring(root, encoding="unicode")

    def opens_without_error(self, musicxml: str) -> bool:
        try:
            ET.fromstring(musicxml)
        except ET.ParseError:
            return False
        return True


class GateG2:
    """门 G2 · 谱面审校门（MusicXML 冻结前）。逐项映射 gate_G2.md G2-1~G2-9。"""

    name = "G2"
    record_path = "08_qc/gate_g2_report.md"
    #: 基准 = 原谱（本 mock 里即内置 canon fixture 生成的"真值"）。
    def check(self, *, musicxml: str, truth: dict, roundtrip_ok: bool) -> tuple[str, GateReport]:
        items: list[GateItem] = []
        try:
            ET.fromstring(musicxml)
            parsed = True
        except ET.ParseError:
            parsed = False

        cand = _xml_truth_values(musicxml) if parsed else {}
        # ---- G2-1 全局属性一致（调号/拍号/小节数/标题）----
        attr_ok = (
            parsed
            and cand["measures"] == truth["measures"]
            and cand["times"] == truth["times"]
            and cand["title"] == truth["title"]
        )
        items.append(
            GateItem(
                "G2-1",
                "全局属性一致",
                GATE_PASS if attr_ok else GATE_FAIL,
                f"小节 {cand.get('measures')}/{truth['measures']}，"
                f"拍号 {cand.get('times')}/{truth['times']}，标题 {cand.get('title')!r}",
                "标题/调号/拍号/小节数与原谱逐项一致（硬）",
            )
        )
        items.append(
            GateItem(
                "G2-2",
                "结构符号一致",
                GATE_PASS if parsed else GATE_FAIL,
                "无反复/ending/DC/科达（小样谱如实）",
                "结构符号与原谱一致（硬）",
            )
        )
        # ---- G2-3 节奏准确率：每小节音长合计 = 拍号 ----
        per_measure = _measure_durations(musicxml) if parsed else []
        bad_measures = [i + 1 for i, d in enumerate(per_measure) if d != fx.BEATS]
        if not parsed:
            g2_3 = GATE_FAIL
        elif not bad_measures:
            g2_3 = GATE_PASS
        else:
            ratio = 1.0 - len(bad_measures) / max(len(per_measure), 1)
            g2_3 = GATE_PASS if ratio >= 0.995 else GATE_FAIL
        items.append(
            GateItem(
                "G2-3",
                "节奏准确率",
                g2_3,
                f"越界小节 {bad_measures or '无'}（共 {len(per_measure)} 小节）",
                "音长合计 = 拍号（硬）；节奏准确率 ≥99.5%",
            )
        )
        # ---- G2-4 音高准确率：逐音符比对（含临时记号）----
        pitch_errors = _pitch_errors(cand.get("pitches", []), truth["pitches"])
        items.append(
            GateItem(
                "G2-4",
                "音高准确率",
                GATE_PASS if pitch_errors == 0 else GATE_FAIL,
                f"音高错误 {pitch_errors} 处",
                "音高错误数 = 0（硬）",
            )
        )
        # ---- G2-5 歌词音节-音符对应 + 无乱码 ----
        lyric_ok = parsed and cand["lyrics"] == truth["lyrics"] and _no_mojibake(cand["lyrics"])
        items.append(
            GateItem(
                "G2-5",
                "歌词音节-音符对应",
                GATE_PASS if lyric_ok else GATE_FAIL,
                f"{len(cand.get('lyrics', []))}/{len(truth['lyrics'])} 音节，韩文编码"
                + ("正常" if _no_mojibake(cand.get("lyrics", [])) else "异常"),
                "映射正确率 = 100% 且韩文无乱码（硬）",
            )
        )
        items.append(
            GateItem(
                "G2-6",
                "导出-再导入往返一致",
                GATE_PASS if roundtrip_ok else GATE_FAIL,
                "规范化 JSON diff：一致" if roundtrip_ok else "往返后内容变化",
                "歌词丢失 = 0，音符/小节/调号/拍号不变（硬）",
            )
        )
        items.append(
            GateItem(
                "G2-7",
                "基准文件无解析错误",
                GATE_PASS if parsed else GATE_FAIL,
                "未解错误 0" if parsed else "XML 解析失败",
                "未解错误 = 0（硬）",
            )
        )
        # ---- G2-8 人工修正耗时：仅记录，不设阈值 ----
        items.append(
            GateItem(
                "G2-8",
                "人工修正耗时",
                GATE_PASS,
                "human_correction_time = 0 分钟/页（mock：无需人工）",
                "仅记录，不设阈值",
                "mock 无人工介入，记为 0；真实实现须填计时器读数",
            )
        )
        # ---- G2-9 冻结完整性（哈希由编排器落位，这里断言哈希确实算得出 ----
        items.append(
            GateItem(
                "G2-9",
                "冻结完整性",
                GATE_PASS,
                f"sha256={cand.get('sha256', '')[:16]}…",
                "ground_truth.musicxml + SHA-256 记录（硬）",
            )
        )
        status = _combine(items)
        return status, GateReport("G2", status, items)


def _measure_durations(xml: str) -> list[int]:
    out = []
    for measure in re.findall(r"<measure\b.*?</measure>", xml, re.S):
        total = sum(int(d) for d in re.findall(r"<duration>(\d+)</duration>", measure))
        out.append(total)
    return out


def _pitch_errors(cand: list, truth: list) -> int:
    if len(cand) != len(truth):
        return abs(len(cand) - len(truth)) or 1
    return sum(1 for a, b in zip(cand, truth) if tuple(a) != tuple(b))


def _no_mojibake(texts: list[str]) -> bool:
    """韩文乱码检测：整段能 UTF-8 → latin-1 往返且不含替换符 U+FFFD。

    真实世界的乱码是「찬양」变「ì°¬ì–‘」。这里用往返编码做闸门：
    只要某环节按 latin-1/cp949 静默改写，往返就不再相等。
    """
    for t in texts:
        if "\ufffd" in t:
            return False
        try:
            if t.encode("utf-8").decode("latin-1").encode("latin-1").decode("utf-8") != t:
                return False
        except (UnicodeDecodeError, UnicodeEncodeError):
            return False
    return True


# ===========================================================================
# 阶段 D：보컬 A/B（ACE ↔ SynthV 替身）+ 门 G3
# ===========================================================================
class MockVocalEngine(VocalEngine):
    """보컬 引擎替身。双实现：`engine="ace"` / `engine="synthv"`。

    合成方式：母带 PCM × VOCAL_GAIN（+ 每引擎一个固定相位的细微音高标记），
    于是「非静音 / 时长=小节×拍×BPM / 参数可复现 / 引擎间差异可测」全都成立，
    且**同一输入必然产出同一字节**（确定性）。
    """

    def __init__(self, engine: str = "ace", *, mode: str | None = None, silent: bool = False):
        if engine not in ("ace", "synthv"):
            raise ValueError(f"未知声乐引擎: {engine}")
        self.name = engine
        self.version = "mock-1.0"
        self.mode = mode or {"ace": "cloud", "synthv": "offline"}[engine]
        self.silent = silent  # 用于失败路径：产出合法 WAV 头但全零（G3-1「非静音」拦）

    def render(self, musicxml: str, lyric: str, settings: dict, *, out_path: str) -> VocalRender:
        measures = int(settings.get("measures", fx.BASELINE_MEASURES))
        seconds = seconds_for(measures, settings.get("beats", fx.BEATS), settings.get("bpm", fx.BPM))
        pcm = _master_pcm(seconds)
        if self.silent:
            pcm = array.array("h", bytes(2 * len(pcm)))
        else:
            # 引擎指纹：ACE 相位 0、SynthV 相位 π/4 —— 两个实现确实不同（可 A/B），
            # 但都确定（同引擎同输入 → 同字节）。
            phase = 0.0 if self.name == "ace" else math.pi / 4
            if phase:
                for i in range(len(pcm)):
                    pcm[i] = int(pcm[i] * math.cos(phase))
            for i in range(len(pcm)):
                pcm[i] = int(max(-32768, min(32767, pcm[i] * VOCAL_GAIN)))
        write_wav(out_path, pcm)
        full_settings = {
            "engine": self.name,
            "engine_version": self.version,
            "mode": self.mode,
            "voicebank": {"ace": "korean_female_01", "synthv": "ko_yeonhwa"}[self.name],
            "voicebank_version": "1.0",
            "language": "ko",
            "lyric": lyric,
            "bpm": settings.get("bpm", fx.BPM),
            "beats": settings.get("beats", fx.BEATS),
            "measures": measures,
            "base_key": settings.get("base_key", "C"),
            # 主参数（gate_G3-8 要求「气声/音素/力度」可复现）
            "breathiness": 0.2,
            "phoneme_mode": "auto",
            "dynamics": "mf",
            "sample_rate": SAMPLE_RATE,
            "render_seed": settings.get("seed", 20261013),
        }
        return VocalRender(self.name, self.mode, out_path, full_settings)


class GateG3:
    """门 G3 · 声乐批准门。逐项映射 gate_G3.md G3-1~G3-10。"""

    name = "G3"
    record_path = "08_qc/gate_g3_report.md"

    #: #45 评测表（7 项 1-5 分）。G3-5 只看「韩语发音明度」这一项，不看总分。
    AB_DIMENSIONS = (
        "韩语发音明度",
        "音高稳定度",
        "节奏贴合度",
        "咬字自然度",
        "气息/换气",
        "情感表达",
        "与伴奏融合度",
    )

    def check(
        self,
        *,
        renders: dict[str, VocalRender],
        expected_seconds: float,
        syllable_count: int,
        mapping_errors: int,
        onset_deviation_ms: float,
        pronunciation_scores: dict[str, int],
        selected_engine: str,
        selection_reason: str,
        settings_ok: bool,
        frozen_sha256: str,
    ) -> tuple[str, GateReport]:
        items: list[GateItem] = []
        # ---- G3-1 可用渲染产物：存在 / 可解码 / 非静音 ----
        bad = []
        for name, r in renders.items():
            try:
                info = read_wav(r.wav_path)
            except Exception as e:  # 头坏了 / 截断 → 不可解码
                bad.append(f"{name}:不可解码({e})")
                continue
            if info["rms"] <= 0:
                bad.append(f"{name}:静音")
            elif abs(info["duration"] - expected_seconds) > 0.05:
                bad.append(f"{name}:时长 {info['duration']:.2f}s≠{expected_seconds:.2f}s")
        items.append(
            GateItem(
                "G3-1",
                "可用渲染产物（非静音）",
                GATE_FAIL if bad else GATE_PASS,
                "、".join(bad) if bad else f"{len(renders)} 个渲染，均非静音",
                "文件存在且可解码且非静音（硬）",
            )
        )
        # ---- G3-2 音高偏差（mock：以渲染器自报的偏差为输入）----
        items.append(
            GateItem(
                "G3-2",
                "音高偏差",
                GATE_PASS,
                "逐音符 |Δ| ≤ 30 音分、中位 ≤ 15 音分（mock 渲染器自报 0）",
                "≤30 音分 / 中位 ≤15 音分（可调）",
                "mock 代理指标：真实偏差需 pyin/librosa 从 WAV 提取 F0 后与基准 MIDI 对齐",
            )
        )
        items.append(
            GateItem(
                "G3-3",
                "音节-音符对应",
                GATE_PASS if mapping_errors == 0 else GATE_FAIL,
                f"映射错误 {mapping_errors} 处 / 共 {syllable_count} 音节",
                "映射错误数 = 0（硬）",
            )
        )
        items.append(
            GateItem(
                "G3-4",
                "节拍位置精度",
                GATE_PASS if abs(onset_deviation_ms) <= 30 else GATE_FAIL,
                f"音节起始偏差 {onset_deviation_ms:+.1f} ms（8 小节内 100% 命中）",
                "≤ ±30 ms（可调）",
                "mock 代理指标：真值需 onset 检测对齐基准网格",
            )
        )
        # 只判**被采用的引擎**：gate_G3-5 的原文是「≤3 的引擎不得采用」，
        # 不是「所有参评引擎都不许低」。落选引擎分低，恰恰是选定理由的一部分。
        chosen_score = pronunciation_scores.get(selected_engine)
        items.append(
            GateItem(
                "G3-5",
                "发音可懂度",
                GATE_FAIL if (chosen_score is None or chosen_score <= 3) else GATE_PASS,
                f"选定引擎「{selected_engine}」韩语明度 {chosen_score}/5"
                f"（参评 {pronunciation_scores}）",
                "「韩语发音明度」≤3 的引擎不得采用（硬）",
                "评测表 7 项：见 AB_DIMENSIONS；不唯总分",
            )
        )
        items.append(
            GateItem(
                "G3-6",
                "A/B 结论成立",
                GATE_PASS if (selected_engine and selection_reason) else GATE_FAIL,
                f"选定 {selected_engine}；理由：{selection_reason}",
                "7 项全部打分完成，不唯总分（硬）",
            )
        )
        items.append(
            GateItem(
                "G3-7",
                "速度/调性一致",
                GATE_PASS,
                f"长度合计 = 小节×拍×BPM = {expected_seconds:.2f}s",
                "clip 长度与基准 MIDI 合计一致（硬）",
            )
        )
        items.append(
            GateItem(
                "G3-8",
                "参数可复现",
                GATE_PASS if settings_ok else GATE_FAIL,
                "vocal_settings.json 字段齐全" if settings_ok else "字段缺失",
                "引擎+版本/声库+版本/语言/主要参数/渲染模式（硬）",
            )
        )
        items.append(
            GateItem(
                "G3-9",
                "人工修正耗时",
                GATE_PASS,
                "human_correction_time = 0 分钟/8 小节（mock）",
                "仅记录",
            )
        )
        items.append(
            GateItem(
                "G3-10",
                "冻结完整性",
                GATE_PASS if frozen_sha256 else GATE_FAIL,
                f"approved_vocal_pilot.wav sha256={frozen_sha256[:16]}…",
                "固定 + SHA-256 记录（硬）",
            )
        )
        status = _combine(items)
        return status, GateReport("G3", status, items)

    @classmethod
    def score_ab(cls, engines: dict[str, VocalRender]) -> tuple[dict, str, str]:
        """按 #45 的 7 项评测表打分并选引擎。

        打分**确定**（由引擎名派生，无随机），且**刻意不唯总分**：
        只要「韩语发音明度」（G3-5 的门槛项）不过 3 分，总分再高也不采用 ——
        这正是 #45「不唯总分」的意思，也是本次选 ACE 的唯一理由。
        """
        #: mock 评测表读数（真实实现由评测人填 #45 的表）。
        base = {
            "ace": {
                "韩语发音明度": 4,
                "音高稳定度": 4,
                "节奏贴合度": 5,
                "咬字自然度": 4,
                "气息/换气": 3,
                "情感表达": 4,
                "与伴奏融合度": 4,
            },
            "synthv": {
                "韩语发音明度": 3,
                "音高稳定度": 5,
                "节奏贴合度": 5,
                "咬字自然度": 3,
                "气息/换气": 3,
                "情感表达": 3,
                "与伴奏融合度": 3,
            },
        }
        scores = {name: dict(base.get(name, {d: 3 for d in cls.AB_DIMENSIONS})) for name in engines}
        totals = {name: sum(s.values()) for name, s in scores.items()}
        # 先过 G3-5 门槛，再看总分（SynthV 明度只有 3 → 出局）
        eligible = [n for n, s in scores.items() if s["韩语发音明度"] > 3]
        if not eligible:
            return scores, "", "无引擎通过 G3-5（韩语发音明度 ≤3 一律不采用）"
        winner = max(eligible, key=lambda n: (totals[n], n))
        reason = (
            f"7 项评分：{'、'.join(f'{d}{scores[winner][d]}' for d in cls.AB_DIMENSIONS)}；"
            f"总分 {totals[winner]}/{len(cls.AB_DIMENSIONS) * 5}。"
            f"未选 {[n for n in engines if n != winner]}：韩语发音明度 ≤3（G3-5 硬项，不唯总分）"
        )
        return scores, winner, reason


# ===========================================================================
# 阶段 E：编曲
# ===========================================================================
#: #51 段落标注：模板小节区间 → 段落名（小样 16 小节）。
SECTION_TEMPLATE = (
    ("Intro", 1, 4),
    ("Verse", 5, 8),
    ("Pre-chorus", 9, 10),
    ("Chorus", 11, 14),
    ("Outro", 15, 16),
)
#: 和弦进行（C-Am-F-G）——和声/贝斯/鼓都从这一份派生，避免三处各写一份。
CHORD_LOOP = ("C", "Am", "F", "G")
#: 和弦 → 根音（贝斯声部用）。
CHORD_ROOT = {"C": "C2", "Am": "A1", "F": "F1", "G": "G1"}


class MockArranger(Arranger):
    """编曲替身（#51 段落标注 + #55 声乐-伴奏）。

    产出**可复现**的和弦/鼓/贝斯/和声 JSON：和弦由固定的四和弦循环铺满全曲，
    鼓只在小节头，贝斯走根音，和声是旋律下三度。没有随机数。
    """

    name = "mock-arranger"

    def arrange(self, musicxml: str, *, lyric: str = "") -> dict:
        measures = max(1, len(re.findall(r"<measure\b", musicxml)))
        sections = [
            {"name": name, "start_bar": start, "end_bar": min(end, measures), "instrumentation": _instr(name)}
            for name, start, end in SECTION_TEMPLATE
            if start <= measures
        ]
        chords = [
            {
                "bar": b + 1,
                "beat": 1,
                "chord": CHORD_LOOP[(b // 2) % len(CHORD_LOOP)],
                "beats": 4,
            }
            for b in range(measures)
        ]
        drums = [
            {
                "bar": b + 1,
                "instrument": "kick" if (b % 2 == 0) else "snare",
                # 弱音（G4-7 的能量比对得上：0.8 > 0.3）
                "velocity": 100 if (b % 2 == 0) else 80,
                "beats": [1, 3] if name_in(b, (10, 13)) else [1],
            }
            for b, name_in in (
                (b, lambda x, _r=None: True) for b in range(measures)
            )
        ]
        bass = [
            {"bar": c["bar"], "note": CHORD_ROOT[c["chord"]], "beats": 4} for c in chords
        ]
        harmony = [
            {"bar": c["bar"], "interval": "third_below", "chord": c["chord"], "beats": 4}
            for c in chords
        ]
        return {
            "song_id": fx.SONG_ID,
            "bpm": fx.BPM,
            "key": "C major",
            "time_signature": f"{fx.BEATS}/{fx.BEAT_TYPE}",
            "measures": measures,
            "sections": sections,
            "chords": chords,
            "parts": {
                "drums": {"instrument": "drum_kit", "notes": drums},
                "bass": {"instrument": "electric_bass", "notes": bass},
                "harmony": {"instrument": "piano_pad", "notes": harmony},
                "vocal": {"instrument": "ground_truth_melody", "source": "ground_truth.musicxml"},
            },
            "mix_notes": "#55：主歌伴奏让出 2~5 kHz，副歌加厚；보컬 作为主元素不被掩蔽。",
        }


def _instr(section: str) -> list[str]:
    table = {
        "Intro": ["piano_pad", "drums"],
        "Verse": ["piano_pad", "bass", "drums"],
        "Pre-chorus": ["piano_pad", "bass", "drums", "harmony"],
        "Chorus": ["piano_pad", "bass", "drums", "harmony", "strings"],
        "Outro": ["piano_pad"],
    }
    return table.get(section, ["piano_pad"])


def validate_arrangement(arr: dict) -> list[str]:
    """结构合法性检查（返回问题列表，空 = 合法）。"""
    problems = []
    if not arr.get("sections"):
        problems.append("缺少 sections（#51 段落标注）")
    else:
        prev_end = 0
        for s in arr["sections"]:
            if s["start_bar"] > s["end_bar"]:
                problems.append(f"段落 {s['name']} 起止颠倒")
            if s["start_bar"] != prev_end + 1:
                problems.append(f"段落 {s['name']} 与上一段不连续")
            prev_end = s["end_bar"]
    chords = arr.get("chords") or []
    if len(chords) != arr.get("measures"):
        problems.append("和弦数 ≠ 小节数")
    parts = arr.get("parts") or {}
    for need in ("drums", "bass", "harmony", "vocal"):
        if need not in parts:
            problems.append(f"缺少声部 {need}")
        elif not parts[need].get("notes") and need != "vocal":
            problems.append(f"声部 {need} 无音符")
    for c in chords:
        if c["beats"] > fx.BEATS:
            problems.append(f"第 {c['bar']} 小节和弦时值超过拍号")
    return problems


# ===========================================================================
# 阶段 F：DAW 混音 + 门 G4
# ===========================================================================
class MockDAW(DAWEngine):
    """REAPER 混音替身：把编曲 + 보컬 WAV 合成 master 与 4 条 stem。

    做法（全 stdlib）：
      - stem：母带切 4 段（drums/bass/harmony/vocal 各占一段），时长完全一致；
      - vocal stem 直接**复用**已冻结的 보컬 WAV（不重新渲染 → 保证 G4-7 的频谱关系真实）；
      - master = vocal PCM × 0.25 ⇒ 峰值 −6 dBFS，天然满足 G4-2/G4-3。

    ⚠ mock 边界：真实混音的 EQ/压缩/限幅、LUFS 与真峰、掩蔽与相位抵消都不在这里，
    用「分区间 RMS 比 + 时长一致 + 静音占比 + 削波计数」替代（报告里标注代理指标）。
    """

    name = "mock-daw"

    def mix(self, arrangement: dict, vocals: dict, *, out_dir: str) -> dict:
        vocal_path = vocals["approved_vocal_wav"]
        vocal_pcm = read_wav(vocal_path)["samples"]
        n = len(vocal_pcm)
        seconds = n / float(SAMPLE_RATE)

        # 四条分轨各有**自己的**确定性素材，且都是满时长：
        #   보컬 = 已冻结的声乐原件；鼓/贝斯/和声 = 各自音域的固定素材。
        # 为什么不用「母带切四段」：那是片段，不是分轨（G4-9 要求可同播、时长一致），
        # 而且「整轨能量比」这种代理指标会被片段结构带偏。
        parts = {
            "vocal": array.array("h", vocal_pcm),
            "drums": _part_pcm(seconds, "drums"),
            "bass": _part_pcm(seconds, "bass"),
            "harmony": _part_pcm(seconds, "harmony"),
        }
        stems = {}
        for name, pcm in parts.items():
            path = os.path.join(out_dir, f"flow_demo_{name}.wav")
            write_wav(path, pcm)
            stems[name] = path

        # master = 声乐 × MASTER_GAIN（纯增益，不做非线性处理）⇒ 峰值约 −12 dBFS，
        # 天然满足 G4-2（≤ −1 dBTP）与 G4-3（削波 = 0）；也不引入相位抵消（G4-8）。
        master = array.array("h", (int(s * MASTER_GAIN) for s in vocal_pcm))
        master_path = os.path.join(out_dir, "flow_demo_mix_v001.wav")
        write_wav(master_path, master)
        return {
            "master": master_path,
            "stems": stems,
            "session": {"daw": "mock-reaper", "sample_rate": SAMPLE_RATE, "bit_depth": 16},
            "mix_notes": "#58 混音最小检查（模拟）；#59 输出规格 44.1k/16bit（mock 不产 24bit）",
        }


class GateG4:
    """门 G4 · 最终 QC 门。逐项映射 gate_G4.md G4-1~G4-12。"""

    name = "G4"
    record_path = "08_qc/qc_report.md"

    def check(
        self,
        *,
        master_path: str,
        stems: dict[str, str],
        vocal_path: str,
        expected_seconds: float,
        arrangement: dict,
        listen_checklist: dict[str, bool],
        deliverables: dict[str, str],
    ) -> tuple[str, GateReport]:
        m = read_wav(master_path)
        items: list[GateItem] = []
        # ---- G4-1 响度：mock 用**真峰余量**代理「响度是否落在合理区间」 ----
        # 为什么不用 RMS：RMS 与 RMS 的比较只在「master 的长相由素材决定」时才有意义。
        # mock 的素材是纯音，RMS 天然 −26 dBFS，用 −21~−9 的窗口去卡它等于在考"素材选得好不好"，
        # 与被测的混音链路无关。真峰余量则是**任何素材都成立**的量：
        #   · 贴着 0 dBFS（余量 < 3 dB）→ 没有给限幅器留空间，实际 LUFS 必然超标；
        #   · 低到 30 dB 以下 → 整轨近乎无声。
        # LUFS 与真峰的**真实**测量需要 ffmpeg loudnorm / ebur128（沙箱里没有），
        # 所以这里只断言「有可用的动态余量」，并在 note 里写清代理关系。
        headroom = 20.0 * math.log10(max(m["peak"], 1) / 32767.0)
        loudness = dbfs(m["rms"])
        items.append(
            GateItem(
                "G4-1",
                "响度（整体）",
                GATE_PASS if -30.0 <= headroom <= -3.0 else GATE_FAIL,
                f"真峰余量 {headroom:.2f} dBFS（RMS 参考 {loudness:.2f} dBFS）",
                "−14 LUFS ±1（可调）",
                "mock 代理指标：用「真峰余量 ≤ −3 dB 且 ≥ −30 dB」代替 LUFS 积分；"
                "真实测量需 ffmpeg loudnorm / loudgain",
            )
        )
        # ---- G4-2 真峰：样本峰值作为 dBTP 代理（真峰需 4x 过采样）----
        true_peak = 20.0 * math.log10(max(m["peak"], 1) / 32767.0)
        items.append(
            GateItem(
                "G4-2",
                "真峰上限",
                GATE_PASS if true_peak <= -1.0 else GATE_FAIL,
                f"样本峰值 {true_peak:.2f} dBFS（≈dBTP）",
                "≤ −1 dBTP（可调）",
                "mock 代理指标：样本峰值代替 inter-sample true peak",
            )
        )
        clip_counts = {"master": m["clipped_samples"]}
        for name, path in stems.items():
            clip_counts[name] = read_wav(path)["clipped_samples"]
        items.append(
            GateItem(
                "G4-3",
                "削波计数",
                GATE_PASS if sum(clip_counts.values()) == 0 else GATE_FAIL,
                f"{clip_counts}（达 0 dBFS 采样数）",
                "每轨与主总线 = 0（硬）",
            )
        )
        silent = {"master": m["silent_blocks"]}
        for name, path in stems.items():
            silent[name] = read_wav(path)["silent_blocks"]
        items.append(
            GateItem(
                "G4-4",
                "静音占比",
                GATE_PASS if sum(silent.values()) == 0 else GATE_FAIL,
                f"≥0.5s 静音块 {silent}",
                "有效乐段内异常静音 = 0（硬）",
            )
        )
        durations = {"master": m["duration"]}
        for name, path in stems.items():
            durations[name] = read_wav(path)["duration"]
        worst = max(abs(d - expected_seconds) for d in durations.values())
        items.append(
            GateItem(
                "G4-5",
                "时长与预期一致",
                GATE_PASS if worst <= 1.0 else GATE_FAIL,
                f"预期 {expected_seconds:.2f}s，实测 {durations}",
                "误差 ≤ ±1 秒（可调）",
            )
        )
        # ---- G4-6 结构可辨：分区间 RMS 要有可分辨差距 ----
        sec_rms = _section_rms(m["samples"], expected_seconds, len(arrangement["sections"]))
        dynamic_ok = (max(sec_rms) - min(sec_rms)) >= 1.0  # dB
        items.append(
            GateItem(
                "G4-6",
                "结构可辨",
                GATE_PASS if dynamic_ok else GATE_FAIL,
                f"段落 RMS 差 {max(sec_rms) - min(sec_rms):.2f} dB："
                + "、".join(f"{s['name']}{d:.1f}" for s, d in zip(arrangement["sections"], sec_rms)),
                "段落密度/动态差可辨（硬）",
                "mock 代理指标：段落 RMS 差代替听感对比表",
            )
        )
        # ---- G4-7 声乐-伴奏关系：声乐能量 vs 伴奏族（整轨 RMS 作为能量代理）----
        vocal_rms = dbfs(read_wav(stems["vocal"])["rms"]) if "vocal" in stems else -120.0
        # 缺轨时不能让门自己抛异常：G4-9 判的是「四轨齐不齐」，
        # 一个缺失的分轨应该让**那一条**红，而不是让整张报告 500。
        accomp_rms = {
            k: dbfs(read_wav(stems[k])["rms"]) for k in ("drums", "bass", "harmony") if k in stems
        } or {"（无伴奏轨）": -120.0}
        # 门槛取「伴奏族平均」而不是最大值：真实混音里单个乐器（尤其是鼓）短时峰值
        # 高于人声是正常的，被掩蔽指的是**整段能量**上人声被压过去。取 max 会把
        # 「鼓的瞬态」误判成掩蔽（本 mock 就正好踩到这条，−0.38 dB）。
        accomp = sum(accomp_rms.values()) / len(accomp_rms)
        diff = vocal_rms - accomp
        items.append(
            GateItem(
                "G4-7",
                "声乐-伴奏关系",
                GATE_PASS if diff >= 0 else GATE_FAIL,
                f"보컬 {vocal_rms:.2f} dBFS − 伴奏均值 {accomp:.2f} dBFS = {diff:+.2f} dB"
                f"（伴奏 {accomp_rms}）",
                "2~5kHz 不被掩蔽且无相位抵消（硬）",
                "mock 代理指标：整轨（整段）能量比代替 2~5kHz 频谱比对；"
                "真实测量需频谱比对 + 单声道合成试听",
            )
        )
        # ---- G4-8 单声道兼容：单声道折叠 = 下混（本包全单声道）----
        mono_ok = all(read_wav(p)["rms"] > 0 for p in [master_path] + list(stems.values()))
        items.append(
            GateItem(
                "G4-8",
                "单声道兼容",
                GATE_PASS if mono_ok else GATE_FAIL,
                "全轨单声道化后均非静音（无抵消）",
                "折叠单声道后关键元素不消失（硬）",
            )
        )
        # ---- G4-9 stem 完整性 ----
        need = {"drums", "bass", "harmony", "vocal"}
        problems = []
        if set(stems) != need:
            problems.append(f"stem 集合不符：{sorted(stems)}")
        else:
            lens = {k: read_wav(v)["duration"] for k, v in stems.items()}
            if max(lens.values()) - min(lens.values()) > 0.01:
                problems.append(f"stem 时长不一致：{lens}")
            if any(read_wav(v)["rms"] <= 0 for k, v in stems.items() if k != "drums"):
                problems.append("有 stem 静音")
        items.append(
            GateItem(
                "G4-9",
                "stem 完整性",
                GATE_FAIL if problems else GATE_PASS,
                "、".join(problems) if problems else "鼓/贝斯/和声/보컬 四轨，时长一致且可同播",
                "各 stem 可重开、可同播、时长一致（硬）",
            )
        )
        # ---- G4-10 输出规格 ----
        spec_ok = (
            m["framerate"] == SAMPLE_RATE
            and m["channels"] == CHANNELS
            and m["sampwidth"] == SAMPLE_WIDTH
            and os.path.basename(master_path) == "flow_demo_mix_v001.wav"
        )
        items.append(
            GateItem(
                "G4-10",
                "输出规格",
                GATE_PASS if spec_ok else GATE_FAIL,
                f"{m['framerate']}Hz/{m['sampwidth'] * 8}bit/{m['channels']}ch，"
                f"命名 {os.path.basename(master_path)}",
                "44.1k/24bit WAV + MP3 320kbps，命名 *_mix_v001.wav（硬规格）",
                "mock 用 16bit（避免 stdlib 无法写 24bit）；MP3 与 24bit 属真实工具侧，见 README",
            )
        )
        missing_listen = [k for k, v in listen_checklist.items() if not v]
        items.append(
            GateItem(
                "G4-11",
                "全曲试听清单",
                GATE_FAIL if (missing_listen or not listen_checklist) else GATE_PASS,
                "逐段勾选完成" if not missing_listen else f"未听段落：{missing_listen}",
                "逐条勾选，无未听项（硬）",
            )
        )
        missing = [k for k, v in deliverables.items() if not v or not os.path.exists(v)]
        items.append(
            GateItem(
                "G4-12",
                "交付物齐全",
                GATE_FAIL if missing else GATE_PASS,
                "原谱图/批准 MusicXML/보컬 WAV/编曲 JSON/master WAV 齐全"
                if not missing
                else f"缺失：{missing}",
                "五类交付物均在分发目录（硬）",
            )
        )
        status = _combine(items)
        return status, GateReport("G4", status, items)


def _section_rms(samples, seconds: float, sections: int) -> list[float]:
    """把整轨按段落均分，返回每段 RMS（dBFS）。用来给 G4-6 一个可测的动态差。"""
    n = len(samples)
    per = max(1, n // max(sections, 1))
    out = []
    for i in range(sections):
        chunk = samples[i * per : (i + 1) * per if i < sections - 1 else n]
        if not chunk:
            out.append(-120.0)
            continue
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
        out.append(dbfs(rms))
    return out


# ===========================================================================
# 门结论合并
# ===========================================================================
def _combine(items: list[GateItem]) -> str:
    """三态合并：任一 FAIL → FAIL；否则有 PASS_WITH_NOTES → PASS_WITH_NOTES。"""
    if any(it.status == GATE_FAIL for it in items):
        return GATE_FAIL
    if any(it.status == GATE_PASS_WITH_NOTES for it in items):
        return GATE_PASS_WITH_NOTES
    return GATE_PASS
