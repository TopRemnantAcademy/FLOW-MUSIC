"""后端 i18n 辅助（GAP-017）——**纯函数模块**，无副作用、无 DB、无 IO。

定位：给「逐步把散落的错误文案替换成错误码」提供迁移路线，本批次**不**改任何既有
错误串（`routers/songs.py` 等被划为不可改，避免并行冲突）。也就是说：

  - 现状：路由层直接返回中文字符串（`HTTPException(detail="...")`）。
  - 目标：路由层返回**稳定错误码**，由本模块按请求语言渲染文案。
  - 迁移方式：新代码用 `pick_message(...)`；老代码原地不动，后续按模块逐个切换。

为什么单独抽一层而不是直接上 gettext/Babel：
  1. 接入成本 —— 迁移期两种形态（码 / 裸串）必须并存，gettext 的全局 catalog 切换
     反而更难「逐点替换」。
  2. 可测试 —— 纯函数，不依赖 app 启动、不需要 locale 中间件。
  3. 词条量很小（错误码 ~30 条），不值得引入编译期 catalog 工具链。

⚠️ 该模块**不**决定客户端语言；语言由调用方从请求（如 `Accept-Language` 或
   cookie `flow_locale`，与前端保持一致）解析后作为 `lang` 传入。
"""

from __future__ import annotations

from typing import Final

# 支持的语言：与前端 messages/{ko,zh,en}.json 一致。
LOCALES: Final[tuple[str, ...]] = ("ko", "zh", "en")
DEFAULT_LANG: Final[str] = "zh"

# ── 错误码常量表 ─────────────────────────────────────────────────────────────
# 命名规范：<域>.<原因>，全小写下划线。**一旦发布不可改**（客户端可能按码分支）。
# 新增错误一律先在此登记，再在 MESSAGES 里补三语词条；缺词条会回退 default/码本身。

SONG_NOT_FOUND: Final[str] = "song.not_found"
SONG_NOT_READY: Final[str] = "song.not_ready"
SONG_FORBIDDEN: Final[str] = "song.forbidden"
SONG_QUOTA_EXCEEDED: Final[str] = "song.quota_exceeded"

AUTH_REQUIRED: Final[str] = "auth.required"
AUTH_INVALID: Final[str] = "auth.invalid"
AUTH_FORBIDDEN: Final[str] = "auth.forbidden"

BILLING_PLAN_INVALID: Final[str] = "billing.plan_invalid"
BILLING_ORDER_NOT_FOUND: Final[str] = "billing.order_not_found"
BILLING_ALREADY_SUBSCRIBED: Final[str] = "billing.already_subscribed"

COMMON_BAD_REQUEST: Final[str] = "common.bad_request"
COMMON_NOT_FOUND: Final[str] = "common.not_found"
COMMON_RATE_LIMITED: Final[str] = "common.rate_limited"
COMMON_INTERNAL: Final[str] = "common.internal"

# 供迁移脚本/审计使用：当前登记的全部错误码。
ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        SONG_NOT_FOUND,
        SONG_NOT_READY,
        SONG_FORBIDDEN,
        SONG_QUOTA_EXCEEDED,
        AUTH_REQUIRED,
        AUTH_INVALID,
        AUTH_FORBIDDEN,
        BILLING_PLAN_INVALID,
        BILLING_ORDER_NOT_FOUND,
        BILLING_ALREADY_SUBSCRIBED,
        COMMON_BAD_REQUEST,
        COMMON_NOT_FOUND,
        COMMON_RATE_LIMITED,
        COMMON_INTERNAL,
    }
)

# code → {lang: 文案}。占位符用 str.format 风格（{name}）。
MESSAGES: Final[dict[str, dict[str, str]]] = {
    SONG_NOT_FOUND: {
        "zh": "歌曲不存在",
        "en": "Song not found",
        "ko": "곡을 찾을 수 없습니다",
    },
    SONG_NOT_READY: {
        "zh": "歌曲尚未生成完成",
        "en": "Song is not ready yet",
        "ko": "곡이 아직 준비되지 않았습니다",
    },
    SONG_FORBIDDEN: {
        "zh": "无权访问该歌曲",
        "en": "You do not have access to this song",
        "ko": "이 곡에 접근할 권한이 없습니다",
    },
    SONG_QUOTA_EXCEEDED: {
        "zh": "今日生成额度已用完",
        "en": "Today's generation quota is used up",
        "ko": "오늘 생성 한도를 모두 사용했습니다",
    },
    AUTH_REQUIRED: {
        "zh": "请先登录",
        "en": "Please sign in first",
        "ko": "먼저 로그인하세요",
    },
    AUTH_INVALID: {
        "zh": "登录已过期或无效",
        "en": "Your session is invalid or expired",
        "ko": "로그인이 만료되었거나 유효하지 않습니다",
    },
    AUTH_FORBIDDEN: {
        "zh": "权限不足",
        "en": "Insufficient permission",
        "ko": "권한이 부족합니다",
    },
    BILLING_PLAN_INVALID: {
        "zh": "套餐不存在或不可用",
        "en": "Plan does not exist or is unavailable",
        "ko": "요금제가 없거나 사용할 수 없습니다",
    },
    BILLING_ORDER_NOT_FOUND: {
        "zh": "订单不存在",
        "en": "Order not found",
        "ko": "주문을 찾을 수 없습니다",
    },
    BILLING_ALREADY_SUBSCRIBED: {
        "zh": "已存在生效中的订阅",
        "en": "An active subscription already exists",
        "ko": "이미 활성 구독이 있습니다",
    },
    COMMON_BAD_REQUEST: {
        "zh": "请求参数有误",
        "en": "Invalid request parameters",
        "ko": "요청 매개변수가 잘못되었습니다",
    },
    COMMON_NOT_FOUND: {
        "zh": "资源不存在",
        "en": "Resource not found",
        "ko": "리소스를 찾을 수 없습니다",
    },
    COMMON_RATE_LIMITED: {
        "zh": "请求过于频繁，请稍后再试",
        "en": "Too many requests, please try again later",
        "ko": "요청이 너무 많습니다. 잠시 후 다시 시도하세요",
    },
    COMMON_INTERNAL: {
        "zh": "服务器内部错误",
        "en": "Internal server error",
        "ko": "서버 내부 오류",
    },
}


def normalize_lang(lang: str | None) -> str:
    """把任意语言标记规整到支持集。

    `zh-CN` / `zh_Hans` / `ko-KR` / `en-US` 这类都接受（取主语言子标签，忽略大小写）。
    未知或 None → DEFAULT_LANG。绝不用未知值去查表，避免 keyerror。
    """
    if not lang:
        return DEFAULT_LANG
    primary = lang.strip().lower().replace("_", "-").split("-")[0]
    return primary if primary in LOCALES else DEFAULT_LANG


def pick_message(
    code: str,
    lang: str | None = None,
    default: str | None = None,
    **params: object,
) -> str:
    """按 `code` + `lang` 取文案（纯函数，无副作用）。

    解析顺序（每一步都可能终止，保证**永不抛异常**）：
      1. code 在表中且该语言有词条 → 用它
      2. 回退 DEFAULT_LANG（zh）词条
      3. 回退 `default`（调用方给的兜底，比如原始中文串）
      4. 回退 code 本身（可见即可定位，不会白屏/静默）

    `params` 用于 `{name}` 占位符格式化。格式化失败（缺参数/花括号异常）时
    **原样返回模板**而不是抛异常 —— 文案问题不该升级成 500。
    """
    entry = MESSAGES.get(code)
    if entry:
        template = entry.get(normalize_lang(lang)) or entry.get(DEFAULT_LANG)
        if template is not None:
            return _safe_format(template, params)
    if default is not None:
        return _safe_format(default, params)
    return code


def _safe_format(template: str, params: dict[str, object]) -> str:
    """`str.format` 的容错版：缺参数或模板本身含非法花括号时返回原模板。"""
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def available_codes() -> list[str]:
    """登记且有三语词条的错误码（迁移审计/测试用）。"""
    return sorted(
        code for code, entry in MESSAGES.items() if all(l in entry for l in LOCALES)
    )
