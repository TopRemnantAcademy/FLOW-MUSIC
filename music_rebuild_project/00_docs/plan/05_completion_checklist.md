# 05 · 遗漏可能性检查清单（completion checklist）

> 来源：#66（遗漏可能性检查清单 completion）。上游：05 §4。
> 定位：交付前**逐项打勾**，任一项未满足 → 禁判完成。勾选状态记 `08_qc/`。

## 规划与目标
- [ ] 目标 / 范围 / 语言 / 曲形已记录（对应 `01_pipeline_and_gates.md` §1）
- [ ] 官方下载与版本已在日志（`01_preflight/install_versions.csv`）

## 谱面（G2）
- [ ] 批准 `ground_truth.musicxml` 存在，且**音高 + 节奏 + 歌词**三项齐全
- [ ] ground_truth 带 **sha256 哈希**（`04_approved/` 只读加固已生效，#27）

## 声乐（G3）
- [ ] 至少 1 条批准的 보컬 WAV
- [ ] 引擎 / 声库 / 语言已记录（`vocal_settings.json`）
- [ ] stem 试听已完成（非仅轨道存在）

## 编曲与混音（E/F）
- [ ] 鼓 / 贝斯 / 和声 MIDI 均存在（#52/#53/#54）
- [ ] chords.txt 已定（#50）；无和弦标记的候选已人工批准（#49，P3）
- [ ] `mix_checks`（#58）与 `g4_checklist`（#61）PASS

## 交付物
- [ ] master WAV/MP3 + 分轨 stem 均可播放（#59）
- [ ] 哈希 manifest 非空（#59 / G4-12）

## 合规与回滚
- [ ] 工具 / 模型 / 音色许可与来源在 `logs/license_notes.md`
- [ ] 回滚点齐备：源（`02_source/`）/ 试点（`03_omr/`）/ 批准本（`04_approved/`）

> 全勾 → 判完成；任一未勾 → 打回对应段（`../failure_recovery.md` #62）。
