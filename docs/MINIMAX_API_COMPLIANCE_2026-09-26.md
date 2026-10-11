# MiniMax Music Generation —— 官方文档对齐核查表

核查日期：2026-09-26
被核查实现：`ai-music-platform/backend/app/orchestrator/minimax_music.py`
（配套：`scripts/bench_music_providers.py`、`tests/test_minimax_music_client.py`）

## 0. 文档来源（一手，非二手转述）

| 用途 | 链接 |
| --- | --- |
| 英文 API 参考 | https://platform.minimax.io/docs/api-reference/music-generation |
| 中文 API 参考 | https://platform.minimax.cn/docs/api-reference/music-generation |
| 英文使用指南 | https://platform.minimax.io/docs/guides/music-generation |

端点：`POST https://api.minimax.io/v1/music_generation`
鉴权：`Authorization: Bearer <MINIMAX_API_KEY>` + `Content-Type: application/json`

> 未找到官方文档明示的国内独立域名。检索到的 `api.minimax.cn/v1/generate`、
> `api.minimax.chat/v1/music/generate` 等均来自 AI 生成的二手文章，**与官方文档不符，
> 未采纳**。国内若确需换基址，应先在官方开放平台文档页确认。

## 1. 官方字段 → 代码映射

| 官方字段 | 官方口径（文档原文要点） | 实现位置 | 状态 |
| --- | --- | --- | --- |
| `model` | 必填枚举 6 值；RPM：付费档 120 / free 档 3 | `OFFICIAL_MODEL_SPECS`、`get_model_spec()` | ✅ 枚举化+校验 |
| `prompt` | 纯音乐 [1,2000]；非纯音乐 [0,2000]；cover 必填 [10,300] | `build_payload()` | ✅ 区间校验 |
| `lyrics` | 非纯音乐必填 [1,3500]；cover [10,1000] 可选（缺省 ASR） | `build_payload()` | ✅ |
| `lyrics_optimizer` | bool，默认 false；仅 music-3.0/2.6 系列；lyrics 为空时生效 | `build_payload()` | ✅ 支持性校验 |
| `is_instrumental` | bool，默认 false；纯音乐时 lyrics 非必填 | `build_payload()` | ✅ |
| `stream` | bool，默认 false；`stream=true` 时 output_format 仅 hex | `build_payload()` | ✅ 约束校验 |
| `output_format` | `hex` \| `url`，默认 hex；url 24 小时过期 | `OUTPUT_FORMAT="hex"` | ✅ 默认 hex |
| `audio_setting` | 对象（子属性在文档页为折叠项） | `AUDIO_SETTING_ALLOWED`、`DEFAULT_AUDIO_SETTING` | ⚠️ 见 §3 |
| `aigc_watermark` | bool，默认 false，**仅在 stream=false 时生效**（中文页条目） | `build_payload()` | ✅ 本次补齐 |
| `audio_url` | 仅 cover 模型；与 `audio_base64` 必须且只给一个 | `build_payload(audio_url=…)` | ✅ 本次补齐 |
| `audio_base64` | 同上 | `build_payload(audio_base64=…)` | ✅ 本次补齐 |
| `cover_feature_id` | 仅 cover；与上面两者互斥；传时 lyrics 必填 [10,1000]，有效期 24h | `build_payload(cover_feature_id=…)` | ✅ 本次补齐 |

响应侧（官方 200 示例）：

| 响应字段 | 实现位置 | 说明 |
| --- | --- | --- |
| `base_resp.status_code / status_msg` | `parse_response()` | `!= 0` 即抛错，并附业务码人话说明 |
| `trace_id` | `MiniMaxResult.trace_id` | 原样透出，不自造 |
| `extra_info.music_duration` | `MiniMaxResult.duration_ms` | 官方单位为毫秒，未做转换 |
| `extra_info.music_sample_rate / music_channel / bitrate / music_size` | 同名映射 | — |
| `data.audio` | hex 解码 / url 直取 | 二者必有其一 |
| `data.status` | `MiniMaxResult.data_status` + 校验 | 官方示例为 2，见 §3 |

## 2. 本次核查发现并修掉的问题

| # | 问题 | 影响 | 处理 |
| --- | --- | --- | --- |
| 1 | `aigc_watermark` 完全缺失 | 服务端若默认开启或后续改默认，A/B 两侧可能带上不同水印 | 在 `build_payload()` 中显式下发 `false`（stream=true 时强制 false） |
| 2 | `model` 未做枚举校验 | 传错模型会被服务端按 2013 打回，排查成本高 | `get_model_spec()` 不在枚举内直接 `ValueError` |
| 3 | 各 model 的长度区间未区分 | cover 模型按 [0,2000] 传 prompt 会被拒 | 按官方三档区间分别校验 |
| 4 | `audio_setting` 各子字段无白名单 | 非法采样率/码率只会换来一个看不懂的 2013 | `AUDIO_SETTING_ALLOWED` 白名单校验 |
| 5 | `data.status` 未参与判定 | 「HTTP 200 + base_resp=0」之外还有一层状态被忽略 | 见 §3 |
| 6 | `output_format` 未校验枚举 | 拼错值不会被本地拦住 | 增加枚举校验 |
| 7 | 默认模型改为 `music-3.0` | 原默认 `music-3.0-free` 已被官方公告停服 | `config.py` / `.env.example` 改为 `music-3.0`，RPM 默认 120 |

## 3. 需要人工确认、暂未 100% 官方确认的项

| 项 | 现状 | 建议 |
| --- | --- | --- |
| `audio_setting` 子属性候选值 | 文档页子属性是折叠区、未展开。当前采用 44.1kHz/256kbps/mp3（官方 curl 示例同款），候选集取自 MiniMax Music FAQ 与主流 API 聚合文档 | 打开文档折叠区截图核对后回填本文件 |
| `data.status` 语义 | 官方只在 200 示例里出现 `2`；「1 进行中 / 2 完成」来自第三方整理 | 现按 **≠2 即视为未完成并报错**；若实跑发现合法成功值，改 `DATA_STATUS_COMPLETE` 一处即可 |
| 业务错误码表（1002/1004/1008/1026/2013/2049） | 官方文档未列错误码表 | `BUSINESS_CODE_HINTS` 仅用于把数字翻成人话；**判定成败只看 `base_resp.status_code != 0`**，不依赖该表 |

## 4. 未实现（官方有、本次评估不需要）

| 官方能力 | 未实现原因 |
| --- | --- |
| Music Cover 一步/两步流程（`music_cover_preprocess` 接口） | Mureka 侧无对应参考音频翻唱链路，A/B 对标不需要；字段已在 `build_payload()` 预留并校验 |
| 歌词生成独立接口 `POST /v1/lyrics_generation` | 评测用例自带原创歌词，避免多一次计费与版权环节 |

## 5. 回归状态（2026-09-26 实测）

- `pytest tests/test_minimax_music_client.py -q --noconftest` → 24 passed，RC=0
- `ruff check` 三个文件 → All checks passed
- `bench_music_providers.py --preflight` → RC=0，4 个用例 payload 全部合官方约束
