# tools/arrangement/tests · `chords.py` 回归测试

## 跑法

```bash
# 从仓库根目录
python -m pytest music_rebuild_project/tools/arrangement/tests/ -q

# 或在 music_rebuild_project/ 下
python -m pytest tools/arrangement/tests/ -q
```

`conftest.py` 把 `tools/` 与仓库根都塞进 `sys.path`，因此无论从哪跑，
`arrangement.chords`（被测）与 `midi.bass`（parity 对照）都可导入。

## 依赖

只依赖 `pytest` 与 Python 标准库。**无真实媒体、无 ffmpeg、无 RNG** ——
所有输入都是字符串字面量，输出逐字节可复现。

## 覆盖矩阵

| 需求（#50） | 用例 |
|---|---|
| 合法根音 A-G + `#`/`b` | `test_valid_roots`、`test_lowercase_bb_is_valid_flat_root` |
| 非法根音 + 行号 | `test_invalid_roots_report_line`、`test_error_carries_index` |
| 性质枚举 | `test_quality_enumeration` |
| 同义写法归一 | `test_quality_aliases_normalize`、`test_leading_quality_without_root` |
| 未知性质 | `test_unknown_quality_reports_line` |
| 转位 / 低音 | `test_slash_bass`、`test_transpose_preserves_slash_relationship` |
| 一行多和弦 | `test_multiple_chords_per_bar`、`test_multiple_chords_accept_commas_and_pipes` |
| 空行 / 注释 | `test_blank_and_comment_lines`、`test_empty_input_yields_header_only` |
| 小节连续 / 重复 / 排序 | `test_gap_rejected`、`test_duplicate_bar_rejected`、`test_out_of_order_lines_are_sorted` |
| 裸和弦自动编号 | `test_bare_chords_auto_number`、`test_explicit_then_bare_continues` |
| JSON 四种形态 | `test_json_mapping`、`test_json_list_of_strings`、`test_json_list_of_objects`、`test_json_list_of_lists` |
| 输出契约 / 幂等 | `test_output_contract`、`test_normalize_is_idempotent`、`test_deterministic_bytes` |
| 与 tools/midi 的边界 | `test_parity_with_midi_bass`、`test_map_feeds_song_context`、`test_load_chord_map_*` |
| CLI 退出码 0/1/2 | `test_cli_exit_0_and_writes`、`test_cli_exit_1_*`、`test_cli_exit_2_on_missing_file` |
| CLI 其余开关 | `test_cli_map_flag_is_json`、`test_cli_validate_does_not_write`、`test_cli_first_bar_offsets`、`test_cli_reads_stdin` |
| 纯 stdlib / 无 RNG | `test_no_third_party_imports`、`test_no_random_source` |
