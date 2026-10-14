# tools/arrangement · 编曲前确认校验器（#48）

管道 **E 段（编曲）** 动手前的最后一道闸：把「编曲规格」本身校验一遍，
免得 agent 拿着缺字段/自相矛盾的半成品规格开始生成。

## 边界（先读这段）

- **只校验规格文本，不生成任何 MIDI。** 生成是 `tools/midi` 的事（#52/#53/#54）。
- **不判音乐好坏。** 调外和弦只给 WARN，因为借用和弦是合法手法；
  判定权在编写规格的人手里，工具只负责把可疑处**指出来**。
- **纯 stdlib**（`json` / `re` / `dataclasses` / `argparse`），无 RNG、无第三方依赖。
- 不碰 `backend/**`、`models.py`、`migrations/`；不新增 DB 表。
- 调号 / 拍号 / 和弦符号的词汇表**复用** `tools/midi/sections` 与 `tools/midi/bass`，
  本包不另写一份解析器（重复实现 = 迟早两处不一致）。

## 文件

| 文件 | 职责 |
|---|---|
| `preconfirm.py` | 规格 schema 校验 + 退出码语义（本包唯一入口） |
| `tests/test_preconfirm.py` | 字段级覆盖矩阵（84 例）；CLI 用真子进程量退出码 |

## 用法

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

## 规格 schema

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

## 退出码 vs finding 级别

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
