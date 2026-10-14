# tools · 校验工具（无媒体）

## 放什么

- `checks/`：校验脚本 —— `check_ascii.sh` / `verify_hashes.sh` / `check_ro.sh`
- `midi/`：编曲 MIDI 生成工具包（#52/#53/#54），纯 stdlib，详见 `midi/README.md`

## 不放什么

- 任何音频 / 乐谱 / 工程文件
- 生成的 MIDI 产物 → `06_arrangement/<song_id>/`（本目录只放生成器与测试）

## 只读规则

可迭代（L1）。脚本仅做校验/生成，不得原地写数据目录。

