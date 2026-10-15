# 04 · 分阶段 Q&A 常见问题

> 来源：#65（分阶段 Q&A 常见问题）。上游：05 §3。

## 手写谱能走 OMR 吗？
不能直接走。手写谱 → 印刷 OMR 不适用（#10 矩阵标记 ❌）。选手动录入 vs 外包成本，登记 `risk_register.md`（R-01）后转人工。

## 歌词导出丢失怎么办？
MusicXML 只信音符，歌词以再导入往返校验为准（#36 / gate_G2-6）。丢失 → **ACE 手动歌词路径**：歌词以 `08_qc/lyric_mapping.csv` 人工表为准，双基准（原谱 + 人工表）核对。

## ACE 有 보컬 没鼓？
属预期：ACE 出声乐，鼓/贝斯/和声走 `tools/midi`（#52/#53/#54）+ DAW + 免费鼓 VSTi（MT Power Drum Kit，GM→VSTi 映射见 #62）。

## MCP 读不到项目？
按序排查：① ACE Studio 是否打开对应项目 ② 路径/工作目录 ③ 版本与 External Agent Access 是否启用。仍失败 → 回退手动 + 只读试点重来（#25），禁猜。

## 付费工具卡 / 超预算？
顺序变更是合理的：优者存 `03_omr_candidates/paid/` 并记理由（#34 选胜者口径不变）。许可与成本记 `logs/license_notes.md`。

## 精度怎么声明？
只由测量报告（逐音符错误计数，#33/#34）声明，禁自动估计（P5，见 `01_pipeline_and_gates.md`）。

## 伴奏听起来单调？
强制段落标签 + 分 clip（#51 sections → #52–54），禁整曲一条轨复制。
