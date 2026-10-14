"""从 `sections.md` + `chords.txt` 生成一整套编曲 MIDI 骨架（演示/联调用）。

用法::

    python -m tools.midi.render_demo \
        --sections music_rebuild_project/06_arrangement/song01/sections.md \
        --chords   music_rebuild_project/06_arrangement/song01/chords.txt \
        --key F --tempo 120 --out /tmp/song01_midi

产物：
  drums.mid / bass.mid / chords_piano.mid / chords_guitar.mid / chords_pad.mid
  arrangement_preview.json（段落结构 + 预期时长，供人工核对 G4-5）

注意：这是**骨架**，用于导入 DAW 继续编；不是成品，也不做混音。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import bass as bass_mod
from . import chords as chords_mod
from . import drums as drums_mod
from . import sections as sections_mod


def build_context(
    sections_md: Path | None,
    chords_txt: Path | None,
    key: str,
    tempo: int,
    beats_per_bar: int,
    bars: int | None,
) -> sections_mod.SongContext:
    if sections_md is not None:
        plan = sections_mod.parse_sections_md(sections_md.read_text(encoding="utf-8"))
    else:
        plan = sections_mod.SectionPlan.from_bars(
            [("Intro", 4), ("Verse", 8), ("Pre-chorus", 4),
             ("Chorus", 8), ("Bridge", 4), ("Outro", 4)]
        )
    chords = (
        sections_mod.parse_chords_txt(chords_txt.read_text(encoding="utf-8"))
        if chords_txt is not None
        else {}
    )
    return sections_mod.SongContext(
        key=sections_mod.KeySignature(key),
        beats_per_bar=beats_per_bar,
        bpm=tempo,
        bars=bars or plan.total_bars,
        plan=plan,
        chords=chords,
    ).validate()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成编曲 MIDI 骨架（#52/#53/#54）")
    parser.add_argument("--sections", type=Path, default=None, help="sections.md（#51）")
    parser.add_argument("--chords", type=Path, default=None, help="chords.txt（#50）")
    parser.add_argument("--key", default="C", help="调号根音，如 C / F / Bb / A")
    parser.add_argument("--tempo", type=int, default=120, help="BPM")
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--bars", type=int, default=None, help="覆盖总小节数")
    parser.add_argument("--out", type=Path, required=True, help="输出目录")
    args = parser.parse_args(argv)

    ctx = build_context(
        args.sections, args.chords, args.key, args.tempo, args.beats_per_bar, args.bars
    )
    args.out.mkdir(parents=True, exist_ok=True)

    # 一个完整工程：鼓 + 贝斯 + 三织体和弦，共用同一时间基准。
    combined, _ = drums_mod.generate(ctx)
    bass_mod.generate(ctx, writer=combined)
    chords_mod.generate(ctx, voices=("piano", "guitar", "pad"), writer=combined)
    (args.out / "song_arrangement.mid").write_bytes(combined.to_bytes())

    # 各声部单轨导出，便于逐轨导入 DAW。
    (args.out / "drums.mid").write_bytes(drums_mod.to_bytes(ctx))
    (args.out / "bass.mid").write_bytes(bass_mod.to_bytes(ctx))
    for voice in ("piano", "guitar", "pad"):
        (args.out / f"chords_{voice}.mid").write_bytes(
            chords_mod.to_bytes(ctx, voices=(voice,))
        )

    preview = {
        "key": args.key,
        "beats_per_bar": ctx.beats_per_bar,
        "bpm": ctx.bpm,
        "bars": ctx.bars,
        "expected_seconds": ctx.seconds(),
        "expected_seconds_note": "bars × beats_per_bar × 60 / bpm（G4-5 判据）",
        "sections": [
            {
                "name": s.name,
                "display": s.display,
                "start_bar_0based": s.start_bar,
                "end_bar_0based": s.end_bar,
                "bars": s.bars,
                "start_seconds": round(
                    s.start_bar * ctx.beats_per_bar * 60.0 / ctx.bpm, 4
                ),
            }
            for s in ctx.plan.sections
        ],
    }
    (args.out / "arrangement_preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(ctx.plan.describe())
    print(f"\n预期时长 = {ctx.bars} × {ctx.beats_per_bar} × 60 / {ctx.bpm} "
          f"= {ctx.seconds()} 秒")
    print(f"产物目录: {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
