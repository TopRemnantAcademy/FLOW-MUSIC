# song01 · 门控判定日志（L2 · 只追加）

| 时间 (UTC) | 门 | 输入目录 | 结论 | 关键依据 |
|---|---|---|---|---|
| 2026-10-16 | G1 | `02_source/song01/originals/` | **FAIL** | 无 embedded DPI；短边 2480 px 达标但 dpi 不可测（G1-1） |
| 2026-10-16 | G1 | `02_source/song01/preprocessed/` | **PASS** | dpi=300，短边=2480，G1-9 哈希一致；7 项 SKIP 仍须人工补判方可提 G2 |

> 注：`preprocessed/` 版本仅在 `originals/` 原样保留基础上补了 JFIF 300 dpi 元数据，像素未改动。
