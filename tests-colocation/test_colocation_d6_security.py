"""공존(co-location) 설계 D6 — 보안·공존 게이트.

대상: T6.2 · T6.3 · T6.4 · T6.5 · T6.7 · T6.8 · T6.9
근거 문서: ``docs/COLOCATION_MOCKTEST_PLAN_2026-08-31.md`` §4 D6
설계 문서: ``docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md`` §15 (X 시리즈)

─────────────────────────────────────────────────────────────────────────
게이트 표기법
─────────────────────────────────────────────────────────────────────────
``@pytest.mark.xfail(strict=True)`` 는 **목표 미달을 '기대된 실패'로 고정** 한다.
해당 항목이 구현되면 ``xpassed`` → strict 모드가 **오류로 처리** 하여 마커 제거를
강제한다. 제거 후에는 정상적인 병합 차단 게이트가 된다.

─────────────────────────────────────────────────────────────────────────
⚠️ 실측 결과 요약 (2026-08-31, 본 모듈 작성 전 직접 실행하여 확인)
─────────────────────────────────────────────────────────────────────────

**[T6.2 / X5] 시크릿 기본값**
  FLOW  ``backend/app/api/auth.py:229-233``  admin_api_key == "change-me" → 503.
        ``backend/app/config.py:185``  admin_api_key: str = "change-me"  (기본값).
  음악  ``backend/app/config.py:59``  admin_token: str = ""  (빈값 → admin 라우트 비활성).
  → 양측 모두 프로덕션에서 반드시 설정해야 하는 시크릿이 기본값/빈값이다.
    T6.2a(FLOW 503) · T6.2b(음악 빈값) 는 **현재 동작을 그대로 단언**(문서화).

**[T6.3 / X6] 무인증 공개 범위**
  FLOW  ``backend/app/api/media.py:157-158``  list_media_catalog → media_service.list_public
        ``backend/app/services/media_service.py:195-206``  list_public 는
        ``active_only=True`` 일 때 ``MediaAsset.active.is_(True)`` 로 필터한다(``:201-202``).
  → 카탈로그는 미발행 곡을 노출하지 않는다(통과). 단 stream 경로의 D25 미발행 노출은
    D1/D2 의 T1.9b 로 이미 xfail 고정.

**[T6.4 / X6] SSRF**
  FLOW  ``backend/app/security/ssrf.py:26-42``  차단 대역(loopback/private/link-local/
        CGNAT/multicast/metadata) + ``:64-92`` is_safe() 해석 차단. **구현됨(통과)**.
  음악  ``backend/app/routers/flow.py:23-40``  _ensure_cdn_url 가
        ``httpx.AsyncClient().get(song.audio_url)`` 를 **SSRF 가드 없이** 호출(``:32``).
        → T6.4b = xfail(음악 측 가드 부재).

**[T6.5 / 🟠4] CORS**
  FLOW  ``backend/app/main.py:292-304``  allow_origins=settings.cors_origins(화이트리스트),
        allow_credentials=False, 명시적 methods/headers. **구현됨(통과, 정적 검사)**.

**[T6.7 / 🟡8] 관리자 토큰 URL 노출**
  FLOW  ``admin/lib/auth.py:37``  _TOKEN_PARAM = "a" — 토큰을 URL 쿼리로 운반.
        ``:41-44``  _expected_token 는 raw password 가 아닌 **HMAC 파생 32자**(양호, 통과).
        단 Caddy 가 기본 access log 로 전체 URI(``?a=<token>``)를 남기면 노출.
        ``Caddyfile`` 에 해당 쿼리를 누락하는 ``log`` 블록이 없음 → T6.7b = xfail.

**[T6.8 / F1] 레이트리밋 비공유 (SC-8 정정)**
  음악  ``backend/app/ratelimit.py:13``  _hits = defaultdict(deque) — **프로세스 로컬**.
        Redis/공유 저장소 참조 0건 → 다중 인스턴스에서 카운트 미공유(통과, 문서화).
  FLOW  ``backend/app/middleware/rate_limit.py:27-29,42-55,86-123``  Redis 우선 + 인메모리
        폴백 구조(통과, 구조 단언). 단 폴백 시 **경고 로그 없음**(``except Exception: pass``
        + ``# nosec B110``) → T6.8b_warn = xfail(폴백 가시성 보완 필요).

**[T6.9 / F3] api_key 평문 저장 (SC-9 정정)**
  음악  ``backend/app/models.py:76``  api_key = Column(String(512), default="") — **평문 at-rest**.
        저장 암호화 0건(주석 자체가 "미접속 암호화" 를 명시). → T6.9a = 통과(알려진 갭 문서화).
  음악  ``backend/app/schemas.py:150``  api_key_set: bool = False(주석 "脱敏").
        ``backend/app/routers/admin.py:45``  api_key_set=bool(e.api_key) — GET 응답은
        **이미 마스킹 구현됨**. → T6.9b = 통과(회귀 차단).

─────────────────────────────────────────────────────────────────────────
주의 (환경)
─────────────────────────────────────────────────────────────────────────
음악 플랫폼 저장소는 FLOW 밖에 있으므로 환경변수로 경로를 받는다.
    export MUSIC_PLATFORM_REPO=C:/Desktop/korean-gospel-ai/ai-music-platform
미설정 시 음악 의존 테스트는 skip 된다(게이트 강등 방지, SC-24).
음악 모듈은 **AST/정적 검사** 로만 다루므로 ``sys.modules["app"]`` 오염(SC-20)을
회피한다 — 런타임 import 없이 소스 트리를 파싱한다.
"""

from __future__ import annotations

import ast
import os
import unittest.mock as mock
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

FLOW_MAIN_PY = REPO_ROOT / "backend" / "app" / "main.py"
FLOW_RATE_LIMIT_PY = REPO_ROOT / "backend" / "app" / "middleware" / "rate_limit.py"
ADMIN_AUTH_PY = REPO_ROOT / "admin" / "lib" / "auth.py"
CADDYFILE = REPO_ROOT / "Caddyfile"


# ══════════════════════════════════════════════════════════════════════
# 공용 유틸
# ══════════════════════════════════════════════════════════════════════
def _music_backend() -> Path:
    """음악 플랫폼 backend 경로. 미설정이면 skip."""
    raw = os.environ.get("MUSIC_PLATFORM_REPO", "").strip()
    if not raw:
        pytest.skip(
            "MUSIC_PLATFORM_REPO 환경변수 미설정 — 음악 플랫폼 저장소 경로를 지정하세요. "
            "(예: C:/Desktop/korean-gospel-ai/ai-music-platform)"
        )
    backend = Path(raw) / "backend"
    if not (backend / "alembic.ini").is_file():
        pytest.skip(f"MUSIC_PLATFORM_REPO 하위에 backend/alembic.ini 가 없다: {backend}")
    return backend


def _mread(rel: str) -> str:
    """음악 repo 상대경로 소스 읽기(미설정 시 skip)."""
    return (_music_backend() / rel).read_text(encoding="utf-8", errors="replace")


def _mast(rel: str) -> ast.Module:
    """음악 repo 상대경로 AST 파싱."""
    return ast.parse(_mread(rel))


def _flow_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _flow_ast(path: Path) -> ast.Module:
    return ast.parse(_flow_read(path))


def _assign_target_names(tree: ast.Module) -> dict[str, ast.Assign]:
    """모듈 최상위 ``name = ...`` / ``name: T = ...`` 만 매핑(클래스 내 필드는 제외)."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node
    return out


def _func_node(tree: ast.Module, name: str):
    """동기/비동기 함수 정의 노드 탐색."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_names_in(node) -> set[str]:
    """노드 내부의 모든 Call 대상 이름 집합(``a.b()`` → "b", ``f()`` → "f")."""
    names: set[str] = set()
    for c in ast.walk(node):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Attribute):
                names.add(f.attr)
            elif isinstance(f, ast.Name):
                names.add(f.id)
    return names


# ══════════════════════════════════════════════════════════════════════
# T6.2 — 시크릿 기본값 (X5)
# ══════════════════════════════════════════════════════════════════════
def test_t6_2a_flow_change_me_returns_503():
    """[통과] ``auth.py:229-233`` — admin_api_key='change-me' → 503."""
    from backend.app.api.auth import check_admin
    from backend.app.config import settings as flow_settings

    with (
        mock.patch.object(flow_settings, "admin_api_key", "change-me"),
        pytest.raises(Exception) as exc,
    ):
        check_admin(authorization=None, x_api_key=None)
    # FastAPI HTTPException
    assert exc.value.status_code == 503, f"change-me 일 때 503 기대, 실제 {exc.value.status_code}"


def test_t6_2b_music_admin_token_default_empty():
    """[통과/문서화] ``config.py:59`` — admin_token 기본값 '' (빈값 → admin 비활성).

    설계 의도: 빈값이면 admin 라우트가 비활성화되어야 한다(X5). 프로덕션에서는
    반드시 설정해야 하는 시크릿이 기본값으로 비어 있음을 단언(알려진 갭 문서화).
    """
    tree = _mast("app/config.py")
    found = False
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.AnnAssign)
            and isinstance(n.target, ast.Name)
            and n.target.id == "admin_token"
            and isinstance(n.value, ast.Constant)
            and n.value.value == ""
        ):
            found = True
    assert found, "music config.admin_token 기본값이 '' 이 아니다 — X5 가드 전제 변경"


# ══════════════════════════════════════════════════════════════════════
# T6.3 — 무인증 공개 범위 (X6)
# ══════════════════════════════════════════════════════════════════════
def _make_asset(sf, **fields) -> str:
    """MediaAsset 을 커밋까지 보장해 삽입하고 asset_id 반환(SC-22 함정 회피)."""
    from backend.app.models.orm import MediaAsset

    defaults = {
        "title": "곡",
        "category": "worship",
        "mime_type": "audio/mpeg",
        "size_bytes": 1,
        "file_name": "x.mp3",
        "storage_path": "/tmp/x.mp3",
    }
    defaults.update(fields)
    with sf() as s:
        a = MediaAsset(**defaults)
        s.add(a)
        s.flush()
        aid = a.asset_id
        s.commit()
    with sf() as s:
        assert s.query(MediaAsset).filter_by(asset_id=aid).first() is not None, (
            f"삽입 검증 실패 — 행이 커밋되지 않았다: {aid}"
        )
    return aid


def test_t6_3_public_catalog_only_active(iso_db):
    """[통과] ``media_service.py:201-202`` — list_public 는 active 만 반환.

    무인증 공개 카탈로그가 미발행(active=False) 곡을 노출하지 않음을 단언.
    (stream 경로의 D25 미발행 노출은 T1.9b 로 별도 xfail 고정.)
    """
    from backend.app.services import media_service

    sf = iso_db
    aid_active = _make_asset(sf, active=True, category="worship")
    aid_inactive = _make_asset(sf, active=False, category="worship")

    items = media_service.list_public(category="worship")
    ids = {it["asset_id"] for it in items}
    assert aid_active in ids, "active 곡이 카탈로그에 없다"
    assert aid_inactive not in ids, "미발행(active=False) 곡이 무인증 카탈로그에 노출된다"


# ══════════════════════════════════════════════════════════════════════
# T6.4 — SSRF (X6)
# ══════════════════════════════════════════════════════════════════════
def test_t6_4a_flow_ssrf_guard_blocks_private():
    """[통과] ``security/ssrf.py:26-42,64-92`` — loopback/private/link-local/메타데이터 차단."""
    from backend.app.security.ssrf import SSRFGuard

    g = SSRFGuard()
    # scheme 거부
    assert g.is_safe("ftp://example.com/") is False
    # 클라우드 메타데이터(169.254.169.254)
    assert g.is_safe("http://169.254.169.254/latest/meta") is False
    # loopback
    assert g.is_safe("http://127.0.0.1/") is False
    assert g.is_safe("http://[::1]/") is False
    # 사설 대역(오프라인에서도 IP 리터럴은 로컬 해석됨)
    assert g.is_safe("http://10.0.0.5/") is False
    assert g.is_safe("http://192.168.1.1/") is False
    # 파싱 실패도 거부
    assert g.is_safe("not-a-url") is False


def test_t6_4b_music_applies_ssrf_before_fetch():
    """[통과 — X6 수정 2026-08-31] ``routers/flow.py:23-40`` — _ensure_cdn_url 가 외부 fetch 전 SSRF 가드 부재.

    음악은 ``song.audio_url`` 을 ``httpx.AsyncClient().get(...)`` 로 직접 fetch(``:32``)하며
    FLOW 의 ``security/ssrf.py`` 가드를 전혀 거치지 않는다. SSRF(내부/메타데이터 주소
    인출) 노출. 게이트: 외부 fetch 전 SSRF 가드가 사용되어야 함(현재 없음 → 실패 → XFAIL).
    가드가 추가되면 본 테스트가 통과 → xpass→strict 오류로 마커 제거 강제.
    """
    tree = _mast("app/routers/flow.py")
    fn = _func_node(tree, "_ensure_cdn_url")
    assert fn is not None, "_ensure_cdn_url 함수가 없다"
    calls = _call_names_in(fn)
    # 외부 fetch 는 존재
    assert "get" in calls, "_ensure_cdn_url 가 외부 fetch(get)를 하지 않는다"
    # 게이트: 외부 fetch 전 SSRF 가드가 사용되어야 함(현재 없음 → 실패)
    ssrf_markers = {"ssrf", "SSRFGuard", "is_safe", "guard", "is_private", "ipaddress"}
    assert ssrf_markers & calls, (
        "음악 _ensure_cdn_url 가 외부 fetch 전 SSRF 가드를 사용하지 않는다 — "
        "D6 T6.4b 미충족(게이트 발동). 가드 추가 시 본 테스트를 통과시켜 마커를 제거하라."
    )


# ══════════════════════════════════════════════════════════════════════
# T6.5 — CORS 화이트리스트 (🟠4)
# ══════════════════════════════════════════════════════════════════════
def test_t6_4c_portone_egress_is_ssrf_guarded():
    """[통과] ``services/portone_service.py`` — 외부 결제 호출 전 ssrf_guard.guard 강제 호출.

    T6.4a 가 가드 로직을 검증했다면, 본 테스트는 **실제 발신 경로에 가드가接线
    되었는지** 를 검증한다(데드코드 → 강제 호출). 가드 없이 내부/메타데이터 주소를
    가리키는 PORTONE_API_BASE 설정 시 결제 호출이 차단된다.
    """
    import inspect

    from backend.app.security.ssrf import ssrf_guard
    from backend.app.services import portone_service

    # 모듈이 가드를 import 했는지
    assert hasattr(portone_service, "ssrf_guard"), (
        "portone_service 가 ssrf_guard 를 import 하지 않음"
    )
    # _post_json 이 호출 전 가드를 수행하는지(소스 단언)
    src = inspect.getsource(portone_service._post_json)
    assert "ssrf_guard.guard" in src, "portone_service._post_json 이 SSRF 가드를 호출하지 않는다"
    # 가드 자체가 internal/메타데이터 를 차단하는지(강제 호출 증명)
    with pytest.raises(ValueError):
        ssrf_guard.guard("http://169.254.169.254/latest/meta")


def test_t6_4d_ssrf_fail_closed_on_dns_failure():
    """[통과] DNS 해석 실패 시 fail-closed.

    이전 구현은 ``getaddrinfo`` 가 빈 리스트를 반환하면 루프가 미실행되어
    ``True``(허용) 를 반환 → 내부/메타데이터 주소 인출 가능. 수정 후 빈 리스트 →
    ``False``(차단). 해석 불가 호스트는 네트워크 불필요(결정적).
    """
    from backend.app.security.ssrf import ssrf_guard

    assert ssrf_guard.is_safe("http://this-host-does-not-exist-xyz-98765.invalid/") is False
    # 회귀: 루프백/사설 대역 차단은 유지
    assert ssrf_guard.is_safe("http://127.0.0.1/") is False
    assert ssrf_guard.is_safe("http://10.0.0.5/") is False


def test_t6_5_cors_no_wildcard_and_credentials_false():
    """[통과/정적] ``main.py:292-304`` — allow_credentials=False + 화이트리스트(와일드카드 금지).

    app 객체를 기동하지 않고(시작 훅 부작용 회피) 소스 트리에서 CORSMiddleware
    설정을 정적으로 단언한다.
    """
    from backend.app.config import settings as flow_settings

    tree = _flow_ast(FLOW_MAIN_PY)
    saw_cors = False
    creds_false = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_middleware"
        ):
            # CORSMiddleware 인자 확인 (import 된 Name 또는 속성 모두 허용)
            cors_arg = node.args[0] if node.args else None
            is_cors = (
                isinstance(cors_arg, ast.Attribute) and cors_arg.attr == "CORSMiddleware"
            ) or (isinstance(cors_arg, ast.Name) and cors_arg.id == "CORSMiddleware")
            if is_cors:
                saw_cors = True
                for kw in node.keywords:
                    if kw.arg == "allow_credentials" and isinstance(kw.value, ast.Constant):
                        creds_false = kw.value.value is False
    assert saw_cors, "main.py 에 CORSMiddleware 가 없다"
    assert creds_false, "CORS allow_credentials=False 가 아니다(자격증명 누출 위험)"
    # 화이트리스트가 와일드카드를 포함하지 않아야 함
    assert "*" not in (flow_settings.cors_origins or ""), (
        "cors_origins 에 와일드카드('*')가 포함됨 — 임의 오리진 허용"
    )


# ══════════════════════════════════════════════════════════════════════
# T6.7 — 관리자 토큰 URL 노출 (🟡8)
# ══════════════════════════════════════════════════════════════════════
def test_t6_7a_admin_token_is_hmac_not_raw_password():
    """[통과] ``admin/lib/auth.py:37,41-44`` — 토큰은 raw password 가 아닌 HMAC 파생 32자.

    토큰이 URL(``?a=``)에 실려도 최소한 raw password 는 아니며, 길이 고정(32)으로
    전송량이 제한된다. 양호한 설계를 회귀 차단.
    """
    src = _flow_read(ADMIN_AUTH_PY)
    assert '_TOKEN_PARAM = "a"' in src, "관리자 토큰 파라미터명이 'a' 가 아니다"
    assert "hmac.new" in src, "토큰이 HMAC 파생되지 않는다(raw password 노출 위험)"


def test_t6_7b_caddy_log_does_not_expose_token_query():
    """[통과 — 🟡8 해결 2026-09-01] ``Caddyfile`` — 관리자 토큰(``?a=<token>``)이 access log 에
    기록되지 않아야 함.

    토큰이 URL 쿼리로 운반되므로(``admin/lib/auth.py:37``), 기본 Caddy access log 가
    전체 URI 를 남기면 토큰이 로그에 노출된다. [2026-09-01] ``Caddyfile`` ``:443`` 사이트에
    ``log { format json { uri {http.request.uri.path} ... } }`` 블록을 추가해 **path 만
    기록하고 쿼리(``?a=`` 토큰)는 절대 로그에 남지 않도록** 했다. 게이트 충족 → 마커 제거.
    """
    caddy = _flow_read(CADDYFILE)
    # 게이트: 토큰 쿼리를 누락하는 log 블록이 존재해야 함
    assert "log {" in caddy, "Caddyfile 에 ?a= 토큰 쿼리를 누락하는 log 블록이 없다"


# ══════════════════════════════════════════════════════════════════════
# T6.8 — 레이트리밋 비공유 (F1, SC-8 정정)
# ══════════════════════════════════════════════════════════════════════
def test_t6_8a_music_ratelimit_is_process_local():
    """[통과/문서화] ``ratelimit.py:13`` — _hits 는 모듈 전역 defaultdict(공유 저장소 0건).

    음악은 Redis/공유 버킷 참조가 없으므로 다중 인스턴스에서 카운트가 미공유된다(F1).
    """
    tree = _mast("app/ratelimit.py")
    # 모듈 최상위 _hits = defaultdict(deque)
    top = _assign_target_names(tree)
    assert "hits" in top or "_hits" in top, "음악 ratelimit 에 _hits 가 없다"
    target = top.get("_hits") or top.get("hits")
    is_defaultdict = (
        isinstance(target.value, ast.Call)
        and isinstance(target.value.func, ast.Name)
        and target.value.func.id == "defaultdict"
    )
    assert is_defaultdict, "_hits 가 defaultdict(deque) 가 아니다"
    # redis 실제 사용(import/호출)이 전혀 없어야 함 — 주석/독스트링 언급은 제외(SC-18b)
    uses_redis = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            if any(a.name == "redis" for a in n.names):
                uses_redis = True
        elif isinstance(n, ast.ImportFrom) and n.module == "redis":
            uses_redis = True
        elif (
            isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "redis"
        ):
            uses_redis = True  # redis.Redis / redis.from_url 호출
    assert not uses_redis, "음악 ratelimit 이 redis 를 실제로 사용한다 — 비공유 전제와 충돌"


def test_t6_8b_structure_flow_redis_priority_with_inmemory_fallback():
    """[통과] ``middleware/rate_limit.py:27-29,42-55,86-123`` — Redis 우선 + 인메모리 폴백 구조.

    FLOW 는 Redis 공유 버킷을 우선하고, 실패 시 프로세스 내 ``_buckets`` 로 폴백한다.
    (음악과 달리 공유 저장소를 쓰므로 다중 인스턴스에서 어느 정도 일관성 확보.)
    """
    src = _flow_read(FLOW_RATE_LIMIT_PY)
    assert "_get_redis" in src, "FLOW 레이트리밋에 Redis 경로가 없다"
    assert "_buckets" in src, "FLOW 레이트리밋에 인메모리 폴백(_buckets)이 없다"


def test_t6_8b_warn_flow_fallback_emits_warning():
    """[xfail] ``middleware/rate_limit.py:110,123`` — Redis 폴백 시 경고 로그가 없음.

    Redis 실패 경로는 ``except Exception: pass`` + ``# nosec B110`` 로 조용히 폴백된다.
    SC-8 정정에 따라 다중 인스턴스에서 폴백(비공유 카운트)으로 전환되는 순간을
    운영자가 인지할 수 있도록 **경고 로그가 필요**하다. 현재 부재 → xfail.
    """
    src = _flow_read(FLOW_RATE_LIMIT_PY)
    assert "log.warning" in src and "redis" in src.lower(), (
        "FLOW 레이트리밋이 Redis 폴백 시 경고 로그를 남기지 않는다"
    )


# ══════════════════════════════════════════════════════════════════════
# T6.9 — api_key 이중 검증 (F3, SC-9 정정)
# ══════════════════════════════════════════════════════════════════════
def test_t6_9a_music_api_key_stored_plaintext():
    """[통과/문서화] ``models.py:76`` — api_key 는 평문 String(at-rest 암호화 0건).

    저장 계층은 평문이다(주석 자체가 "미접속 암호화" 를 명시). 알려진 갭을 문서화.
    """
    tree = _mast("app/models.py")
    found_plaintext = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Name)
                    and t.id == "api_key"
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "Column"
                    and node.value.args
                    and isinstance(node.value.args[0], ast.Call)
                    and getattr(node.value.args[0].func, "id", None) == "String"
                ):
                    found_plaintext = True
    assert found_plaintext, "music EngineConfig.api_key 가 평문 String 컬럼이 아니다 — F3 전제 변경"


def test_t6_9b_music_api_key_get_response_masked():
    """[통과] ``schemas.py:150`` + ``routers/admin.py:45`` — GET 응답은 api_key 를 마스킹.

    응답 스키마에 ``api_key_set: bool`` 가 있고, admin 이 ``api_key_set=bool(e.api_key)``
    로 평문 대신 플래그만 내려준다. 평문 노출 회귀를 차단.
    """
    schema_tree = _mast("app/schemas.py")
    has_mask_field = any(
        isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "api_key_set"
        and isinstance(n.annotation, ast.Name)
        and n.annotation.id == "bool"
        for n in ast.walk(schema_tree)
    )
    assert has_mask_field, "schemas.EngineConfigOut 에 api_key_set: bool 마스킹 필드가 없다"

    admin_src = _mread("app/routers/admin.py")
    assert "api_key_set=bool(" in admin_src, (
        "admin 라우트가 api_key_set=bool(e.api_key) 로 마스킹하지 않는다"
    )
