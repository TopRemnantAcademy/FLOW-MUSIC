"""文案辅助 LLM 客户端（OpenAI 兼容 /chat/completions 协议）。

用途：创作页「✨ 自动优化 / ✨ 自动生成 / ✨ 自动取名」按钮背后的真实 AI 能力。

供应商决策（2026-09-10）：
  默认 **腾讯混元 OpenAI 兼容端点**（hunyuan-lite，免费档）——
  · 目标用户在中国大陆 → 国内合规、数据不出境
  · hunyuan-lite 免费 → 辅助按钮零边际成本
  · OpenAI 兼容协议 → 换 DeepSeek / Qwen / GLM 只改 .env 三个值，代码零改动

设计要点：
  · ASSIST_LLM_API_KEY 为空 = 功能关闭 → 路由层自动降级本地确定性规则
    （app/services/prompt_assist.py），按钮永不因 AI 缺失而 5xx
  · 上游错误细节只进服务端日志（响应体可能含第三方品牌词），用户只见中性文案
  · 模块级共享 httpx.AsyncClient（连接复用 + DNS 缓存）。[perf 2026-09-10 实测]
    每调用新建 client 在 100 并发下 = 100 次 DNS+TLS 握手风暴，getaddrinfo 挤占
    anyio 线程池拖慢全部端点（rps 164→48）。共享后单连接多路复用，握手仅一次。
    按 event loop 缓存：asyncio.run 每次新 loop（测试场景）时自动重建，避免跨 loop 复用。
  · 所有 system 提示都强制简体中文输出（UI 全中文规则）
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """上游 LLM 调用失败（非 2xx / 超时 / 响应结构异常 / 空输出）。

    异常文本仅用于服务端日志，不得透传给用户。
    """


_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _get_client() -> httpx.AsyncClient:
    """取（或建）当前 event loop 的共享 AsyncClient。"""
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 无运行中 loop（纯同步上下文）——不应发生，兜底新建
        loop = None
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(
            timeout=settings.assist_llm_timeout_seconds,
            # 连接池：单实例对上游的合理并发上限；keepalive 复用 TLS 连接
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        _client_loop = loop
    return _client


def llm_enabled() -> bool:
    """是否配置了辅助 LLM（运行时读取 settings，便于测试注入）。"""
    return bool(settings.assist_llm_api_key.strip())


async def chat(system: str, user: str, *, max_tokens: int = 800, temperature: float = 0.7) -> str:
    """单轮对话：返回模型文本输出；任何失败抛 LLMError（调用方负责降级）。"""
    api_key = settings.assist_llm_api_key.strip()
    if not api_key:
        raise LLMError("assist llm not configured")
    url = settings.assist_llm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.assist_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        client = _get_client()
        resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise LLMError(f"request failed: {e}") from e
    if resp.status_code // 100 != 2:
        # 响应体可能含供应商信息 → 只记录状态码，原文进日志
        log.warning("assist llm upstream %s: %s", resp.status_code, resp.text[:300])
        raise LLMError(f"upstream status {resp.status_code}")
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"unexpected response shape: {e}") from e
    if not isinstance(content, str) or not content.strip():
        raise LLMError("empty content")
    return content.strip()


# ── 提示词（输出全部为简体中文；结构标记保留英文——生成引擎按其识别段落） ──

ASSIST_STYLE_SYSTEM = (
    "你是基督教音乐创作助手中的风格整理器。用户会给出【风格】与可选的【歌词】。\n"
    "若给出【歌词】，请基于歌词的情绪、主题与意象提炼曲风/速度/情绪/乐器/人声；"
    "若只给【风格】，则仅整理用户给出的风格。\n"
    "输出简体中文音乐风格标签串：从曲风、速度、情绪、乐器、人声中选取，最多 6 个标签，"
    "用中文逗号分隔，按「曲风→速度→情绪→乐器→人声」顺序排列。只输出标签串本身，"
    "不要解释、不要引号、不要句号。示例：福音，慢速，温暖，钢琴，女声"
)

ASSIST_PROMPT_SYSTEM = (
    "你是基督教音乐创作助手中的主题优化器。把用户给的主题或情绪扩写成一段不超过 80 字的"
    "简体中文歌意描述：画面具体、情绪明确，适合作为歌曲创作指引，"
    "可涉及敬拜、感恩、盼望与日常生活中的信仰。只输出描述本身，"
    "不要解释、不要引号、不要列表、不要标签格式。"
)

ASSIST_LYRICS_SYSTEM = (
    "你是华语基督教歌曲的作词人。根据用户给的主题与情绪创作一首完整歌词，要求：\n"
    "1. 歌词正文全部简体中文；\n"
    "2. 用 [Verse]、[Pre-Chorus]、[Chorus]、[Bridge]、[Outro] 英文结构标记分段"
    "（生成引擎按这些标记识别段落，必须使用英文标记，不要翻译成中文）；\n"
    "3. 结构至少含：主歌→副歌→主歌→副歌→桥段→副歌→尾奏；各段副歌保持一致、易记易唱；\n"
    "4. 内容积极、贴合敬拜与日常生活，避免说教与宗教术语堆砌；\n"
    "5. 第一行输出「标题：某某」（标题不超过 12 个字），随后直接输出歌词正文，"
    "不要任何解释或多余说明。"
)


def split_lyrics_output(text: str) -> tuple[str, str]:
    """解析模型输出 → (title, lyrics)。首行「标题：XX」可缺省，容忍 markdown 围栏。"""
    lines = text.strip().splitlines()
    title = ""
    if lines:
        first = lines[0].strip()
        if first.startswith("标题"):
            for sep in ("：", ":"):
                if sep in first:
                    title = first.split(sep, 1)[1].strip().strip("《》“”\"'")
                    break
            lines = lines[1:]
    lyrics = "\n".join(lines).strip()
    if lyrics.startswith("```"):
        lyrics = lyrics[3:]
    if lyrics.endswith("```"):
        lyrics = lyrics[:-3]
    return title, lyrics.strip()
