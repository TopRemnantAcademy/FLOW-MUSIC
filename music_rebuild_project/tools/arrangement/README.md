# tools/arrangement · 编曲前置校验 + 和弦文本规范化 + 段落标注（#48 + #50 + #51）

本包覆盖编曲阶段的三件「文本层」工具，互不重复实现：

- **preconfirm（#48）**：编曲规格 JSON 动手前的最后一道闸 —— schema 校验 + 退出码语义。
- **chords（#50）**：逐小节和弦标记（自由文本 / JSON）规范化 → 可校验、可复用的 `chords.txt`，供 `tools/midi` 消费。
- **sections（#51）**：声明式 spec → 机器可读 `sections.json`（逐段秒/帧起止），供下游混音/母带/视频对齐消费。

## 边界（先读这段）

- **只校验 / 规范化文本，不生成任何 MIDI / 音频。** 生成是 `tools/midi` 的事。
- **不判音乐好坏。** 调外和弦只给 WARN（借用和弦是合法手法）；判定权在写规格的人。
- **纯 stdlib**（`json` / `re` / `dataclasses` / `argparse`），无 RNG、无第三方依赖。
- 不碰 `backend/**`、`models.py`、`migrations/`；不新增 DB 表。
- 调号 / 拍号 / 和弦符号的词汇表**复用** `tools/midi/sections` 与 `tools/midi/bass`，
  本包不另写一份解析器（重复实现 = 迟早两处不一致）。
- **只读输入、只写调用方指定的 `--out`**，不猜路径、不写数据目录。

## 文件

| 文件 | 职责 |
|---|---|
| `preconfirm.py` | 规格 schema 校验 + 退出码语义（#48 入口） |
| `chords.py` | 解析（自由文本 / JSON）→ 规范化 → 校验 → 渲染 `chords.txt`，含 CLI（#50 入口） |
| `sections.py` | spec 解析 → 逐段秒/帧起止 → 字段级校验 → `sections.json`；含 CLI（#51 入口） |
| `tests/test_preconfirm.py` | 字段级覆盖矩阵（84 例）；CLI 用真子进程量退出码 |
| `tests/test_chords.py` | 覆盖矩阵（合法/非法根音、性质、转位、多和弦、空行、行号报错、JSON 三种形态、输出契约、CLI 退出码、与 `tools/midi` 的 parity） |
| `tests/test_annotate.py` | 校验与时间轴的单元测试 |
| `tests/test_cli.py` | CLI 端到端：产物落盘 + 退出码语义 |

## preconfirm（#48）用法

```bash
python -m music_rebuild_project.tools.arrangement.preconfirm --spec spec.json
python -m music_rebuild_project.tools.arrangement.preconfirm --spec spec.json --strict
```

stdout 恒为 JSON 报告；退出码：

| 码 | 含义 |
|---|---|
| `0` | PASS（可含 WARN） |
| `1` | FAIL：至少一条 FAIL；`--strict` 时 WARN 也升级为 1 |
| `2` | 用法错误：参数错 / 文件不存在 / JSON 解析失败 |

### 规格 schema（preconfirm）

```json
{
  "song_id": "song01",
  "key": "F#m",
  "time_sig": "4/4",
  "tempo": 120,
  "bars": 12,
  "repeats": 1,
  "sections": [{"name": "Intro", "bars": 4}, {"name": "Verse", "bars": 8}],
  "chord_marks": {"1": "F#m", "2": "Bm", "3": "C#7"}
}
```

| 字段 | 类型 | 必填 | 校验 |
|---|---|---|---|
| `key` | str | 是 | 合法调号；大小写/别名归一（`am`→`Am`、`cMaj`→`C`） |
| `time_sig` | str | 是 | 白名单 `4/4` `3/4` `6/8` |
| `tempo` | int | 是 | ∈ [40, 240] |
| `bars` | int | 是 | 正整数，≤ 999 |
| `sections` | list | 否 | `bars` 之和须 = `bars`；段落名须为规范的六段落之一 |
| `repeats` | int | 否 | ≥ 0；给出时提示演奏遍数 |
| `chord_marks` | dict / list | 否 | 逐小节和弦，1-based；数量须 = `bars`（或显式 `TBD`） |

`chord_marks` 两种写法等价：

```json
{"1": "C", "2": "G"}      // dict：键为 1-based 小节号
["C", "G"]                // list：按顺序逐小节
```

### 退出码 vs finding 级别（preconfirm）

| 校验项 | 级别 | code |
|---|---|---|
| 必填字段缺失 | FAIL | `MISSING` |
| 字段类型错 | FAIL | `TYPE` |
| 调号 / 拍号 / 和弦格式非法 | FAIL | `INVALID` |
| tempo / bars / repeats 越界 | FAIL | `RANGE` |
| 段落小节和 ≠ bars | FAIL | `BAR_MISMATCH` |
| 和弦数量 ≠ bars | FAIL | `COUNT_MISMATCH` |
| 和弦键非正整数小节号 | FAIL | `KEYS` |
| 调号被归一 | WARN | `NORMALIZED` |
| 调外和弦占比 > 50% | WARN | `OUT_OF_KEY` |
| 存在待定和弦 | WARN | `TBD` |
| 未提供 `chord_marks` | WARN | `OMITTED` |
| `repeats` 影响演奏小节数 | WARN | `REPEAT_NOTE` |

> 待定和弦是 WARN 不是 FAIL：`arrangement_precheck.md`（#48 I-3）允许先记「有无和弦符号」，
> 编曲固化前由人工批准（#49）——工具只保证「不静默跳过」，不替人做批准。

## chords（#50）用法

把「逐小节和弦标记」变成**规范化、可校验、可复用**的文本资产 `chords.txt`，
供 `tools/midi`（#52/#53/#54）消费。

### 输入形态

```text
1 Cmaj7                 小节号 + 和弦（1-based，音乐人习惯）
2 Am7
3 Dm7 G7                同一小节内多个和弦，空格分隔
4 Am7, G7 | Cmaj7       逗号 / 竖线分隔等价
Cmaj7                   裸和弦：按出现顺序自动编号
5 Cmaj7                 显式小节号之后的裸行按 +1 顺延
```

JSON（`--in` 指向 `.json`，或内容以 `{`/`[` 开头自动识别）：

```json
{"1": "Cmaj7", "2": "Dm7 G7"}
["Cmaj7", "Am7"]
[{"bar": 2, "chord": "M7"}, {"bar": 1, "chords": ["F", "C"]}]
```

### 规范化规则

| 维度 | 规则 |
|---|---|
| 根音 | `A-G` + 可选单个 `#`/`b`；大小写均接受，输出首字母大写 |
| 性质 | `maj`(默认) / `min` / `dom7` / `maj7` / `min7` / `sus2` / `sus4` / `dim` / `aug` |
| 同义写法 | `M`→`maj`、`m`/`-`→`min`、`m7`/`-7`→`min7`、`7`→`dom7`、`M7`/`ma7`/`Δ7`→`maj7`、`°`/`o`→`dim`、`+`→`aug` |
| 无根音形态 | `M7` / `-7` / `Δ7` 默认建在 **C** 上（`M7` → `Cmaj7`） |
| 低音 | 可选 `/低音`（如 `Cmaj7/E`）；低音也是合法音名 |
| 输出 | 每行 `<小节号> <和弦>[ <和弦>...]`，1-based 升序连续；`maj` 不写后缀 |

### 校验

- 非法根音 / 性质 / 低音 → `ChordSyntaxError`，带 `line`（行号）、
  `index`（行内序号）、`text`（原文）、`reason`（人读原因）。
- 小节号**必须连续且从 `--first-bar` 起**；重复、缺失、越界一律报错。
- `--expect-bars N` 额外要求恰好覆盖 N 个小节（接段落/全曲小节数）。

### CLI

```bash
# 规范化 → chords.txt
python -m music_rebuild_project.tools.arrangement.chords \
    --in  06_arrangement/song01/chords_raw.txt \
    --out 06_arrangement/song01/chords.txt

# 只校验（不写盘）
python -m music_rebuild_project.tools.arrangement.chords --in chords_raw.txt --validate

# 输出 {0-based 小节: 和弦} JSON，直接喂 tools/midi.SongContext.chords
python -m music_rebuild_project.tools.arrangement.chords --in chords.txt --map

# 只规范化某段落时对齐全局小节号
python -m ...chords --in verse_raw.txt --first-bar 5 --out verse_chords.txt
```

**退出码语义（chords）**

| 码 | 含义 | 触发 |
|---|---|---|
| 0 | 通过 / 写出成功 | 校验无误；`--out` 写入成功 |
| 1 | 校验失败 | 非法标记、小节不连续/重复/越界、`--expect-bars` 不符、JSON 解析失败 |
| 2 | 用法或读取错误 | 输入文件不存在/不可读、`--out` 无法写入 |

## 跑测试（preconfirm / chords）

```bash
cd music_rebuild_project
python -m pytest tools/arrangement/tests/ -q
```

## 与 `tools/midi` 的边界（只桥接，不重写）

- **不重复实现** MIDI 生成：本包只产出文本，`Chord`/`normalize_chord` 是
  **规范化层**（带行号错误 + 别名归一），与 `midi.bass.ChordSymbol`（生成层）
  对合法规范输入口径一致，有 `test_parity_with_midi_bass` 钉住。
- `tools/midi/sections.py::parse_chords_txt` 是**宽松读取器**（畸形行跳过，给生成侧兜底）；
  本包的 `load_chord_map()` / `--map` 是**严格校验器**（畸形行报错）。用哪个取决于
  你是要「跑起来」还是「卡住」。

## sections（#51）用法

把编曲输出的「段落结构」变成**机器可读、可校验**的标注，供下游（混音 / 母带 / 视频对齐）消费。

一句话：**段落是人写的，时间轴得是算出来的。** 本工具负责后者。

```bash
cd music_rebuild_project

# 声明式 spec → sections.json（+ 人读的 markdown 表）
python -m music_rebuild_project.tools.arrangement.sections \
    --spec 06_arrangement/song01/sections.spec.json \
    --out  06_arrangement/song01/sections.json \
    --md   06_arrangement/song01/sections.md
```

退出码：`0` = 通过，`1` = 校验失败（错误打到 stderr，**不静默退出**），`2` = spec 读不到或不是合法 JSON。

### 放什么（sections）

| 文件 | 职责 |
|---|---|
| `sections.py` | spec 解析 → 逐段秒/帧起止 → 字段级校验 → `sections.json`；含 CLI |
| `tests/test_annotate.py` | 校验与时间轴的单元测试（覆盖矩阵见文末） |
| `tests/test_cli.py` | CLI 端到端：产物落盘 + 退出码语义 |

不放什么：任何 MIDI / 音频 / 工程文件（本工具**不产媒体**）；段落模型本身 → 复用 `tools/midi/sections.py`；数据目录的落点 → 由调用方的 `--out` 决定，工具自己不猜路径。

### 边界（sections，先读这段）

- **不重复实现 `tools/midi`。** 规范段落名（`Intro/Verse/Pre-chorus/Chorus/Bridge/Outro`）、
  别名收敛、`SectionPlan` 的「无损覆盖」不变量，`tools/midi/sections.py` 已有一份
  （#48/#50/#51 共用）。本工具 `import` 复用；复制一份出来等于让两处规则各自漂移。
  测试里 `test_section_types_track_tools_midi` 把这条钉死。
- **不依赖 `tools/render` / `tools/checks` / `tools/naming`**：只按约定读写 JSON。
  校验结果里的「类型非法/重叠/间隙」是本工具自己的语义，不重写 G1–G4 的阈值定义。
- **不碰后端**：不改 `backend/**`、`models.py`、`migrations/`，不新增迁移或 DB 表。

### 输入契约：`sections.spec.json`

人写的最小声明。所有字段除 `sections` 外都有默认值。

```json
{
  "bpm": 120,
  "beats_per_bar": 4,
  "fps": 30,
  "duration_s": 68.0,
  "duration_tolerance_s": 1.0,
  "allow_gap": false,
  "extra_types": ["Rap"],
  "sections": [
    { "type": "Intro",      "bars": 4 },
    { "type": "Verse",      "bars": 8, "label": "Verse 1" },
    { "type": "Pre-chorus", "bars": 4 },
    { "type": "Chorus",     "bars": 8 },
    { "type": "Bridge",     "bars": 4, "bpm": 100 },
    { "type": "Outro",      "bars": 6 }
  ]
}
```

#### 顶层字段

| 字段 | 默认 | 说明 |
|---|---|---|
| `bpm` | `120` | 全曲默认速度；每段可用自己的 `bpm` 覆盖 |
| `beats_per_bar` | `4` | 每小节拍数，同上可被每段覆盖 |
| `fps` | `30` | 帧率（非 drop-frame），只影响派生的 `start_frame` / `end_frame` |
| `duration_s` | 无 | 公布的预期时长；给出后会按容差校验（G4-5 的 ±1s 同源） |
| `duration_tolerance_s` | `1.0` | 上项的容差（秒） |
| `allow_gap` | `false` | 是否允许段落之间**显式声明**的静默（见下） |
| `extra_types` | `[]` | 额外放行的段落类型（等价于 CLI 的 `--extra-type`，可多个） |
| `sections` | 必填 | 非空数组 |

#### 每段字段

| 字段 | 默认 | 说明 |
|---|---|---|
| `type` | 必填 | 段落类型，见「枚举约束」。前后空白会被吃掉 |
| `bars` | 与 `end_bar` 二选一 | 本段小节数（正整数） |
| `end_bar` | 与 `bars` 二选一 | **1-based 闭区间末小节**。`Intro` 4 小节写 `end_bar: 4` |
| `start_bar` | 紧随上一段 | **1-based** 起始小节；首段固定为 `1` |
| `bpm` / `beats_per_bar` | 继承顶层 | 本段生效值（**段内不变速**，spec 的粒度就是「一段一个 BPM」） |
| `label` | `""` | 实例标记，如 `Verse` 出现两次时用 `"Verse 1"` / `"Verse 2"` |
| `gap_before_s` | `0` | 本段**之前**的静默时长（秒）。首段不可用；需顶层 `allow_gap: true` |

> **`end_bar` 还是 `bars`？** 两者等价，给一个就行（都写时以 `end_bar` 为准）。
> 谱面会话用 `end_bar`（跟乐谱一致），脚本生成用 `bars`（跟算法一致）。

#### 枚举约束

- 规范类型（`tools/midi` 的 `SECTION_ORDER`）：`Intro`、`Verse`、`Pre-chorus`、`Chorus`、`Bridge`、`Outro`
- 附带放行：`Instrumental`、`Post-chorus`、`Solo`、`Interlude`、`Break`
- 别名（沿用 `tools/midi` 的别名表，**放行并原样保留**，只给一条 WARN 提示规范写法）：
  `V` / `ch` / `coda` / `pre` / `br` 等
- 其它自定义类型：**必须**在 `extra_types` 或 `--extra-type` 里显式放行。
  这是 #51「允许自定义但需枚举约束」的落地方式 —— 不做「未知就当合法」的猜测。
- 比较**大小写不敏感**（`Pre-Chorus` 与 `Pre-chorus` 同义），输出保留你写的那个名字。

### 输出契约：`sections.json`

```json
{
  "schema_version": 1,
  "fps": 30,
  "bpm": 120,
  "beats_per_bar": 4,
  "bars": 34,
  "duration_s": 68.0,
  "type_counts": { "Intro": 1, "Verse": 1, "Pre-chorus": 1, "Chorus": 1, "Bridge": 1, "Outro": 1 },
  "extra_types": [],
  "sections": [
    {
      "index": 0,
      "type": "Intro",
      "start_bar": 0,
      "end_bar": 4,
      "start_s": 0.0,
      "end_s": 8.0,
      "duration_s": 8.0,
      "start_frame": 0,
      "end_frame": 240,
      "bpm": 120,
      "beats_per_bar": 4
    }
  ],
  "validation": { "ok": true, "errors": [], "warnings": [] }
}
```

约定三条：

1. **`start_bar` / `end_bar` 是 0-based 左闭右开**，与 `tools/midi.Section` 同口径
   （`bars = end_bar - start_bar`，相邻段 `prev.end_bar == next.start_bar`）。
   注意这与 spec 里人写的 **1-based 闭区间** 不同 —— 换算只发生在工具内部，
   JSON 里一眼能对上的还是上面这套。
2. **时间按段累加**：段内时长 = `bars × beats_per_bar × 60 / bpm`，
   前段结束秒即后段起始秒（有 `gap_before_s` 时加上那个间隙）。
   `duration_s` 是全曲累加值，可直接喂给 G4-5 的预期时长。
3. **`validation` 随产物一起落盘**：下游拿到的 JSON 自己就说明「这份标注过没过校验」。
   失败时也会落盘（含算得出来的部分），方便按 `path` 逐条改 spec。

`--md` 会额外产出一张人读表：

| # | 段落 | 小节 | 时间（s） | 帧 | BPM | 拍号 |
|---|---|---|---|---|---|---|
| 0 | Intro | 0–4 | 0.000–8.000 | 0–240 | 120 | 4/4 |

### 校验规则

| code | 语义 | 示例 `path` |
|---|---|---|
| `spec_invalid` | 顶层不是对象 / `sections` 非数组 | `$` |
| `invalid_field` | 字段类型或取值非法（含 `bool` 冒充整数、`4.5` 非整数） | `$.sections[1].bars` |
| `unknown_type` | 段落类型不在枚举内且未显式放行 | `$.sections[2].type` |
| `missing_bars` | 既没 `bars` 也没 `end_bar`，长度无法确定 | `$.sections[0].bars` |
| `overlap` | 与上一段**重叠**（同一小节属于两段） | `$.sections[1].start_bar` |
| `gap` | 与上一段之间**留空小节**且未用 `gap_before_s` 说明 | `$.sections[1].start_bar` |
| `unexpected_gap` | 声明了 `gap_before_s` 但顶层没开 `allow_gap` | `$.sections[1].gap_before_s` |
| `discontinuous` | 时间轴断裂（秒对不上且非声明的静默） | `$.sections[1]` |
| `structure_invalid` | 结构与 `tools/midi` 的不变量不一致 | `$.sections` |
| `duration_mismatch` | 累加时长与 `duration_s` 差出容差 | `$.duration_s` |
| `type_canonical_hint` | **WARN**：别名/大小写可以写成规范名 | `$.sections[0].type` |

规则口径：

- **不重叠**：严格首尾相接。重叠段的时间轴无法定义（同一小节归属谁？），
  所以报 `overlap` 并**跳过该段**，不把脏数据算进时间轴。
- **连续**：允许间隙，但**必须显式**。两条路：
  把小节回收进相邻段落（默认），或用 `gap_before_s` + `allow_gap: true` 声明静默。
  「工具没发现」不算说明 —— 这是 #51 那句「允许间隙但须显式说明」的实现。
- **时长**：`duration_s` 给出后按 `duration_tolerance_s`（默认 ±1s，与 G4-5 同源）校验。
  注意它校验的是**声明值之间**的一致性，不是 master 实际时长 ——
  G4-5 的最终判定仍在 #60 的 `ffprobe` 上做。

### 测试覆盖矩阵（sections）

```bash
python -m pytest music_rebuild_project/tools/arrangement/tests/ -q    # 72 passed
```

| 类别 | 用例 |
|---|---|
| 正常链 | `test_full_song_covers_every_bar`、`test_two_bars_spec_gives_exact_seconds`、`test_end_bar_form_is_equivalent_to_bars_form` |
| 重叠报错 | `test_overlap_by_start_bar_is_rejected`、`test_overlapping_section_is_excluded_from_timeline` |
| 类型非法 | `test_unknown_type_is_rejected[*]`、`test_extra_type_is_explicitly_allowlisted` |
| 间隙 | `test_bar_gap_is_reported`、`test_declared_silence_requires_allow_gap`、`test_declared_silence_with_allow_gap_shifts_time` |
| BPM 变化 | `test_per_section_bpm_accumulates_seconds`、`test_per_section_beats_per_bar`、`test_variable_bpm_does_not_break_continuity_check` |
| 时长容差 | `test_duration_within_one_second_passes[*]` / `test_duration_outside_one_second_fails[*]` |
| 字段级校验 | `test_field_level_errors_carry_a_path[*]`（12 组 bad spec） |
| 复用一致性 | `test_section_types_track_tools_midi`、`test_structure_matches_tools_midi_section_plan` |
| 确定性 | `test_annotation_is_deterministic`、`test_cli_output_is_byte_stable` |
| 退出码 | `test_cli_exit_code_1_on_validation_failure[*]`、`test_cli_exit_code_2_*` |

### 诚实边界（sections）

- **段内不变速。** spec 的粒度是「一段一个 BPM」；段内渐快 / 渐慢（ritardando）表达不了，
  要标就切段。做加速度曲线需要另一套输入（MIDI tempo map），不在 #51 范围。
- **`fps` 只是换算。** 帧号按秒 × fps 取整得出，不是 drop-frame 时间码，
  也没做视频对齐的帧级校准 —— 那是下游剪辑工具的事。
- **不做段落识别。** 段落是**人声明的**；本工具不从音频/MIDI 反推结构。
- **`duration_s` 不是 G4-5 的判定。** 它给的是「按标注算，应该是多长」，
  实际 master 时长仍由 F 段渲染 + #60 脚本测。
