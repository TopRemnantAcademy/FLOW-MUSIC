"""classify_input（#29）测试：容器识别 / 尺寸 DPI / PDF 子型 / 手写永不自动判定。"""

from __future__ import annotations

import json
import struct
import zlib

import pytest

from music_rebuild_project.tools.omr import classify_input as CI


# ---- 合成素材（确定性，无 RNG） --------------------------------------------

def _png(width=100, height=200, ppm_x=11811) -> bytes:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + ctype + data
                + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    phys = struct.pack(">IIB", ppm_x, ppm_x, 1)  # unit=1 → meters
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"pHYs", phys) + chunk(b"IEND", b""))


def _jpeg(width=80, height=60, units=1, density=300) -> bytes:
    app0 = (b"JFIF\x00" + struct.pack(">BB", 1, 1)      # version
            + struct.pack(">B", units)                   # units
            + struct.pack(">HH", density, density)       # X/Y density
            + struct.pack(">BB", 0, 0))                  # X/Y thumbnail
    # APP0: marker(ff e0) + seglen(2) + app0 data
    seg_app0 = b"\xff\xe0" + struct.pack(">H", 2 + len(app0)) + app0
    sof0_data = struct.pack(">BHHB", 8, height, width, 3)
    seg_sof0 = b"\xff\xc0" + struct.pack(">H", 2 + len(sof0_data)) + sof0_data
    return b"\xff\xd8" + seg_app0 + seg_sof0 + b"\xff\xd9"


def _tiff(width=50, height=70, res=300) -> bytes:
    hdr = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    rat_off = 62
    entries = b""
    entries += struct.pack("<HHI", 256, 3, 1) + struct.pack("<H", width) + b"\x00\x00"
    entries += struct.pack("<HHI", 257, 3, 1) + struct.pack("<H", height) + b"\x00\x00"
    entries += struct.pack("<HHI", 282, 5, 1) + struct.pack("<I", rat_off)
    entries += struct.pack("<HHI", 296, 3, 1) + struct.pack("<H", 2) + b"\x00\x00"
    ifd = struct.pack("<H", 4) + entries + struct.pack("<I", 0)
    rational = struct.pack("<II", res, 1)
    assert 8 + len(ifd) == rat_off, (8 + len(ifd), rat_off)
    return hdr + ifd + rational


def _bmp(width=40, height=30, ppm=11811) -> bytes:
    dib = (struct.pack("<I", 40) + struct.pack("<ii", width, height)
           + struct.pack("<HH", 1, 24) + struct.pack("<I", 0)
           + struct.pack("<I", 0) + struct.pack("<i", ppm) + struct.pack("<i", ppm)
           + struct.pack("<I", 0) + struct.pack("<I", 0))
    return b"BM" + struct.pack("<I", 14 + len(dib)) + struct.pack("<I", 0) \
        + struct.pack("<I", 14) + dib


def _pdf(kind: str) -> bytes:
    head = b"%PDF-1.4\n"
    if kind == "vector":
        return head + b"/Type /Page\n/Font /F1\nBT ET\n"
    if kind == "scanned":
        return head + b"/Type /Page\n/Subtype /Image\n/DCTDecode\n"
    if kind == "mixed":
        return head + b"/Type /Page\n/Font /F1\n/Subtype /Image\n/CCITTFaxDecode\n"
    return head + b"nothing recognizable\n"


def write(tmp_path, name: str, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---- 容器识别 --------------------------------------------------------------

@pytest.mark.parametrize("builder,name,container", [
    (_png, "a.png", CI.Container.PNG),
    (_jpeg, "a.jpg", CI.Container.JPEG),
    (_tiff, "a.tif", CI.Container.TIFF),
    (_bmp, "a.bmp", CI.Container.BMP),
])
def test_container_detection(tmp_path, builder, name, container):
    p = write(tmp_path, name, builder())
    assert CI.classify_file(p)["container"] == container


def test_webp_and_unknown(tmp_path):
    p = write(tmp_path, "a.webp", b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 ")
    assert CI.classify_file(p)["container"] == CI.Container.WEBP
    q = write(tmp_path, "a.bin", b"totally-not-a-known-format")
    assert CI.classify_file(q)["container"] == CI.Container.UNKNOWN


# ---- 尺寸 / DPI ------------------------------------------------------------

def test_png_size_and_dpi(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.png", _png(100, 200, 11811)))
    assert (r["width"], r["height"]) == (100, 200)
    assert r["dpi"] == round(11811 * 0.0254, 2)  # ≈300 dpi
    assert r["dpi"] and 299 < r["dpi"] < 301


def test_jpeg_size_and_dpi(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.jpg", _jpeg(80, 60, 1, 300)))
    assert (r["width"], r["height"]) == (80, 60)
    assert r["dpi"] == 300.0


def test_jpeg_dots_per_cm(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.jpg", _jpeg(80, 60, 2, 118)))
    assert r["dpi"] == round(118 * 2.54, 2)


def test_tiff_size_and_dpi(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.tif", _tiff(50, 70, 300)))
    assert (r["width"], r["height"]) == (50, 70)
    assert r["dpi"] == 300.0


def test_bmp_size_and_dpi(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.bmp", _bmp(40, 30, 11811)))
    assert (r["width"], r["height"]) == (40, 30)
    assert r["dpi"] and 299 < r["dpi"] < 301


# ---- PDF 子型 --------------------------------------------------------------

def test_pdf_vector_no_omr(tmp_path):
    r = CI.classify_file(write(tmp_path, "v.pdf", _pdf("vector")))
    assert r["subtype"] == CI.SubType.VECTOR_PDF
    assert r["omr_required"] is False


def test_pdf_scanned_requires_omr(tmp_path):
    r = CI.classify_file(write(tmp_path, "s.pdf", _pdf("scanned")))
    assert r["subtype"] == CI.SubType.SCANNED_PDF
    assert r["omr_required"] is True


def test_pdf_mixed_treated_as_scanned(tmp_path):
    r = CI.classify_file(write(tmp_path, "m.pdf", _pdf("mixed")))
    assert r["subtype"] == CI.SubType.SCANNED_PDF
    assert r["omr_required"] is True


def test_pdf_unknown_is_honest(tmp_path):
    r = CI.classify_file(write(tmp_path, "u.pdf", _pdf("other")))
    assert r["subtype"] == CI.SubType.UNKNOWN
    assert r["omr_required"] is False
    assert "人工" in r["reason"]


# ---- 位图 → OMR 路径，手写永不自动判定 ------------------------------------

def test_raster_requires_omr_and_handwriting_not_guessed(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.png", _png()))
    assert r["subtype"] == CI.SubType.RASTER
    assert r["omr_required"] is True
    assert r["handwriting"] == "requires_human"  # P5：禁自动估计


def test_unsupported_container_not_omr(tmp_path):
    r = CI.classify_file(write(tmp_path, "a.bin", b"nope"))
    assert r["omr_required"] is False
    assert "人工" in r["reason"]


# ---- sha256 / 目录 / 错误 --------------------------------------------------

def test_sha256_and_determinism(tmp_path):
    p = write(tmp_path, "a.png", _png())
    a, b = CI.classify_file(p), CI.classify_file(p)
    assert a == b and len(a["sha256"]) == 64


def test_classify_dir_skips_gitkeep(tmp_path):
    write(tmp_path, "a.png", _png())
    write(tmp_path, ".gitkeep", b"")
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "b.jpg", _jpeg())
    rep = CI.classify_dir(tmp_path)
    assert rep["count"] == 2


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        CI.classify_file(tmp_path / "nope.png")


def test_empty_file_raises(tmp_path):
    with pytest.raises(ValueError):
        CI.classify_file(write(tmp_path, "empty.png", b""))


def test_dir_of_file_raises(tmp_path):
    p = write(tmp_path, "a.png", _png())
    with pytest.raises(ValueError):
        CI.classify_dir(p)


# ---- CLI -------------------------------------------------------------------

def _cli(argv, cwd):
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    from music_rebuild_project.tools.omr.tests.conftest import REPO_ROOT
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "music_rebuild_project.tools.omr.classify_input", *argv],
        capture_output=True, text=True, env=env, cwd=str(cwd))


def test_cli_file_exit0(tmp_path):
    write(tmp_path, "a.png", _png())
    p = _cli([str(tmp_path / "a.png"), "--out", str(tmp_path / "o.json")], tmp_path)
    assert p.returncode == 0, p.stderr
    rep = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert rep["container"] == "png"


def test_cli_min_dpi_warns(tmp_path):
    write(tmp_path, "a.png", _png(10, 10, 3937))  # ≈100 dpi
    p = _cli([str(tmp_path / "a.png"), "--min-dpi", "300"], tmp_path)
    assert p.returncode == 0
    assert "dpi_warning" in p.stdout


def test_cli_missing_path_exit2(tmp_path):
    p = _cli([str(tmp_path / "nope.png")], tmp_path)
    assert p.returncode == 2


def test_no_random_source():
    from music_rebuild_project.tools.omr.tests.conftest import REPO_ROOT
    src = (REPO_ROOT / "music_rebuild_project/tools/omr/classify_input.py").read_text(
        encoding="utf-8")
    for banned in ("import random", "import numpy", "uuid.uuid4", "time.time()"):
        assert banned not in src, f"classify_input 不应包含 {banned}"
