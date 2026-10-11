# 01_preflight · 环境预检证据（阶段2）

## 放什么

- `install_versions.csv`：工具安装版本表，表头 `tool,version,source,checksum,installed_at,verified_by`
- `risk_register.md`：风险登记表（#14 六类风险 + 阻断措施）
- `issue_log/`：问题诊断日志，`env_YYYY-MM-DD_<slug>.md`（现象/原因/处置/回滚点，#28）
- `source_manifest.txt`：原始输入哈希清单（G1-9 校验用）

## 不放什么

- 工具二进制本体
- 实际输入的乐谱图像 → `02_source/`

## 只读规则

可迭代（L1）。仅官方源安装，每次安装/变更追加一行，不改历史行。
