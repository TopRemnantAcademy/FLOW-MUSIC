# tools/arrangement · 编曲前置校验 + 和弦文本规范化（#48 + #50）

本包覆盖编曲阶段的两件「文本层」工具，互不重复实现：

- **preconfirm（#48）**：编曲规格 JSON 动手前的最后一道闸 —— schema 校验 + 退出码语义。
- **chords（#50）**：逐小节和弦标记（自由文本 / JSON）规范化 → 可校验、可复用的 `chords.txt`，供 `tools/midi`（#52/#53/#54）消费。

## 边界（先读这段）

- **只校验 / 规范化文本，不生成任何 MIDI。** 生成是 `tools/midi` 的事。
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
| `tests/test_preconfirm.py` | 字段级覆盖矩阵（84 例）；CLI 用真子进程量退出码 |
| `tests/test_chords.py` | 覆盖矩阵（合法/非法根音、性质、转位、多和弦、空行、行号报错、JSON 三种形态、输出契约、CLI 退出码、与 `tools/midi` 的 parity） |

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

## 跑测试

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
