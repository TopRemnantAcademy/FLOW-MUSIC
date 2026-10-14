# tools/checks/tests · 校验脚本回归测试

## 跑法

```bash
# 从仓库根目录
python -m pytest music_rebuild_project/tools/checks/tests/ -q
```

仓库根目录无 `pyproject.toml`/`pytest.ini`，所以**不依赖 rootdir 配置**：
`test_qc_file_check.py` 通过 `importlib` 按文件路径加载被测脚本；
`test_mix_checks.py` 通过 `checks`/`render` 命名空间包导入（自带 `sys.path` 兜底）。

## 依赖

只依赖 `pytest` 与 Python 标准库。**不依赖任何真实媒体文件**——
所有 WAV 都在 `tmp_path` 里现场合成（`render.wavio` + 确定性正弦/LCG 伪噪声，
**无 RNG**，跨平台字节一致）。

## 覆盖

### `test_qc_file_check.py`（#60）

| 门 | 用例 |
|---|---|
| G4-3 削波计数 | `test_full_scale_dc_counts_as_clipping_when_asked`、`test_normal_signal_has_no_clipping` |
| G4-4 静音占比 | `test_pure_silence_fails`、`test_long_tail_silence_is_anomalous`、`test_short_fade_gap_is_whitelisted` |
| G4-5 时长误差 | `test_duration_within_tolerance_passes`、`test_duration_beyond_tolerance_fails` |
| G4-9 stem 完整性 | 存在·大小·时长·规格四项各自用例 |
| G4-10 输出规格 | `test_spec_rate_mismatch_fails`、`test_sample_widths_are_decoded` |
| 退出码契约 | `test_cli_exit_code_*`、`test_cli_json_is_machine_readable` |
| 无新依赖 | `test_no_third_party_imports` |

### `test_mix_checks.py`（#58）

| 维度 | 用例 |
|---|---|
| 削波 | `TestClipping::test_near_full_scale_sine_fails` / `test_moderate_sine_passes` / `test_threshold_is_configurable` |
| 보컬优先 | `TestVocalPriority::test_loud_vocal_passes` / `test_buried_vocal_fails` / `test_silent_vocal_fails` |
| 单声道兼容 | `TestMonoCompat::test_correlated_stereo_passes` / `test_antiphase_stereo_warns` / `test_mono_source_is_not_applicable` / `test_correlation_math_is_signed` |
| 静音占比 | `TestSilence::test_normal_signal_passes` / `test_silent_track_fails` / `test_half_silent_is_at_boundary` |
| 频率平衡 | `TestSpectrum::test_low_heavy_warns` / `test_broadband_noise_passes` / `test_ratios_sum_to_one` |
| 端到端 | `TestEndToEnd::*`（四轨齐五项齐、健康混音无 FAIL、单 master、缺文件报错） |
| 退出码契约 | `TestExitCodes::test_all_pass_is_zero` / `test_fail_is_one` / `test_warn_only_is_zero_but_marked` / `test_usage_error_is_two` / `test_missing_file_is_two` |
| 无新依赖 | `test_no_numpy_scipy_imported`（子进程 `sys.modules` 断言） |
