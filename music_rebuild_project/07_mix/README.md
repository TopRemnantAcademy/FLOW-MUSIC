# 07_mix · DAW 工程 / stem / 成品

## 放什么

- `<song_id>/project/`：DAW 工程（REAPER `.rpp` / `.ardour`，**只允许相对路径引用**）
- `<song_id>/stems/`：分轨 stem（鼓/贝斯/和声/보컬）
- `<song_id>/masters/`：master WAV + 共享 MP3 + `*_manifest.txt`
- `<song_id>/dist/`：分发文件夹（原谱图 + approved MusicXML + 보컬 WAV + 编曲 MIDI + master WAV）

## 不放什么

- 声乐源 WAV → `05_vocals/`
- 门判定记录 → `08_qc/`

## 只读规则

工程与 stem 可迭代（L1）。**过 G4 后 `masters/**` 与 `dist/**` 只读**；工程内禁绝对路径，保证可搬迁复现。
