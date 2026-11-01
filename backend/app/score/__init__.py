"""악보 → 음악 파이프라인의 1차 변환 계층.

스코어(이미지/PDF/MusicXML)를 받아 텍스트→음악 엔진(Mureka 등)이 소비할 수 있는
``prompt`` / ``lyric`` / ``style`` 로 변환한다. 실제 오디오 생성은 기존
``_process_song`` → ``generate()`` 경로를 그대로 재사용하므로, 이 모듈은
"입력 다형성(다양한 악보 포맷)"만 책임진다.

의존성 분리:
- MusicXML 파싱은 외부 라이브러리 없이 stdlib ``xml.etree`` 로 처리 (순수 변환, 노드 독립적).
- 이미지/PDF OMR 은 oemer 가 설치된 별도 venv 로 subprocess 기동 (``omr.py``).
  oemer 미설치 노드에서는 ``OMRUnavailable`` → 엔드포인트 501 fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "ScorePrompt",
    "ScoreConversionError",
    "UnsupportedScoreFormat",
    "OMRUnavailable",
    "OMRFailed",
    "OMRTimeoutError",
    "score_to_prompt",
    "validate_score",
    "validate_prompt",
    "ScoreValidation",
]


class ScoreConversionError(Exception):
    """악보 변환 실패의 최상위 (엔드포인트에서 400/502 로 매핑)."""


class UnsupportedScoreFormat(ScoreConversionError):
    """지원하지 않는 업로드 확장자/타입."""


class OMRUnavailable(ScoreConversionError):
    """이 노드에서 OMR(oemer) 을 사용할 수 없음 (enable_omr=False 또는 미설치)."""


class OMRFailed(ScoreConversionError):
    """OMR 하위 프로세스가 비정상 종료."""


class OMRTimeoutError(ScoreConversionError):
    """OMR 이 예산을 초과해 강제 종료됨 (프로세스 무한 대기 방지)."""


@dataclass
class ScorePrompt:
    """변환 결과: 텍스트→음악 엔진 입력으로 직결."""

    prompt: str  # 엔진 프롬프트 (멜로디/템포/조성 등 컨텍스트)
    lyric: str  # 가사 (음표 아래 syllable / 단어)
    style: str  # 스타일 태그
    title: Optional[str] = None
    inferred: dict = field(default_factory=dict)  # 디버그/감사용 메타


# 하위 모듈 재노출 (ScorePrompt 정의 이후에 import 해야 순환 없음)
from app.score.musicxml import (  # noqa: E402
    ScoreValidation,
    validate_prompt,
    validate_score,
)


_MUSICXML_EXTS = (".musicxml", ".xml", ".mxl")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
_PDF_EXTS = (".pdf",)


def score_to_prompt(
    data: bytes,
    filename: str,
    *,
    title: Optional[str] = None,
    style_hint: Optional[str] = None,
) -> ScorePrompt:
    """업로드 바이트 → ``ScorePrompt``. 확장자로 분기.

    - ``.musicxml`` / ``.xml`` / ``.mxl`` → 직접 파싱
    - 이미지/PDF → OMR 인식 후 파싱 (oemer 필요, 미설치 시 ``OMRUnavailable``)
    - 기타 → ``UnsupportedScoreFormat`` (400)
    """
    if isinstance(filename, (bytes, bytearray)):
        filename = filename.decode("utf-8", "ignore")
    name = (filename or "").lower()
    ext = ""
    for e in (*_MUSICXML_EXTS, *_IMAGE_EXTS, *_PDF_EXTS):
        if name.endswith(e):
            ext = e
            break

    from app.score import musicxml, omr  # 순환 import 방지: 호출 시점 지연 import

    if ext in _MUSICXML_EXTS:
        score = musicxml.parse_musicxml(data)
        return musicxml.convert_to_prompt(score, title=title, style_hint=style_hint)

    if ext in _IMAGE_EXTS or ext in _PDF_EXTS:
        mxl_bytes = omr.recognize_score(data, filename)
        score = musicxml.parse_musicxml(mxl_bytes)
        # OMR 후처리: 키 복구(화성→장조 역산) · 반복 마디 가사 중복 정리
        from app.score import omr_post
        omr_post.refine_score(score)
        return musicxml.convert_to_prompt(score, title=title, style_hint=style_hint)

    raise UnsupportedScoreFormat(
        f"지원하지 않는 악보 형식: {filename or '(이름 없음)'}"
        f"（MusicXML(.musicxml/.xml/.mxl) 또는 이미지/PDF 만 가능）"
    )
