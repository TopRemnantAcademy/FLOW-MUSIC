"""测试夹具：把 `tools/` 加入 sys.path，并提供几个标准曲式上下文。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parents[2]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from midi import sections  # noqa: E402


@pytest.fixture
def simple_context() -> sections.SongContext:
    """最简上下文：C 大调 4/4、120 BPM、主歌 8 小节。"""
    return sections.SongContext(
        key=sections.KeySignature("C"),
        beats_per_bar=4,
        bpm=120,
        bars=8,
    ).validate()


@pytest.fixture
def full_song() -> sections.SongContext:
    """完整曲式：Intro/Verse/Pre-chorus/Chorus/Bridge/Outro，共 34 小节。

    34 小节 × 4 拍 × 60 / 120 BPM = 68.0 秒（G4-5 的预期时长）。
    """
    plan = sections.SectionPlan.from_bars(
        [
            ("Intro", 4),
            ("Verse", 8),
            ("Pre-chorus", 4),
            ("Chorus", 8),
            ("Bridge", 4),
            ("Outro", 6),
        ]
    )
    return sections.SongContext(
        key=sections.KeySignature("F"),
        beats_per_bar=4,
        bpm=120,
        plan=plan,
        chords={0: "F", 1: "F", 2: "Bb", 3: "C", 4: "Dm", 5: "Bb"},
    ).validate()


@pytest.fixture
def three_four_context() -> sections.SongContext:
    """3/4 拍、90 BPM、12 小节 → 12 × 3 × 60 / 90 = 24.0 秒。"""
    return sections.SongContext(
        key=sections.KeySignature("G"),
        beats_per_bar=3,
        bpm=90,
        bars=12,
    ).validate()
