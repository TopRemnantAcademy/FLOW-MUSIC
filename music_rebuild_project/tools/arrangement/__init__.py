"""`tools/arrangement` —— 编曲前置确认校验器（#48）。

纯 stdlib，不生成 MIDI、不碰后端。职责只有一个：**在编曲 agent 动手前**，
判定一份「编曲规格 JSON」是否完整、自洽、字段合法，并给出字段级报告。

  - `preconfirm` —— 规格 schema 校验 + 退出码语义（本包唯一入口）

与 `tools/midi` 的边界：本包**只校验规格文本**，不读也不写任何 MIDI；
调号/拍号/和弦符号的**词汇表**复用 `tools.midi.sections` / `tools.midi.bass`，
不重写一份解析器。

子模块不在此处 import —— 保持 `python -m ...preconfirm` 干净启动。
"""
