# 03_omr · OMR 识别区 · 可重建

## 放什么

- `<song_id>/raw/`：Audiveris / 付费候选的原始输出 `.mxl/.musicxml/.log`
- `<song_id>/review/`：MuseScore 审校工作版 `.mscz` / 迭代 `.musicxml`
- `<song_id>/retest/`：逐曲重测参数与结果对比、胜者选定记录
- `<song_id>/reports/`：`error_report_vNNN.md`

## 不放什么

- 批准冻结件 → `04_approved/`（禁止把批准本放这里）

## 只读规则

可覆盖、可重建（L1）。但 **raw/ 原始输出不改**：重测出新版本号，保留旧文件。
