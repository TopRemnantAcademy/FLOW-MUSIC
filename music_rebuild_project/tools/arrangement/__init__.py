"""`tools/arrangement` —— 编曲前置校验（#48）与和弦文本规范化（#50）。

两个入口各管一段，互不重复实现：

  - `preconfirm` —— 编曲规格 JSON 的 schema 校验 + 退出码语义（动手前最后一道闸）
  - `chords`     —— 逐小节和弦标记（自由文本 / JSON）规范化 → `chords.txt`，供 `tools/midi` 消费

与 `tools/midi` 的边界：本包**只校验 / 规范化文本**，不生成 MIDI、不碰后端、
不读写数据目录（落点由调用方用 `--out` 指定）。调号 / 拍号 / 和弦符号的词汇表
复用 `tools.midi.sections` / `tools.midi.bass`，不另写一份解析器。

子模块不在此处 import —— 保持 `python -m ...preconfirm` / `...chords` 干净启动。
"""
