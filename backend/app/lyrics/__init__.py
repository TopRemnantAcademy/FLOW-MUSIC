"""歌词域：LRC 时间轴编解码与「改词后重对齐」。

仅依赖标准库 + 纯函数，不碰 ORM/DB —— 便于单测（tests/test_lyrics_lrc.py）。
"""
from app.lyrics.lrc import (  # noqa: F401
    LrcLine,
    parse_lrc,
    build_lrc,
    realign_sync,
    extract_plain_lines,
    lyric_from_lines,
    normalize_timeline,
)

__all__ = [
    "LrcLine",
    "parse_lrc",
    "build_lrc",
    "realign_sync",
    "extract_plain_lines",
    "lyric_from_lines",
    "normalize_timeline",
]
