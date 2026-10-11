# ACE-Step 1.5 私有引擎接入与部署

> 适用范围：`backend/app/orchestrator/ace_step.py`（provider）+ `backend/app/config.py` 的 `ace_step_*` 配置。
> 协议基线来源：`vendor/ace-step-1.5/`（CNB `chguanw/ace-step-1.5`，MIT © 2026 ACEStudio，已入库）。
> 许可说明：**代码 MIT 可商用**；模型权重按各自的 Modelscope / HuggingFace 仓库条款，部署前须自查。

本文只写「怎么把它接到我们平台上」，引擎自身的参数含义以官方 `vendor/ace-step-1.5/docs/en/` 为准。

---

## 1. 结论先行

| | 无 GPU 环境 | 有 GPU 环境 |
|---|---|---|
| 能否跑 | **能，仅推理**，但显著慢（`INSTALL.md:449`） | 能，推荐路径 |
| 必须开的开关 | DiT-only：`ACESTEP_INIT_LLM=false`（`INSTALL.md:457`） | 按显存档位自动（`GPU_COMPATIBILITY.md:3`） |
| 可用模型 | DiT turbo；LM 不可用（<6GB 时 LM 默认关闭，`GPU_COMPATIBILITY.md:55`） | 0.6B / 1.7B / 4B LM，按显存自动选（`GPU_COMPATIBILITY.md:7-16`） |
| 并发 | 1 | 1～8，按显存档位（`GPU_COMPATIBILITY.md:7-16`） |
| 生产可用 | **否**（只适合本地验证协议连通性） | 是 |
| 本站默认状态 | 关（`enabled=False`，未进 `DEFAULT_ROUTING`） | 关（**默认始终关闭**，需你手动开） |

**默认不开引擎是我们刻意的设计**：没有 GPU 时启用它只会让用户等几分钟并拿到超时；
有 GPU 时也应该由人显式决定，而不是悄悄切走付费引擎的流量。

---

## 2. 我们这侧的对接点

provider 只调用三个端点（均在 `vendor/ace-step-1.5/acestep/api_server.py` 实取核对）：

| 端点 | 源码位置 | 用途 |
|---|---|---|
| `POST /release_task` | `api_server.py:2253` | 提交生成任务，返回 `task_id` |
| `POST /query_result` | `api_server.py:2467` | 轮询状态，成功时返回音频文件路径 |
| `GET /v1/audio?path=` | `api_server.py:2809` | 把引擎本地磁盘上的音频拉回 |

两个必须知道的引擎行为：

1. **音频在引擎本地磁盘，不在任何 URL 里。** 成功态必须走 `GET /v1/audio` 回源，上传我们的对象存储后
   才能被 Web / APK 播放。provider 已实现（`_fetch_and_publish`），且对象存储未配置时会明确报错而不是静默失败。
2. **回源不复用 `app/storage/s3.download_to_temp`。** 该函数对 URL 做 SSRF guard，会直接拒绝同机的
   `127.0.0.1:8001`。这里是自托管受控服务，目标明确，故走直连；**若将来把引擎搬到公网地址，必须在此重新接入 guard。**

状态语义（`api_server.py:239 STATUS_MAP`）：`0`=进行中 / `1`=成功 / `2`=失败。
响应统一被 `_wrap_response` 包了一层 `{"data": ...}`，provider 已剥掉这一层。

---

## 3. 引擎侧启动

官方默认值（实取 `api_server.py:2844-2912`）：

| 参数 | 环境变量 | 默认 |
|---|---|---|
| `--host` | `ACESTEP_API_HOST` | `127.0.0.1` |
| `--port` | `ACESTEP_API_PORT` | `8001` |
| `--api-key` | `ACESTEP_API_KEY` | 无（免鉴权） |
| `--init-llm` | `ACESTEP_INIT_LLM` | `False` |
| `--download-source` | `ACESTEP_DOWNLOAD_SOURCE` | `auto`（可设 `modelscope`） |
| `--no-init` | `ACESTEP_NO_INIT` | `False` |

**有 GPU**（推荐，8GB 起可跑 turbo）：

```bash
ACESTEP_DOWNLOAD_SOURCE=modelscope python acestep/api_server.py --port 8001
# 显存 ≥24GB 可再加 --lm-model-path acestep-5Hz-lm-4B 提升质量
# 注意 --no-init 时模型懒加载：首个请求会慢得离谱（含下载+编译），别误判成故障
```

**无 GPU**（仅验证用）：

```bash
ACESTEP_INIT_LLM=false python acestep/api_server.py --port 8001
```

`ACESTEP_INIT_LLM=false` 是必须的：DiT-only 是低显存/无 GPU 下唯一稳的模式（`INSTALL.md:452,457`）。
不要指望它出成品——官方文档原文是「performance will be significantly slower」。

国内网络取模型可用 `--download-source modelscope`（`api_server.py:2865`），比 `auto` 稳。

---

## 4. 启用步骤（有 GPU 之后）

Provider 已实现，**但默认关闭**。要启用共 3 步，缺一不可：

1. **跑起引擎**（见上一节），确认 `curl -X POST http://127.0.0.1:8001/release_task ...` 能通。
2. **打开开关**：`backend/app/orchestrator/selection.py` 中 `ace_step_private` 的 `"enabled": True`
   （当前 `False`，见 `selection.py:63`）。
3. **决定路由**：`DEFAULT_ROUTING`（`selection.py:108-112`）默认把 general / christian_worship / instrumental
   三类都指向 mureka。想让 worship 类走私有引擎，就把对应那条改成 `provider="ace_step_private"`。

顺序不能反：没跑引擎就打开开关，所有请求会以「连不上 127.0.0.1:8001」失败——这不是 bug，
provider 会抛出带处置建议的 `AceStepError`。

---

## 5. 安全与成本边界

- **`ACE_STEP_API_KEY` 留空 = 引擎免鉴权。** 此时引擎**绝不能**暴露到公网，否则任何人都能白嫖你的 GPU。
  同机部署（默认 `127.0.0.1:8001`）时这一点天然成立；一旦改内网地址，务必填 key。
- **并发必须限。** `ace_step_max_concurrent` 默认 1。本地 GPU 上并发上去只会互相 OOM，不会更快。
- **模型权重许可不在 MIT 覆盖范围内。** MIT 只管代码。权重在 Modelscope / HF 各按自己的条款，
  **商用前必须自己确认**（尤其是几款带 NC / 研究用途限制的模型）。
- **这是合规通道的效果**：本地自托管不经过第三方 API，不存在第三方内容审核误杀。
  但Conversely，我们得自己承担内容责任——上线前请自备审核环节。

---

## 6. 诚实的能力边界

provider 只声明了 `generate` 与 `custom` 两类任务，**没有**宣称以下能力（缺前置步骤，会走 501）：

- `cover` / `remix`：需要先把远程音频上传进 ACE-Step 工作目录，这一环我们没接。
- `vocals2bgm`（对应引擎的 `extract` 任务）：属音轨分离，未接线。
- `make_instrumental`：用「空歌词 + 无人声 instruction」表达，不是真的音轨分离。

## 7. 未验证项

- 本文所有引擎侧结论来自**官方文档与源码实取**（`vendor/ace-step-1.5/` 内 file:line），
  **不是实机测试结果**——当前环境无 GPU，尚未做过一次真实端到端生成。
- 首次接真实引擎时，建议先跑 `backend/tests/test_ace_step_provider.py`（HTTP 层 mock，不依赖 GPU），
  再逐项核对上面第 4 节的三步。
