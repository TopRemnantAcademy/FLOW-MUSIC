# song01 · G1 门判定记录（#31 · 图像质量验收）

> 模板来源 `08_qc/gate_g1_report.md`。本报告由 `tools/checks/g1_image_quality.py --json` 输出填充，SKIP 项待人工补判。

## 0. 批次与阈值

| 项 | 值 |
|---|---|
| song_id | song01 |
| 标题 | 小小望楼（弗 3:8） |
| 输入目录 | `02_source/song01/preprocessed/`（`originals/` 因缺 embedded DPI 未过 G1） |
| 工具版本 | `g1_image_quality.py` @ `ff0e101` |
| 阈值快照 | `08_qc/gate_config.json` |
| 报告日期 (UTC) | 2026-10-16 |
| 报告人 | irewood |

## 1. 逐页判定表

| 文件 | 容器 | 子类型 | dpi | 短边 px | G1-1 | G1-8 | G1-9(目录级) | 文件结论 |
|---|---|---|---|---|---|---|---|---|
| `song01_preprocessed_v001.jpeg` | jpeg | raster | 300 | 2480 | PASS | PASS | PASS | PASS |

## 2. 工具 SKIP 项（必须人工补判，否则不得提 G2）

| G1 项 | 判据 | 人工结果（✓/✗ + 截图证据） |
|---|---|---|
| G1-1 线距≥8px | 五线谱线距 | 待补判 |
| G1-2 歪斜角 | 绝对值 <1°（<2° 可 NOTE） | 待补判 |
| G1-3 裁切完整性 | 首/末小节与页码标记未被切 | 待补判 |
| G1-4 顺序/缺页 | 页码序列严格一致、缺页=0 | 单页，无需排序 |
| G1-5 文字可辨 | 歌词逐行可读、字高≥16px | 待补判 |
| G1-6 音符可区分 | 音符头/符尾/附点/连音线/临时记号可区分 | 待补判 |
| G1-7 信息密度 | 每页系统数≤6、小节数≤24 | 待补判 |

## 3. 门控结论

| 检查 | 结果 |
|---|---|
| 有任一项 FAIL？ | **N**（preprocessed 目录级无 FAIL） |
| SKIP 项是否全部人工补判且通过？ | **N**（7 项仍待补判） |
| G1-8 是否拦截了手写/低质/畸变页（未进自动 OMR）？ | **Y**（子类型 raster，手写待人工确认） |
| G1-9 原始 `02_source/` 是否哈希一致、未被改？ | **Y**（`originals/` 与 `preprocessed/` 各 Manifest 一致） |
| **提 G2** | **N**（SKIP 项未补判） |

## 4. 原始目录 FAIL 说明

`02_source/song01/originals/song01_original_v001.jpeg` 因无 embedded DPI 元数据，G1-1 被判 FAIL。该图尺寸 2480×3508 px 等价于 A4 @ 300 dpi，故 `preprocessed/` 版本仅补 JFIF 300 dpi 元数据、未改像素，原始文件仍原样保留于 `originals/`。

**签字**：irewood　日期 2026-10-16
