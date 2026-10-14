# tools/checks/tests · `qc_file_check.py` 回归测试

## 跑法

```bash
# 从仓库根目录
python -m pytest music_rebuild_project/tools/checks/tests/ -q

# 或在该目录内（rootdir 自适应）
cd music_rebuild_project/tools/checks && python -m pytest tests/ -q
```

仓库根目录无 `pyproject.toml`/`pytest.ini`，所以**不依赖 rootdir 配置**；
测试通过 `importlib` 按文件路径加载被测脚本，不要求包结构、不要求 `PYTHONPATH`。

## 依赖

只依赖 `pytest` 与 Python 标准库。**不依赖任何真实媒体文件**——
仓库不放二进制，所有 WAV 都在 `tmp_path` 里用 stdlib `wave` + `struct` 现场合成
（纯静音 / 满幅削波 / 正常正弦 / 时长不符 / 短淡入淡出间隙 / 8·16·32-bit / 单双声道）。

## 覆盖

| 门 | 用例 |
|---|---|
| G4-3 削波计数 | `test_full_scale_dc_counts_as_clipping_when_asked`、`test_normal_signal_has_no_clipping` |
| G4-4 静音占比 | `test_pure_silence_fails`、`test_long_tail_silence_is_anomalous`、`test_short_fade_gap_is_whitelisted` |
| G4-5 时长误差 | `test_duration_within_tolerance_passes`、`test_duration_beyond_tolerance_fails` |
| G4-9 stem 完整性 | 存在·大小·时长·规格四项各自用例 |
| G4-10 输出规格 | `test_spec_rate_mismatch_fails`、`test_sample_widths_are_decoded` |
| 退出码契约 | `test_cli_exit_code_*`、`test_cli_json_is_machine_readable` |
| 无新依赖 | `test_no_third_party_imports` |
