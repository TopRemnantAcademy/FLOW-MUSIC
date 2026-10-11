# 术语表

| 术语 | 含义 |
|---|---|
| `song_id` | 曲目稳定标识，ASCII 定长：`song01`、`song02`…（超 99 用三位）；同一 id 在全部目录保持一致 |
| `gate` | 质量门 G1-G4，定义见 `gates/gate_G1.md` ~ `gate_G4.md` |
| `approved` | 过门即冻结的批准件（G2 谱面、G3 보컬），只读不可原地修改 |
| `retest` | 逐曲重测：换参数/工具后重新识别并对比，胜者选定写入 `omr_selection.md`（#34） |
| `PASS` / `PASS_WITH_NOTES` / `FAIL` | 门判定三态；`PASS_WITH_NOTES` 须记录已知偏差并追认 |
| `gate_config.json` | 阈值快照，逐项含 `value / adjustable / changed_by / changed_at / reason` |
| `ground_truth` | 过 G2 的冻结基准谱 `04_approved/<song_id>/ground_truth.musicxml`，全下游唯一裁判 |
| L0 / L1 / L2 | 数据成熟度：L0 只进不改、L1 可迭代重建、L2 只读/只追加 |

## 交叉引用

- #7 交付物、#11 门标准、#12 目录树、#13 命名与冻结、#27 只读加固
