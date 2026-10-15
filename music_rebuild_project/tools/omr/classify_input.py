"""输入分类工具。

分类只做**客观可测**的部分（容器 / 尺寸 / DPI / PDF 是否含图像），
一切主观判断（是否手写、是否"够清楚"）一律标 ``requires_human``——
遵守原则 P5：精度只由测量报告声明，禁自动估计。

纯 stdlib，无第三方依赖，无 RNG（确定性）。
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

__all__ = [
    "SIGNATURES", "classify_file", "classify_dir", "sha256_of",
    "read_png_info", "read_jpeg_info", "read_tiff_info", "classify_pdf",
    "Container", "SubType",
]


class Container:
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    BMP = "bmp"
    WEBP = "webp"
    PDF = "pdf"
    UNKNOWN = "unknown"


class SubType:
    VECTOR_PDF = "vector_pdf"      # 矢量 PDF：可直接导出，不需 OMR
    SCANNED_PDF = "scanned_pdf"    # 扫描件 PDF：含位图 XObject
    RASTER = "raster"              # 位图（扫描/照片/截图，二者机器不可分）
    UNKNOWN = "unknown"


# 魔数签名（顺序敏感：先匹配更具体的）
SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", Container.PNG),
    (b"\xff\xd8\xff", Container.JPEG),
    (b"II*\x00", Container.TIFF),
    (b"MM\x00*", Container.TIFF),
    (b"BM", Container.BMP),
    (b"RIFF", Container.WEBP),   # 需进一步在 8..12 校验 "WEBP"
    (b"%PDF-", Container.PDF),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_container(head: bytes) -> str:
    for sig, name in SIGNATURES:
        if head.startswith(sig):
            if name == Container.WEBP:
                return Container.WEBP if head[8:12] == b"WEBP" else Container.UNKNOWN
            return name
    return Container.UNKNOWN


# ---- 尺寸 / DPI 解析 --------------------------------------------------------

def _dpi_from_ppm(ppm: int) -> float | None:
    """pixels-per-meter → dpi（1 inch = 0.0254 m）。"""
    if not ppm:
        return None
    return round(ppm * 0.0254, 2)


def read_png_info(data: bytes) -> dict:
    """解析 IHDR（宽高）与 pHYs（DPI）。chunk 结构：len(4) type(4) data crc(4)。"""
    info: dict = {"width": None, "height": None, "dpi": None}
    if len(data) < 24:
        return info
    # IHDR 固定在 8..24
    try:
        w, h = struct.unpack(">II", data[16:24])
        info["width"], info["height"] = w, h
    except struct.error:
        return info
    pos = 8
    n = len(data)
    while pos + 8 <= n:
        try:
            (clen,) = struct.unpack(">I", data[pos:pos + 4])
        except struct.error:
            break
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + clen]
        if ctype == b"pHYs" and len(body) >= 9:
            ppux, ppuy, unit = struct.unpack(">IIB", body[:9])
            if unit == 1:  # meters
                info["dpi"] = _dpi_from_ppm(ppux)
            elif unit == 2:  # ??? 非标准，保守忽略
                info["dpi"] = None
        if ctype == b"IEND":
            break
        pos += 12 + clen
    return info


def read_jpeg_info(data: bytes) -> dict:
    """扫描 SOFn 取宽高；读 APP0/JFIF 密度。"""
    info: dict = {"width": None, "height": None, "dpi": None}
    n = len(data)
    i = 2  # 跳过 SOI
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # 独立标记（无长度字段）
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            break
        (seglen,) = struct.unpack(">H", data[i + 2:i + 4])
        seg = data[i + 4:i + 2 + seglen]
        if marker == 0xE0 and seg[:5] == b"JFIF\x00" and len(seg) >= 12:
            units = seg[7]
            xden = struct.unpack(">H", seg[8:10])[0]
            if units == 1:      # dots/inch
                info["dpi"] = float(xden)
            elif units == 2:    # dots/cm → dpi
                info["dpi"] = round(xden * 2.54, 2)
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if len(seg) >= 5:
                h, w = struct.unpack(">HH", seg[1:5])
                info["height"], info["width"] = h, w
            break
        if seglen <= 0:
            break
        i += 2 + seglen
    return info


def read_tiff_info(data: bytes) -> dict:
    """最小 IFD 解析：ImageWidth(256)/ImageLength(257)/XResolution(282)/ResolutionUnit(296)。"""
    info: dict = {"width": None, "height": None, "dpi": None}
    if len(data) < 8:
        return info
    endian = "<" if data[:2] == b"II" else ">"
    try:
        (off,) = struct.unpack(endian + "I", data[4:8])
        if off + 2 > len(data):
            return info
        (count,) = struct.unpack(endian + "H", data[off:off + 2])
        res_x, unit = None, 2
        for k in range(count):
            base = off + 2 + k * 12
            if base + 12 > len(data):
                break
            tag, typ = struct.unpack(endian + "HH", data[base:base + 4])
            (nvals,) = struct.unpack(endian + "I", data[base + 4:base + 8])
            if typ == 3 and nvals == 1:      # SHORT
                val = struct.unpack(endian + "H", data[base + 8:base + 10])[0]
            else:
                (voff,) = struct.unpack(endian + "I", data[base + 8:base + 12])
                if typ == 4 and nvals == 1:  # LONG
                    val = voff
                elif typ == 5 and nvals == 1 and voff + 8 <= len(data):  # RATIONAL
                    num, den = struct.unpack(endian + "II", data[voff:voff + 8])
                    val = (num / den) if den else None
                else:
                    val = None
            if tag == 256:
                info["width"] = val
            elif tag == 257:
                info["height"] = val
            elif tag == 282:
                res_x = val
            elif tag == 296:
                unit = val if val in (1, 2, 3) else 2
        if res_x:
            info["dpi"] = float(res_x) if unit == 2 else _dpi_from_ppm(int(res_x))
    except (struct.error, IndexError, ValueError):
        return info
    return info


def read_bmp_info(data: bytes) -> dict:
    info: dict = {"width": None, "height": None, "dpi": None}
    if len(data) < 26:
        return info
    try:
        w, h = struct.unpack("<ii", data[18:26])
        ppm_x = struct.unpack("<i", data[38:42])[0] if len(data) >= 42 else 0
        info["width"], info["height"] = abs(w), abs(h)
        if ppm_x:
            info["dpi"] = _dpi_from_ppm(ppm_x)
    except struct.error:
        pass
    return info


def classify_pdf(data: bytes) -> dict:
    """矢量 vs 扫描：看是否存在图像 XObject 与字体/文本算子。

    PDF 可能是压缩流，纯字节匹配只是**启发式**；命中不了时标 unknown 而非猜测。
    """
    out = {"has_image_xobject": False, "has_text": False, "pages_guess": None}
    head = data[:1 << 20]  # 只扫前 1MB，够用且确定
    img_markers = (b"/Image", b"/DCTDecode", b"/CCITTFaxDecode", b"/JPXDecode", b"/FlateDecode")
    out["has_image_xobject"] = any(m in head for m in img_markers)
    out["has_text"] = (b"/Font" in head) or (b"BT" in head) or (b"/Type /Page" in head) \
        or (b"/Type/Page" in head)
    # /Type /Page 计数（近似页数）
    out["pages_guess"] = max(head.count(b"/Type /Page"), head.count(b"/Type/Page")) or None
    return out


# ---- 主入口 ----------------------------------------------------------------

def classify_file(path: str | Path) -> dict:
    """分类单个文件，返回客观可测字段；主观项一律 requires_human。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if not p.is_file():
        raise ValueError(f"不是普通文件: {p}")
    data = p.read_bytes()
    if not data:
        raise ValueError(f"空文件: {p}")
    head = data[:16]
    container = detect_container(head)

    info: dict = {"width": None, "height": None, "dpi": None}
    pdf: dict = {}
    if container == Container.PNG:
        info = read_png_info(data)
    elif container == Container.JPEG:
        info = read_jpeg_info(data)
    elif container == Container.TIFF:
        info = read_tiff_info(data)
    elif container == Container.BMP:
        info = read_bmp_info(data)
    elif container == Container.PDF:
        pdf = classify_pdf(data)

    if container == Container.PDF:
        if pdf["has_image_xobject"] and not pdf["has_text"]:
            subtype = SubType.SCANNED_PDF
        elif pdf["has_text"] and not pdf["has_image_xobject"]:
            subtype = SubType.VECTOR_PDF
        elif pdf["has_text"] and pdf["has_image_xobject"]:
            subtype = SubType.SCANNED_PDF  # 含图 → 按扫描件走 OMR 路径
        else:
            subtype = SubType.UNKNOWN
        omr_required = subtype == SubType.SCANNED_PDF
        reason = {
            SubType.SCANNED_PDF: "含位图 XObject → 走 OMR 路径",
            SubType.VECTOR_PDF: "矢量文本层可直接导出，不需 OMR",
            SubType.UNKNOWN: "PDF 结构未识别（可能加密/压缩流）→ 需人工确认",
        }[subtype]
    elif container in (Container.PNG, Container.JPEG, Container.TIFF,
                       Container.BMP, Container.WEBP):
        subtype = SubType.RASTER
        omr_required = True
        reason = "位图 → 走 OMR 路径；是否手写/足够清晰须人工判（P5）"
    else:
        subtype = SubType.UNKNOWN
        omr_required = False
        reason = "不支持的容器格式 → 转人工录入（#65）"

    return {
        "path": str(p),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "container": container,
        "subtype": subtype,
        "width": info["width"],
        "height": info["height"],
        "dpi": info["dpi"],
        "pdf_pages_guess": pdf.get("pages_guess"),
        "omr_required": omr_required,
        "reason": reason,
        "handwriting": "requires_human",  # 永不自动判定
    }


def classify_dir(path: str | Path) -> dict:
    """递归分类目录内所有文件（跳过 .gitkeep 等占位）。"""
    root = Path(path)
    if not root.is_dir():
        raise ValueError(f"不是目录: {root}")
    items = []
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name != ".gitkeep":
            try:
                items.append(classify_file(f))
            except (ValueError, OSError) as e:
                items.append({"path": str(f), "error": str(e)})
    return {"root": str(root), "count": len(items), "files": items}


# ---- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="classify_input",
        description="输入分类（#29）：容器/尺寸/DPI/PDF 子型 + OMR 是否需要；手写永不自动判定")
    parser.add_argument("target", help="文件或目录")
    parser.add_argument("--out", help="输出 JSON 路径（缺省打印 stdout）")
    parser.add_argument("--min-dpi", type=float, default=None,
                        help="最低 DPI 门槛，低于则 warning（不代 G1 判定）")
    args = parser.parse_args(argv)

    try:
        target = Path(args.target)
        if not target.exists():
            raise FileNotFoundError(f"路径不存在: {args.target}")
        report = classify_dir(target) if target.is_dir() else classify_file(target)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"读取错误: {e}", file=sys.stderr)
        return 2

    if args.min_dpi is not None:
        files = report.get("files", [report]) if "files" in report else [report]
        for f in files:
            dpi = f.get("dpi")
            if dpi is None:
                f["dpi_warning"] = "DPI 未知"
            elif dpi < args.min_dpi:
                f["dpi_warning"] = f"DPI {dpi} < {args.min_dpi}"

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out = Path(args.out)
        if out.parent and not out.parent.exists():
            print(f"写入错误: 目录不存在 {out.parent}", file=sys.stderr)
            return 2
        out.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
