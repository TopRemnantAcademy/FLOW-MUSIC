# 06_arrangement · 编曲 MIDI

## 放什么

- `<song_id>/drums/`、`bass/`、`chords/`：各声部 MIDI
- `<song_id>/arrangement.json`：声部清单 / 音源映射 / BPM / 调号 / 对齐基准
- `<song_id>/sections.md`：段落标注（Intro/Verse/Chorus...）
- `<song_id>/chords.txt`：和弦进行（批准后固化）
- `<song_id>/interference_notes.md`：与声乐的频段/速度/相位避让

## 不放什么

- 声乐工程 / WAV → `05_vocals/`
- 混音工程 → `07_mix/`

## 只读规则

可迭代（L1）。`chords.txt` 经人工批准后只读（#49）；`sections.md` 随曲目修订。
