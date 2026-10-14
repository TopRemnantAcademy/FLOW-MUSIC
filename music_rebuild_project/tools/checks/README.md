# tools/checks · 校验脚本

## 放什么

- `check_ascii.sh`：全树 ASCII 命名与长度检查（Windows 风险规避）
- `verify_hashes.sh`：按 `*.sha256` 校验冻结件
- `check_ro.sh`：`02_source/` 与 `04_approved/` 只读位检查
- `qc_file_check.py`：**交付件**验证（#60）—— 存在/大小/时长/静音/削波/规格
- `mix_checks.py`：**混音**最小检查（#58）—— 削波/보컬优先/单声道兼容/静音占比/频率平衡
- `g4_checklist.py`：**整门** G4-1~G4-12 检查表（清单驱动，#61）

## 不放什么

- 媒体文件、判定记录
- 门阈值定义 → `00_docs/gates/gate_G4.md`（本目录只引用，不重复定义）

## g4_checklist.py（#61）

把 `gate_G4.md` 的最终交付 QC 条款变成一条可执行命令：读 #59 产出的
manifest（`*_manifest.json`）+ 四轨 stem WAV，对**能纯 stdlib 判定**的条目
逐条产出 PASS/FAIL/SKIP，输出机器可读报告。

```bash
python -m music_rebuild_project.tools.checks.g4_checklist \
    --manifest 07_mix/song01/masters/song_name_mix_v001_manifest.json \
    --stems-dir 07_mix/song01/stems \
    --duration-expected 210.0 \
    --json --out 08_qc/g4_report.json
```

退出码：`0` = 无 FAIL（SKIP 不算失败）；`1` = 有 FAIL；`2` = 用法/读取错误。

### 覆盖矩阵

| 门 | 判据 | 状态 |
|---|---|---|
| G4-1 LUFS | 需 ffmpeg loudnorm | **SKIP** |
| G4-2 dBTP | 需 ffmpeg ebur128 | **SKIP** |
| G4-3 削波 | 峰值 ≥ -0.5dBFS 视为疑似削波 | 可判定 |
| G4-4 静音占比 | 异常静音段 = 0；整轨占比 ≤ 5% | 可判定 |
| G4-5 时长 | master vs 预期 ≤ ±1s（需 `--duration-expected`） | 可判定 |
| G4-6 结构可辨 | 需段落能量对比 + 人工 | **SKIP** |
| G4-7 声乐掩蔽 | 需 #55 频谱工具 | **SKIP** |
| G4-8 单声道 | 需试听 | **SKIP** |
| G4-9 stem 完整性 | 四轨齐 + 时长极差 ≤ 5ms（复用 `render.stems`） | 可判定 |
| G4-10 输出规格 | 44.1k/24bit WAV（MP3 位率需 ffprobe） | 可判定 |
| G4-11 试听清单 | 人工勾选 | **SKIP** |
| G4-12 交付物齐全 | 五类分发物 + 四类记录 + sha256 非空 | 可判定 |

### 与 #60 的分工（不重复实现）

`qc_file_check.py` 查**单个文件**的五项；`g4_checklist.py` 查**整门**的十二条，
输入是 manifest 清单。G4-3/G4-4 的 dBFS 口径与 #60 一致，但 G4-3 收紧到
gate_G4 明文的「峰值 ≥ -0.5dBFS 即疑似削波」（#60 默认是 0dBFS）。

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

`g4_checklist.py` 只在显式给出的 `--out` 下落盘报告（不得原地写数据目录）。
