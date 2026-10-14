# tools/checks · 校验脚本

## 放什么

- `check_ascii.sh`：全树 ASCII 命名与长度检查（Windows 风险规避）
- `verify_hashes.sh`：按 `*.sha256` 校验冻结件
- `check_ro.sh`：`02_source/` 与 `04_approved/` 只读位检查
- `qc_file_check.py`：**交付件**验证（#60）—— 存在/大小/时长/静音/削波/规格
- `mix_checks.py`：**混音**最小检查（#58）—— 削波/보컬优先/单声道兼容/静音占比/频率平衡

## 不放什么

- 媒体文件、判定记录

## 只读规则

可迭代（L1）。脚本仅做校验，不写数据目录。

## `qc_file_check.py` vs `mix_checks.py`

两者都读 WAV，但**管的事不同**，不要互相替代：

| | `qc_file_check.py`(#60) | `mix_checks.py`(#58) |
|---|---|---|
| 时机 | 分发前（gate 本体） | 混音中（gate **之前**的守门员） |
| 对象 | 单条交付件（master/stem） | 四轨 stem（或单条 master）**整体** |
| 维度 | 存在·大小·时长·静音·削波·规格 | 削波·보컬优先·单声道兼容·静音占比·频率平衡 |
| 判定 | PASS/FAIL/SKIP | PASS/FAIL/**WARN** |

`mix_checks.py` 的 WAV 解析**复用** `tools/render/wavio`（#59），
不另写一份位深解析 —— 避免「24bit 读错」有两个可能来源。

## `mix_checks.py` 退出码

```
0  无 FAIL（全 PASS，或仅 WARN —— WARN 不阻断，报告中标出）
1  至少一条 FAIL
2  用法 / 读取错误
```

跑法：

```bash
python tools/checks/mix_checks.py 07_mix/<song_id>/stems/            # 四轨
python tools/checks/mix_checks.py 07_mix/<song_id>/masters/<file>.wav --json
```
