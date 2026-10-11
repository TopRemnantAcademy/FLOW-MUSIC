"""歌词机审（MVP 版：敏感词匹配）。

生产建议：接 ASR（音频转文字）+ 专业内容安全 API（如自建分类模型 / 云审核）。
路由层在生成前调用 check_lyric；返回 False 即拦截。

机制默认启用（settings.enable_moderation=True，协同部署面向中国目标用户，
文本机审默认开启；关闭会让歌曲停留 pending 且不进入 explore/同步）。
启用后若仍用占位词库（blocklist.txt 缺失），首次机审打印一次告警，
防止「开了开关却用示例词假拦截」。
"""

import logging
from pathlib import Path

from app.config import settings

_logger = logging.getLogger(__name__)
_audio_placeholder_warned = False
_blocklist_placeholder_warned = False

# 默认拦截词（占位，请按合规要求替换为完整词库，或放 blocklist.txt）
DEFAULT_BLOCKLIST = ["示例敏感词1", "示例敏感词2"]


def _blocklist_path() -> Path:
    """[D33] 단어장 경로를 **CWD 가 아닌 패키지 기준** 으로 결정한다.

    기존 구현은 `open("blocklist.txt")` 였다 — 즉 **프로세스 CWD 에 의존** 했다.
    리포에는 `backend/blocklist.txt`(50행, 실제 단어) 가 존재하므로
    `cd backend` 실행(로컬/테스트) 에서는 맞지만, CWD 가 달라지면
    **파일이 이미지 안에 있어도** 조용히 자리표시자 2단어로 대체된다.
    → 컨테이너에서만 심의가 무력화되는 전형적인 환경 의존 결함.

    기준점: 본 파일은 `backend/app/moderation.py` → `backend/blocklist.txt`.
    `MODERATION_BLOCKLIST_PATH` 로 명시적 override 가능.
    """
    override = getattr(settings, "moderation_blocklist_path", None)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "blocklist.txt"


def _load_blocklist() -> tuple[list[str], bool, str]:
    """返回 (词表, 是否使用占位, 实际读取路径)。"""
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


_BLOCKLIST, _USING_PLACEHOLDER, _BLOCKLIST_PATH = _load_blocklist()


def describe_state() -> dict:
    """[D33] 심의 상태를 운영 가시성용으로 노출(health/로그).

    `enable_moderation=True` 인데 `using_placeholder=True` 면
    **"켰지만 실제로는 아무것도 걸러지지 않는"** 상태다 — 반드시 경고로 드러내야 한다.
    """
    return {
        "enabled": bool(settings.enable_moderation),
        "using_placeholder": bool(_USING_PLACEHOLDER),
        "path": _BLOCKLIST_PATH,
        "terms": len(_BLOCKLIST),
        "audio_enabled": bool(settings.enable_audio_moderation),
    }


def check_lyric(text: str) -> bool:
    """返回 True 表示通过；False 表示命中敏感词。

    启用机审却仍用占位词库时，仅首次打印一次告警，避免误判「已生效」。
    """
    global _blocklist_placeholder_warned
    if not text:
        return True
    if _USING_PLACEHOLDER and settings.enable_moderation and not _blocklist_placeholder_warned:
        _blocklist_placeholder_warned = True
        _logger.warning(
            "机审使用占位敏感词库（blocklist.txt 缺失，回退示例词）；"
            "请勿在长期开启 enable_moderation 的环境下依赖自动拦截，请配置真实合规词库。"
        )
    return not any(w in text for w in _BLOCKLIST)


def moderate_audio(audio_url: str) -> bool:
    """音频机审（P3 扩展点）：返回 True 表示通过。

    生产接 ASR（音频转文字）+ 文本审核 check_lyric：先转写再审。受
    settings.enable_audio_moderation 控制——开关关闭时调用方（_process_song）
    根本不进入本函数，音频保持 pending 走人工审核主路径；开关开启却仍用
    占位，则为静默放行。为避免「开了开关却忘了接实现」导致假通过，仅首次
    调用打印一次告警提示接入真实服务。
    """
    global _audio_placeholder_warned
    if not _audio_placeholder_warned:
        _audio_placeholder_warned = True
        _logger.warning(
            "moderate_audio 为占位实现（返回通过），未接入真实 ASR+审核；"
            "请勿在长期开启 enable_audio_moderation 的未接入环境下依赖自动审核。"
        )
    return True
