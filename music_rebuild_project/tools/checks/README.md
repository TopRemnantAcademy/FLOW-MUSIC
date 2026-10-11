# tools/checks · 校验脚本

## 放什么

- `check_ascii.sh`：全树 ASCII 命名与长度检查（Windows 风险规避）
- `verify_hashes.sh`：按 `*.sha256` 校验冻结件
- `check_ro.sh`：`02_source/` 与 `04_approved/` 只读位检查

## 不放什么

- 媒体文件、判定记录

## 只读规则

可迭代（L1）。当前为占位，随 #27 落地实现。
