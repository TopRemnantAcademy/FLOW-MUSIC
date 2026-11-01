"""MusicXML(stdlib 전용) → ``ScorePrompt`` 변환. 외부 라이브러리 의존성 0.

MusicXML 는 ``<score-partwise>`` 트리 구조다:
    score-partwise > part > measure > (attributes | note | sound | ...)
    note > (pitch > step/octave/alter | rest) , duration , lyric > text

이 파서는 **멜로디 음정 시퀀스**와 **음표 아래 붙은 가사**를 추출해, 텍스트→음악
엔진(Mureka 등)이 소비할 수 있는 ``prompt``/``lyric``/``style`` 로 환원한다.
음정은 "참고 컨텍스트"로만 쓰이며(엔진이 실제 음정을 따르진 않음), 가사가 실제 싱잉 텍스트다.
"""
from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from app.score import ScorePrompt

__all__ = [
    "parse_musicxml",
    "convert_to_prompt",
    "validate_score",
    "validate_prompt",
    "ScoreValidation",
]


# alter(반음 오프셋, -2..2, 일부 분수) → 악보 임시표 기호
_ALTER_NAMES = {
    0: "",
    1: "#",
    2: "##",
    -1: "b",
    -2: "bb",
    0.5: "#",
    -0.5: "b",
}

# MusicXML <kind> → 리드시트/재즈 표기 접미사. 미매핑은 원문 그대로 폴백.
_KIND_SUFFIX = {
    "major": "",
    "minor": "m",
    "augmented": "aug",
    "diminished": "dim",
    "half-diminished": "m7b5",
    "minor-seventh": "m7",
    "major-seventh": "maj7",
    "dominant": "7",
    "dominant-seventh": "7",
    "minor-major-seventh": "mmaj7",
    "augmented-seventh": "aug7",
    "diminished-seventh": "dim7",
    "minor-sixth": "m6",
    "major-sixth": "6",
    "dominant-ninth": "9",
    "dominant-11th": "11",
    "dominant-13th": "13",
    "major-ninth": "maj9",
    "minor-ninth": "m9",
    "suspended-second": "sus2",
    "suspended-fourth": "sus4",
    "power": "5",
    "other": "",
    "none": "",
}


@dataclass
class ParsedScore:
    title: Optional[str] = None
    parts: int = 0
    note_count: int = 0
    has_lyric: bool = False
    note_names: list[str] = field(default_factory=list)
    lyric_tokens: list[str] = field(default_factory=list)
    chord_labels: list[str] = field(default_factory=list)  # 화성 진행 (리드시트 코드 심볼)
    key_fifths: Optional[int] = None
    beats: Optional[int] = None
    beat_type: Optional[int] = None
    tempo_bpm: Optional[float] = None


@dataclass
class ScoreValidation:
    """파싱 결과의 신뢰성 검증 (OMR fail-closed 게이트).

    - ``critical``: 곡 생성 불가 수준 (멜로디 0개 등) → 422 거부
    - ``warnings``: 손실 가능성 경고 (키/템포/화성 누락 등)
    - ``low_confidence``: critical 있거나 경고가 2개 이상 → OMR 결과 신뢰 불가
    """
    critical: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.critical

    @property
    def low_confidence(self) -> bool:
        return bool(self.critical) or len(self.warnings) >= 2


def _local(tag: str) -> str:
    """네임스페이스 무시하고 로컬 태그명 반환."""
    return tag.split("}")[-1]


def _note_name(step: str, alter: float, octave: int) -> str:
    acc = _ALTER_NAMES.get(alter, "#" if alter > 0 else "b")
    return f"{step}{acc}{octave}"


def _step_name(step: str, alter: float) -> str:
    """코드 root/bass 표기 (예: F + 1 → F# , B + -1 → Bb)."""
    step = (step or "").strip().upper()
    if not step:
        return ""
    return step + _ALTER_NAMES.get(alter, "#" if alter > 0 else "b")


def _chord_name(harmony_el) -> Optional[str]:
    """<harmony> → 'F#m7', 'E/G#' 같은 리드시트 코드 라벨.

    root-step / root-alter / kind / bass-step / bass-alter 를 조합.
    kind 매핑이 없으면 원문 그대로 폴백(예: 'sus2').
    """
    root = harmony_el.find("{*}root")
    kind_el = harmony_el.find("{*}kind")
    bass = harmony_el.find("{*}bass")
    if root is None and kind_el is None:
        return None
    root_step = root.find("{*}root-step") if root is not None else None
    root_alter = root.find("{*}root-alter") if root is not None else None
    if root_step is None or not root_step.text:
        return None  # root 없는 화성(페달톤 등)은 라벨 생략
    alter = 0.0
    if root_alter is not None and root_alter.text:
        try:
            alter = float(root_alter.text)
        except ValueError:
            pass
    kind = (kind_el.text or "major").strip() if kind_el is not None else "major"
    suffix = _KIND_SUFFIX.get(kind, kind if kind else "")
    label = _step_name(root_step.text.strip(), alter) + suffix
    if bass is not None:
        b_step = bass.find("{*}bass-step")
        b_alter = bass.find("{*}bass-alter")
        if b_step is not None and b_step.text:
            ba = 0.0
            if b_alter is not None and b_alter.text:
                try:
                    ba = float(b_alter.text)
                except ValueError:
                    pass
            label += "/" + _step_name(b_step.text.strip(), ba)
    return label


def _extract_mxl(data: bytes) -> bytes:
    """``.mxl``(ZIP) 에서 실제 MusicXML 텍스트를 꺼낸다.

    컨테이너 안에 ``score.musicxml``(또는 다른 .xml/.musicxml)이 있다.
    ZIP 이 아니거나 깨진 경우 원본을 그대로 반환(호출자에서 ET 파싱 에러로 자연스레 실패).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return data
    names = zf.namelist()
    if not names:
        return data
    # score.musicxml 를 최우선, 그 외 .musicxml/.xml 순, 디렉터리 엔트리 제외
    cands = [n for n in names if n.lower().endswith((".musicxml", ".xml")) and not n.endswith("/")]
    if not cands:
        cands = [n for n in names if not n.endswith("/")]
    cands.sort(key=lambda n: (0 if n.lower().endswith("score.musicxml") else 1, n))
    return zf.read(cands[0])


def parse_musicxml(data: bytes) -> ParsedScore:
    # .mxl 은 ZIP 컨테이너(내부에 score.musicxml). 압축 파일이면 해제한다.
    # (MuseScore / Finale / 낭독 소프트 등 대부분의 내보내기가 .mxl 압축 형태)
    if data[:4] == b"PK\x03\x04":
        data = _extract_mxl(data)
    root = ET.fromstring(data)
    if _local(root.tag) == "score-timewise":
        raise ValueError(
            "score-timewise 는 미지원입니다 (score-partwise 만 지원). "
            "내보내기 옵션에서 'Partwise' 로 저장하세요."
        )
    score = ParsedScore()

    # 제목 (work-title > movement-title)
    for tag in ("work-title", "movement-title"):
        el = root.find(f".//{{*}}{tag}")
        if el is not None and el.text and el.text.strip():
            score.title = el.text.strip()
            break

    divisions = 4  # 기본 분할 (duration 단위). attributes 에서 갱신됨.
    part_count = 0
    for part in root.iter():
        if _local(part.tag) != "part":
            continue
        part_count += 1
        for measure in part:
            if _local(measure.tag) != "measure":
                continue
            # divisions / key / time 는 measure 직속 attributes 에서
            attrs = measure.find("{*}attributes")
            if attrs is not None:
                div = attrs.find("{*}divisions")
                if div is not None and div.text:
                    try:
                        divisions = int(div.text)
                    except ValueError:
                        pass
                fifths = attrs.find("{*}key/{*}fifths")
                if fifths is not None and fifths.text:
                    try:
                        score.key_fifths = int(fifths.text)
                    except ValueError:
                        pass
                time = attrs.find("{*}time")
                if time is not None:
                    b = time.find("{*}beats")
                    bt = time.find("{*}beat-type")
                    if b is not None and b.text:
                        try:
                            score.beats = int(b.text)
                        except ValueError:
                            pass
                    if bt is not None and bt.text:
                        try:
                            score.beat_type = int(bt.text)
                        except ValueError:
                            pass
            # tempo 는 measure 내 sound(직속 또는 direction 내부) 어디든
            for snd in measure.findall(".//{*}sound"):
                tempo = snd.get("tempo")
                if tempo:
                    try:
                        score.tempo_bpm = float(tempo)
                    except ValueError:
                        pass
            # 화성(harmony) — 리드시트 코드 심볼(A, E/G#, F#m7, Bm7 …).
            # measure 직속 또는 direction 내부 어디든 문서 순서로 수집.
            # 주의: Element.iter('{*}tag') 와일드카드는 3.13 에서 동작하지 않으므로
            # 무인자 iter() 후 로컬 태그로 필터링한다 (findall 과 동작 차이).
            for elem in measure.iter():
                if _local(elem.tag) != "harmony":
                    continue
                chord = _chord_name(elem)
                if chord:
                    score.chord_labels.append(chord)
            # 음표
            for note in measure.findall("{*}note"):
                if note.find("{*}rest") is not None:
                    continue
                pitch = note.find("{*}pitch")
                if pitch is None:
                    continue
                step_el = pitch.find("{*}step")
                oct_el = pitch.find("{*}octave")
                alter_el = pitch.find("{*}alter")
                if step_el is None or oct_el is None or not step_el.text:
                    continue
                alter = 0.0
                if alter_el is not None and alter_el.text:
                    try:
                        alter = float(alter_el.text)
                    except ValueError:
                        pass
                score.note_names.append(
                    _note_name(step_el.text.strip(), alter, int(oct_el.text))
                )
                score.note_count += 1
                for lyr in note.findall("{*}lyric"):
                    txt = lyr.find("{*}text")
                    if txt is not None and txt.text and txt.text.strip():
                        score.lyric_tokens.append(txt.text.strip())
                        score.has_lyric = True
    score.parts = part_count
    return score


def convert_to_prompt(
    score: ParsedScore,
    *,
    title: Optional[str] = None,
    style_hint: Optional[str] = None,
) -> ScorePrompt:
    lyric = " ".join(score.lyric_tokens).strip()
    # 멜로디 참고 (앞 24음만, 나머지 생략)
    melody = " ".join(score.note_names[:24])
    if len(score.note_names) > 24:
        melody += " …"

    key_name = _key_name(score.key_fifths)

    ctx: list[str] = []
    if score.title:
        ctx.append(f"원제목: {score.title}")
    if key_name:
        ctx.append(f"조성: {key_name}")
    if score.beats and score.beat_type:
        ctx.append(f"박자: {score.beats}/{score.beat_type}")
    if score.tempo_bpm:
        ctx.append(f"템포: {score.tempo_bpm:g} BPM")
    if score.chord_labels:
        # 화성 진행: 멜로디와 함께 곡의和声을 결정하는 핵심 컨텍스트
        progression = " | ".join(score.chord_labels[:48])
        if len(score.chord_labels) > 48:
            progression += " …"
        ctx.append(f"화성 진행(chords): {progression}")
    ctx.append(f"멜로디 참고(원조 음정): {melody}")
    prompt = "악보(MusicXML)를 기반으로 다음 컨텍스트를 살려 노래를 완성하세요.\n" + "\n".join(ctx)

    if style_hint:
        style = style_hint
    elif score.has_lyric:
        style = "보컬 独唱" if score.parts <= 1 else "합창/중창"
    else:
        style = "연주곡 instrumental"

    inferred = {
        "parts": score.parts,
        "note_count": score.note_count,
        "has_lyric": score.has_lyric,
        "chord_count": len(score.chord_labels),
        "key_fifths": score.key_fifths,
        "time": f"{score.beats}/{score.beat_type}" if score.beats else None,
        "tempo_bpm": score.tempo_bpm,
    }
    return ScorePrompt(
        prompt=prompt,
        lyric=lyric,
        style=style,
        title=title or score.title,
        inferred=inferred,
    )


def validate_score(score: ParsedScore) -> ScoreValidation:
    """파싱 결과 신뢰성 검증 (OMR fail-closed 게이트).

    MusicXML 은 확정 입력이라 거의 항상 ok. 이미지 OMR 은 인식 실패 시
    멜로디/화성이 비어 있을 수 있어, 이 검증으로 '신뢰 불가' 결과를 422 로 거부한다.
    """
    critical: list[str] = []
    warnings: list[str] = []
    if score.note_count == 0:
        critical.append("멜로디 음표를 추출하지 못했습니다 (OMR 인식 실패 가능)")
    if score.key_fifths is None:
        warnings.append("조성(key)을 추출하지 못했습니다")
    if score.tempo_bpm is None:
        warnings.append("템포를 추출하지 못했습니다")
    if not score.chord_labels:
        warnings.append("화성(chord)을 추출하지 못했습니다 (리드시트 화성 손실 위험)")
    if score.has_lyric and score.parts > 1:
        warnings.append("다성부 악보에 가사가 붙어 있습니다 (가사 할당 오인식 가능)")
    return ScoreValidation(critical=critical, warnings=warnings)


def validate_prompt(sp: ScorePrompt) -> ScoreValidation:
    """``ScorePrompt`` (이미 변환된 결과) 기준 신뢰성 검증 — 엔드포인트에서 재파싱 없이 사용."""
    inf = sp.inferred or {}
    critical: list[str] = []
    warnings: list[str] = []
    if not inf.get("note_count"):
        critical.append("멜로디 음표를 추출하지 못했습니다 (OMR 인식 실패 가능)")
    if inf.get("key_fifths") is None:
        warnings.append("조성(key)을 추출하지 못했습니다")
    if inf.get("tempo_bpm") is None:
        warnings.append("템포를 추출하지 못했습니다")
    if not inf.get("chord_count"):
        warnings.append("화성(chord)을 추출하지 못했습니다 (리드시트 화성 손실 위험)")
    if inf.get("has_lyric") and inf.get("parts", 0) > 1:
        warnings.append("다성부 악보에 가사가 붙어 있습니다 (가사 할당 오인식 가능)")
    return ScoreValidation(critical=critical, warnings=warnings)


def _key_name(fifths: Optional[int]) -> Optional[str]:
    if fifths is None:
        return None
    # 으뜸음 기준 장조명 (C=0, G=1, D=2, ... F=-1, Bb=-2 ...)
    major = {
        0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
        -1: "F", -2: "Bb", -3: "Eb", -4: "Ab", -5: "Db", -6: "Gb", -7: "Cb",
    }
    return major.get(fifths, f"fifths={fifths}")
