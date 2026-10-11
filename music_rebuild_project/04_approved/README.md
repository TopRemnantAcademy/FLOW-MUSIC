# 04_approved · 审校批准版 · 只读冻结

## 放什么

- `<song_id>/ground_truth.musicxml`：过 G2 的冻结基准谱
- `<song_id>/ground_truth.musicxml.sha256`：`<sha256>  <file>  <utc_time>  <approver>`
- `<song_id>/song01_approved_v001.mscz`：与基准同源的批准工程
- `<song_id>/approval.md`：批准人 / 时间 / 判据 → 链接 gate_log

## 不放什么

- 审校工作版 → `03_omr/<song_id>/review/`

## 只读规则

**强只读（L2，冻结）**。过 G2 即文件系统级只读（#13/#27）；任何修改生成 `v002`，**不得原地覆盖**；下游 D/E 段一律以哈希校验读取。
