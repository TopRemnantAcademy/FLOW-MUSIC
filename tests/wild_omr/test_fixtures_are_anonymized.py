"""野上传 fixture 的**防漂移**断言（脱敏副本被换成原图 / 被改小就红）。

为什么需要：`mock-omr-wild-upload` 的结论依赖 fixture 的两个性质 ——
① 它必须是**脱敏副本**（拿原图进仓库 = 把别人的乐谱原文上传）；
② 它必须**短边不足**（否则 G1 那条 FAIL 断言失去意义，变成"绿灯装饰"）。
两条都容易被"顺手换回原图让测试好过一点"的改动破坏。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from _deps import require  # noqa: E402 - 同目录 helper：裸环境缺包 → skip

_REPO = _HERE.parents[2]
FIXTURES = _HERE.parent / "fixtures"

WILD_IMAGE = FIXTURES / "song01_preprocessed_v001_blur300.jpeg"
WILD_XML = FIXTURES / "wild_song01.musicxml"
WILD_MANIFEST = FIXTURES / "wild_song01.manifest.md"

#: 冻结的指纹（改 fixture 必须同步改这里，并在 PR 里说明原因）
SHA = {
    WILD_IMAGE: "72642cf75f96cf5e983ef7eea540a3e53927499ed886e85f2ee8fed7ac7d2f0b",
    WILD_XML: "ed5e2dfe73b84ff60e2062227c6ec5de9e87526dd4fbc3afd92faba0caf111ab",
    WILD_MANIFEST: "18d6a082f7d23b246e97c7a7af588493cf012dc251814eacd25f64809b4f946c",
}


@pytest.mark.parametrize("path", list(SHA), ids=lambda p: p.name)
def test_fixture_hash_is_frozen(path: Path):
    assert path.is_file(), f"fixture 缺失：{path}"
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    assert got == SHA[path], f"{path.name} 被改动了（{got}）；改 fixture 请同步 SHA 与 PR 说明"


def test_image_is_a_downscaled_anonymized_copy():
    """与原图不同 / 更小 / 半分辨率 —— 三条都成立才算"脱敏副本"。"""
    original = _REPO / "music_rebuild_project" / "02_source" / "song01" / "preprocessed" / \
        "song01_preprocessed_v001.jpeg"
    assert original.is_file(), "对照用的原始页缺失，无法证明脱敏"

    require("PIL", "读图像头需要 Pillow")
    from PIL import Image

    with Image.open(original) as a, Image.open(WILD_IMAGE) as b:
        assert b.size[0] * b.size[1] <= a.size[0] * a.size[1] / 3, "副本必须明显小于原图"
    assert WILD_IMAGE.stat().st_size < original.stat().st_size / 5


def test_no_leak_of_original_in_fixture_metadata():
    """副本的文件头里不得残留原图路径/设备等可追溯字符串。"""
    blob = WILD_IMAGE.read_bytes()
    for needle in (b"piaoy", b"Downloads", b"\\Users", "小小望楼".encode()):
        assert needle not in blob, f"fixture 残留可追溯信息：{needle!r}"
