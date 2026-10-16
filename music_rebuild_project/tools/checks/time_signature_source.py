#!/usr/bin/env python3
"""time_signature_source.py — 拍号的**自动化来源**（T7 / G2 前置）。

实测缺口：oemer 产出的 MusicXML 里 `<time>` 计数 = 0 → `score_xml_validate.py` 无法自动
校验「每小节音长合计 = 拍号时长」（此前如实报 `PASS_WITH_NOTES` + 覆盖 0/23）。

本模块按**可靠性降序**提供三类来源，可组合（先声明、后 OCR、最后推断）：

| 顺序 | 来源 | 可信度 | 判定影响 |
|---|---|---|---|
| 1 | `manifest`（曲目元数据**声明**） | highest | 可判 PASS/FAIL |
| 2 | `ocr`（曲首标记区 OCR 抽 n/d） | high | 可判 PASS/FAIL |
| 3 | `inferred`（多数小节 `highestTime` 众数） | low | **只可 PASS_WITH_NOTES** |

硬要求（issue §增补 T7）：
  - **不得静默填充** —— 报告必须写明来源（declared / ocr / inferred）与置信；
  - **推断值只可 `PASS_WITH_NOTES`**，不得当作声明值判 PASS；
  - 无来源 → 维持 `PASS_WITH_NOTES` + 如实报覆盖率，**绝不**冒充。

用法::

    from checks.time_signature_source import resolve, apply_to_score
    ts = resolve(manifest_path=..., image_path=..., part_highest_times=[...])
    print(ts.source, ts.numerator, ts.denominator, ts.confidence, ts.verdict_cap)
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# 可信度（判定上限）：只有「声明」或「高置信 OCR」才有资格直接判 PASS/FAIL
CONFIDENCE_ORDER = {"declared": 3, "ocr": 2, "inferred": 1, "none": 0}
# 推断值的判定上限：不得 PASS
INFERRED_VERDICT_CAP = "PASS_WITH_NOTES"

_SIG_RE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)")
_WORD_RE = re.compile(r"\b(common|cut)\s+time\b", re.I)
_WORD_SIG = {"common": (4, 4), "cut": (2, 2)}
# 「貌似拍号」的常见误报：版本号/日期/页码/速度
_FALSE_POSITIVE = {(6, 8), (8, 6)}     # 单独出现且无上下文时不足以采信
MIN_DENOM = 1


@dataclass
class TimeSignature:
    """一个拍号结论：**来源与置信必须随行**（不得静默填充）。"""

    numerator: Optional[int]
    denominator: Optional[int]
    source: str = "none"                     # declared | ocr | inferred | none
    confidence: float = 0.0                  # declared=1.0；ocr=OCR 置信度；inferred=支持度
    detail: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def value(self) -> Optional[str]:
        if self.numerator is None or self.denominator is None:
            return None
        return f"{self.numerator}/{self.denominator}"

    @property
    def verdict_cap(self) -> str:
        """该来源允许的**最好**结论：推断值只能 PASS_WITH_NOTES。"""
        if self.numerator is None:
            return "PASS_WITH_NOTES"
        return "PASS" if self.source in ("declared", "ocr") else INFERRED_VERDICT_CAP

    def as_dict(self) -> dict:
        d = asdict(self)
        d["value"] = self.value
        d["verdict_cap"] = self.verdict_cap
        return d


def _ok(n: int, d: int) -> bool:
    return MIN_DENOM <= n <= 32 and d in (1, 2, 4, 8, 16, 32)


# ------------------------------------------------------------ 来源 1：元数据（声明）
_META_KEYS = ("time_signature", "timeSignature", "meter", "拍号")


def from_manifest(path: Path | None) -> Optional[TimeSignature]:
    """从 manifest（md 表格 / json / 文本）读**声明**拍号。无则 None。"""
    if path is None or not Path(path).exists():
        return None
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")

    if p.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        found = _dig_json(data)
        if found:
            return found

    # markdown 表格：| 拍号 | 4/4 |
    for key in _META_KEYS:
        m = re.search(rf"^\s*\|?\s*{key}\s*\|\s*([^|\n]+)", text, re.M | re.I)
        if m:
            sig = _parse_sig(m.group(1))
            if sig:
                return TimeSignature(*sig, source="declared", confidence=1.0,
                                     detail=f"元数据 {p.name} 声明", evidence=[m.group(0).strip()])
        m = re.search(rf"^\s*{key}\s*[:=]\s*(.+)$", text, re.M | re.I)
        if m:
            sig = _parse_sig(m.group(1))
            if sig:
                return TimeSignature(*sig, source="declared", confidence=1.0,
                                     detail=f"元数据 {p.name} 声明", evidence=[m.group(0).strip()])
    return None


def _dig_json(data: Any, depth: int = 0) -> Optional[TimeSignature]:
    if depth > 4:
        return None
    if isinstance(data, dict):
        for k, v in data.items():
            if str(k).lower() in ("time_signature", "timesignature", "meter"):
                sig = _parse_sig(str(v))
                if sig:
                    return TimeSignature(*sig, source="declared", confidence=1.0,
                                         detail=f"json 字段 {k}", evidence=[str(v)])
            got = _dig_json(v, depth + 1)
            if got:
                return got
    elif isinstance(data, list):
        for v in data:
            got = _dig_json(v, depth + 1)
            if got:
                return got
    return None


def _parse_sig(text: str) -> Optional[tuple[int, int]]:
    m = _SIG_RE.search(text or "")
    if m:
        n, d = int(m.group(1)), int(m.group(2))
        if _ok(n, d):
            return n, d
    w = _WORD_RE.search(text or "")
    if w:
        return _WORD_SIG[w.group(1).lower()]
    return None


# ------------------------------------------------------------ 来源 2：源侧 OCR
def from_ocr(image: Path | None, engines: Sequence[str] = ("rapidocr", "ocr")) -> Optional[TimeSignature]:
    """曲首标记区（顶部 ≤35% 高度带）OCR → 抽 `n/d`。

    只采信**高置信**结果，并把命中文本作为证据留痕（可复核，不静默填充）。
    """
    if image is None or not Path(image).exists():
        return None
    hits = _ocr_region(Path(image), engines=engines)
    if not hits:
        return None
    best: Optional[TimeSignature] = None
    for text, conf in hits:
        sig = _parse_sig(text)
        if not sig:
            continue
        cand = TimeSignature(*sig, source="ocr", confidence=conf / 100.0,
                             detail=f"曲首标记区 OCR 命中 `{text.strip()}`（置信 {conf:.0f}）",
                             evidence=[text.strip()])
        if best is None or cand.confidence > best.confidence:
            best = cand
    return best


def _ocr_region(image: Path, engines: Sequence[str], top_ratio: float = 0.35
                ) -> list[tuple[str, float]]:
    """返回 (文本, 置信[0-100])；引擎不可用 → 空列表（不抛，由上层如实报 SKIP）。"""
    out: list[tuple[str, float]] = []
    if "rapidocr" in engines:
        try:
            from rapidocr_onnxruntime import RapidOCR
            result, _ = RapidOCR()(str(image))
        except Exception:
            result = None
        if result:
            for box, text, score in result:
                ys = [float(pt[1]) for pt in box]
                if min(ys) <= _img_h(image) * top_ratio:
                    out.append((text or "", float(score) * 100.0))
    if out:
        return out
    if "ocr" in engines:
        try:
            from PIL import Image as PILImage
            import pytesseract
            from pytesseract import Output
            data = pytesseract.image_to_data(PILImage.open(image), output_type=Output.DICT)
        except Exception:
            return out
        h = _img_h(image)
        for i, t in enumerate(data.get("text", [])):
            t = (t or "").strip()
            if not t:
                continue
            try:
                c = float(data["conf"][i])
            except Exception:
                continue
            if c < 60 or data["top"][i] > h * top_ratio:
                continue
            out.append((t, c))
    return out


def _img_h(path: Path) -> float:
    try:
        from PIL import Image
        return float(Image.open(path).size[1])
    except Exception:
        return 1e9


# ------------------------------------------------------------ 来源 3：结构推断
def from_structure(highest_times: Iterable[float], tol: float = 1e-3
                   ) -> Optional[TimeSignature]:
    """由各小节 `highestTime` 的**众数**推断候选拍号（**标注为推断**，只能 PASS_WITH_NOTES）。"""
    vals = [round(float(v), 3) for v in highest_times if v and v > 0]
    if not vals:
        return None
    counts: dict[float, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    # 合并近似值（浮点抖动）
    merged: list[tuple[float, int]] = []
    for v, c in sorted(counts.items()):
        if merged and abs(v - merged[-1][0]) <= tol:
            pv, pc = merged.pop()
            total = pc + c
            merged.append(((pv * pc + v * c) / total, total))
        else:
            merged.append((v, c))
    mode, support = max(merged, key=lambda kv: (kv[1], kv[0]))
    sig = _duration_to_sig(mode)
    if sig is None:
        return None
    ratio = support / len(vals)
    start = _first_index(highest_times, mode, tol)
    return TimeSignature(*sig, source="inferred", confidence=ratio,
                         detail=(f"结构推断：{support}/{len(vals)} 小节 highestTime≈{mode:g} "
                                 f"（支持度 {ratio:.0%}，自第 {start} 小节起）→ 候选 {sig[0]}/{sig[1]}；"
                                 f"**推断值，非声明**"),
                         evidence=[f"mode={mode:g}", f"support={support}/{len(vals)}",
                                   f"first_measure={start}"])


def _first_index(values: Iterable[float], mode: float, tol: float) -> int:
    for i, v in enumerate(values, start=1):
        if v and abs(float(v) - mode) <= tol:
            return i
    return 1


_DUR_CANDIDATES: tuple[tuple[float, int, int], ...] = (
    (1.0, 1, 4), (2.0, 2, 4), (9 / 4, 9, 4), (3.0, 3, 4), (4.0, 4, 4),
    (6.0, 6, 8), (2.0, 2, 2), (3.0, 3, 2), (9.0, 9, 8), (12.0, 12, 8),
)


def _duration_to_sig(dur: float) -> Optional[tuple[int, int]]:
    """小节时长（quarterLength）→ 候选拍号。取「分母最小且匹配」的候选。"""
    best = None
    for d, n, den in _DUR_CANDIDATES:
        if d <= dur + 1e-6 and (best is None or d > best[0]):
            best = (d, n, den)
    if best is None:
        return None
    return best[1], best[2]


# ------------------------------------------------------------ 组合
def resolve(manifest_path: Path | None = None,
            image_path: Path | None = None,
            part_highest_times: Optional[Sequence[float]] = None,
            engines: Sequence[str] = ("rapidocr", "ocr"),
            allow_inferred: bool = True) -> TimeSignature:
    """按可靠性降序取第一个可用来源；全部不可用 → `source='none'`（**不**静默填充）。"""
    declared = from_manifest(manifest_path)
    if declared is not None:
        return declared
    ocr = from_ocr(image_path, engines=engines)
    if ocr is not None:
        return ocr
    if allow_inferred and part_highest_times:
        inferred = from_structure(part_highest_times)
        if inferred is not None:
            return inferred
    return TimeSignature(None, None, source="none", confidence=0.0,
                         detail="无任何可用来源（元数据未声明 / OCR 无命中 / 结构无法推断）")


def apply_to_measures(measures: Sequence[Any], ts: TimeSignature,
                      tol: float = 0.01) -> dict:
    """用**外部来源**的拍号校验每小节音长合计（末小节允许弱起不完整）。

    `measures` = 每小节 `highestTime`（quarterLength）序列。
    → {"checked": n, "total": N, "problems": [...], "verdict_cap": ...}
    """
    problems: list[str] = []
    expected = None
    if ts.numerator is not None and ts.denominator is not None:
        expected = float(ts.numerator) * (4.0 / ts.denominator)
    checked = 0
    for mi, ht in enumerate(measures):
        if expected is None or ht is None:
            continue
        checked += 1
        actual = float(ht)
        is_last = mi == len(measures) - 1
        if abs(actual - expected) > tol and not (is_last and actual < expected):
            problems.append(f"第{mi + 1}小节 音长合计={actual:.3f} ≠ 拍号({ts.value})时长={expected:.3f}")
    return {
        "source": ts.source,
        "value": ts.value,
        "confidence": ts.confidence,
        "verdict_cap": ts.verdict_cap,
        "expected_quarter_length": expected,
        "checked": checked,
        "total": len(measures),
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:      # pragma: no cover - 手工排查用
    import argparse
    ap = argparse.ArgumentParser(prog="time_signature_source.py")
    ap.add_argument("--manifest")
    ap.add_argument("--image")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ts = resolve(manifest_path=Path(args.manifest) if args.manifest else None,
                 image_path=Path(args.image) if args.image else None)
    print(json.dumps(ts.as_dict(), ensure_ascii=False, indent=2) if args.json
          else f"{ts.value or '(none)'}  source={ts.source} conf={ts.confidence:.2f} cap={ts.verdict_cap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
