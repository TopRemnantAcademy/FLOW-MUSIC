"""FastAPI 入口：CORS、路由挂载、启动建表。"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.audit import log_admin_access
from app.config import settings
from app.db import init_db
from app.mureka_health import run_startup_preflight
from app.routers import admin, auth, songs, flow, billing, plaza, library, search, share, lyrics, export, release, users, analytics_dashboard, notifications, developer
from app.routers.auth import _resolve_admin_role

log = logging.getLogger("music-api")

# [D37] 공개된 기본 서명 키 집합(routers/auth.py 와 동일 기준).
# 여기서는 **기동 시점 가시성** 용으로만 쓴다 — 실제 차단은 auth.py 가 수행한다.
_INSECURE_JWT_SECRETS = frozenset(
    {"", "change-me-please", "changeme", "change-me", "secret", "test"}
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── T5.6 / X9 — create_all 폴백 환경 분기 (FLOW 와 동일) ──
    # 운영(공유 Postgres) 에서는 Alembic `upgrade head` 로 스키마를 구성하므로
    # create_all 을 비활성화한다(SCHEMA_CREATE_ALL=0). 개발·SQLite 기본값은 활성.
    # 빈 DB 에서 FLOW upgrade head 가 스키마를 완성하지 못하는 경우(D21) 를 대비해
    # 기본값은 "1"(활성) 로 둔다.
    if os.environ.get("SCHEMA_CREATE_ALL", "1") == "1":
        init_db()
    # [perf 2026-09-10 实测复盘] 曾尝试把 anyio sync 线程帽 40→100：**回归**（rps 164→26、
    # Phase A 67s→104s、tokens 77/100）——100 个 sync handler 线程抢 GIL 造成 convoy，
    # CPU 密集的 Python 段（路由/pydantic/ORM）全面变慢。GIL 下线程数超过物理核数是负收益，
    # 保持 anyio 默认 40。连接池扩容（db.py 50/50=100）独立生效：池耗尽型 500 与线程数解耦，
    # 在途请求最多 100 个 → 池 100 可全覆盖「依赖解析完、handler 排队等线程」时的连接停放。
    # [D37] 기동 즉시 경고 — 첫 로그인 시도 전에도 운영자가 오구성을 볼 수 있게 한다.
    # (차단 자체는 routers/auth.py:_assert_jwt_secret_configured 가 503 으로 수행)
    if settings.jwt_secret.strip().lower() in _INSECURE_JWT_SECRETS:
        log.error(
            "[SECURITY] JWT_SECRET 이 공개된 기본값(%r) 입니다 — 누구나 위조 토큰을 만들 수 있습니다. "
            "JWT_SECRET 환경변수를 반드시 설정하세요. (docker-compose.yml 의 기본값 주입도 제거됨)",
            settings.jwt_secret,
        )
    # Mureka 出口自检（P0-CRIT）：把「403 地域封锁」从「用户生成失败后才发现」
    # 变成「部署完第一行日志就可见」。warn 模式永不阻断启动；测试环境由 conftest 置 off。
    try:
        await run_startup_preflight()
    except RuntimeError:
        raise  # strict 模式：地域封锁/认证失败 → 有意为之，拒绝启动
    except Exception as e:  # 自检自身异常绝不能拖垮启动
        log.warning("Mureka 出口自检异常（已忽略，不影响启动）: %s", e)
    yield


# 🔒 安全加固（2026-09-13 审计，CWE-200 信息泄露 / OWASP A05）：
# 默认关闭 /docs、/openapi.json、/redoc —— 它们会完整暴露认证/管理/计费接口结构，
# 显著降低攻击者枚举成本。开发环境用 DOCS_ENABLED=true 显式打开。
_DOCS_KWARGS = (
    {} if settings.docs_enabled else {"docs_url": None, "openapi_url": None, "redoc_url": None}
)
app = FastAPI(title="AI 音乐平台 API", version="0.1.0", lifespan=lifespan, **_DOCS_KWARGS)

# 🔒 安全加固（2026-09-13 审计，CWE-693 缺失防护头 / OWASP A05 安全配置错误）：
# 补齐 nosniff / X-Frame-Options / Referrer-Policy / Permissions-Policy / CSP，
# HTTPS 下追加 HSTS。交互文档依赖外部 CDN 资源，严格 CSP 会打断，故对文档路径跳过。
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    ),
}
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith(_DOCS_PATHS):
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# [FLOW APP 音乐广场] /api/plaza 是匿名、只读的公开端点。
# 为让 FLOW APP 在任何源（localhost / Capacitor WebView / PWA）都能读取广场，
# 这里对 /api/plaza 单独放开 CORS：反射请求方 Origin（无 Origin 时回落 *），
# 且明确 Allow-Credentials: false（该端点不依赖凭据，避免凭证泄漏面）。
# 其余需要登录的端点仍走上面的全局 CORSMiddleware 白名单，不受影响。
@app.middleware("http")
async def plaza_cors_passthrough(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/plaza"):
        origin = request.headers.get("origin")
        # 🔒 安全加固（2026-09-13 审计，CWE-942 过度宽松 CORS）：原实现原样反射任意
        # Origin，等于绕过全局白名单。现仅回填白名单内 Origin，其余回落 "*"
        # （本端点匿名只读 + Access-Control-Allow-Credentials: false，故 * 是安全的）。
        if origin is not None and origin not in settings.cors_origin_list:
            origin = "*"
        if request.method == "OPTIONS":
            return JSONResponse(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin or "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Accept, Content-Type",
                    "Access-Control-Max-Age": "600",
                },
                content={},
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Accept, Content-Type"
        response.headers["Access-Control-Allow-Credentials"] = "false"
        return response
    return await call_next(request)


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def admin_rbac_audit(request: Request, call_next):
    """管理后台 RBAC + 审计（GAP-006）：

    - viewer 令牌对变更类(GET 外)接口直接 403；super 令牌不受影响。
    - 所有 /api/admin 访问（含 401/403/200）写入审计日志，便于追溯入侵与操作。
    """
    path = request.url.path
    is_admin = path.startswith("/api/admin")
    response = None
    if is_admin and request.method in _MUTATING_METHODS:
        _admin_tok = request.headers.get("X-Admin-Token") or request.cookies.get(
            settings.admin_cookie_name
        )
        if _resolve_admin_role(_admin_tok) == "viewer":
            response = JSONResponse(
                status_code=403, content={"detail": "只读管理员无权执行变更操作"}
            )
    if response is None:
        response = await call_next(request)
    if is_admin:
        token = request.headers.get("X-Admin-Token") or request.cookies.get(
            settings.admin_cookie_name
        )
        role = _resolve_admin_role(token) or "none"
        ip = request.client.host if request.client else ""
        log_admin_access(
            method=request.method,
            path=path,
            status_code=response.status_code,
            token_prefix=(token or "")[:6],
            role=role,
            ip=ip,
        )
    return response


app.include_router(auth.router)
# GAP-028 批量导出：**必须排在 songs.router 之前**。
# `songs.router` 里有 `GET /{song_id}` 通配路径参数，先注册它会把
# `/api/songs/export.csv` 吃成 song_id="export.csv" → 422（实测），
# 从而遮住导出端点。FastAPI 按注册顺序匹配，先注册者胜。
app.include_router(export.router)
app.include_router(songs.router)
app.include_router(flow.router)
app.include_router(admin.router)
app.include_router(billing.router)
# 广场（原「发现」）：已发布作品 + 排行 + 无文本互动（赞/收藏/星标）
app.include_router(plaza.router)
app.include_router(library.router)
# 搜索/发现（GAP-014）：标签浏览 + 按标签筛选 + 相似推荐 + 时间窗趋势
app.include_router(search.router)
# 公开资料页（GAP-015 只读部分）：GET /api/users/{id} + /songs。
# ⚠ 前缀 /api/users 与 admin 的 /api/admin/users/* 不重叠（后者带 admin 段），
# 但注册顺序仍放在 admin 之后，避免将来有人给两个前缀做「通配收口」时踩到歧义。
app.include_router(users.router)
# 无状态分享链接（GAP-013）：HMAC 签名 token，匿名可访问
app.include_router(share.router)
# 歌词编辑（GAP-012）：LRC 导出/导入 + 行级时间轴编辑 + 改词后重对齐。
# 与 songs.router 同前缀 /api/songs，只是把「歌词」这一独立闭环拆出来，
# 免得本已 2300 行的 songs.py 继续膨胀（见 routers/lyrics.py 模块注释）。
app.include_router(lyrics.router)
# 应用内更新日志（GAP-040）：当前版本 / 是否有新版本 / 要点（无 DB 依赖）
app.include_router(release.router)
# 产品分析看板（GAP-020）：/api/admin/analytics/*，只读聚合，无新表/无迁移（口径见模块头部）
app.include_router(analytics_dashboard.router)
# 通知通道（GAP-021）：Web Push 订阅登记 + 通道自检 + 模板化发送。
# 前缀 /api/notifications 与其它路由不重叠；无通配路径参数，故注册顺序无所谓。
app.include_router(notifications.router)
# 开放平台（GAP-022）：开发者凭证 + Webhook 订阅。前缀 /api/developer 与既有
# /api/* 均不重叠，注册在最后即当前缀为独立命名空间，不参与任何通配匹配。
app.include_router(developer.router)


@app.get("/health")
def health():
    return {"status": "ok"}
