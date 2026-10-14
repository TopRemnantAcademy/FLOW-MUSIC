# 编曲自动化提示词模板（CodeBuddy / Agent 用）

> 来源：#57（[阶段4] CodeBuddy 自动化—编曲提示词与安全规则），上游 #48–#51 / #52–#55。
> 定位：本文只给**可执行的提示词模板与输入/输出契约**。门阈值（G1–G4）**不在本文重复定义**，一律以
> `gates/gate_G1.md` ~ `gate_G4.md`（#11）为唯一权威；与门标准冲突时**以门标准为准**，并在本节标注。
> 配套：安全红线见同目录 `automation_safety_rules.md`（**先读它，再跑本文提示词**）。

## 适用范围与前置条件

| 项 | 要求 | 出处 |
|---|---|---|
| 触发条件 | **2/4 已确认的 MCP「读 + 编」权限可用**时才允许启动编曲自动化 | #57 正文 |
| 输入基准 | `04_approved/<song_id>/ground_truth.musicxml` 存在且**哈希校验通过** | gate_G2 冻结定义 / #13 |
| 声乐只读 | `05_vocals/<song_id>/vocal_wav/approved_vocal_pilot.wav` + `vocal_settings.json` 只读 | gate_G3 / #13 |
| 权限模式 | 不用 `--dangerously-skip-permissions` 类选项；MCP 工具按步最小授权 | #27 |

任一项不满足 → **停下并报告**（见安全规则 §越权处置），不得自行降级继续。

---

## 1. 输入契约（Input Contract）

Agent 动手前必须逐项确认下列产物存在、可读、且哈希对得上。**缺一项即停。**

| # | 输入 | 路径 | 校验方式 | 来源 |
|---|---|---|---|---|
| I-1 | 冻结基准谱 | `04_approved/<song_id>/ground_truth.musicxml` | `verify_hashes.sh`，比对 `ground_truth.musicxml.sha256`（SHA-256 + UTC 时间 + 批准人） | gate_G2 冻结定义、#13/#27 |
| I-2 | 批准本哈希 | `04_approved/<song_id>/ground_truth.musicxml.sha256` | 文件行数 = 1，字段齐全 | gate_G2 |
| I-3 | 编曲前确认单 | `06_arrangement/<song_id>/arrangement_precheck.md` | 调号 / 拍号 / 小节数 / 速度 / 反复结构 / 有无和弦符号，**六项均已记录** | #48 |
| I-4 | 和弦进行 | `06_arrangement/<song_id>/chords.txt` | **逐小节**（如「1 小节 C，2 小节 G …」）；**须已人工批准**才可引用固化 | #49/#50 |
| I-5 | 段落标注 | `06_arrangement/<song_id>/sections.md` | Intro/Verse/Pre-chorus/Chorus/Bridge/Outro + 小节范围 | #51 |
| I-6 | 声乐批准件（只读参考） | `05_vocals/<song_id>/vocal_wav/approved_vocal_pilot.wav` | 只读**读取**，用于避让 2~5 kHz 频段；**禁改** | gate_G3、#55 |

**和弦缺失分支（#49）**：`arrangement_precheck.md` 若无和弦符号 → Agent 可按旋律流**生成和弦候选**，但：

- 候选**只能**先落到 `06_arrangement/<song_id>/chords_candidate.md`（推导过程 + 逐小节建议）；
- **人工批准前禁止编曲固化**——不得据此生成鼓/贝斯/和弦 MIDI，不得写 `chords.txt`；
- `chords.txt` 由人工批准后才生效，写入后即为冻结件（`06_arrangement/README.md`：批准后只读）。

---

## 2. 通用提示词骨架（所有乐器共用）

```text
你是本项目的编曲助手，工作区根目录：music_rebuild_project/。

【硬约束 · 不可协商】
1. 禁写禁改禁删 02_source/ 与 04_approved/（冻结区）。要改基准 = 出 v002，禁原地覆盖。
2. 禁改 05_vocals/ 下已批准的 보컬 clip / WAV / vocal_settings.json。只读参考。
3. 新产物一律落 06_arrangement/<song_id>/<声部>/，用新版本号，禁覆盖任何已有冻结件。
4. 一次只做 1 个 section（如「Verse」或「Chorus」），禁一次生成整曲。
5. 禁渲染、禁导出 WAV、禁写入交付目录；只产出 MIDI 与说明。
6. 禁止为迁就 AI 输出而修改基准音符/和弦（#43 明令禁止）。

【输入】song_id = <song01>；本段 = <Verse>；小节范围 = <1-8>；
       基准 = 04_approved/<song_id>/ground_truth.musicxml（哈希已校验）；
       和弦 = 06_arrangement/<song_id>/chords.txt（已人工批准）；
       段落 = 06_arrangement/<song_id>/sections.md。

【输出】MIDI 落 06_arrangement/<song_id>/<声部>/<songNN>_<声部>_<段>_vNNN.mid；
       完成后回报：路径 / 音符数 / 小节数 / 依据的基准哈希。
```

> 骨架与 `规划/AI音乐平台规划书.md` §8.4 的提示词 SOP 一致：**契约先行 + 不改无关文件 + 写完后给自测命令**。

---

## 3. 逐类乐器提示词

### 3.1 鼓（#52）

```text
生成鼓 MIDI，遵循 #52：
- 主歌/副歌分独立 clip，禁复用同一 clip。
- 4 小节循环，末小节加 fill。
- velocity：底鼓 < 军鼓 < 踩镲；军鼓加 ghost note。
- 同 8 小节连播不得听出「完全重复」。
- 安全起步 pattern（4/4，BPM 取 arrangement_precheck.md）：
  kick 1·3 强拍 + 第2小节起的3拍前反拍；
  snare 2·4 反拍；
  hi-hat 8 分持续，每 4 小节末 2 拍转 16 分；
  chorus 首小节 1 拍 crash。
```

**验收子清单（鼓）**

- [ ] 主歌与副歌是**两个独立 clip**，无复用
- [ ] 4 小节循环，末小节有 fill
- [ ] velocity 关系正确：底鼓 < 军鼓 < 踩镲
- [ ] 军鼓存在 ghost note
- [ ] 8 小节连播无「完全重复」听感（人工勾选）
- [ ] chorus 首小节 1 拍有 crash
- [ ] 小节数与本段范围一致；音长合计 = 拍号（gate_G2-3 口径）
- [ ] 落点在 `06_arrangement/<song_id>/drums/`，未触碰冻结区

### 3.2 贝斯（#53）

```text
生成贝斯 MIDI，遵循 #53：
- 每小节以和弦根音为基音。
- 与底鼓时机不撞（避免同拍同根音叠加）。
- 不连击同一根音；小节边界用 5 度/8 度移动或 hold。
- 副歌增加贝斯移动以提节奏感。
```

**验收子清单（贝斯）**

- [ ] 每小节以 `chords.txt` 的根音为基音
- [ ] 与底鼓时机无冲突
- [ ] 无同根音连击（持续音用 hold）
- [ ] 小节边界有 5 度/8 度移动或 hold
- [ ] 副歌有可辨的贝斯移动
- [ ] 落点在 `06_arrangement/<song_id>/bass/`

### 3.3 和弦乐器 · 钢琴 / 吉他（#54）

```text
生成和弦乐器 MIDI，遵循 #54：
- 避免与 보컬 同 octave 重叠（整体上移或下移让位）。
- 分段不同 pattern：钢琴主歌 8 分 strum / 副歌 4 分 arp。
- 铺底/弦乐可长 sustain，但和弦内重叠音 ≤ 4（根·3·5 度以上）。
```

**验收子清单（和弦乐器）**

- [ ] 与 보컬 无同 octave 重叠（避 2~5 kHz 区，口径见 #55）
- [ ] 主歌与副歌 pattern 不同（strum vs arp）
- [ ] 和弦内重叠音 ≤ 4，且为根/3/5 度以上
- [ ] 铺底 sustain 不糊（无密集堆积）
- [ ] 落点在 `06_arrangement/<song_id>/chords/`

### 3.4 铺底 / Pad（#54）

```text
生成铺底（pad/strings）MIDI，遵循 #54：
- 长 sustain 为主，跟随和弦进行。
- 与 보컬 频段避让；密度低于钢琴/吉他。
- 段落切换处（如 Verse→Chorus）做密度推进。
```

**验收子清单（铺底）**

- [ ] sustain 与和弦进行一致
- [ ] 与 보컬 频段避让（#55 口径）
- [ ] Verse→Chorus 有密度差（对应 gate_G4-6「结构可辨」的准备项）
- [ ] 落点在 `06_arrangement/<song_id>/chords/`（`*_pad_*.mid`）

---

## 4. 输出契约（Output Contract）

| 产出 | 落点 | 命名 | 冻结 |
|---|---|---|---|
| 鼓 MIDI | `06_arrangement/<song_id>/drums/` | `<songNN>_drums_main_vNNN.mid`、`_chorus_vNNN.mid` | ○ 可迭代（L1） |
| 贝斯 MIDI | `06_arrangement/<song_id>/bass/` | `<songNN>_bass_vNNN.mid` | ○ |
| 和弦 MIDI | `06_arrangement/<song_id>/chords/` | `<songNN>_piano_vNNN.mid`、`_guitar_vNNN.mid`、`_pad_vNNN.mid` | ○ |
| 声部清单 | `06_arrangement/<song_id>/arrangement.json` | 声部清单 / 音源映射 / BPM / 调号 / 对齐基准 | ○ |
| 干扰预防记录 | `06_arrangement/<song_id>/interference_notes.md` | 频段/速度/相位与声乐避让（#55） | ○ |

**禁止落点（冻结区 · 一律不得写入/修改/删除）**

- `02_source/**`（L0，只进不改）
- `04_approved/**`（L2 冻结；改动须出 v002，禁原地覆盖）
- `05_vocals/<song_id>/vocal_wav/approved_vocal_pilot.wav`、`vocal_settings.json`（过 G3 只读）
- `07_mix/<song_id>/masters/**`、`07_mix/<song_id>/dist/**`、`08_qc/gate_g*.md`、`09_archive/**`
- `06_arrangement/<song_id>/chords.txt`（人工批准后只读，Agent 不得改写）

> 命名细则（分隔符、大小写等）归 #13；本文只沿用 `songNN_<类型>_vNNN.扩展名` 形态。
> 版本号 `vNNN` 从 `v001` 递增，**新版本 = 新文件**，老文件永久保留。

---

## 5. 整体验收清单（每次自动化动作后勾）

- [ ] 输入契约 I-1 ~ I-6 全部满足（哈希校验通过）
- [ ] 只动 `06_arrangement/`，未触碰任何冻结区
- [ ] 一次一个 section，动作可重跑（幂等，见安全规则 §幂等与回滚）
- [ ] 产出 MIDI 的音长合计 = 拍号（对齐 gate_G2-3）
- [ ] 记录了本次动作的依据哈希 + 时间 + 命令
- [ ] 回报内容含：路径 / 音符数 / 小节数 / 基准哈希
- [ ] 未渲染、未导出 WAV、未写入交付目录

## 6. 交叉引用

- 输入来源：#48 编曲前确认、#49 和弦候选、#50 chords.txt、#51 段落标注
- 提示词对象：#52 鼓、#53 贝斯、#54 和弦乐器
- 避让与门：#55 声乐-伴奏干扰、gate_G3 / gate_G4（阈值唯一定义处）
- 命名与冻结：#13；只读加固：#27；失败恢复：#62
- 安全红线：`00_docs/automation_safety_rules.md`
