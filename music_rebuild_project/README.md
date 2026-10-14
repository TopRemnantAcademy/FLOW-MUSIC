# music_rebuild_project

OMR 乐谱重建管线的工作目录（阶段1 目录标准）。这不是 FLOW-MUSIC 代码平台的一部分，而是「乐谱 → 数字乐谱 → AI 보컬 → 多乐器音源」的人工+工具流水线载体。

## 目录树

```text
music_rebuild_project/
├── README.md                         # 树总说明 + 三层数据成熟度 + 并行策略
├── 00_docs/                          # 规划 / 门标准 / 交付物清单
│   ├── README.md
│   ├── plan/                         # 01~05 规划书索引
│   ├── gates/                        # G1~G4 判定标准 gate_G1.md..gate_G4.md
│   ├── deliverables.md               # 交付物→目录落位映射（权威见 #7）
│   └── glossary.md                   # song_id / gate / approved / retest
├── 01_preflight/                     # 环境预检证据（阶段2）
│   ├── README.md
│   ├── install_versions.csv          # tool,version,source,checksum,installed_at,verified_by
│   ├── risk_register.md              # 风险登记表
│   └── issue_log/                    # env_YYYY-MM-DD_<slug>.md
├── 02_source/                        # 原始乐谱 · 只进不改
│   ├── README.md
│   └── <song_id>/
│       ├── originals/                # 扫描件 / 照片 / PDF 原样保留
│       ├── preprocessed/             # 预处理后图像（#30）
│       └── manifest.md               # 来源 页数 DPI 时间 sha256 采集人
├── 03_omr/                           # OMR 识别区 · 可重建
│   ├── README.md
│   └── <song_id>/
│       ├── raw/                      # Audiveris 原始输出 .mxl/.musicxml/.log
│       ├── review/                   # MuseScore 审校工作版
│       ├── retest/                   # 逐曲重测参数与结果对比
│       └── reports/                  # error_report_vNNN.md
├── 04_approved/                      # 审校批准版 · 只读冻结
│   ├── README.md
│   └── <song_id>/
│       ├── ground_truth.musicxml
│       ├── ground_truth.musicxml.sha256
│       └── approval.md               # 批准人 时间 判据 → 链接 gate_log
├── 05_vocals/                        # 보컬生成区（ACE / Synthesizer V）
│   ├── README.md
│   └── <song_id>/
│       ├── projects/                 # 宿主工程 .acep/.svp
│       ├── clips/                    # 音符/歌词/时序 MIDI 或 clip
│       ├── vocal_wav/                # 渲染 vocal WAV
│       └── vocal_settings.json       # 引擎 音色 参数 + 所依据 ground_truth 哈希
├── 06_arrangement/                   # 编曲 MIDI
│   ├── README.md
│   └── <song_id>/
│       ├── drums/  bass/  chords/
│       └── arrangement.json          # 声部清单 音源映射 BPM 调号 对齐基准
├── 07_mix/                           # DAW 工程 / stem / 成品
│   ├── README.md
│   └── <song_id>/
│       ├── project/                  # DAW 工程（只允许相对路径引用）
│       ├── stems/
│       ├── masters/                  # master WAV / 共享 MP3
│       └── dist/                     # 分发文件夹（过 G4 只读）
├── 08_qc/                            # 门判定记录与 QC 报告
│   ├── README.md
│   └── <song_id>/
│       ├── gate_log.md               # 逐门判定，一行一条，不改历史行
│       └── reports/                  # qc_report_vNNN.md
├── 09_archive/                       # 废弃版本 · 只进不改
│   ├── README.md
│   └── YYYY-MM/<原路径镜像>/
│       └── ARCHIVE_NOTE.md           # 废弃原因 替代版本 时间 决策人
└── tools/                            # 校验工具（无媒体）
    ├── README.md
    ├── arrangement/                  # chords.txt 规范化/校验（#50）
    ├── checks/                       # check_ascii.sh / verify_hashes.sh / check_ro.sh
    ├── midi/                         # 编曲 MIDI 生成（#52/#53/#54）
    ├── naming/                       # 命名与冻结规则（#13）
    └── render/                       # 输出格式工具包（#59）
```

> 相比 #12 issue 里给的骨架，保留了 `00~09` 主体，只新增 `tools/`（校验脚本）——ASCII 检查、哈希校验、只读校验得有家，否则规则只停在文档上。

## 路径口径（已裁决）

目录以 `01_preflight/02_source/03_omr/04_approved/05_vocals/06_arrangement/07_mix/08_qc/09_archive` 一套为准。此前的 `00_source/02_omr_candidates/03_score_review/04_vocal/05_arrangement_midi/06_daw/07_renders` 一套**作废**。对照表见 `00_docs/deliverables.md`。

## 三层数据成熟度

**L1 随便改，L0 只加不改，L2 加新版本不改老文件。**

| 层 | 目录 | 可写性 |
|---|---|---|
| L0 | `02_source/` | 只进不改（append-only） |
| L1 | `03_omr/` `05_vocals/` `06_arrangement/` `07_mix/` | 可覆盖，可重建 |
| L2 | `04_approved/` `08_qc/` `09_archive/` | 只读 / 只追加 |

每个目录的 README 都按 **放什么 / 不放什么 / 只读规则** 三段写。

## 多曲并行策略

推荐「单项目 + 每曲 `<song_id>` 子目录」，不推荐单曲单项目。理由：

1. 门标准、工具版本、校验脚本是**跨曲共享资产**，单曲单项目 = 同一份标准 N 副本，必然漂移。
2. 只读加固（#27）一旦按曲拆项目，就要重复配置 N 次，漏一个就是漏洞。
3. 阶段1→4 是**同一条流水线**，曲与曲只差数据不差流程；用 `<song_id>` 隔离数据、根级共享流程，维护成本最低。
4. 批量校验（ASCII / 命名 / 哈希 / 孤儿文件）一次扫全树，单曲独立项目做不到。

`song_id` 规范：`song01`、`song02`…（ASCII 定长，超 99 用三位），同一 id 在全部目录保持一致。

## 文件命名规范（占位，细则归 #13）

- 版本号 `vNNN` 从 `v001` 递增，**新版本 = 新文件**，老文件永久保留。
- 形态：`<song_id>_<artifact>_vNNN.<ext>`，例：`song01_ground_truth_v001.musicxml`。
- 冻结版本的哈希落在同目录 `*.sha256`。
- 本文档**不定义**细则（分隔符、大小写、时间戳等），全部指向 #13，避免两处定义打架。

## Windows 路径风险规避（强制）

1. **一律 ASCII**：目录/文件名只允许 `[A-Za-z0-9._-]`，禁中文、空格、韩文、emoji。
2. **大小写不敏感**：NTFS 下 `Stems/` 与 `stems/` 同目录 → 全树统一**小写**。
3. **长度**：单段 ≤ 40 字符，全路径 ≤ 200 字符（给 260 上限留余量）。
4. **保留名/字符**：不用 `CON PRN AUX NUL COM1..9 LPT1..9`，不用 `<>:"|?*`，不以 `.`/空格 结尾。
5. **编码**：统一 UTF-8 + LF，禁非 UTF-8 文件名，防 Git 乱码与 pathspec 失配。
6. 提交前跑 `tools/checks/check_ascii.sh`（占位，随 #27 落地）。

## 交叉引用

- #7 交付物落位（`00_docs/deliverables.md`）
- #11 门 G1-G4（`00_docs/gates/gate_G1.md` ~ `gate_G4.md`）
- #12 目录树（本文件）
- #13 命名与冻结规则（本树只留占位）
- #27 `02_source/` 与 `04_approved/` 文件系统级只读加固
