"""野上传 · 断言 ③：`score_xml_validate` 在**没有拍号来源**时不得静默报 PASS。

被测事实
--------
oemer 0.1.8 实测输出里 `<time>` 计数 = 0。没有拍号，「每小节音长合计 = 拍号时长」
这条校验**根本没跑**。此时合理的结论是 `PASS_WITH_NOTES`（并如实报覆盖 0/N）；
**`PASS` 是不可接受的** —— 那等于宣称校验通过、实际一项没验。

护栏三道：
  1. 有 manifest 声明 4/4 → 校验真的跑满（覆盖 4/4），错误输入能被抓出来；
  2. 无任何来源 → `PASS_WITH_NOTES`、覆盖 0/N、并写明"未执行"；
  3. 外加一条静态断言：该脚本**没有**在覆盖为 0 时返回 PASS 的分支。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_TOOLS = _REPO / "music_rebuild_project" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

FIXTURES = _HERE.parent / "fixtures"
WILD_XML = FIXTURES / "wild_song01.musicxml"
WILD_MANIFEST = FIXTURES / "wild_song01.manifest.md"

# 依赖在**用例内**跳过：`importorskip` 放模块级会让「没装 music21」的环境**收集期就报错**，
# 而不是 skip（本包不挂 PR/push，但本地/裸容器里跑起来也别炸）。
def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except ImportError:      # 上游包内部 import 失败时 find_spec 会抛，别让它把收集期炸掉
        return False


HAS_MUSIC21 = _has("music21")
needs_music21 = pytest.mark.skipif(not HAS_MUSIC21, reason="Structure 校验依赖 music21（omr-qa 栈）")


def _validate(**kw):
    from checks.score_xml_validate import validate
    return validate(WILD_XML, **kw)


def test_fixture_has_no_time_signature():
    """fixture 得真的是 oemer 那种「结构可解析但 <time> 缺失」的形状。"""
    text = WILD_XML.read_text(encoding="utf-8")
    body = text.split("-->", 1)[-1]          # 注释里写了「无 <time>」，别把注释当内容
    assert "<time" not in body
    assert body.count("<measure ") == 4


@needs_music21
def test_no_source_is_pass_with_notes_not_pass():
    rep = _validate(engines=())          # 指定空引擎：演示「OCR 也不可用」的野上传现场
    assert rep["verdict"] == "PASS_WITH_NOTES", f"无来源时不得报 {rep['verdict']}（静默 PASS）"
    assert rep["ok"] is True             # 结构本身没毛病：不 FAIL，只是**没验完**
    assert rep["duration_checks_run"] == 0
    assert rep["duration_checks_total"] == 4
    assert rep["time_signature"]["source"] == "none"
    assert any("未执行" in n for n in rep["notes"]), f"必须写明哪项没执行：{rep['notes']}"


@needs_music21
def test_declared_meter_makes_checks_run():
    """有声明来源 → 校验真的跑满；结论上限放开到 PASS（来源可信）。

    注意：本 fixture 是「谱面缺 `<time>`」形态，工具会**如实**留一条 note
    （"音长校验使用外部来源拍号"）→ 结论仍是 PASS_WITH_NOTES。这是设计意图：
    降级通过要留痕，不是静默 PASS。判 PASS 需要谱面自带 `<time>`。
    """
    rep = _validate(manifest_path=WILD_MANIFEST, engines=())
    assert rep["time_signature"]["source"] == "declared"
    assert rep["time_signature"]["value"] == "4/4"
    assert rep["time_signature"]["verdict_cap"] == "PASS", "声明来源才有资格放开上限"
    assert rep["duration_checks_run"] == 4, "有声明来源时，4 小节必须全比过"
    assert rep["duration_checks_total"] == 4
    assert rep["verdict"] == "PASS_WITH_NOTES"
    assert any("外部来源" in n for n in rep["notes"]), "用外部拍号必须留痕"


@needs_music21
def test_time_signature_mismatch_still_fails(tmp_path: Path):
    """反向验证：光「不报 PASS」不够 —— 真错了必须 FAIL，否则校验等于没装。"""
    xml = WILD_XML.read_text(encoding="utf-8")
    # 第 2 小节砍成 2 拍（whole → half），4/4 下必然对不上
    xml = xml.replace(
        '<measure number="2">\n      <note><pitch><step>D</step><octave>4</octave></pitch>'
        '<duration>16</duration><type>whole</type>',
        '<measure number="2">\n      <note><pitch><step>D</step><octave>4</octave></pitch>'
        '<duration>8</duration><type>half</type>')
    broken = tmp_path / "broken.musicxml"
    broken.write_text(xml, encoding="utf-8")

    from checks.score_xml_validate import validate
    rep = validate(broken, manifest_path=WILD_MANIFEST, engines=())
    assert rep["verdict"] == "FAIL", f"音长不符必须 FAIL：{rep}"
    assert rep["ok"] is False
    assert any("第2小节" in p for p in rep["problems"])


@needs_music21
def test_cli_exit_codes_are_honest():
    """退出码层也不许说谎：0=结构过（含 NOTE）、1=有结构问题、2=解析不了。"""
    tool = _TOOLS / "checks" / "score_xml_validate.py"
    code = (
        "import runpy,sys;"
        "sys.argv=['score_xml_validate.py', %r, '--json'];"
        "runpy.run_path(%r, run_name='__main__')" % (str(WILD_XML), str(tool))
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"无来源但结构 OK 应为 0（PASS_WITH_NOTES 不属失败）：{r.stderr[-400:]}"
    assert '"PASS_WITH_NOTES"' in r.stdout or "PASS_WITH_NOTES" in r.stdout


def test_script_never_passes_with_zero_coverage():
    """静态断言：判定逻辑里没有「覆盖=0 却给 PASS」的路。"""
    src = (_TOOLS / "checks" / "score_xml_validate.py").read_text(encoding="utf-8")
    assert 'if not problems and external.source == "none"' in src or "verdict_cap" in src
    # 关键：无来源时 notes 非空 → 落到 PASS_WITH_NOTES，这条链必须在源码里看得见
    assert 'elif notes:' in src and 'verdict = "PASS_WITH_NOTES"' in src
