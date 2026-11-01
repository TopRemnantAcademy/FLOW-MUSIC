"""악보 → 음악 변환 계층 단위 테스트 (OMR 불필요, 로컬 검증 가능).

검증 범위:
- MusicXML 파싱 (제목/키/박자/tempo/음정/가사 추출)
- 프롬프트 변환 (가사/스타일/컨텍스트 조립)
- 디스패처 분기 (MusicXML 직접 / 이미지-OMR unavailable / 미지원 포맷 400)
- 연주곡(instrumental) 추론: 가사가 없으면 instrumental 스타일
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.score import (
    OMRUnavailable,
    ScoreConversionError,
    UnsupportedScoreFormat,
    score_to_prompt,
)
from app.score import musicxml as mxl_mod
from app.score import omr as omr_mod

_SAMPLE_MXL = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>테스트 곡</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>Voice</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>4</divisions>
        <key><fifths>2</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <sound tempo="120"/>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>quarter</type><lyric number="1"><text>안</text></lyric></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration><type>quarter</type><lyric number="1"><text>녕</text></lyric></note>
      <note><pitch><step>E</step><alter>1</alter><octave>4</octave></pitch><duration>4</duration><type>quarter</type><lyric number="1"><text>하</text></lyric></note>
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><type>quarter</type><lyric number="1"><text>세</text></lyric></note>
    </measure>
  </part>
</score-partwise>
"""

_INSTRUMENTAL_MXL = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions><time><beats>3</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>G</step><octave>3</octave></pitch><duration>4</duration></note>
      <note><pitch><step>A</step><octave>3</octave></pitch><duration>4</duration></note>
    </measure>
  </part>
</score-partwise>
"""


def test_parse_musicxml_extracts_metadata_and_melody():
    score = mxl_mod.parse_musicxml(_SAMPLE_MXL.encode("utf-8"))
    assert score.title == "테스트 곡"
    assert score.parts == 1
    assert score.note_count == 4
    assert score.note_names == ["C4", "D4", "E#4", "F4"]
    assert score.lyric_tokens == ["안", "녕", "하", "세"]
    assert score.has_lyric is True
    assert score.key_fifths == 2
    assert score.beats == 4 and score.beat_type == 4
    assert score.tempo_bpm == 120.0


def test_convert_to_prompt_with_lyric():
    score = mxl_mod.parse_musicxml(_SAMPLE_MXL.encode("utf-8"))
    sp = mxl_mod.convert_to_prompt(score)
    assert sp.lyric == "안 녕 하 세"
    assert sp.style == "보컬 独唱"  # 단일 파트 + 가사 있음
    assert sp.title == "테스트 곡"
    assert "조성: D" in sp.prompt          # fifths=2 → D 장조
    assert "박자: 4/4" in sp.prompt
    assert "템포: 120 BPM" in sp.prompt
    assert "멜로디 참고(원조 음정): C4 D4 E#4 F4" in sp.prompt


def test_convert_to_prompt_instrumental_when_no_lyric():
    score = mxl_mod.parse_musicxml(_INSTRUMENTAL_MXL.encode("utf-8"))
    assert score.has_lyric is False
    sp = mxl_mod.convert_to_prompt(score)
    assert sp.lyric == ""
    assert sp.style == "연주곡 instrumental"
    assert "박자: 3/4" in sp.prompt


def test_dispatcher_musicxml_direct():
    sp = score_to_prompt(_SAMPLE_MXL.encode("utf-8"), "song.musicxml")
    assert sp.lyric == "안 녕 하 세"
    assert "조성: D" in sp.prompt


def _make_mxl_zip(xml_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("score.musicxml", xml_text)
    return buf.getvalue()


def test_parse_mxl_zip_container():
    """실제 악보는 대부분 .mxl(ZIP) 형태. ZIP 해제 후 정상 파싱되어야 함."""
    zipped = _make_mxl_zip(_SAMPLE_MXL)
    assert zipped[:4] == b"PK\x03\x04"
    score = mxl_mod.parse_musicxml(zipped)
    assert score.title == "테스트 곡"
    assert score.note_names == ["C4", "D4", "E#4", "F4"]
    assert score.lyric_tokens == ["안", "녕", "하", "세"]


def test_dispatcher_mxl_zip():
    sp = score_to_prompt(_make_mxl_zip(_SAMPLE_MXL), "song.mxl")
    assert sp.lyric == "안 녕 하 세"
    assert "조성: D" in sp.prompt


def test_timewise_rejected():
    """score-timewise 는 미지원 → 명확한 ValueError."""
    tw = b'<?xml version="1.0"?><score-timewise><measure/></score-timewise>'
    with pytest.raises(ValueError):
        mxl_mod.parse_musicxml(tw)


def test_dispatcher_rejects_unsupported_format():
    with pytest.raises(UnsupportedScoreFormat):
        score_to_prompt(b"not a score", "memo.txt")


def test_dispatcher_image_omr_unavailable_by_default(monkeypatch):
    """이미지 업로드는 oemer 미설치/비활성 노드에서 OMRUnavailable → 엔드포인트 501."""
    # settings.enable_omr 가 False 인 기본 상태에서 이미지 경로는 OMR 로 분기 → unavailable
    with pytest.raises(OMRUnavailable):
        score_to_prompt(b"\x89PNG fake", "score.png")


def test_omr_recognize_unavailable_without_config(monkeypatch):
    # 기본 설정(enable_omr=False)에서 recognize_score 는 즉시 OMRUnavailable
    with pytest.raises(OMRUnavailable):
        omr_mod.recognize_score(b"\x89PNG", "score.png")


# ---------------------------------------------------------------------------
# 화성(chord) 추출 — 리드시트 코드 심볼이 빠짐없이 들어가야 함 (정확도 핵심)
# ---------------------------------------------------------------------------
_LEAD_WITH_CHORDS = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>리드시트</work-title></work>
  <part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>4</divisions>
        <key><fifths>3</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
      </attributes>
      <harmony><root><root-step>A</root-step></root><kind>major</kind></harmony>
      <harmony><root><root-step>E</root-step></root><kind>major</kind><bass><bass-step>G</bass-step><bass-alter>1</bass-alter></bass></harmony>
      <note><pitch><step>A</step><octave>4</octave></pitch><duration>4</duration><lyric><text>하</text></lyric></note>
    </measure>
    <measure number="2">
      <harmony><root><root-step>F</root-step><root-alter>1</root-alter></root><kind>minor-seventh</kind></harmony>
      <harmony><root><root-step>B</root-step></root><kind>minor-seventh</kind></harmony>
      <note><pitch><step>B</step><octave>4</octave></pitch><duration>4</duration><lyric><text>늘</text></lyric></note>
    </measure>
  </part>
</score-partwise>
"""


def test_parse_extracts_chord_progression():
    score = mxl_mod.parse_musicxml(_LEAD_WITH_CHORDS.encode("utf-8"))
    assert score.chord_labels == ["A", "E/G#", "F#m7", "Bm7"], score.chord_labels
    # 임시표/슬래시 코드 정확도
    assert "E/G#" in score.chord_labels
    assert "F#m7" in score.chord_labels


def test_convert_includes_chord_progression_in_prompt():
    score = mxl_mod.parse_musicxml(_LEAD_WITH_CHORDS.encode("utf-8"))
    sp = mxl_mod.convert_to_prompt(score)
    assert "화성 진행(chords): A | E/G# | F#m7 | Bm7" in sp.prompt
    assert sp.inferred["chord_count"] == 4


def test_validate_score_flags_empty_melody_as_critical():
    empty = """<?xml version="1.0"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>X</part-name></score-part></part-list>
  <part id="P1"><measure number="1"><attributes><divisions>4</divisions></attributes>
    <note><rest/><duration>4</duration></note></measure></part>
</score-partwise>"""
    score = mxl_mod.parse_musicxml(empty.encode("utf-8"))
    v = mxl_mod.validate_score(score)
    assert not v.ok                      # 멜로디 0개 → critical
    assert v.low_confidence is True
    assert any("멜로디" in c for c in v.critical)


def test_validate_score_ok_for_good_lead_sheet():
    score = mxl_mod.parse_musicxml(_LEAD_WITH_CHORDS.encode("utf-8"))
    v = mxl_mod.validate_score(score)
    assert v.ok is True
    # 템포만 없고(경고 1개) 화성 있음 → low_confidence 아님
    assert v.low_confidence is False


# ---------------------------------------------------------------------------
# OMR 후처리 — 이미지 경로 정확도 보정 (결정적 로직, 로컬 검증 가능)
# ---------------------------------------------------------------------------
from app.score import omr_post as post_mod
from app.score import omr_prep as prep_mod
from app.score.musicxml import ParsedScore


def test_infer_key_from_chords_major():
    # A E/G# F#m7 Bm7 → A 장조 (fifths=3). OMR 이 키를 못 뽑아도 복구.
    assert post_mod.infer_key_from_chords(["A", "E/G#", "F#m7", "Bm7"]) == 3


def test_infer_key_from_chords_ambiguous_returns_none():
    # 1개 코드로는 모호 → 복구 안 함
    assert post_mod.infer_key_from_chords(["C"]) is None


def test_dedup_exact_lyric_repeat():
    assert post_mod.dedup_exact_lyric_repeat(["하", "늘", "하", "늘"]) == ["하", "늘"]
    # 비반복(짝수여도 전반≠후반)은 그대로
    assert post_mod.dedup_exact_lyric_repeat(["하", "늘", "별", "빛"]) == ["하", "늘", "별", "빛"]
    # 홀수는 그대로
    assert post_mod.dedup_exact_lyric_repeat(["하", "늘", "별"]) == ["하", "늘", "별"]


def test_refine_score_recovers_key_and_dedups_lyric():
    score = ParsedScore(
        key_fifths=None,  # OMR 이 키 누락
        chord_labels=["A", "E/G#", "F#m7", "Bm7"],
        lyric_tokens=["하", "늘", "하", "늘"],  # 반복 마디 중복
    )
    post_mod.refine_score(score)
    assert score.key_fifths == 3
    assert score.lyric_tokens == ["하", "늘"]


def test_preprocess_returns_input_on_unusable_image():
    # PIL 부재 노드 또는 깨진 입력 → 원본 그대로 반환(degrade 안전)
    bad = b"\x89PNG not a real image"
    assert prep_mod.preprocess_image(bad) == bad
