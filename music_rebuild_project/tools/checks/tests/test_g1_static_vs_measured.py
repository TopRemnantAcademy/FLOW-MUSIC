"""G1-1（静态：dpi + 像素短边）vs G1-1L（实测：五线谱线距）一致性标定 —— issue #170。

两条判据判同一件事（「这张图的分辨率够不够 OMR 用」），证据却来自两个世界：

    G1-1   静态代理  ``g1_image_quality.check_g1_1``  容器 dpi 元数据 + 像素短边
    G1-1L  实测线距  ``g1_auto.verdict_line_space``   OMR/CV 量出的五线谱线距

本文件只做**标定**：语料逐张跑 ``evaluate_path(path, cfg, auto=True)``，把两侧状态摆在一张
表上，看它们在哪些语料上同意、在哪些语料上打架。不碰任何阈值，也不替打架的那些下结论。

三条语料，三种该有的结局
    ① song01 预处理件（2480×3508，dpi=300）→ 两侧皆 PASS（无假阴性）
    ② 真糊图（线距 <6px）                  → 两侧皆 FAIL（无假阳性）
    ③ 矛盾 case（静态 FAIL / 实测 PASS）   → 进 ``CONTRADICTION`` 清单，**交人工**

变红的条件只有两条
    - 真值断言不符：好图被挡（假阴性）、坏图被放（假阳性）；
    - 已知矛盾 case 没有被识别成矛盾（被静默判成 PASS/FAIL）。

矛盾 case 数量 > 0 **不**变红 —— 那是已知待裁事实，不是回归。

依赖分层
    纯裁决层（一致性分类 / 真值语义 / 语料契约）零依赖，哪儿都能跑；
    需要 cv2 的实测用例在无 cv2 的环境（如 fast gate）自动 skip ——
    「本环境测不了」≠「测错了」。它们真跑的地方是 api_trigger：

        python -m pytest music_rebuild_project/tools/checks/tests/test_g1_static_vs_measured.py -q
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]                 # music_rebuild_project/tools
_PROJECT = _HERE.parents[3]                   # music_rebuild_project/
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import g1_auto as A               # noqa: E402
from checks import g1_image_quality as Q      # noqa: E402

SONG01_PREPROCESSED = (_PROJECT / "02_source" / "song01" / "preprocessed"
                       / "song01_preprocessed_v001.jpeg")
SONG01_ORIGINAL = (_PROJECT / "02_source" / "song01" / "originals"
                   / "song01_original_v001.jpeg")

# 本会话上传图：不在仓库里，只认实体文件（环境变量 / 约定路径）。
UPLOAD_ENV = "G1_CONSISTENCY_UPLOAD"
UPLOAD_TRIED = [
    Path("test_uploads/小小望楼_upload.jpg"),
    _PROJECT / "02_source" / "upload" / "小小望楼_upload.jpg",
]

# 实测引擎顺序：只用无模型 CV（oemer 缺失时它自己在 errors 里降级，不会伪装成 PASS）。
# 本文件只用 ``cv``：标定的是"判据一致性"，不该被模型下载/网络带进不确定性。
ENGINES = ("cv",)


# ================================================================ 纯裁决层（零依赖）
# 一致性四分类。SKIP 单独成一类：引擎没说话时既不算同意，也不算打架。
USABLE = {"PASS", "NOTE"}
BLOCKED = {"FAIL"}

DIRECTION = {
    ("FAIL", "PASS"): "静态 FAIL / 实测 PASS（静态假阴性 → 误挡可用图）",
    ("FAIL", "NOTE"): "静态 FAIL / 实测 NOTE（静态假阴性；实测亦仅放宽档 → 双证据都不足）",
    ("PASS", "FAIL"): "静态 PASS / 实测 FAIL（静态假阳性 → 放行不可用图，更危险）",
    ("NOTE", "FAIL"): "静态 NOTE / 实测 FAIL（放宽档被实测否决 → 以实测为准）",
}


def pair_status(static: str, measured: str) -> str:
    """两侧状态 → AGREE / CONTRADICTION / BAND_DIFF / INDETERMINATE。"""
    if static == "SKIP" or measured == "SKIP":
        return "INDETERMINATE"
    if static in BLOCKED and measured in USABLE:
        return "CONTRADICTION"
    if static in USABLE and measured in BLOCKED:
        return "CONTRADICTION"
    if static in BLOCKED and measured in BLOCKED:
        return "AGREE"
    if static in USABLE and measured in USABLE:
        return "AGREE" if static == measured else "BAND_DIFF"
    return "INDETERMINATE"


def direction(static: str, measured: str) -> str:
    return DIRECTION.get((static, measured), f"未知方向 {static}/{measured}")


def classify(row: dict) -> str | None:
    """一行结果的归类（``None`` = 语料缺失，未参与判定）。"""
    if row.get("missing"):
        return None
    return pair_status(row["static"], row["measured"])


def truth_ok(row: dict) -> bool:
    """真值断言语义。``expected`` 为 None 的语料永不参与断言。"""
    exp = row.get("expected")
    if exp is None:
        return True
    st, ms = row["static"], row["measured"]
    if exp == "agree_pass":
        return st == "PASS" and ms == "PASS"
    if exp == "agree_fail":
        # 「真坏图」的失败有两种形态，都算两侧都认它不行：
        #   ① 实测 FAIL（线距量得到，但 <6px）；
        #   ② 实测 SKIP（糊到分不开，引擎弃权 —— 绝不冒充 PASS）。
        return st == "FAIL" and ms in ("FAIL", "SKIP")
    if exp == "contradiction":
        return pair_status(st, ms) == "CONTRADICTION"
    raise AssertionError(f"未定义的真值期望：{exp!r}")


class TestPairStatus:
    """一致性分类：只归类，不做"折中判断"。"""

    @pytest.mark.parametrize("s,m", [("PASS", "PASS"), ("FAIL", "FAIL"), ("NOTE", "NOTE")])
    def test_agree(self, s, m):
        assert pair_status(s, m) == "AGREE"

    @pytest.mark.parametrize("s,m", [("FAIL", "PASS"), ("FAIL", "NOTE"),
                                     ("PASS", "FAIL"), ("NOTE", "FAIL")])
    def test_contradiction_when_one_side_blocks(self, s, m):
        """一侧判可用、另一侧判不可用 → 必须单列，不得被"从严"或"择优"吞掉。"""
        assert pair_status(s, m) == "CONTRADICTION"

    @pytest.mark.parametrize("s,m", [("PASS", "NOTE"), ("NOTE", "PASS")])
    def test_band_diff_is_not_contradiction(self, s, m):
        assert pair_status(s, m) == "BAND_DIFF"

    @pytest.mark.parametrize("s,m", [("SKIP", "PASS"), ("PASS", "SKIP"),
                                     ("SKIP", "FAIL"), ("SKIP", "SKIP")])
    def test_skip_never_pretends(self, s, m):
        """引擎没说话 ≠ 同意，也 ≠ 打架。"""
        assert pair_status(s, m) == "INDETERMINATE"

    def test_direction_distinguishes_false_negative_from_false_positive(self):
        assert "假阴性" in direction("FAIL", "PASS")
        assert "假阳性" in direction("PASS", "FAIL")
        assert direction("PASS", "PASS").startswith("未知方向")


class TestTruthSemantics:
    """真值断言的语义 —— 这是"会变红"的那条线本身。"""

    def _row(self, static, measured, expected="agree_fail"):
        return {"static": static, "measured": measured, "expected": expected}

    def test_good_image_must_pass_both_sides(self):
        assert truth_ok(self._row("PASS", "PASS", "agree_pass"))
        # 好图被静态挡下（假阴性）→ 断言不符
        assert not truth_ok(self._row("FAIL", "PASS", "agree_pass"))
        # 好图实测测不到 → 不满足"实测 PASS"
        assert not truth_ok(self._row("PASS", "SKIP", "agree_pass"))

    def test_bad_image_fail_or_skip_both_count_as_blocked(self):
        assert truth_ok(self._row("FAIL", "FAIL"))
        assert truth_ok(self._row("FAIL", "SKIP"))

    def test_bad_image_admitted_is_false_positive(self):
        """真糊图被实测放行 → 假阳性，必须判为断言不符。"""
        assert not truth_ok(self._row("FAIL", "PASS"))
        assert not truth_ok(self._row("FAIL", "NOTE"))

    def test_contradiction_must_stay_contradiction(self):
        assert truth_ok(self._row("FAIL", "PASS", "contradiction"))
        # 被"和稀泥"成一致 → 断言不符（这条就是防误判）
        assert not truth_ok(self._row("FAIL", "FAIL", "contradiction"))

    def test_no_expectation_never_asserts(self):
        assert truth_ok(self._row("PASS", "FAIL", None))

    def test_unknown_expectation_is_a_bug_not_a_pass(self):
        with pytest.raises(AssertionError):
            truth_ok(self._row("PASS", "PASS", "agree_maybe"))


# ================================================================ 语料（合成器 + 真值）
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def synth_staff_png(width: int = 1240, height: int = 1754, *, spacing: int = 12,
                    systems: int = 3, margin: int = 60, line_width: int = 2) -> bytes:
    """确定性合成一张"五线谱骨架"灰度 PNG（纯 stdlib，无 dpi 元数据）。

    只画水平谱线 + 组内小节线：足够让 CV 量线距，不假装是完整乐谱。
    刻意不写 ``pHYs`` —— 要测的就是「无 dpi」这一类输入。
    """
    px = bytearray(b"\xff" * (width * height))
    for s in range(systems):
        top = margin + s * (4 * spacing + 3 * margin)
        for i in range(5):
            y = top + i * spacing
            for dy in range(line_width):
                if y + dy >= height:
                    continue
                row = (y + dy) * width
                px[row + margin:row + width - margin] = b"\x00" * (width - 2 * margin)
        for bx in range(margin, width - margin, max(1, spacing * 8)):
            for y in range(top, min(height, top + 4 * spacing + 1)):
                px[y * width + bx] = 0
    raw = b"".join(b"\x00" + bytes(px[y * width:(y + 1) * width]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


def materialise(item: dict, work_dir: Path) -> Path | None:
    """语料落盘。缺失就返回 None —— **不拿合成图冒充真图**。"""
    kind = item["kind"]
    if kind == "repo":
        return item["path"] if Path(item["path"]).is_file() else None
    if kind == "synth":
        out = work_dir / f"{item['id']}.png"
        out.write_bytes(synth_staff_png(**item["params"]))
        return out
    if kind == "external":
        import os
        env = os.environ.get(item.get("env_var", ""), "").strip()
        for cand in ([Path(env)] if env else []) + [Path(p) for p in item["tried_paths"]]:
            if Path(cand).is_file():
                return Path(cand)
        return None
    raise ValueError(f"未知语料类型：{kind}")


# expected:
#   "agree_pass"    好图：两侧皆 PASS
#   "agree_fail"    真坏图：两侧皆不放行（实测 FAIL 或 SKIP）
#   "contradiction" 已知"静态 FAIL / 实测 PASS"：必须被单列，不得被静默判成一致
#   None            无真值：只记录，不断言
CORPUS: list[dict] = [
    {
        "id": "song01_preprocessed",
        "kind": "repo",
        "label": "song01 预处理件（2480×3508，dpi=300）",
        "path": SONG01_PREPROCESSED,
        "expected": "agree_pass",
    },
    {
        # issue 里那张上传图的等价物：同像素、只差 dpi 元数据。上传图本身不入库，
        # 这里用它坐实"静态假阴性"这条事实（线距 18px 达标，静态却因缺 dpi 判 FAIL）。
        "id": "song01_original",
        "kind": "repo",
        "label": "song01 原件（同像素，无 dpi 元数据）",
        "path": SONG01_ORIGINAL,
        "expected": "contradiction",
    },
    {
        # issue 要的真糊图：线距 5px < 6px 放宽线。谱线还分得开、量得到，
        # 所以是"真的坏"（FAIL），而不是"量不到"（SKIP）—— 二者都必须不放行。
        "id": "blurred_true",
        "kind": "synth",
        "label": "真糊图（线距 5px < 6px：谱线尚可分，但分辨率不够）",
        "params": {"width": 1240, "height": 1754, "spacing": 5, "systems": 7, "margin": 40},
        "expected": "agree_fail",
    },
    {
        "id": "session_upload",
        "kind": "external",
        "label": "本会话上传图（1357×1920，无 dpi）",
        "env_var": UPLOAD_ENV,
        "tried_paths": UPLOAD_TRIED,
        "expected": "contradiction",
    },
]

# 无真值语料只许落在这两种结局：语料缺失（None）与 INDETERMINATE（任一侧 SKIP）。
# 一旦被判成 AGREE / CONTRADICTION / BAND_DIFF，说明工具替人下了结论。
UNDECIDED = {None, "INDETERMINATE"}


class TestCorpusContract:
    """语料表自身的契约：id 唯一、缺失语料不造替身、真值口径受控。"""

    def test_ids_are_unique(self):
        ids = [c["id"] for c in CORPUS]
        assert len(ids) == len(set(ids))

    def test_every_item_declares_a_known_kind(self):
        assert {c["kind"] for c in CORPUS} <= {"repo", "synth", "external"}

    def test_every_item_declares_a_known_truth(self):
        assert {c["expected"] for c in CORPUS} <= {"agree_pass", "agree_fail",
                                                   "contradiction", None}

    def test_issue_three_corpus_classes_are_covered(self):
        """issue 要求的三类语料都必须在场：好图、真糊图、矛盾 case。"""
        assert {"agree_pass", "agree_fail", "contradiction"} <= {c["expected"] for c in CORPUS}

    def test_missing_corpus_is_reported_not_faked(self, tmp_path):
        item = {"id": "ghost", "kind": "external", "tried_paths": [tmp_path / "nope.jpg"],
                "env_var": "G1_TEST_GHOST_UNSET"}
        assert materialise(item, tmp_path) is None
        # 缺失就是缺失：不会在 work_dir 里凭空出现一个同名替身
        assert not (tmp_path / "ghost.png").exists()

    def test_synth_is_deterministic_and_has_no_dpi(self, tmp_path):
        assert synth_staff_png(spacing=12) == synth_staff_png(spacing=12)
        p = tmp_path / "s.png"
        p.write_bytes(synth_staff_png(spacing=12))
        assert Q.classify_file(p)["dpi"] is None       # 刻意不写 pHYs

    def test_song01_preprocessed_is_the_known_good_page(self):
        """好图语料的 dpi/短边必须真的站在 PASS 侧（否则整个标定就失去锚点）。"""
        st, detail = Q.check_g1_1(Q.classify_file(SONG01_PREPROCESSED), Q.Config())
        assert st == "PASS", detail
        assert "短边=2480" in detail


# ================================================================ 端到端（需 cv2）
def require_cv() -> None:
    """本环境能不能做 CV 测量。

    fast gate 只装 pytest（无 cv2/numpy），代理判定会抛 ``EngineUnavailable`` ——
    对生产调用方那是正确降级，对测试只是"测不了"，必须 skip 而不是把门禁拖红。
    用 ``find_spec`` 而非 ``importorskip``：模块不存在时两者都是 skip，但 find_spec
    不会把"存在却导入失败"的环境错误藏成 skip。
    """
    try:
        missing = [m for m in ("cv2", "numpy") if importlib.util.find_spec(m) is None]
        measurable = not missing and _cv_can_measure()
    except ImportError:          # find_spec 触发导入期崩溃（缺 libGL/libxcb 之类）
        measurable = False
    if not measurable:
        pytest.skip("本环境无法做 CV 线距测量（缺 cv2/numpy 或库初始化失败）；"
                    "端到端标定在 g1-static-vs-measured job 跑")


def _cv_can_measure() -> bool:
    """比"装了 cv2"更硬：真的能从一张合成页量出线距。

    有 cv2 但初始化失败（无头容器缺 libGL/libxcb 等）时 import 能过、测量静默返回
    None —— 那同样是"测不了"，不能算"测错了"。
    """
    try:
        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "probe.png"
            probe.write_bytes(synth_staff_png(width=620, height=880, spacing=16, systems=2))
            return A._cv_collect(probe).unit_size_px is not None
    except Exception:
        return False


def _provider(img: Path) -> A.AutoMetrics:
    return A.collect_metrics(img, engines=ENGINES)


def measure(item: dict, path: Path) -> dict:
    """单语料 → 两侧状态。只读调用 ``evaluate_path(..., auto=True)``，不改任何判定。"""
    rep = Q.evaluate_path(path, Q.Config(), auto=True, provider=_provider)
    checks = rep["checks"]
    st1 = checks["G1-1"]
    stl = checks.get("G1-1L", {"status": "SKIP", "detail": "未启用实测判定"})
    return {
        "id": item["id"],
        "label": item["label"],
        "path": str(path),
        "expected": item["expected"],
        "width": rep.get("width"),
        "height": rep.get("height"),
        "short_edge": min(rep.get("width") or 0, rep.get("height") or 0),
        "dpi": rep.get("dpi"),
        "static": st1["status"],
        "measured": stl["status"],
        "static_detail": st1["detail"],
        "measured_detail": stl["detail"],
    }


def _row(rows: list[dict], corpus_id: str) -> dict:
    return next(r for r in rows if r["id"] == corpus_id)


# 只用一个"类内 skip"就能表达"需要 cv2"：不注册自定义 marker，也就没有 UnknownMark 警告，
# 且 skip 发生在用例执行期（fixture 内），fast gate 上是 skip 而不是 ERROR。
class TestEndToEnd:
    """端到端：真语料 + 合成语料，逐张跑 ``evaluate_path(auto=True)``。

    fixture 是**类内**的，不在模块级 —— 模块级 fixture 里 ``pytest.skip()`` 会在 setup
    阶段被 pytest 记成 ERROR（不是 skip），那会让 fast gate 变红。
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def rows(tmp_path_factory):
        """跑一遍全部语料（类级：测量慢，只跑一次）。"""
        require_cv()
        work = tmp_path_factory.mktemp("g1_static_vs_measured")
        out = []
        for item in CORPUS:
            path = materialise(item, work)
            if path is None:
                out.append({"id": item["id"], "label": item["label"],
                            "expected": item["expected"], "missing": True,
                            "static": "SKIP", "measured": "SKIP"})
                continue
            out.append(measure(item, path))
        return out

    def test_song01_preprocessed_is_pass_on_both_sides(self, rows):
        """① 好图：静态 PASS、实测 PASS。任一为 FAIL 就是假阴性。"""
        r = _row(rows, "song01_preprocessed")
        assert r["static"] == "PASS", r["static_detail"]
        assert r["measured"] == "PASS", r["measured_detail"]
        assert classify(r) == "AGREE"

    def test_true_blur_fails_on_both_sides(self, rows):
        """② 真糊图：两侧皆 FAIL（issue 的硬要求，不得拿 SKIP 糊过去）。"""
        r = _row(rows, "blurred_true")
        assert r["static"] == "FAIL", r["static_detail"]
        assert r["measured"] == "FAIL", f"线距 {r['measured_detail']} —— 应量得到但不够"
        assert classify(r) == "AGREE"
        assert truth_ok(r)

    def test_song01_original_is_the_known_contradiction(self, rows):
        """③ 矛盾 case：同像素、只缺 dpi → 静态 FAIL / 实测 PASS。"""
        r = _row(rows, "song01_original")
        assert r["static"] == "FAIL", r["static_detail"]
        assert r["measured"] == "PASS", r["measured_detail"]
        assert classify(r) == "CONTRADICTION"
        assert "假阴性" in direction(r["static"], r["measured"])

    def test_contradictions_are_listed_not_silently_resolved(self, rows):
        """矛盾 case 必须单列进清单，且清单与事实一致（不多不少）。"""
        listed = {r["id"] for r in rows if classify(r) == "CONTRADICTION"}
        assert "song01_original" in listed
        for r in rows:
            if r["id"] in listed:
                assert direction(r["static"], r["measured"]).startswith("静态"), \
                    "矛盾 case 必须能描述方向"

    def test_undecided_corpus_is_never_concluded(self, rows):
        """真值未定的语料只许"不确定"，不许被静默判成 PASS/FAIL。"""
        for r in rows:
            if r.get("missing"):
                assert classify(r) is None
                continue
            if r["expected"] is None:
                assert classify(r) in UNDECIDED, f"{r['id']} 无真值却被归类为 {classify(r)}"

    def test_no_truth_mismatch(self, rows):
        """真值断言全通过 —— 这条就是本测试"会红"的那条线。"""
        bad = [(r["id"], r["expected"], classify(r)) for r in rows
               if not r.get("missing") and not truth_ok(r)]
        assert not bad, f"真值断言不符：{bad}"

    def test_static_side_still_rejects_missing_dpi(self, rows):
        """静态侧本次**未改**：缺 dpi 仍旧 FAIL。放宽要走 gate 评审，不在测试里偷偷放行。"""
        r = _row(rows, "song01_original")
        assert r["dpi"] is None
        assert r["static"] == "FAIL"
        assert "dpi" in r["static_detail"]

    def test_upload_is_measured_if_present_and_missing_is_honest(self, rows):
        """上传图：在仓库里就照实测；不在就记 missing，绝不拿合成图顶替。"""
        r = _row(rows, "session_upload")
        if r.get("missing"):
            pytest.skip("未提供上传图（$G1_CONSISTENCY_UPLOAD / test_uploads/...），如实记 missing")
        assert classify(r) == "CONTRADICTION", f"{r['static']} / {r['measured']}"
