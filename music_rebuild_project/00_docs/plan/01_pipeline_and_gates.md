# 01 · 全流程管道、门控与核心原则

> 来源：#8（核心原则）、#9（管道 A→F 与门控位置）、#63（文档地图 / 执行顺序）。
> 上游：01 §2 / §3、05_전체점검표_실행순서.md §1。
> 定位：本文是管道的**唯一权威定义**。门阈值不在此重复，一律以 `../gates/gate_G1.md` ~ `gate_G4.md` 为权威。

## 1. 核心原则（#8）

| # | 原则 | 含义 |
|---|---|---|
| P1 | **规则→审校→批准 门控** | 所有 OMR / 보컬合成 / 编曲生成产出，必须经「规则校验 → 人审校 → 批准冻结」三段，缺一不得进入下游 |
| P2 | **图像为基本输入** | 乐谱图像是唯一 ground-truth 源；音频转写（MuScriptor / Klangio）仅作辅助参考，**不得替代**图像 |
| P3 | **候选不禁固化** | 无和弦标记的乐谱只生成**候选**（#49），人工批准前禁止固化为编曲输入 |
| P4 | **approved/ 不可变** | `04_approved/` 一经批准即只读；보컬 clip 的音符·歌词在伴奏阶段只读，禁就地改动 |
| P5 | **精度只由测量声明** | 「95% 精度」仅由测量报告（逐音符错误计数）声明，**禁自动估计 / 口头声明** |

## 2. 管道 A→F 与门控位置（#9）

```
A 输入验证 / 图像预处理（#29 分类 → #30 预处理）
        │
        ▼
   [G1] 图像质量验收（gate_G1）──────────────── 不过 → 打回 A 段
        │
        ▼
B OMR 并行比较（#32 Audiveris 基线 + 付费候选 → #33 同页对比 → #34 选胜者）
        │
        ▼
C MuseScore 全数审校（#35 八步 → #36 导出/再导入歌词保留）
        │
        ▼
   [G2] ground_truth.musicxml 冻结 + 哈希（gate_G2）── 不过 → 打回 C 段
        │
        ▼
D 보컬 A/B（#39 ACE 韩语声库 → 条件性 #44 SynthV2 对比 → #45 引擎选定）
        │
        ▼
   [G3] 批准声乐 WAV 冻结 + vocal_settings.json（gate_G3）── 不过 → 打回 D 段
        │
        ▼
E 编曲（#48 preconfirm → #50 chords.txt → #49 候选 → #52/#53/#54 鼓/贝斯/和声 MIDI）
        │
        ▼
F DAW / ACE 编曲混音（#56 DAW 选型 → #55 干扰预防 → #58 混音检查 → #59 输出 → #60 分发前验证）
        │
        ▼
   [G4] 最终 QC 通过（gate_G4 / #61 g4_checklist.py）── 不过 → 打回 F 段
        │
        ▼
   输出 master WAV/MP3 + 分轨 stem + 哈希 manifest（#59）
```

门位置小结：**G1** 在 A→B 之间（图像准入）、**G2** 在 C→D 之间（谱面冻结）、**G3** 在 D→E 之间（声乐冻结）、**G4** 在 F→输出之间（成品准入）。

## 3. 文档地图与执行顺序（#63）

执行顺序**恒为 01→02→03→04**，前一文件终止条件未满足不得进入下一步。

| 阶段 | 文档 / 目录 | 责任工具 | 主门 |
|---|---|---|---|
| 00 | `../`（gates / deliverables / failure_recovery / glossary） | — | 定义 |
| 01 | `../`（preflight 规划） | WorkBuddy / CodeBuddy | — |
| 02 | `01_preflight/`（install_versions / risk_register / issue_log） | 安装类工具 + ACE MCP | — |
| 03 | `02_source/` → `03_omr/` → `04_approved/` | Audiveris / MuseScore | G1 / G2 |
| 04 | `05_vocals/` → `06_arrangement/` → `07_mix/` → `08_qc/` | ACE / SynthV / DAW / tools | G3 / G4 |

关联文档：
- 门标准：`../gates/gate_G1.md` ~ `gate_G4.md`
- 交付物落位：`../deliverables.md`
- 失败恢复：`../failure_recovery.md`（#62）
- 提示词与安全：`../arrangement_prompts.md` / `../automation_safety_rules.md`（#57）
- 工具矩阵：`02_tool_selection_matrix.md`（#10）
- 快捷执行：`03_quickstart_10min.md`（#64）
