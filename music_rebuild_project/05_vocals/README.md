# 05_vocals · 보컬生成区（ACE / Synthesizer V）

## 放什么

- `<song_id>/projects/`：宿主工程 `.acep` / `.svp`
- `<song_id>/clips/`：音符 / 歌词 / 时序 MIDI 或 clip
- `<song_id>/vocal_wav/`：渲染 vocal WAV（含 `approved_vocal_pilot.wav`）+ `*.sha256`
- `<song_id>/vocal_settings.json`：引擎 / 声库 / 语言 / 主要参数 + 所依据 ground_truth 哈希

## 不放什么

- 门判定记录 → `08_qc/`
- 改动过的基准谱（只读引用 `04_approved/`）

## 只读规则

工作产物可迭代（L1）。**`vocal_wav/approved_vocal_pilot.wav` + `vocal_settings.json` 过 G3 即只读**；声乐轨音符/歌词在编曲阶段禁改（#13/#27）。
