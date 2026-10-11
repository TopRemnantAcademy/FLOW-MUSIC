# 平台交付物清单 (Deliverables)

> 来源：#7 评论全文。
> 定位：本文只做「清单定义」（产出什么、什么格式、过哪个门、谁产出、是否冻结），门阈值不在本文重复，全部以 `gates/gate_G1.md` ~ `gate_G4.md`（#11）为准。

**路径口径（已裁决）**：目录树以 `01_preflight/02_source/03_omr/04_approved/05_vocals/06_arrangement/07_mix/08_qc/09_archive` 一套为准。此前的 `00_source/04_vocal/05_arrangement_midi/06_daw/07_renders` 一套（#12 原骨架）**作废**。文末保留对照表，供回溯。

标签约定：产出方式 `[人工]` / `[工具]`（工具括注）；冻结列 ◉=过门即只读、○=可迭代、—=不适用。

---

## A. 输入准备（Input Preparation）

对应管道 A 段（#9），门 G1（#11 / #31）。

| 交付物 | 路径 / 文件名 | 格式 | 验收标准 | 产出方式 | 冻结 |
|---|---|---|---|---|---|
| 原始乐谱扫描件 | `02_source/<song_id>/originals/song01_source_p001_v001.tif`（每页一文件，PDF 另存 `song01_source_v001.pdf`） | tif/pdf/jpg | G1：分辨率/歪斜/裁切/页序达标（阈值见 gate_G1） | [人工] 扫描或拍照；[工具] 预处理去斜分页 | ◉ 只进不改（#27） |
| 输入分类记录 | `08_qc/input_classification.md` | md | G1 前置：标注矢量PDF/扫描/照片/手写/截图类型（#29） | [人工] 判定 | ○ |
| 图像预处理产物 | `02_source/<song_id>/preprocessed/song01_p001_v001.png` + `08_qc/preprocess_log.md` | png/md | G1：预处理（去斜/分页/旋转统一）可复现（#30） | [工具] 图像工具（ImageMagick 等） | ○ |
| G1 判定记录 | `08_qc/gate_g1_record.md` | md | G1 通过/打回结论 + 页级问题清单（#31） | [人工] 签核 | ◉ 过门后只读 |
| G1 未过页处置 | 打回 `02_source/` 重扫，记录于 `08_qc/gate_g1_record.md` | md | 人难读页禁止进 OMR | [人工] | — |
| 环境安装版本表 | `01_preflight/install_versions.csv` | csv | 记录 日期/工具/版本/路径；仅官方源 | [人工] 填表（#22） | ○ 安装追加时更新 |
| 风险登记表 | `01_preflight/risk_register.md` | md | 覆盖 #14 六类风险 + 事前阻断措施 | [人工] | ○ 随风险变化更新 |
| 问题诊断日志 | `01_preflight/issue_log/env_YYYY-MM-DD_<slug>.md` | md | 每次故障 = 现象/原因/处置/回滚点（#28） | [人工] | ○ |

## B. OMR 识别（OMR）

对应管道 B 段，门 G1→G2 之间（#32/#33/#34）。

| 交付物 | 路径 / 文件名 | 格式 | 验收标准 | 产出方式 | 冻结 |
|---|---|---|---|---|---|
| Audiveris 原始输出 | `03_omr/<song_id>/raw/song01_Audiveris_v001.musicxml` | musicxml | 未审校原始产物，保留以便回溯 | [工具] Audiveris | ◉ 原始输出不再改，重测出新版本号 |
| 付费候选原始输出 | `03_omr/<song_id>/raw/song01_<tool>_v001.musicxml` | musicxml | 与上同页对比用 | [工具] 付费 OMR（如有） | ◉ |
| 页级并行比较表 | `03_omr/<song_id>/retest/page_compare_pilot.md` | md | 2页 Pilot：错误类型/计数逐页并列（#33） | [人工] 计数 + [工具] 生成 | ○ |
| OMR 胜者选定记录 | `03_omr/<song_id>/retest/omr_selection.md` | md | 逐曲重测后选定胜者 + 依据（#34） | [人工] 决策 | ◉ 选定后作为审校基线 |

## C. 谱面审校（Score Review）

对应管道 C 段，门 G2（#35/#36/#37）。

| 交付物 | 路径 / 文件名 | 格式 | 验收标准 | 产出方式 | 冻结 |
|---|---|---|---|---|---|
| 审校工作版 | `03_omr/<song_id>/review/song01_review_v001.mscz` | mscz | 8步全数审校过程中的工作文件（#35） | [工具] MuseScore + [人工] 改谱 | ○ |
| 审校版 MusicXML（迭代） | `03_omr/<song_id>/review/song01_review_v001.musicxml` | musicxml | 每轮导出，随修正升版 | [工具] MuseScore 导出 | ○ |
| 往返一致性记录 | `08_qc/xml_roundtrip_check.md` | md | 导出-再导入 歌词/结构保留（#36） | [工具] 导出 + [人工] 比对 | ○ |
| **冻结基准谱** | `04_approved/<song_id>/ground_truth.musicxml` | musicxml | G2：谱面全数对照、基准未解错误 0（判据见 gate_G2） | [人工] 批准 + [工具] 定稿导出 | ◉◉ 只读（#13/#27） |
| 冻结基准哈希 | `04_approved/<song_id>/ground_truth.musicxml.sha256` | sha256 | 记录 SHA-256 + 批准时间（#37） | [工具] 生成哈希 | ◉◉ 只读 |
| 批准谱面工程 | `04_approved/<song_id>/song01_approved_v001.mscz` | mscz | 与 ground_truth.musicxml 同源可复现 | [工具] MuseScore | ◉ 只读 |
| 批准说明 | `04_approved/<song_id>/approval.md` | md | 批准人 / 时间 / 判据 → 链接 gate_log | [人工] | ◉ 只读 |
| 谱面 QC 报告 | `08_qc/score_qc_report.md` | md | 页级错误 + 人工修正耗时 + 工具对比（#47） | [人工] | ○ |
| G2 判定记录 | `08_qc/gate_g2_record.md` | md | 通过/打回 + 哈希回填 | [人工] 签核 | ◉ 过门后只读 |

> 注意：`04_approved/<song_id>/ground_truth.musicxml` 一旦过 G2 即文件系统级只读，修改只能另生 v002（#13）。

## D. 声乐生成（Vocal Generation）

对应管道 D 段，门 G3（#38-#46）。产物落 `05_vocals/`（与 #46/#47 一致）。

| 交付物 | 路径 / 文件名 | 格式 | 验收标准 | 产出方式 | 冻结 |
|---|---|---|---|---|---|
| ACE 工程 | `05_vocals/<song_id>/projects/song01_ace_v001.acep`（或工具原生工程） | 工具工程 | 声库/歌词/参数可复现（#39） | [工具] ACE Studio | ○ |
| ACE Pilot WAV | `05_vocals/<song_id>/vocal_wav/song01_ace_pilot8bar_v001.wav` | wav | 同 8 小节 A/B 素材（#43） | [工具] ACE Studio | ◉ Pilot 定稿后只读 |
| SynthV 对比工程（条件性） | `05_vocals/<song_id>/projects/song01_sv2_v001.svp` | 工具工程 | 仅在触发对比时产出（#44） | [工具] Synthesizer V | ○ |
| SynthV Pilot WAV | `05_vocals/<song_id>/vocal_wav/song01_sv2_pilot8bar_v001.wav` | wav | 与 ACE 同段落可比 | [工具] Synthesizer V | ◉ |
| 音节-音符对应核对表 | `08_qc/vocal_lyric_mapping.csv` | csv | 歌词-音符对应 0 错（#40） | [人工] 全数核对 | ○ |
| 音高校验记录 | `08_qc/vocal_pitch_check.md` | md | 与基准 MIDI 叠加对比 0 错（#42） | [工具] 比对 + [人工] 判读 | ○ |
| A/B 评测表 | `08_qc/vocal_ab_report.md` | md | 结论 + 引擎选定（#45/#47） | [人工] 主观评测 | ◉ 选定后只读 |
| **冻结批准声乐** | `05_vocals/<song_id>/vocal_wav/approved_vocal_pilot.wav` | wav | G3：音高/歌词对应全数通过（判据见 gate_G3） | [工具] 引擎渲染 + [人工] 批准 | ◉◉ 只读 |
| 声乐设置记录 | `05_vocals/<song_id>/vocal_settings.json` | json | 引擎/声库/语言/主要参数（#46） | [人工] 填写 | ◉ 与批准 WAV 绑定只读 |
| G3 判定记录 | `08_qc/gate_g3_record.md` | md | 通过/打回 | [人工] 签核 | ◉ 过门后只读 |

> 冻结规则：`approved_vocal_pilot.wav` + `vocal_settings.json` 过 G3 只读；**声乐轨的音符与歌词在编曲阶段只读**，禁止为迁就伴奏改动（#13/#27）。

## E. 编曲（Arrangement）

对应管道 E 段，门 G3→G4 之间（#48-#55）。

| 交付物 | 路径 / 文件名 | 格式 | 验收标准 | 产出方式 | 冻结 |
|---|---|---|---|---|---|
| 编曲前确认单 | `06_arrangement/<song_id>/arrangement_precheck.md` | md | 调号/拍号/小节/反复/和弦标记已确认（#48） | [人工] | ○ |
| 和弦进行记录 | `06_arrangement/<song_id>/chords.txt` | txt | 逐小节记录、人工批准后才固化（#49/#50） | [人工] 批准 + [工具] 记录 | ◉ 批准后只读 |
| 段落标注 | `06_arrangement/<song_id>/sections.md` | md | Intro/Verse/Chorus/... 强制标注（#51） | [人工] | ○ |
| 鼓 MIDI | `06_arrangement/<song_id>/drums/song01_drums_main_v001.mid` + `_chorus_v001.mid` | midi | 主歌/副歌独立 clip + 力度变化（#52） | [工具] DAW/编曲工具 | ○ |
| 贝斯 MIDI | `06_arrangement/<song_id>/bass/song01_bass_v001.mid` | midi | 根音随底鼓对齐（#53） | [工具] | ○ |
| 和弦乐器 MIDI | `06_arrangement/<song_id>/chords/song01_piano_v001.mid`、`_guitar_v001.mid`、`_pad_v001.mid` | midi | 钢琴/吉他/铺底（#54） | [工具] | ○ |
| 声部清单 | `06_arrangement/<song_id>/arrangement.json` | json | 声部清单 + 音源映射 + BPM/调号 + 对齐基准 | [工具] 汇总 | ○ |
| 干扰预防记录 | `06_arrangement/<song_id>/interference_notes.md` | md | 频段/速度/相位与声乐避让（#55） | [人工] | ○ |

## F. 混音 / 渲染 / QC（Mix & QC）

对应管道 F 段，门 G4（#56-#62）。

| 交付物 | 路径 / 文件名 | 格式 | 验收标准 | 产出方式 | 冻结 |
|---|---|---|---|---|---|
| DAW 工程 | `07_mix/<song_id>/project/song01_mix_v001.rpp`（REAPER）或 `.ardour` | 工具工程 | 可重开复现混音（#56）；只允许相对路径引用 | [工具] DAW | ○ |
| 混音检查记录 | `08_qc/mix_checklist.md` | md | 增益/보컬优先/EQ/混响/单声道兼容（#58） | [人工] | ○ |
| **最终 Master WAV** | `07_mix/<song_id>/masters/song_name_mix_v001.wav` | wav | G4：时长误差 ±1s；判据（响度/真峰/底噪）见 gate_G4 | [工具] DAW 渲染 + [人工] 批准 | ◉ 过 G4 只读 |
| 共享 MP3 | `07_mix/<song_id>/masters/song_name_mix_v001.mp3` | mp3 | 44.1k/24bit 源下 320kbps | [工具] DAW 导出 | ○ |
| 分轨 stem | `07_mix/<song_id>/stems/song01_drums_v001.wav`、`_bass_v001.wav`、`_chords_v001.wav`、`_vocal_v001.wav` | wav | stem 可单独重开且同步同播（#59） | [工具] DAW 导出 | ○ |
| 分发文件夹 | `07_mix/<song_id>/dist/`（原谱图 + approved MusicXML + 보컬 WAV + 编曲 MIDI + master WAV） | 混合 | G4：分发物齐全（#61） | [人工] 汇总 | ◉ 过 G4 只读 |
| 文件验证脚本产物 | `08_qc/verify_report.json` + `08_qc/verify_report.md` | json/md | 存在/大小/时长/静音占比/削波计数（#60） | [工具] 脚本 | ○ |
| G4 QC 检查表 | `08_qc/gate_g4_record.md` | md | 门 G4 全项通过（#61） | [人工] 签核 | ◉ 过门后只读 |
| 许可·版本·设置·批准日志 | `08_qc/approval_log.md` | md | 许可/版本/设置/批准记录齐全（#61） | [人工] | ◉ 终验后只读 |
| 常见失败恢复文档 | `00_docs/failure_recovery.md` | md | 失败场景与恢复流程（#62） | [人工] | ○ |
| 废弃版本 | `09_archive/YYYY-MM/<原路径镜像>/` | 任意 | 未过门或被取代版本归档，附 `ARCHIVE_NOTE.md` | [人工] 移动 | ◉ 归档区只读 |

---

## 冻结规则汇总（对齐 #13 / #27）

| 冻结类别 | 具体产物 | 触发时点 | 规则 |
|---|---|---|---|
| 源只读 | `02_source/**` | 入库即生效 | 只进不改，文件系统级只读（#27） |
| 批准本冻结 | `04_approved/<song_id>/ground_truth.musicxml`、`*.sha256`、`song01_approved_*.mscz` | 过 G2 | 只读；改动须另生 v002 |
| 声乐冻结 | `05_vocals/<song_id>/vocal_wav/approved_vocal_pilot.wav`、`vocal_settings.json` | 过 G3 | 只读；声乐音符/歌词在编曲阶段禁改 |
| 和弦冻结 | `06_arrangement/<song_id>/chords.txt` | 人工批准后 | 批准前禁固化（#49） |
| 终验冻结 | `07_mix/<song_id>/masters/**`、`07_mix/<song_id>/dist/**`、`08_qc/gate_g4_record.md`、`approval_log.md` | 过 G4 | 只读 |
| 归档只读 | `09_archive/**` | 归档即生效 | 只读 |
| 名称规则 | 全部产物 | 每次生成 | `songNN_<类型>_v001.扩展名`，递增；细则归 #13 |

## 目录口径对照表（已裁决，旧路径作废）

| 交付物类别 | #12 原骨架（作废） | 现行采用（本表 / 目录树） |
|---|---|---|
| 源 | `00_source/` | `02_source/` |
| OMR 原始 | `02_omr_candidates/raw/` | `03_omr/<song_id>/raw/` |
| 审校 | `03_score_review/` | `03_omr/<song_id>/review/` + `04_approved/` |
| 声乐 | `04_vocal/` | `05_vocals/` |
| 编曲 | `05_arrangement_midi/` | `06_arrangement/` |
| DAW/混音 | `06_daw/` | `07_mix/` |
| 渲染 | `07_renders/` | `07_mix/`（masters / stems / dist） |
| QC | `08_qc/` | `08_qc/`（一致） |

## 交叉引用

- #1 总览：五阶段执行顺序 01→02→03→04
- #9 管道 A→F 与门位置
- #11 门 G1-G4 阈值（**本文不重复定义**，验收列仅指向该门，落定于 `gates/gate_G1.md` ~ `gate_G4.md`）
- #12 目录树（口径差异见上表，已裁决）
- #13 版本命名与批准本冻结
- #27 源/批准目录只读加固
- 明细来源：#14/#22/#29-#34/#35-#37/#38-#47/#48-#55/#56-#62
