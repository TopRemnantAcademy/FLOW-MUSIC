#!/usr/bin/env python3
"""g1_auto.py — G1 自动化判定核心（去人工：G1-1 线距 + G1-2~G1-7）。

架构（刻意分层，保证可测）：
  ┌ 引擎适配（重依赖**全部懒加载**）：oemer（OMR）/ pytesseract（OCR）/ OpenCV（裁切余量）
  └ 裁决逻辑（**纯函数、零依赖**）：AutoMetrics → (status, detail)     ← 单测只测这一层

阈值唯一来源：``00_docs/gates/gate_G1.md``；快照：``08_qc/gate_config.json``。
引擎缺失/异常 → 对应项 ``SKIP`` 并注明原因，**绝不冒充 PASS**（P5：精度只由测量声明）。

用法（供 g1_image_quality.py --auto 调用）::

    from g1_auto import auto_checks, auto_checks_dir, collect_metrics
    m = collect_metrics(path)                 # 真实引擎（可注入 provider 做测试）
    checks = auto_checks(path, provider=...)  # 7 项判定
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, Sequence

# ---------------------------------------------------------------- 阈值（gate_G1.md）
LINE_SPACE_PASS_PX = 8.0        # G1-1 子项：五线谱线距 ≥8px
LINE_SPACE_RELAXED_PX = 6.0     # 放宽：≥6px 仅可 PASS_WITH_NOTES
SKEW_PASS_DEG = 1.0             # G1-2：|歪斜角| <1°
SKEW_RELAXED_DEG = 2.0          # 放宽：<2° 仅 NOTE；≥2° FAIL
TEXT_CONF_MIN = 60.0            # G1-5：Tesseract 词置信度（0-100）达标线
TEXT_LEGIBLE_RATIO = 0.90       # G1-5：高置信词占比 ≥90%
TEXT_HEIGHT_MIN_PX = 16.0       # G1-5：字高 ≥16px
SYSTEMS_MAX = 6                 # G1-7：每页系统数 ≤6
MEASURES_MAX = 24               # G1-7：每页小节数 ≤24
CROP_MARGIN_MIN_RATIO = 0.01    # G1-3：内容 bbox 距页边 ≥1% 短边

CROP_TOUCH_PX = 2.0             # G1-3：内容距页边 <2px 视为触边（疑似被切）

OEMER_ORT_HINT = "onnxruntime==1.20.0"   # 见 plan/patches/：1.31 对老检查点负 pad 报错


# ---------------------------------------------------------------- 汇聚指标
@dataclass
class AutoMetrics:
    """跨引擎汇聚的**可测字段**（None = 未测到，判定层因此 SKIP 而非猜测）。"""

    source: str = "none"
    # oemer（OMR）
    unit_size_px: float | None = None          # 线距（已换算回原图像素）
    unit_size_min_px: float | None = None
    skew_deg_max: float | None = None          # |atan(slope)| 的最大值
    skew_deg_mean: float | None = None
    n_staves: int | None = None
    n_systems: int | None = None               # MusicXML new-system 标记数 + 1
    n_measures: int | None = None
    n_notes: int | None = None
    n_barlines: int | None = None
    n_clefs: int | None = None
    n_sfns: int | None = None
    n_rests: int | None = None
    # OCR（pytesseract）
    text_total: int | None = None
    text_confident: int | None = None
    text_conf_median: float | None = None
    text_height_px: float | None = None
    page_no: int | None = None                 # 页眉/页脚 OCR 到的页码
    # CV（裁切）
    content_margin_ratio: float | None = None  # 内容 bbox 到页边最小距离 / 短边
    errors: dict = field(default_factory=dict)

    def merge(self, other: "AutoMetrics") -> "AutoMetrics":
        """以 other 的非空字段覆盖本体的空字段（先到先得，不覆盖已有值）。"""
        for k, v in asdict(other).items():
            if k == "errors":
                self.errors.update(other.errors)
            elif v is not None and getattr(self, k) is None:
                setattr(self, k, v)
        if other.source != "none":
            self.source = f"{self.source}+{other.source}" if self.source != "none" else other.source
        return self


class MetricsProvider(Protocol):
    def __call__(self, img: Path) -> AutoMetrics: ...


class EngineUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- 纯裁决层（零依赖）
def verdict_line_space(m: AutoMetrics) -> tuple[str, str]:
    u = m.unit_size_px if m.unit_size_px is not None else m.unit_size_min_px
    if u is None:
        return "SKIP", "无 OMR 线距测量（引擎不可用/未测到）"
    if u >= LINE_SPACE_PASS_PX:
        return "PASS", f"线距={u:.1f}px ≥ {LINE_SPACE_PASS_PX:g}px（G1-1 子项，OMR 实测）"
    if u >= LINE_SPACE_RELAXED_PX:
        return ("NOTE",
                f"线距={u:.1f}px ∈ [{LINE_SPACE_RELAXED_PX:g},{LINE_SPACE_PASS_PX:g}) → 仅可 PASS_WITH_NOTES")
    return "FAIL", f"线距={u:.1f}px < {LINE_SPACE_RELAXED_PX:g}px（OMR 实测不足）"


def verdict_skew(m: AutoMetrics) -> tuple[str, str]:
    if m.skew_deg_max is None:
        return "SKIP", "无歪斜测量（OMR 引擎不可用/未测到）"
    a = m.skew_deg_max
    if a < SKEW_PASS_DEG:
        return "PASS", f"|歪斜|max={a:.3f}° < {SKEW_PASS_DEG:g}°（OMR slope 实测）"
    if a < SKEW_RELAXED_DEG:
        return "NOTE", f"|歪斜|max={a:.3f}° ∈ [{SKEW_PASS_DEG:g},{SKEW_RELAXED_DEG:g}) → 仅可 PASS_WITH_NOTES"
    return "FAIL", f"|歪斜|max={a:.3f}° ≥ {SKEW_RELAXED_DEG:g}°（须重拍/deskew）"


def verdict_crop(m: AutoMetrics) -> tuple[str, str]:
    r = m.content_margin_ratio
    if r is None:
        return "SKIP", "无内容边界测量（OCR/CV 引擎不可用）"
    if r <= 0.0:
        return "FAIL", f"内容触及页边（余量={r:.4%}）→ 疑似裁切，须核对原始页"
    if r < CROP_MARGIN_MIN_RATIO:
        return "NOTE", f"内容距页边仅 {r:.2%} < {CROP_MARGIN_MIN_RATIO:.0%} → 疑似裁切，须记录"
    return "PASS", f"内容距页边最小 {r:.2%} ≥ {CROP_MARGIN_MIN_RATIO:.0%}（无触边）"


def verdict_order(entries: Sequence[tuple[str, Optional[int]]],
                  expected_pages: Optional[int] = None) -> tuple[str, str]:
    """G1-4（目录级）：entries = [(relpath, 页码|None)]，按给定顺序（文件名序）。

    ``expected_pages``（来自 source 声明的总页数）是**证明「缺页=0」的必要条件**：
    未提供且只有 1 页时无法区分「单页曲目」与「多页曲目缺了页」→ 只能 NOTE，不判 PASS。
    """
    if not entries:
        return "SKIP", "无输入文件"
    nums = [n for _, n in entries]
    if all(n is None for n in nums):
        return "SKIP", f"全 {len(nums)} 页未 OCR 到页码 → 无法自动核序（缺页=0 无法证明）"
    missing = [p for p, n in entries if n is None]
    seq = [(p, n) for p, n in entries if n is not None]
    got = [n for _, n in seq]
    dups = sorted({n for n in got if got.count(n) > 1})
    if dups:
        return "FAIL", f"页码重复：{dups} → 重复页"
    if got != sorted(got):
        return "FAIL", f"页码非单调：{got}（顺序与源不一致）"
    gaps = [b for a, b in zip(got, got[1:]) if b != a + 1]
    if gaps:
        return "FAIL", f"页码缺号：{got} → 缺页"
    if expected_pages is not None and len(entries) != expected_pages:
        return "FAIL", f"实际页数={len(entries)} ≠ 源声明页数={expected_pages} → 缺页/多页"
    n_none = len(missing)
    if n_none:
        return ("NOTE",
                f"已 OCR 页码序列 {got[0]}..{got[-1]} 连续无缺无重；但 {n_none} 页无页码（无法证明其位置）")
    if len(got) < 2 and expected_pages is None:
        return ("NOTE",
                f"仅 {len(got)} 页（页码={got[0]}）且未提供源声明总页数 → **无法证明缺页=0**")
    tail = f"，与源声明页数 {expected_pages} 一致" if expected_pages is not None else ""
    return "PASS", f"页码序列 {got[0]}..{got[-1]} 连续、无缺号、无重复（{len(got)} 页{tail}）"


def verdict_text(m: AutoMetrics) -> tuple[str, str]:
    if m.text_total is None:
        return "SKIP", "无 OCR 文本测量（Tesseract 不可用）"
    if m.text_total == 0:
        return "NOTE", "未检测到任何文字（纯器乐谱可接受）；歌词可辨性无法自动证明"
    ratio = (m.text_confident or 0) / m.text_total
    h = m.text_height_px
    detail = (f"词数={m.text_total} 高置信(≥{TEXT_CONF_MIN:g})={m.text_confident} 占比={ratio:.0%}"
              f" 中位置信={m.text_conf_median:.1f} 字高={h if h is not None else 'n/a'}")
    if h is not None and h < TEXT_HEIGHT_MIN_PX:
        return "FAIL", f"字高 {h:.1f}px < {TEXT_HEIGHT_MIN_PX:g}px（{detail}）"
    if ratio < TEXT_LEGIBLE_RATIO:
        return "FAIL", f"可辨词占比 {ratio:.0%} < {TEXT_LEGIBLE_RATIO:.0%}（{detail}）"
    return "PASS", f"文字可辨（{detail}）"


def verdict_symbols(m: AutoMetrics) -> tuple[str, str]:
    """G1-6：符号非退化 = 检出音符 且 至少覆盖 {barlines, clefs} 两类结构符号。"""
    if m.n_notes is None:
        return "SKIP", "无 OMR 符号测量（引擎不可用）"
    n_notes = m.n_notes
    classes = {
        "barlines": m.n_barlines,
        "clefs": m.n_clefs,
        "sfns": m.n_sfns,
        "rests": m.n_rests,
    }
    present = [k for k, v in classes.items() if v]
    detail = (f"notes={n_notes} barlines={m.n_barlines} clefs={m.n_clefs} "
              f"sfns={m.n_sfns} rests={m.n_rests}")
    if n_notes <= 0:
        return "FAIL", f"未检出任何音符 → 符号不可区分（{detail}）"
    if not (m.n_barlines or 0) > 0 or not (m.n_clefs or 0) > 0:
        return ("NOTE",
                f"音符有检出但结构符号不足（{detail}）→ 疑似识别退化，须复检")
    return "PASS", f"符号可区分（{detail}，类别={'/'.join(present)}）"


def verdict_density(m: AutoMetrics) -> tuple[str, str]:
    if m.n_systems is None and m.n_measures is None:
        return "SKIP", "无 OMR 计数（引擎不可用）"
    systems, measures = m.n_systems, m.n_measures
    bad = []
    if systems is not None and systems > SYSTEMS_MAX:
        bad.append(f"系统数={systems} > {SYSTEMS_MAX}")
    if measures is not None and measures > MEASURES_MAX:
        bad.append(f"小节数={measures} > {MEASURES_MAX}")
    if bad:
        return "FAIL", "；".join(bad) + "（超出则拆页或重拍）"
    return "PASS", f"系统数={systems} ≤ {SYSTEMS_MAX}；小节数={measures} ≤ {MEASURES_MAX}"


# 检查项 → 判定函数（G1-1 线距为 G1-1 的子项，报告中单列 "G1-1L"）
VERDICTS: dict[str, Callable[[AutoMetrics], tuple[str, str]]] = {
    "G1-1L": verdict_line_space,
    "G1-2": verdict_skew,
    "G1-3": verdict_crop,
    "G1-5": verdict_text,
    "G1-6": verdict_symbols,
    "G1-7": verdict_density,
}


def auto_checks(img: Path, provider: Optional[MetricsProvider] = None) -> dict[str, dict]:
    """对单图跑 6 项图级判定（G1-4 为目录级，见 auto_checks_dir）。"""
    m = (provider or collect_metrics)(img)
    out: dict[str, dict] = {}
    for key, fn in VERDICTS.items():
        status, detail = fn(m)
        out[key] = {"status": status, "detail": detail}
    out["_metrics"] = asdict(m)  # type: ignore[assignment]
    return out


def auto_checks_dir(files: Sequence[Path], provider: Optional[MetricsProvider] = None,
                    expected_pages: Optional[int] = None) -> dict:
    """目录级：逐图 6 项 + 跨页 G1-4（页序/缺页）。"""
    m_provider = provider or collect_metrics
    per_file = []
    entries: list[tuple[str, Optional[int]]] = []
    for f in files:
        checks = auto_checks(f, provider=m_provider)
        per_file.append({"path": str(f), "checks": checks})
        entries.append((f.name, checks["_metrics"].get("page_no")))  # type: ignore[union-attr]
    order = verdict_order(entries, expected_pages=expected_pages)
    return {"files": per_file, "G1-4": {"status": order[0], "detail": order[1]}}


# ---------------------------------------------------------------- 引擎适配层（懒加载）
def _oemer_collect(img: Path) -> AutoMetrics:
    """oemer 全链（含检查点自动下载）→ 线距/歪斜/符号/系统/小节。

    依赖钉版：onnxruntime==1.20.0（1.31 对老检查点负 pad 报 ShapeInferenceError）。
    兼容补丁：bbox.find_lines（HoughLinesP 形状），见 plan/patches/。
    """
    try:
        import numpy as np
        import oemer.ete as ete
        from oemer import MODULE_PATH, layers
    except Exception as e:  # pragma: no cover - 环境相关
        raise EngineUnavailable(f"oemer 不可用：{e}") from e

    try:
        import onnxruntime as ort
        ort_ver = ort.__version__
    except Exception:  # pragma: no cover
        ort_ver = "?"
    if not ort_ver.startswith("1.20"):
        # 不阻断（可能已被单独修好），但把事实带进报告
        pass

    import argparse
    import math as _math

    chk = os.path.join(MODULE_PATH, "checkpoints", "unet_big", "model.onnx")
    if not os.path.exists(chk):
        with contextlib.redirect_stdout(io.StringIO()):   # oemer 下载进度会 print 到 stdout
            for title, url in ete.CHECKPOINTS_URL.items():
                sub = "unet_big" if title.startswith("1st") else "seg_net"
                save_dir = os.path.join(MODULE_PATH, "checkpoints", sub)
                os.makedirs(save_dir, exist_ok=True)
                ete.download_file(title, url, os.path.join(save_dir, title.split("_")[1]))

    out_dir = Path(tempfile.mkdtemp(prefix="oemer_auto_"))
    ns = argparse.Namespace(img_path=str(img), output_path=str(out_dir),
                            use_tf=False, save_cache=False, without_deskew=False)
    # oemer 内部大量 print()（进度/统计）会污染本工具的 --json 输出 → 全程吞掉 stdout
    with contextlib.redirect_stdout(io.StringIO()):
        ete.clear_data()
        mxl = ete.extract(ns)

    m = AutoMetrics(source="oemer")
    staffs = [s for s in np.array(layers.get_layer("staffs")).reshape(-1) if s is not None]
    us, sl = [], []
    for st in staffs:
        try:
            us.append(float(st.unit_size))
        except Exception:
            pass
        try:
            sl.append(float(st.slope))
        except Exception:
            pass
    m.n_staves = len(staffs)

    # 换算回原像素：staff_pred 是模型工作分辨率
    from PIL import Image as PILImage
    W, H = PILImage.open(img).size
    fy = None
    try:
        mask = np.array(layers.get_layer("staff_pred"))
        if mask.shape[0]:
            fy = H / float(mask.shape[0])
    except Exception:
        pass
    if us:
        scale = fy if fy else 1.0
        m.unit_size_px = (sum(us) / len(us)) * scale
        m.unit_size_min_px = min(us) * scale
    if sl:
        degs = [abs(_math.degrees(_math.atan(s))) for s in sl]
        m.skew_deg_max = max(degs)
        m.skew_deg_mean = sum(degs) / len(degs)

    for name, attr in (("notes", "n_notes"), ("barlines", "n_barlines"),
                       ("clefs", "n_clefs"), ("sfns", "n_sfns"), ("rests", "n_rests")):
        try:
            v = layers.get_layer(name)
            setattr(m, attr, int(np.array(v).size))
        except Exception:
            pass

    try:
        txt = Path(str(mxl)).read_text(encoding="utf-8", errors="replace")
        m.n_measures = txt.count("<measure ")
        if m.n_notes is None:
            m.n_notes = txt.count("<note")
        m.n_systems = 1 + txt.count('new-system="yes"')
    except Exception as e:
        m.errors["musicxml"] = str(e)
    return m


_TXT_RE = re.compile(r"\d{1,4}")


def _ocr_collect(img: Path) -> AutoMetrics:
    """pytesseract → 文字置信/字高/页码/内容边界余量（裁切）。"""
    try:
        from PIL import Image
        import pytesseract
        from pytesseract import Output
    except Exception as e:  # pragma: no cover
        raise EngineUnavailable(f"pytesseract/PIL 不可用：{e}") from e

    try:
        data = pytesseract.image_to_data(Image.open(img), output_type=Output.DICT)
    except Exception as e:
        raise EngineUnavailable(f"tesseract 调用失败（未安装?）：{e}") from e

    m = AutoMetrics(source="ocr")
    confs, heights, boxes = [], [], []
    n_total = n_conf = 0
    for i, t in enumerate(data.get("text", [])):
        t = (t or "").strip()
        if not t:
            continue
        try:
            c = float(data["conf"][i])
        except Exception:
            continue
        if c < 0:
            continue
        n_total += 1
        confs.append(c)
        if c >= TEXT_CONF_MIN:
            n_conf += 1
        h = float(data["height"][i]) if data.get("height") else 0.0
        if h:
            heights.append(h)
        boxes.append((data["left"][i], data["top"][i],
                      data["left"][i] + data["width"][i], data["top"][i] + data["height"][i]))
    if n_total:
        m.text_total = n_total
        m.text_confident = n_conf
        confs.sort()
        m.text_conf_median = confs[len(confs) // 2]
        m.text_height_px = (sum(heights) / len(heights)) if heights else None

    # 内容边界余量（G1-3 裁切代理）
    try:
        from PIL import Image as PILImage
        W, H = PILImage.open(img).size
        if boxes:
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            margin = min(x0, y0, W - x1, H - y1)
            m.content_margin_ratio = max(0.0, margin) / float(min(W, H))
    except Exception:
        pass

    # 页码（页眉/页脚区域，取最靠边且孤立的小整数）
    try:
        cands = []
        for i, t in enumerate(data.get("text", [])):
            tt = (t or "").strip()
            if not tt or not _TXT_RE.fullmatch(tt):
                continue
            try:
                c = float(data["conf"][i])
            except Exception:
                continue
            if c < 50:
                continue
            top, hgt = data["top"][i], data["height"][i]
            from PIL import Image as PILImage
            H = PILImage.open(img).size[1]
            if top < H * 0.12 or (top + hgt) > H * 0.88:   # 页眉/页脚带
                cands.append((int(tt), top))
        if cands:
            cands.sort(key=lambda x: x[1])   # 最靠上的小整数更像页码
            m.page_no = cands[0][0]
    except Exception:
        pass
    return m


def _rapidocr_collect(img: Path) -> AutoMetrics:
    """RapidOCR（ONNX，pip 即得，**无需系统 Tesseract 二进制**）→ 文字/字高/页码/内容余量。

    作为 ``ocr``（pytesseract）的等价备选：两者任一可用即可判定 G1-3/G1-5。
    """
    try:
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:  # pragma: no cover - 环境相关
        raise EngineUnavailable(f"rapidocr 不可用：{e}") from e
    try:
        engine = RapidOCR()
        result, _elapse = engine(str(img))
    except Exception as e:
        raise EngineUnavailable(f"rapidocr 推理失败：{e}") from e

    m = AutoMetrics(source="rapidocr")
    if not result:
        m.text_total = 0
        return m

    W, H = Image.open(img).size
    scores, heights, boxes, cands = [], [], [], []
    n_conf = 0
    for box, text, score in result:
        s = float(score) * 100.0
        scores.append(s)
        if s >= TEXT_CONF_MIN:
            n_conf += 1
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        heights.append(max(ys) - min(ys))
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
        t = (text or "").strip()
        if _TXT_RE.fullmatch(t) and s >= 50.0 and (min(ys) < H * 0.12 or max(ys) > H * 0.88):
            cands.append((int(t), min(ys)))

    m.text_total = len(result)
    m.text_confident = n_conf
    scores.sort()
    m.text_conf_median = scores[len(scores) // 2]
    heights.sort()
    m.text_height_px = heights[len(heights) // 2] if heights else None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    m.content_margin_ratio = max(0.0, min(x0, y0, W - x1, H - y1)) / float(min(W, H))
    if cands:
        cands.sort(key=lambda x: x[1])
        m.page_no = cands[0][0]
    return m


def _cv_collect(img: Path) -> AutoMetrics:
    """纯 CV 引擎（numpy/OpenCV，**无模型**）：线距 + 歪斜 + 内容余量。

    价值有二：① OMR 不可用时的**无模型回退**（许可更干净、无检查点下载）；
    ② 为 L1 合成标定（``g1_calibrate.py``）提供**可测链路**。
    方法：Otsu 二值化 → 水平投影找五线谱行（线距）；Hough 近水平长直线取中位角（歪斜）；
    形态学开运算后墨迹 bbox 距页边最小距离（余量）。
    """
    try:
        import cv2
        import numpy as np
    except Exception as e:  # pragma: no cover - 环境相关
        raise EngineUnavailable(f"cv2/numpy 不可用：{e}") from e

    gray = cv2.imread(str(img), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise EngineUnavailable("无法读取图像")
    H, W = gray.shape[:2]
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    m = AutoMetrics(source="cv")

    # ---- 内容余量（去掉孤立噪点，再取墨迹 bbox）----
    # ⚠ 不能用方形结构元：1px 细线（合成标定集、低分辨率扫描都是这种）会被 3x3 开/腐蚀
    #   整体抹掉，bbox 直接消失 → 余量测不到（#163 T1 实测坑）。
    #   改用**轴向 1×7**结构元：能去掉孤立噪点，但不伤害整行连续的谱线。
    kn = np.ones((1, 31), np.uint8)
    ink = cv2.morphologyEx(bw.copy(), cv2.MORPH_OPEN, kn, iterations=1)
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:          # 图上没有连续 ≥31px 的水平笔迹 → 保底退回未去噪的二值图
        ys, xs = np.nonzero(bw)
    if len(xs):
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        m.content_margin_ratio = max(0.0, min(x0, y0, W - 1 - x1, H - 1 - y1)) / float(min(W, H))

    # ---- 歪斜：Hough 近水平长直线 → 中位角 ----
    min_len = max(40, int(W * 0.25))
    lines = cv2.HoughLinesP(bw, 1, np.pi / 720, threshold=100,
                            minLineLength=min_len, maxLineGap=8)
    angs: list[float] = []
    if lines is not None:
        for ln in lines:
            ln = np.asarray(ln).reshape(-1)
            if ln.size < 4:
                continue
            dx = float(ln[2] - ln[0])
            dy = float(ln[3] - ln[1])
            if abs(dx) < 1.0:
                continue
            a = math.degrees(math.atan2(dy, dx))
            if abs(a) <= 10.0:          # 谱线必然近水平
                angs.append(a)
    if angs:
        angs.sort()
        m.skew_deg_mean = abs(angs[len(angs) // 2])
        m.skew_deg_max = max(abs(a) for a in angs)

    # ---- 线距：**逐列**投影 → 行中心 → 取**最小重复间距**（五线谱内间距）----
    # 为何逐列：谱页一旦倾斜（哪怕 0.8°），五条 1px 细线会在同一行带内互相「路过」，
    #   **全页**水平投影把它们糊成一条厚墨带 → 细线全被筛掉、线距测不到。
    #   （#163 T3 合成标定集**当场抓到**的真实缺陷；改为逐列后按列取众数间距。）
    per_col: dict[float, int] = {}
    for x in _col_samples(W):
        col = bw[:, x]
        diffs = _line_diffs(col)
        if diffs:
            per_col[round(float(np.median(diffs)), 1)] = per_col.get(
                round(float(np.median(diffs)), 1), 0) + 1
    if per_col:
        best_pitch, _ = max(per_col.items(), key=lambda kv: (kv[1], -kv[0]))
        if best_pitch > 0.5:
            m.unit_size_px = round(best_pitch, 1)
            m.unit_size_min_px = m.unit_size_px
    return m


def _col_samples(width: int, max_cols: int = 64) -> list[int]:
    """等距取列（避免大图上逐列扫描成为瓶颈）。"""
    if width <= max_cols:
        return list(range(width))
    step = width / max_cols
    return sorted({int(i * step) for i in range(max_cols)} | {width - 1})


def _line_diffs(col, min_run: int = 1, max_run: int = 6) -> list[float]:
    """单列里的「细谱线」行中心 → 相邻间距（竖直游程 1~6px 视为一条谱线）。"""
    import numpy as np

    ys = np.nonzero(col)[0]
    if ys.size == 0:
        return []
    centers: list[float] = []
    runs: list[tuple[int, int]] = []          # 竖直游程（连续墨迹段）
    start = prev = int(ys[0])
    for y in ys[1:]:
        y = int(y)
        if y - prev > 1:
            runs.append((start, prev))
            start = y
        prev = y
    runs.append((start, prev))                # 别丢最后一段（曾经被哨兵值吞掉 → 少一个间距）
    for a, b in runs:
        if min_run <= (b - a + 1) <= max_run:
            centers.append((a + b) / 2.0)
    return [round(b - a, 3) for a, b in zip(centers, centers[1:]) if (b - a) > 0.5]


def consensus_status(statuses: Sequence[str]) -> tuple[str, str]:
    """多引擎共识（fail-closed）：任一 FAIL → FAIL；PASS/NOTE 分歧 → NOTE（**不放行 PASS**）。

    这是 09 文档 §5「L2 双引擎共识」的判定原语：引擎分歧时宁可 NOTE 也不给 PASS。
    """
    vals = [s for s in statuses if s]
    if not vals:
        return "SKIP", "无引擎结果"
    live = [s for s in vals if s != "SKIP"]
    if not live:
        return "SKIP", f"全部引擎弃权（{len(vals)} 个）"
    if "FAIL" in live:
        return "FAIL", f"含 FAIL：{vals}"
    if len(set(live)) > 1:
        return "NOTE", f"引擎分歧 {vals} → 不放行 PASS"
    return live[0], f"引擎一致 {live[0]}（{len(vals)} 个引擎）"


_ENGINES: dict[str, Callable[[Path], AutoMetrics]] = {
    "oemer": _oemer_collect,
    "ocr": _ocr_collect,            # pytesseract（需系统 tesseract 二进制）
    "rapidocr": _rapidocr_collect,  # 纯 pip/ONNX 备选，无系统依赖
    "cv": _cv_collect,              # 纯 CV 无模型回退（线距/歪斜/余量）
}


def collect_metrics(img: Path,
                    engines: Iterable[str] = ("oemer", "ocr", "rapidocr", "cv")) -> AutoMetrics:
    """按引擎顺序采集并合并；单引擎失败只记录 errors，不抛出。"""
    merged = AutoMetrics(source="none")
    for name in engines:
        fn = _ENGINES.get(name)
        if fn is None:
            continue
        try:
            merged.merge(fn(img))
        except EngineUnavailable as e:
            merged.errors[name] = str(e)
        except Exception as e:  # 引擎内部异常同样降级，不炸主流程
            merged.errors[name] = f"{type(e).__name__}: {e}"
    return merged


if __name__ == "__main__":  # 手工冒烟：python tools/checks/g1_auto.py <img>
    p = Path(sys.argv[1])
    print(json.dumps(auto_checks(p), ensure_ascii=False, indent=2))
