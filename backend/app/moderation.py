"""歌词/音频机审（GAP-016：文本词库 + 真实 ASR + 可选专业审核 API）。

路由层在生成前调用 check_lyric；返回 False 即拦截。
`moderate_audio` 从「占位恒 True」改为真实链路：**ASR 转写 → check_lyric → 可选云审核**，
任何环节失败都 fail-closed（拦下走人工，绝不放行）。

机制默认启用（settings.enable_moderation=True，协同部署面向中国目标用户，
文本机审默认开启；关闭会让歌曲停留 pending 且不进入 explore/同步）。

词库数据源优先级（`blocklist`）：
  1. `MODERATION_BLOCKLIST_PATH` 指定的完整合规词库；
  2. 包内 `backend/blocklist.txt`（本仓库随附的扩展词库）；
  3. 内置 `DEFAULT_BLOCKLIST`（**兜底**：仅当上述文件缺失时回退，会显式告警——
     但不再是「占位示例词」，而是覆盖常见违规类目的最小可用集合，见下）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

_logger = logging.getLogger(__name__)
_audio_placeholder_warned = False
_blocklist_placeholder_warned = False

# 兜底词库（文件缺失时使用）。**非**「示例敏感词」占位，而是按类目组织的最小可用集；
# 完整合规词库应通过 blocklist.txt / MODERATION_BLOCKLIST_PATH 提供（见文档）。
DEFAULT_BLOCKLIST = [
    # 暴力/血腥
    "暴力", "杀戮", "血腥", "砍杀", "虐杀", "屠杀",
    # 色情/低俗
    "色情", "淫秽", "裸聊", "性交易", "约炮", "嫖娼",
    # 赌博/博彩
    "赌博", "博彩", "赌场", "外围赌", "私彩", "六合彩",
    # 毒品/违禁药品
    "毒品", "吸毒", "制毒", "贩毒", "违禁药", "冰毒",
    # 诈骗/违法
    "诈骗", "洗钱", "非法集资", "传销", "刷单", "电信诈骗",
    # 仇恨/歧视
    "仇恨", "种族歧视", "地域歧视", "煽动对立", "纳粹",
    # 自残/危险行为
    "自杀", "自残", "教唆自伤", "危险挑战", "蓝鲸游戏",
]


def _blocklist_path() -> Path:
    """[D33] 단어장 경로를 **CWD 가 아닌 패키지 기준** 으로 결정한다.

    기존 구현은 `open("blocklist.txt")` 였다 — 즉 **프로세스 CWD 에 의존** 했다.
    리포에는 `backend/blocklist.txt`(실제 단어) 가 존재하므로
    `cd backend` 실행(로컬/테스트) 에서는 맞지만, CWD 가 달라지면
    **파일이 이미지 안에 있어도** 조용히 자리표시자로 대체된다.
    → 컨테이너에서만 심의가 무력화되는 전형적인 환경 의존 결함.

    기준점: 본 파일은 `backend/app/moderation.py` → `backend/blocklist.txt`.
    `MODERATION_BLOCKLIST_PATH` 로 명시적 override 가능.
    """
    override = getattr(settings, "moderation_blocklist_path", None)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "blocklist.txt"


def _load_blocklist() -> tuple[list[str], bool, str]:
    """返回 (词表, 是否使用兜底, 实际读取路径)。"""
    path = _blocklist_path()
    try:
        with open(path, encoding="utf-8") as f:
            return (
                [w.strip() for w in f if w.strip() and not w.strip().startswith("#")],
                False,
                str(path),
            )
    except FileNotFoundError:
        return DEFAULT_BLOCKLIST, True, str(path)


_BLOCKLIST, _USING_FALLBACK, _BLOCKLIST_PATH = _load_blocklist()


def describe_state() -> dict:
    """[D33] 심의 상태를 운영 가시성용으로 노출(health/로그)。

    `enable_moderation=True` 인데 `using_fallback=True` 면
    **"켰지만 실제로는 최소词库만 적용"** 상태다 — 반드시 경고로 드러내야 한다.
    """
    from app import asr

    return {
        "enabled": bool(settings.enable_moderation),
        "using_fallback": bool(_USING_FALLBACK),
        "using_placeholder": bool(_USING_FALLBACK),  # 兼容旧字段名
        "path": _BLOCKLIST_PATH,
        "terms": len(_BLOCKLIST),
        "audio_enabled": bool(settings.enable_audio_moderation),
        "asr": asr.describe_state(),
        "moderation_api": bool(settings.moderation_api_base),
    }


def check_lyric(text: str) -> bool:
    """返回 True 表示通过；False 表示命中敏感词。

    启用机审却仍用兜底词库时，仅首次打印一次告警，避免误判「已生效」。
    """
    global _blocklist_placeholder_warned
    if not text:
        return True
    if _USING_FALLBACK and settings.enable_moderation and not _blocklist_placeholder_warned:
        _blocklist_placeholder_warned = True
        _logger.warning(
            "机审未加载到完整词库（blocklist.txt 缺失，回退内置兜底词库 %d 词）；"
            "请配置完整合规词库（MODERATION_BLOCKLIST_PATH）或接入专业审核 API。",
            len(DEFAULT_BLOCKLIST),
        )
    return not any(w in text for w in _BLOCKLIST)


def check_lyric_remote(text: str) -> bool:
    """可选：专业内容审核 API（OpenAI 兼容 /moderations，如 omni-moderation）。

    返回 True 通过 / False 命中。**未配置时返回 True**（视为「不额外拦」，
    与 check_lyric 的词库路径互补，不是安全底线）；配置后调用失败按 fail-closed
    返回 False（宁可拦下走人工，也不放行未审内容）。
    """
    base = (settings.moderation_api_base or "").strip().rstrip("/")
    if not base or not text:
        return True
    import httpx

    try:
        resp = httpx.post(
            f"{base}/moderations",
            headers={
                "Authorization": f"Bearer {settings.moderation_api_key}",
                "Content-Type": "application/json",
            },
            json={"model": settings.moderation_api_model, "input": text},
            timeout=settings.moderation_api_timeout_seconds,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            # 结构异常：无法判定 → fail-closed
            return not settings.moderation_fail_closed
        return not bool(results[0].get("flagged", False))
    except Exception as e:  # noqa: BLE001 — 上游异常一律按策略处理
        _logger.warning("专业审核 API 调用失败：%s", e)
        return not settings.moderation_fail_closed


def moderate_audio(audio_url: str) -> bool:
    """音频机审：**真实** ASR 转写 → check_lyric（+ 可选云审核）。返回 True 通过。

    替换旧的占位实现（曾恒返回 True）。链路：
      1. `asr.transcribe(audio_url)` → 歌词/人声文本；
      2. `check_lyric(text)` 命中词库 → 不通过；
      3. `check_lyric_remote(text)` 云审核命中 → 不通过。

    **fail-closed 策略**（本模块的安全底线）：
      · ASR 未配置（asr_provider 为空）→ **不放行**（返回 False），
        否则「开了开关但没接实现」又回到「假通过」的老坑；
      · ASR 调用失败/超时 → 按 moderation_fail_closed 处理（默认不放行）；
      · 转写为空（无人声/静音）是合法结果 → 通过。
    """
    global _audio_placeholder_warned
    from app import asr

    provider = asr.provider_name()
    if provider not in ("whisper", "openai"):
        if not _audio_placeholder_warned:
            _audio_placeholder_warned = True
            _logger.warning(
                "enable_audio_moderation=True 但未配置 ASR（asr_provider=%r）——"
                "音频机审无法执行，按 fail-closed 拦下走人工审核。",
                provider,
            )
        return False

    try:
        text = asr.transcribe(audio_url)
    except asr.ASRError as e:
        _logger.warning("音频 ASR 转写失败（fail-closed=%s）：%s", settings.moderation_fail_closed, e)
        return not settings.moderation_fail_closed

    if not text:
        # 无人声/静音：合法通过（不能把「没转出字」当成违规）。
        return True
    if not check_lyric(text):
        _logger.info("音频机审命中本地词库：%s", text[:80])
        return False
    if not check_lyric_remote(text):
        _logger.info("音频机审命中云审核：%s", text[:80])
        return False
    return True
