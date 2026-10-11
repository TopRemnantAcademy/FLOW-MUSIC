# 08_qc · 门判定记录与 QC 报告

## 放什么

- `gate_config.json`：阈值快照（`value / adjustable / changed_by / changed_at / reason`）
- `gate_g1_record.md` ~ `gate_g4_record.md`：各门判定记录
- `score_qc_report.md`、`vocal_ab_report.md`、`vocal_lyric_mapping.csv`、`xml_roundtrip_check.md`
- `qc_file_check.py`、`verify_report.json/md`、`qc_iterations.md`、`mix_checklist.md`、`approval_log.md`
- `<song_id>/gate_log.md`：逐门判定，一行一条

## 不放什么

- 被判定的媒体文件本体 → 各数据目录
- 门阈值定义 → `00_docs/gates/`

## 只读规则

可追加（L2）。`gate_log.md` **不改历史行**，只追加新行；`gate_g4_record.md`、`approval_log.md` 终验后只读。
