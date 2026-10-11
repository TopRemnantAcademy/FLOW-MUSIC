"""찬양/묵상 음원 카탈로그 서비스.

기획안: AI/self-produced meditation instrumentals.
두 가지 소스를 통합 지원:
  1. file — data/music/ 디렉토리의 실제 오디오 파일 (mp3/ogg/wav/m4a)
  2. generated — Web Audio 합성 파라미터로 클라이언트가 실시간 합성

운영자가 data/music/ 에 파일을 넣으면 자동으로 카탈로그에 파일 트랙이 추가됨.
파일이 없는 트랙은 generated 타입으로 폴백.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

log = logging.getLogger("gospel-api.music")

MUSIC_DIR = settings.root_dir / "data" / "music"

# 기본 generated 트랙 — 파일 트랙이 없을 때 폴백
_GENERATED_TRACKS = [
    {
        "id": "new-strength",
        "title": "새 힘",
        "mood": "잔잔한 기도와 회복",
        "tags": ["회복", "기도", "새벽"],
        "root": 196.0,
    },
    {
        "id": "grace-morning",
        "title": "은혜의 아침",
        "mood": "밝은 묵상과 감사",
        "tags": ["감사", "아침", "은혜"],
        "root": 220.0,
    },
    {
        "id": "peace-path",
        "title": "평안의 길",
        "mood": "느린 호흡과 위로",
        "tags": ["평안", "위로", "저녁"],
        "root": 174.61,
    },
    {
        "id": "at-the-cross",
        "title": "십자가 앞에서",
        "mood": "고요한 회개와 소망",
        "tags": ["십자가", "회개", "소망"],
        "root": 146.83,
    },
]

# 스트리밍 지원 오디오 확장자 (선호 순)
_AUDIO_EXTS = (".mp3", ".ogg", ".m4a", ".wav")


@dataclass
class MusicTrack:
    id: str
    title: str
    mood: str
    type: str  # "file" | "generated"
    tags: list[str]
    file_path: Path | None = None
    root: float | None = None  # generated 타입일 때 Web Audio 주파수

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "mood": self.mood,
            "type": self.type,
            "tags": self.tags,
        }
        if self.type == "generated" and self.root is not None:
            d["audio_params"] = {"root": self.root}
        return d


def _scan_file_tracks() -> list[MusicTrack]:
    """data/music/ 디렉토리의 오디오 파일을 스캔.

    파일명 규칙: '{id}-{title}.mp3' 또는 '{title}.mp3'
    README.md, .gitkeep 등은 무시.
    """
    tracks: list[MusicTrack] = []
    if not MUSIC_DIR.exists():
        return tracks

    for f in sorted(MUSIC_DIR.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _AUDIO_EXTS:
            continue
        stem = f.stem
        # 파일명에서 id 추출 (앞부분이 id, 뒤는 제목) — '01-새힘.mp3' → id='01', title='새힘'
        if "-" in stem and stem.split("-", 1)[0].strip().isalnum():
            file_id, title = stem.split("-", 1)
            file_id = file_id.strip()
            title = title.strip()
        else:
            file_id = stem
            title = stem
        tracks.append(
            MusicTrack(
                id=file_id,
                title=title,
                mood="묵상 찬양",
                type="file",
                tags=["오디오"],
                file_path=f,
            )
        )
    return tracks


def get_catalog() -> list[dict]:
    """전체 음원 카탈로그. 파일 트랙 + generated 폴백.

    디렉토리 mtime 기반 캐싱 — 파일 추가/삭제 시 갱신, 그 외에는 캐시 재사용.
    """
    global _catalog_cache
    try:
        dir_mtime = MUSIC_DIR.stat().st_mtime if MUSIC_DIR.exists() else 0.0
    except OSError:
        dir_mtime = 0.0

    if _catalog_cache and _catalog_cache[0] == dir_mtime:
        return _catalog_cache[1]

    catalog = _build_catalog()
    _catalog_cache = (dir_mtime, catalog)
    return catalog


# mtime 기반 카탈로그 캐시: (dir_mtime, catalog_list)
_catalog_cache: tuple[float, list[dict]] | None = None


def _build_catalog() -> list[dict]:
    """캐시 없이 매번 카탈로그를 빌드."""
    tracks: list[MusicTrack] = []

    # 1. 파일 트랙 (data/music/ 에 있으면 우선)
    try:
        file_tracks = _scan_file_tracks()
        tracks.extend(file_tracks)
    except Exception as exc:
        log.warning("[music] 파일 트랙 스캔 실패: %s", exc)

    # 2. generated 폴백 — 파일에 없는 id만 추가
    existing_ids = {t.id for t in tracks}
    for g in _GENERATED_TRACKS:
        if g["id"] not in existing_ids:
            tracks.append(
                MusicTrack(
                    id=g["id"],
                    title=g["title"],
                    mood=g["mood"],
                    type="generated",
                    tags=g["tags"],
                    root=g["root"],
                )
            )

    return [t.to_dict() for t in tracks]


def get_track_file(track_id: str) -> tuple[Path, str] | None:
    """특정 트랙의 파일 경로와 MIME 타입 반환. 파일이 없으면 None."""
    track = _find_track(track_id)
    if not track or not track.file_path or not track.file_path.exists():
        return None
    mime = {
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
    }.get(track.file_path.suffix.lower(), "audio/mpeg")
    return track.file_path, mime


def _find_track(track_id: str) -> MusicTrack | None:
    """id로 트랙 찾기."""
    for g in _GENERATED_TRACKS:
        if g["id"] == track_id:
            return MusicTrack(
                id=g["id"],
                title=g["title"],
                mood=g["mood"],
                type="generated",
                tags=g["tags"],
                root=g["root"],
            )
    # 파일 트랙 확인
    file_tracks = _scan_file_tracks()
    for t in file_tracks:
        if t.id == track_id:
            return t
    return None
