# 03 · 10 分钟快捷执行顺序

> 来源：#64（10 分钟快捷执行顺序）。上游：05 §2。
> 定位：一页跑通首曲的**最短路径**。每步必须有产出与报告（证据推进，禁静默）。详细口径见 `01_pipeline_and_gates.md` 与各 gate。

| 步 | 动作 | 命令 / 工具 | 产出（证据） |
|---|---|---|---|
| ① | 原谱放入 `02_source/`（**只读**） | 人工放置 | 文件名 + 页数记 `issue_log/` |
| ② | 安装确认 + 记版本 | 跑环境确认，版本写 `01_preflight/install_versions.csv` | `install_versions.csv` 更新行 |
| ③ | 代表 2 页 Audiveris + 付费候选比 MusicXML | Audiveris（基线）+ 1 付费候选 | `03_omr/` 两份候选 + 错误计数（#33） |
| ④ | MuseScore 全数校正，固定 ground_truth | MuseScore Studio | `04_approved/ground_truth.musicxml` + sha256（G2） |
| ⑤ | ACE 导入 8 小节生 보컬，必要时 SV2 A/B | ACE Studio（#39/#43） | `05_vocals/` WAV + 试听记录（G3） |
| ⑥ | chords.txt 定后做鼓/贝斯/和声 MIDI | `tools/arrangement` + `tools/midi` | `06_arrangement/` chords.txt + SMF |
| ⑦ | 先渲 8 小节听 G4，通过后扩全曲 | `tools/checks` + `tools/render` | `07_mix/` 8 小节 + g4 报告；扩全曲 master/stem（#59） |

**终止条件**：任一步产出缺失或门 FAIL → 停，查 `../failure_recovery.md`（#62），禁带病进下一步。

**一键自检**（无需桌面工具，纯 stdlib）：
```bash
python -m pytest music_rebuild_project/tools/ -q     # 校验/检查/gate 脚本全绿
```
