#!/usr/bin/env python3
"""악보 → 프롬프트 추출 충실도(accuracy) 실측 장치.

정의: "정확도" = 악보(MusicXML) → 추출 메타데이터(조성/박자/템포/멜로디/화성/가사)
그라운드트루스 대비 필드별 일치율. (Mureka 는 프롬프트를 해석하는 생성 모델이라
음표 1:1 재현은 범위 밖 — 따라서 추출 충실도를 측정 대상으로 삼는다.)

측정 불가 경계(정직): 이미지 OMR(oemer) 경로는 이 dev 머신에 oemer 가 없어
실측 불가. 이 장치는 OMR-무관한 MusicXML 파싱/변환 경로의 정확도를 실측하며,
OMR 경로는 배포 노드에서 동일 픽스처로 측정하도록 설계됨(아래 프로토콜 주석 참고).

사용: backend/ 디렉터리에서 `python scripts/score_accuracy.py`
"""
from __future__ import annotations

import io
import sys
import zipfile

sys.path.insert(0, ".")

from app.score.musicxml import parse_musicxml, convert_to_prompt  # noqa: E402

# --- 그라운드트루스 픽스처 -------------------------------------------------
_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>테스트 곡</work-title></work>
  <part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions><key><fifths>2</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <sound tempo="120"/>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><lyric><text>안</text></lyric></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration><lyric><text>녕</text></lyric></note>
      <note><pitch><step>E</step><alter>1</alter><octave>4</octave></pitch><duration>4</duration><lyric><text>하</text></lyric></note>
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration><lyric><text>세</text></lyric></note>
    </measure>
  </part>
</score-partwise>"""

_INSTRUMENTAL = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions><time><beats>3</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>G</step><octave>3</octave></pitch><duration>4</duration></note>
      <note><pitch><step>A</step><octave>3</octave></pitch><duration>4</duration></note>
    </measure>
  </part>
</score-partwise>"""

_LEAD = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>리드시트</work-title></work>
  <part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions><key><fifths>3</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
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
</score-partwise>"""


def _zip(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("score.musicxml", xml)
    return buf.getvalue()


# expected: 각 필드 그라운드트루스. None = "없음이 정답"(추출 결과도 None 이어야 함).
FIXTURES = [
    {
        "name": "메타+멜로디+가사 (D장조 4/4 120)",
        "data": _SAMPLE.encode("utf-8"),
        "exp": {"title": "테스트 곡", "key_fifths": 2, "beats": 4, "beat_type": 4,
                "tempo_bpm": 120.0, "notes": ["C4", "D4", "E#4", "F4"],
                "chords": [], "lyric": ["안", "녕", "하", "세"]},
    },
    {
        "name": "연주곡 (3/4, 가사/화성 없음)",
        "data": _INSTRUMENTAL.encode("utf-8"),
        "exp": {"title": None, "key_fifths": None, "beats": 3, "beat_type": 4,
                "tempo_bpm": None, "notes": ["G3", "A3"], "chords": [], "lyric": []},
    },
    {
        "name": "리드시트+화성 (A E/G# F#m7 Bm7)",
        "data": _LEAD.encode("utf-8"),
        "exp": {"title": "리드시트", "key_fifths": 3, "beats": 4, "beat_type": 4,
                "tempo_bpm": None, "notes": ["A4", "B4"],
                "chords": ["A", "E/G#", "F#m7", "Bm7"], "lyric": ["하", "늘"]},
    },
    {
        "name": ".mxl ZIP 컨테이너 (샘플과 동일)",
        "data": _zip(_SAMPLE),
        "exp": {"title": "테스트 곡", "key_fifths": 2, "beats": 4, "beat_type": 4,
                "tempo_bpm": 120.0, "notes": ["C4", "D4", "E#4", "F4"],
                "chords": [], "lyric": ["안", "녕", "하", "세"]},
    },
]


def _check_field(name: str, got, exp) -> bool:
    ok = got == exp
    mark = "OK " if ok else "FAIL"
    if not ok:
        print(f"    [{mark}] {name}: got={got!r} exp={exp!r}")
    return ok


def main() -> int:
    total = 0
    correct = 0
    for fx in FIXTURES:
        print(f"\n=== {fx['name']} ===")
        score = parse_musicxml(fx["data"])
        sp = convert_to_prompt(score)
        exp = fx["exp"]
        fields = {
            "title": (score.title, exp["title"]),
            "key_fifths": (score.key_fifths, exp["key_fifths"]),
            "time": ((score.beats, score.beat_type), (exp["beats"], exp["beat_type"])),
            "tempo_bpm": (score.tempo_bpm, exp["tempo_bpm"]),
            "notes": (score.note_names, exp["notes"]),
            "chords": (score.chord_labels, exp["chords"]),
            "lyric": (score.lyric_tokens, exp["lyric"]),
        }
        for fname, (got, e) in fields.items():
            total += 1
            if _check_field(fname, got, e):
                correct += 1
        # 추론 스타일도 부수적 확인
        print(f"    style={sp.style!r}")
    acc = (correct / total * 100) if total else 0.0
    print("\n" + "=" * 56)
    print(f"추출 충실도(accuracy): {correct}/{total} 필드 = {acc:.1f}%")
    print("=" * 56)
    # 95% 목표 달성 여부
    if acc >= 95.0:
        print("✅ 목표 95% 달성 (MusicXML 경로, OMR-무관).")
    else:
        print("⚠ 목표 95% 미달 — 파서 보정 필요.")
    return 0 if acc >= 95.0 else 1


if __name__ == "__main__":
    sys.exit(main())
