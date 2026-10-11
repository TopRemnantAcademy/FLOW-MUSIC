# 00_docs/gates · 质量门标准

## 放什么

- `gate_G1.md` ~ `gate_G4.md`：质量门 G1-G4 的通过判据与测量标准（#11 P0 定稿）。

## 不放什么

- 门阈值以外的流程定义 → `00_docs/plan/`
- 交付物清单 → `00_docs/deliverables.md`
- 门判定**记录**（实际跑出来的结果）→ `08_qc/`

## 只读规则

可迭代（L1）。门阈值一旦定稿即视为冻结基线；修改须同步更新 `08_qc/gate_config.json` 并记录 `changed_by / changed_at / reason`，且**变更后须重跑该门**。历史结论不追溯改写。

## 幂等约定

`gate_G1.md` ~ `gate_G4.md` 是 G1-G4 阈值的唯一权威定义处，各门 issue 只引用、不重复定义。
