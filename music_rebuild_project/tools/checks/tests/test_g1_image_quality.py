"""g1_image_quality（#31 G1 图像质量验收门）回归测试。

策略
  不依赖真实扫描件：全部素材在 ``tmp_path`` 里用 stdlib ``struct`` 现场合成
  （确定性，无 RNG），直接复用 ``classify_input`` 的解析口径。断言钉死 G1-1/G1-8/G1-9
  的判定与退出码契约（对齐 `00_docs/gates/gate_G1.md`）：

    dpi≥300 且短边≥2400                 → G1-1 PASS
    dpi∈[200,300) 且短边∈[1800,2400)   → G1-1 NOTE（=PASS_WITH_NOTES）
    dpi<200 或短边不足                  → G1-1 FAIL
    dpi 不可测（无 pHYs/JFIF units=0）  → G1-1 FAIL（不能证明达标）
    矢量 PDF                            → G1-1 SKIP / G1-8 PASS
    扫描 PDF（无 dpi 元数据）           → G1-8 PASS / G1-1 FAIL（dpi 不可测）
    不支持容器 / 未识别子类型            → G1-8 FAIL
    目录无 source_manifest.txt          → G1-9 SKIP
    哈希一致                            → G1-9 PASS
    哈希不一致 / 清单外新增             → G1-9 FAIL

  另有硬约束：本工具**纯 stdlib**，且**不重复实现 classify_input** 的解析。
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]              # music_rebuild_project/tools
_REPO_ROOT = _HERE.parents[4]              # 仓库根
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from checks import g1_image_quality as G          # noqa: E402
from omr import classify_input as CI              # noqa: E402


# ---- 合成素材（与 test_classify_input 同口径，确定性无 RNG） ------------------

def _png(width=100, height=200, ppm_x=11811) -> bytes:
    def chunk(ctype, data):
        return (struct.pack(">I", len(data)) + ctype + data
                + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    phys = struct.pack(">IIB", ppm_x, ppm_x, 1)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"pHYs", phys) + chunk(b"IEND", b""))


def _png_no_dpi(width=2400, height=3000) -> bytes:
    """无 pHYs → dpi 不可测。"""
    def chunk(ctype, data):
        return (struct.pack(">I", len(data)) + ctype + data
                + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _jpeg(width=80, height=60, units=1, density=300) -> bytes:
    app0 = (b"JFIF\x00" + struct.pack(">BB", 1, 1) + struct.pack(">B", units)
            + struct.pack(">HH", density, density) + struct.pack(">BB", 0, 0))
    seg_app0 = b"\xff\xe0" + struct.pack(">H", 2 + len(app0)) + app0
    sof0 = struct.pack(">BHHB", 8, height, width, 3)
    seg_sof0 = b"\xff\xc0" + struct.pack(">H", 2 + len(sof0)) + sof0
    return b"\xff\xd8" + seg_app0 + seg_sof0 + b"\xff\xd9"


def _tiff(width=50, height=70, res=300, unit=2) -> bytes:
    hdr = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    rat_off = 62
    e = b""
    e += struct.pack("<HHI", 256, 3, 1) + struct.pack("<H", width) + b"\x00\x00"
    e += struct.pack("<HHI", 257, 3, 1) + struct.pack("<H", height) + b"\x00\x00"
    e += struct.pack("<HHI", 282, 5, 1) + struct.pack("<I", rat_off)
    e += struct.pack("<HHI", 296, 3, 1) + struct.pack("<H", unit) + b"\x00\x00"
    ifd = struct.pack("<H", 4) + e + struct.pack("<I", 0)
    rational = struct.pack("<II", res, 1)
    assert 8 + len(ifd) == rat_off
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
    return head + b"nothing recognizable\n"


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ---- G1-1 dpi / 短边 --------------------------------------------------------

def test_g1_1_pass(tmp_path):
    p = _write(tmp_path, "p.png", _png(2400, 3000, 11811))  # 300dpi, 短边2400
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-1"]["status"] == "PASS"
    assert r["dpi"] == 300.0


def test_g1_1_note_relaxed_band(tmp_path):
    # dpi≈250 ∈[200,300)，短边 2000 ∈[1800,2400)
    p = _write(tmp_path, "p.png", _png(2000, 2000, 9843))
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-1"]["status"] == "NOTE"


def test_g1_1_fail_low_dpi(tmp_path):
    p = _write(tmp_path, "p.png", _png(1000, 1000, 3937))  # 100dpi
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-1"]["status"] == "FAIL"


def test_g1_1_fail_dpi_unmeasurable(tmp_path):
    # 无 pHYs → dpi 不可测 → 不能证明达标 → FAIL
    p = _write(tmp_path, "p.png", _png_no_dpi(2400, 3000))
    r = G.evaluate_path(p, G.Config())
    assert r["dpi"] is None
    assert r["checks"]["G1-1"]["status"] == "FAIL"


def test_g1_1_fail_jpeg_units0_unmeasurable(tmp_path):
    p = _write(tmp_path, "j.jpg", _jpeg(2400, 3000, units=0, density=300))
    r = G.evaluate_path(p, G.Config())
    assert r["dpi"] is None
    assert r["checks"]["G1-1"]["status"] == "FAIL"


def test_g1_1_pass_jpeg_and_tiff_and_bmp(tmp_path):
    for name, data in [
        ("j.jpg", _jpeg(2400, 3000, 1, 300)),
        ("t.tif", _tiff(2400, 3000, 300, 2)),
        ("b.bmp", _bmp(2400, 3000, 11811)),
    ]:
        r = G.evaluate_path(_write(tmp_path, name, data), G.Config())
        assert r["checks"]["G1-1"]["status"] == "PASS", name


# ---- G1-8 分类与准入 --------------------------------------------------------

def test_g1_8_vector_pdf_skip_g1_1_pass(tmp_path):
    p = _write(tmp_path, "v.pdf", _pdf("vector"))
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-8"]["status"] == "PASS"
    assert r["checks"]["G1-1"]["status"] == "SKIP"  # 矢量不进 OMR


def test_g1_8_scanned_pdf_g1_1_fail_dpi(tmp_path):
    p = _write(tmp_path, "s.pdf", _pdf("scanned"))
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-8"]["status"] == "PASS"  # 子类型可准入
    assert r["checks"]["G1-1"]["status"] == "FAIL"  # 但 PDF 无 dpi 元数据


def test_g1_8_fail_unsupported_container(tmp_path):
    p = _write(tmp_path, "x.bin", b"nope")
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-8"]["status"] == "FAIL"
    assert r["container"] == "unknown"


def test_g1_8_fail_unknown_subtype(tmp_path):
    p = _write(tmp_path, "u.pdf", _pdf("other"))
    r = G.evaluate_path(p, G.Config())
    assert r["checks"]["G1-8"]["status"] == "FAIL"


# ---- G1-9 原始只读哈希 ------------------------------------------------------

def _manifest_for(directory: Path) -> str:
    lines = []
    for f in sorted(directory.rglob("*")):
        if f.is_file() and f.name != "source_manifest.txt":
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            lines.append("%s  %s" % (sha, f.name))
    return "\n".join(lines) + "\n"


def test_g1_9_skip_without_manifest(tmp_path):
    _write(tmp_path, "p.png", _png(2400, 3000, 11811))
    rep = G.evaluate_dir(tmp_path, G.Config())
    assert rep["G1-9"]["status"] == "SKIP"


def test_g1_9_pass_with_manifest(tmp_path):
    _write(tmp_path, "p.png", _png(2400, 3000, 11811))
    _write(tmp_path, "q.jpg", _jpeg(2400, 3000, 1, 300))
    (tmp_path / "source_manifest.txt").write_text(_manifest_for(tmp_path), encoding="utf-8")
    rep = G.evaluate_dir(tmp_path, G.Config())
    assert rep["G1-9"]["status"] == "PASS"


def test_g1_9_fail_tampered(tmp_path):
    _write(tmp_path, "p.png", _png(2400, 3000, 11811))
    (tmp_path / "source_manifest.txt").write_text(
        "0" * 64 + "  p.png\n", encoding="utf-8")
    rep = G.evaluate_dir(tmp_path, G.Config())
    assert rep["G1-9"]["status"] == "FAIL"


# ---- 目录级结论与退出码 -----------------------------------------------------

def test_verdict_pass_when_no_fail(tmp_path):
    _write(tmp_path, "p.png", _png(2400, 3000, 11811))
    _write(tmp_path, "v.pdf", _pdf("vector"))  # G1-1 SKIP, G1-8 PASS
    rep = G.evaluate_dir(tmp_path, G.Config())
    assert G.gate_verdict(rep) == "PASS"


def test_verdict_fail_when_low_quality(tmp_path):
    _write(tmp_path, "p.png", _png(1000, 1000, 3937))  # G1-1 FAIL
    rep = G.evaluate_dir(tmp_path, G.Config())
    assert G.gate_verdict(rep) == "FAIL"


def test_cli_exit_codes(tmp_path):
    pass_img = _write(tmp_path, "p.png", _png(2400, 3000, 11811))
    fail_img = _write(tmp_path, "f.png", _png(1000, 1000, 3937))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # PASS 文件 → 退出码 0
    ok = subprocess.run([sys.executable, "-m", "music_rebuild_project.tools.checks.g1_image_quality",
                         str(pass_img), "--json"], capture_output=True, text=True,
                        env=env, cwd=str(_REPO_ROOT))
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["verdict"] == "PASS"
    # FAIL 文件 → 退出码 1
    bad = subprocess.run([sys.executable, "-m", "music_rebuild_project.tools.checks.g1_image_quality",
                          str(fail_img), "--json"], capture_output=True, text=True,
                         env=env, cwd=str(_REPO_ROOT))
    assert bad.returncode == 1, bad.stderr
    # 缺文件 → 退出码 2
    miss = subprocess.run([sys.executable, "-m", "music_rebuild_project.tools.checks.g1_image_quality",
                           str(tmp_path / "nope.png")], capture_output=True, text=True,
                          env=env, cwd=str(_REPO_ROOT))
    assert miss.returncode == 2


def test_no_third_party_imports():
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import checks.g1_image_quality;"
        "bad = [m for m in sys.modules if m.split('.')[0] in {'numpy','scipy','requests'}];"
        "print('BAD:' + ','.join(bad)); sys.exit(1 if bad else 0)"
    ) % str(_TOOLS_DIR)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                        timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
