# L1 合成标定集（G1 自动判定）

- 生成器/测量工具：`music_rebuild_project/tools/checks/g1_calibrate.py`
- 口径：**只覆盖「测量 → 阈值 → 三态」链路**（合成样本）。真实世界准确率仍需 L3 标注集。
- 最新报告：`l1_synthetic_report.json`（`python tools/checks/g1_calibrate.py --report <该文件>`）

## 复现

```bash
# 纯 CV 测量（无需模型，秒级）；--fail-under 未达标会非零退出
python music_rebuild_project/tools/checks/g1_calibrate.py --json --fail-under 0.99

# 叠加 OMR 系统维度探测（需要 oemer；不可用会如实报 SKIP）
python music_rebuild_project/tools/checks/g1_calibrate.py --engine oemer --json
```

## 网格（含 issue #163 增补要求）

- 线距 `s ∈ {5,6,8,10,14,20} px` × 歪斜 `a ∈ {0,0.3,0.8,1.2,2.5}°`
- **systems `K ∈ {4,6,8,10}`**（增补要求）× 页边留白 `bbox ∈ {0,16,48} px`
- 默认 360 个样本（`--repeat` 可扩样），每样本注入已知真值。

## 判定真值（来自 `00_docs/gates/gate_G1.md`）

| 项 | 真值 |
|---|---|
| 线距 | `s≥8 → PASS`；`6≤s<8 → NOTE`；`s<6 → FAIL` |
| 歪斜 | `\|a\|<1 → PASS`；`<2 → NOTE`；`≥2 → FAIL` |
| 内容余量 | 触边(0) → FAIL；有余量 → PASS |
| 系统数 | `K ≤ systems_max` → PASS；否则 FAIL |

`false_pass`（真值 FAIL → 自动 PASS）必须为 **0**；出现即非零退出。

## G1-7 阈值（`systems_max`）

标定分**两条腿**，缺一不可：

1. **几何侧**（本工具默认跑，确定性）：K∈{4,6,8,10} 时线距测量完备率、谱面纵向跨度是否装得下。
2. **OMR 侧**（`--engine oemer`，依赖引擎）：注入 K 个系统 → 检出 `systems` 是否等于 K。

**纪律**：只有当 OMR 侧给出「K>6 仍能完整识别」的实测数据，才允许考虑放宽 `systems_max`；
在此之前维持 6，song01（8 系统）继续 **FAIL（待标定 + 拆页/重拍）**，不做人工裁定。
