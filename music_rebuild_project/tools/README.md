# tools · 校验工具（无媒体）

## 放什么

- `checks/`：校验脚本 —— `check_ascii.sh` / `verify_hashes.sh` / `check_ro.sh`
  / `qc_file_check.py`(#60 交付件) / `mix_checks.py`(#58 混音)
- `midi/`：编曲 MIDI 生成工具包（#52/#53/#54），纯 stdlib，详见 `midi/README.md`
- `arrangement/`：编曲前规格校验器（#48）—— 只校验规格 JSON，不生成 MIDI；纯 stdlib
- `render/`：输出格式工具包（#59）—— master WAV / 分轨 stem / 哈希 manifest；纯 stdlib

## 不放什么

- 任何音频 / 乐谱 / 工程文件
- 生成的 MIDI 产物 → `06_arrangement/<song_id>/`（本目录只放生成器与测试）

## 只读规则

可迭代（L1）。脚本仅做校验/生成，不得原地写数据目录。
`arrangement/` 是**只读校验**：读规格 JSON，stdout 出报告，从不写盘。
`render/` 例外地**会产出**（master/manifest），但只在调用方显式给出的
`--out` / `--out-dir` 下落盘，从不猜路径、从不写 `08_qc/`。
