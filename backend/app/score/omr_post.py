"""OMR 인식 결과 후처리 (이미지 경로 정확도 보정).

oemer 는 이미지에서 음표/화성/가사를 뽑지만, 키(key) 누락·반복 마디 가사 중복 등
오인식이 흔하다. 이 모듈은 **결정적(deterministic)** 보정만 한다 — 확률적 추측이 아님.
- ``infer_key_from_chords``: 화성(chord)이 있으나 키가 없으면, 코드 근음으로 가장
  부합하는 장조를 역산(리드시트는 대부분 장조). OMR 이 키를 못 뽑아도 복구.
- ``dedup_exact_lyric_repeat``: OMR 이 반복 마디를 그대로 중복 인식해 가사가 A B A B
  로 두 번 들어온 경우 한 번으로 합침(2절/코러스 중복 제거).

이 모듈은 ``pydantic_settings``/oemer 와 무관하게 순수 로직이라 로컬에서 단위 검증 가능.
실제 OMR 출력에 대한 효과는 배포 노드에서 ``scripts/score_accuracy.py`` 로 실측.
"""
from __future__ import annotations

import re
from typing import Optional

from app.score.musicxml import ParsedScore

# step → pitch class (C=0 … B=11)
_STEP_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# 장조 fifths → 대조군 스케일 pitch class 집합 (C major 를 5도 올림으로 생성, 전사 오류 방지)
_MAJOR_SCALE = {
    f: {(pc + f * 7) % 12 for pc in (0, 2, 4, 5, 7, 9, 11)} for f in range(7)
}
# fifths 양수(장조)만 다룬다 — 마이너는 보수적으로 복구하지 않음(오추론 방지).

_CHORD_ROOT_RE = re.compile(r"^([A-Ga-g])(#|b)?")


def _root_step_alter(label: str):
    m = _CHORD_ROOT_RE.match(label.strip())
    if not m:
        return None, 0
    step = m.group(1).upper()
    alter = 1 if m.group(2) == "#" else (-1 if m.group(2) == "b" else 0)
    return step, alter


def infer_key_from_chords(chord_labels: list[str]) -> Optional[int]:
    """화성으로 가장 부합하는 장조 fifths 역산. 복구 불가능하면 None.

    코드 근음 pitch class 가 특정 장조 스케일에 얼마나 많이 속하는지 스코어링.
    최소 2개 코드여야 추론(1개로는 모호).
    """
    if len(chord_labels) < 2:
        return None
    roots: list[int] = []
    for c in chord_labels:
        step, alter = _root_step_alter(c)
        if step is None:
            continue
        pc = (_STEP_PC[step] + alter) % 12
        roots.append(pc)
    if len(roots) < 2:
        return None
    first_root = roots[0]
    best_fifths: Optional[int] = None
    best_count = -1
    best_score = -1
    for fifths, scale in _MAJOR_SCALE.items():
        count = sum(1 for r in roots if r in scale)
        # 근음(첫 코드)이 후보 장조의 토닉이면 가산 — 리드시트는 보통 토닉에서 시작.
        # 동점 시 토닉 일치 키를 우선(A E F#m Bm 는 A장조와 G장조 모두 부합하나
        # 첫 코드 A 가 토닉인 A장조를 채택).
        tonic_pc = (fifths * 7) % 12
        score = count + (3 if tonic_pc == first_root else 0)
        if score > best_score:
            best_score = score
            best_count = count
            best_fifths = fifths
    # 절반 이상의 코드가 스케일에 속하면만 채택(그렇지 않으면 모호 → 복구 안 함)
    if best_count >= max(2, len(roots) // 2 + 1):
        return best_fifths
    return None


def dedup_exact_lyric_repeat(lyric_tokens: list[str]) -> list[str]:
    """정확히 A B A B 형태로 중복된 가사를 A B 로 합침.

    OMR 이 반복 마디를 중복 인식하는 경우에만 동작(보수적). 길이가 짝수이고
    전반부==후반부 일 때만 축소. 그렇지 않으면 원본 그대로.
    """
    n = len(lyric_tokens)
    if n < 4 or n % 2 != 0:
        return lyric_tokens
    half = n // 2
    if lyric_tokens[:half] == lyric_tokens[half:]:
        return lyric_tokens[:half]
    return lyric_tokens


def refine_score(score: ParsedScore) -> ParsedScore:
    """OMR 산출 ParsedScore 를 결정적 보정. score 를 제자리 수정해 반환."""
    # 1) 키 복구: OMR 이 키를 못 뽑았으나 화성이 있으면 장조 역산
    if score.key_fifths is None and score.chord_labels:
        inferred = infer_key_from_chords(score.chord_labels)
        if inferred is not None:
            score.key_fifths = inferred
    # 2) 반복 마디 가사 중복 정리
    if score.lyric_tokens:
        score.lyric_tokens = dedup_exact_lyric_repeat(score.lyric_tokens)
    return score
