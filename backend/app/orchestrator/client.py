"""编排客户端：按后台路由的引擎名选择供应商，并负责提交后的轮询直到完成。

引擎选择两路：
- 后台控制生效路径：_process_song 传入 song.engine_name（由 selection.resolve_engine 按
  routing_rules 解析），本模块按引擎名构造对应 MusicProvider，使后台改路由即生效。
- 回退路径：未传引擎名时，用 settings.music_provider（最后兜底默认值）。
- 测试注入：set_orchestrator 设置全局覆盖，所有调用均返回该 provider（conftest 注入 MockProvider）。
"""

import asyncio
import importlib

from app.config import settings
from app.orchestrator.base import (
    GenerateRequest,
    ModerationBlockedError,
    MusicProvider,
    SongResult,
)

# 已实现 provider 的延迟导入映射（引擎名 -> "模块.类"）
_PROVIDER_CLASSES: dict[str, str] = {
    "suno": "app.orchestrator.suno.SunoProvider",
    "mock": "app.orchestrator.mock.MockProvider",
    "musicgen": "app.orchestrator.musicgen.MusicGenProvider",
    "mureka": "app.orchestrator.mureka.MurekaProvider",
    # ACE-Step 1.5 私有引擎（本地 GPU 自托管，MIT 可商用）— selection.DEFAULT_ENGINES
    # 已预留 ace_step_private 但长期「已注册未接入」→ 实现后后台可直接启用。
    "ace_step_private": "app.orchestrator.ace_step.AceStepPrivateProvider",
}

_provider_override: MusicProvider | None = None  # 测试/注入覆盖（最高优先级）
_providers: dict[str, MusicProvider] = {}  # 按引擎名缓存的 provider 实例


def _build(name: str) -> MusicProvider:
    if name in _PROVIDER_CLASSES:
        mod_path, cls_name = _PROVIDER_CLASSES[name].rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        return getattr(mod, cls_name)()
    raise ValueError(
        f"引擎 '{name}' 已注册但未接入 provider 实现。"
        f"后台可启用已实现引擎：{', '.join(_PROVIDER_CLASSES)}"
    )


def get_orchestrator(engine_name: str | None = None) -> MusicProvider:
    global _provider_override
    if _provider_override is not None:
        return _provider_override
    name = (engine_name or settings.music_provider or "suno").lower()
    if name not in _providers:
        _providers[name] = _build(name)
    return _providers[name]


def set_orchestrator(provider: "MusicProvider | None") -> None:
    """测试/注入用：覆盖全局 provider（如替换为 MockProvider）；传 None 清除覆盖。"""
    global _provider_override
    _provider_override = provider
    _providers.clear()


async def generate(
    req: GenerateRequest,
    engine_name: str | None = None,
    engine_chain: list[str] | None = None,
) -> SongResult:
    """提交生成并轮询，直到 completed / failed 或超时。

    Failover（P2）：提供 engine_chain 时依次尝试；
    - 某引擎 create() 抛 ModerationBlockedError / ValueError（未接入）→ 试下一引擎；
    - 轮询中出现 failed（含内容拦截/基础设施失败）→ 试下一引擎；
    - 任一引擎 completed → 立即返回；链耗尽 → 返回 failed（带最后原因）。
    未提供 engine_chain 时退化为单引擎（向下兼容）。
    """
    if not engine_chain:
        engine_chain = [engine_name or settings.music_provider or "suno"]
    last_note: str | None = None
    last_blocked = False
    for name in engine_chain:
        if not name:
            continue
        try:
            provider = get_orchestrator(name)
            created = await provider.create(req)
        except ModerationBlockedError as e:
            last_note = f"[{name}] 内容被审核拦截: {e}"
            last_blocked = True
            continue
        except ValueError as e:  # 引擎未注册/未接入
            last_note = f"[{name}] 引擎未接入: {e}"
            continue

        waited = 0
        while waited < settings.song_poll_timeout:
            await asyncio.sleep(settings.song_poll_interval)
            waited += settings.song_poll_interval
            try:
                status = await provider.get(created.external_id)
            except Exception as e:  # 查询异常也视为该引擎失败，尝试下一引擎
                last_note = f"[{name}] 查询异常: {e}"
                break
            if status.status == "completed":
                return status
            if status.status == "failed":
                note = getattr(status, "moderation_note", None)
                if getattr(status, "moderation_blocked", False) or _looks_like_moderation(note):
                    last_blocked = True
                    last_note = f"[{name}] 内容被审核拦截: {note or '未提供原因'}"
                else:
                    last_note = f"[{name}] 生成失败: {note or '未知原因'}"
                break  # 试下一引擎
        else:
            last_note = f"[{name}] 轮询超时"
    return SongResult(
        external_id="",
        status="failed",
        moderation_blocked=last_blocked,
        moderation_note=last_note,
    )


# 失败原因文本中疑似内容审核拦截的关键词（启发式，避免把基础设施失败误报为内容拦截）。
_MODERATION_KEYWORDS = (
    "moderat",
    "审核",
    "审查",
    "policy",
    "content",
    "block",
    "拒绝",
    "reject",
    "敏感",
    "sensitive",
    "宗教",
    "religious",
    "inappropriate",
    "违规",
    "violation",
    "forbidden",
    "banned",
)


def _looks_like_moderation(note: str | None) -> bool:
    if not note:
        return False
    low = note.lower()
    return any(k.lower() in low for k in _MODERATION_KEYWORDS)
