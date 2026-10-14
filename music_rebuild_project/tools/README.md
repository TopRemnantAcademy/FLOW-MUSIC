# tools · 校验工具（无媒体）

## 放什么

- `checks/`：校验脚本 —— `check_ascii.sh` / `verify_hashes.sh` / `check_ro.sh`
  / `qc_file_check.py`(#60 交付件) / `mix_checks.py`(#58 混音) / `g4_checklist.py`(#61)
- `midi/`：编曲 MIDI 生成工具包（#52/#53/#54），纯 stdlib，详见 `midi/README.md`
- `arrangement/`：编曲前规格校验器（#48）—— 只校验规格 JSON，不生成 MIDI；纯 stdlib
- `render/`：输出格式工具包（#59）—— master WAV / 分轨 stem / 哈希 manifest；纯 stdlib
- `naming/`：命名与冻结规则（#13）—— 只回答「这个名字合不合规」
- `arrangement/`：编曲输入文本资产（#50）—— `chords.txt` 规范化/校验；纯 stdlib

## 不放什么

- 任何音频 / 乐谱 / 工程文件
- 生成的 MIDI 产物 → `06_arrangement/<song_id>/`（本目录只放生成器与测试）

## 只读规则

可迭代（L1）。脚本仅做校验/生成，不得原地写数据目录。
`arrangement/` 是编曲前置校验与和弦文本规范化：读规格 / 和弦文本，报告与 `chords.txt` 只写到调用方显式给出的 `--out`，从不写数据目录。
`render/` 例外地**会产出**（master/manifest），但只在调用方显式给出的
`--out` / `--out-dir` 下落盘，从不猜路径、从不写 `08_qc/`。
