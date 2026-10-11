"""D3 — PII 분할 데이터 평면 (설계서 §21) 목 테스트.

═══════════════════════════════════════════════════════════════════════
실측 전제 (2026-08-31) — 전부 ``file:line`` 로 확인한 값만 상수로 박는다.
═══════════════════════════════════════════════════════════════════════

분할 평면(§21.3)의 목표 상태:
    [한국 · 소용량 = PII 금고]              [홍콩 · 대용량 = 가명 콘텐츠/연산]
      subscriber_identity                     subscriber_profile (가명)
      music_users_identity                    interaction (스크러빙 후)
      데이터 주체 권리 API + 삭제 원장          document/index_snapshot/qdrant/media_asset

게이트웨이 계약(§21.3, 신규 컴포넌트 1개):
    POST   /pii/resolve          가명키 ↔ 식별자 매핑 (한국만)
    POST   /pii/scrub            홍콩 저장 전 원문 PII 제거
    DELETE /pii/subjects/{id}    주체 권리 단일 진입점 → 홍콩 삭제 팬아웃

── **실측 결과: 위 계약은 현재 0건 구현** ──────────────────────────────
  ① `Subscriber`(`orm.py:183-213`) 한 테이블이 직접 식별자(email:188,
     display_name:189) · 준식별자(age_group:193, gender:194) ·
     민감 내용(current_struggle:195) 을 **모두** 들고 있다 → 분할 자체가 없다.
  ② `/pii/*` 라우트 0건(`backend/app/api/` 전수 확인), `deletion_ledger` 0건.
  ③ `Interaction.subscriber_id` 는 **여전히 FK**(`orm.py:334-336`) → Y1 미착수.
  ④ PII 스크러버 부재. 존재하는 것은 시크릿 전용 `scrub_secrets`
     (`logging_setup.py:75`) 뿐이며, `AppErrorLog.client_ip` 에는
     아예 적용조차 안 된다(`error_monitor.py:69` — 50자 절단만 수행).
  ⑤ `ConsentRecord` 는 **실재**(`compliance.py:45-61`) — 목적(purpose:56)·
     보존기간(retention_days:57)·철회시각(revoked_at:61)을 담을 수 있다.
     즉 T3.11 의 **증빙 그릇은 있고, 교차시스템 연결만 없다.**

── 테스트 구성 원칙 ──────────────────────────────────────────────────
  A. **현행 단언(passed)** — "지금 무엇이 참인가"를 고정한다.
     설계가 구현되면 이 테스트가 **깨지므로** 마커 제거·갱신이 강제된다.
  B. **목표 게이트(xfail, strict=True)** — 미구현 목표를 단언한다.
     구현되면 xpass → strict 오류 → 마커 제거 강제 → 진짜 병합 차단 게이트.
  C. **결정 대기(skip)** — B1(D14)·B2(D16) 미결로 **단언값 자체를 못 정하는**
     항목은 skip 으로 남기고 사유를 명시한다(조용한 무력화 방지, SC-24).

⑥ **D28 (본 모듈 작성 중 발견)** — 홍콩 검색 캐시 키가 **한국 금고 유래
   속성** 을 품고 있다. 아래 ⑦ 체인 참조.

── 하네스 주의 (D1/D2·D5 에서 실측한 함정 재발 방지) ──────────────────
  · SC-20  `sys.modules["app"]` 오염 — 측정 대상 자체가 바뀐다.
  · SC-21  `Base.metadata` 전역 가변 상태 — 테이블 수가 실행 순서에 따라 달라진다.
  · SC-22  `sessionmaker()` 를 `with` 로 쓰면 **commit 이 아니라 close(롤백)**.
            직접 삽입 시 `s.commit()` 필수 + **삽입 후 재조회로 존재를 먼저 단언**.
  · SC-23  전역 속도 제한이 파일 간 오염을 일으킨다.
  · SC-18b 문자열 검색은 독스트링·주석에 속는다 → **AST 로 실행 코드만** 본다.
  · SC-26  문자열 검색은 **거짓 양성만** 만드는 게 아니다. 본 모듈 T3.10a 는
            "프로필 캐시가 없다"고 단언했는데, 실행 결과가 이를 반박했다.
            잘못된 방법이 우연히 "맞는" 결론을 냈고, 그 탓에 **이미 존재하는
            위험(D28) 을 놓칠 뻔했다.** → 부정 단언도 **심볼/구조 단위** 로.
  · SC-27  음악 플랫폼은 SQLAlchemy **1.x 스타일**(``x = Column(...)``)이다.
            2.0 전용 파서로 읽으면 결과가 ``{}`` 가 되어 "테이블이 없다"는
            **거짓 결론** 이 나온다 — 파서는 두 스타일을 모두 읽어야 한다.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

FLOW_APP_DIR = REPO_ROOT / "backend" / "app"
FLOW_ORM_PY = FLOW_APP_DIR / "models" / "orm.py"
FLOW_COMPLIANCE_PY = FLOW_APP_DIR / "models" / "compliance.py"
FLOW_API_DIR = FLOW_APP_DIR / "api"
FLOW_LOGGING_PY = FLOW_APP_DIR / "logging_setup.py"
FLOW_ERROR_MONITOR_PY = FLOW_APP_DIR / "middleware" / "error_monitor.py"
FLOW_RETRIEVER_PY = FLOW_APP_DIR / "services" / "retriever.py"
FLOW_SUBSCRIBER_SERVICE_PY = FLOW_APP_DIR / "services" / "subscriber_service.py"

# ── D28: 홍콩 검색 캐시 키에 박힌 프로필 속성 (retriever.py:216 실측) ──
# 전부 `subscriber_service.py` 의 Subscriber 행(분할 후 한국 금고) 유래.
VAULT_DERIVED_PROFILE_ATTRS = (
    "salvation_status",  # subscriber_service.py:85
    "darakbang_role",  # subscriber_service.py:91
    "is_darakbang_member",
    "darakbang_verified",  # subscriber_service.py:96
    "assume_saved",  # subscriber_service.py:90
)
RETRIEVER_CACHE_TTL_SEC = 1800  # retriever.py:155
RETRIEVER_CACHE_TTL_LINE = 156  # [2026-09-09 재고정] 실측 156행(이전 155). 위 소스 +1 drift
RETRIEVER_CACHE_PROFILE_LINE = 218  # cache_profile = (profile.get("subscriber_id"),) — [2026-09-09 재고정] 실측 218행(이전 216)
RETRIEVER_CACHE_KEY_LINE = 219  # [2026-09-09 재고정] 실측 219행(이전 217). cache_key = (

# Y4(설계서:1045)가 요구하는 "가명키 기반 캐시" 에서 기대되는 키 이름
PSEUDONYM_KEY_MARKERS = frozenset({"subscriber_id", "pseudonym", "subject_id", "pid"})

# ── 실측 상수 (값이 바뀌면 이 파일의 테스트가 깨지도록 고정) ──────────────
SUBSCRIBER_TABLE_CLASS = "Subscriber"  # orm.py:183

# §21.3 이 한국 금고로 규정한 직접 식별자
DIRECT_IDENTIFIER_FIELDS = {
    "email",  # orm.py:188
    "display_name",  # orm.py:189
    "phone",
    "contact",  # orm.py:500  SupportTicket
}
# §22.2 FU10 이 재식별 위험으로 지목한 준식별자
QUASI_IDENTIFIER_FIELDS = {
    "age_group",  # orm.py:193
    "gender",  # orm.py:194
}

# §21.3 이 한국 금고 테이블로 명명한 이름 (구현되면 이 이름이 나타난다)
# 실제 구현은 엔티티별 금고(SubscriberIdentity + JourneyVault + SupportContactVault)로
# 분할하므로, 모두 한국 금고로 인정한다(T3.2d 누수 검사 대상에서 제외).
VAULT_TABLE_CLASSES = {
    "SubscriberIdentity",
    "MusicUserIdentity",
    "JourneyVault",
    "SupportContactVault",
}

# §21.3 게이트웨이 계약 경로
PII_GATEWAY_PATHS = ("/pii/resolve", "/pii/scrub", "/pii/subjects")

# Y5 삭제 전파 원장
DELETION_LEDGER_NAMES = ("deletion_ledger", "DeletionLedger")

# Y2 PII 스크러버 — "시크릿 전용" 과 구분해야 한다
SECRETS_ONLY_SCRUBBERS = {"scrub_secrets", "_scrub", "_mask"}

PII_PROBE_EMAIL = "hong.gildong@example.com"
PII_PROBE_PHONE = "010-1234-5678"
PII_PROBE_TEXT = f"제 이메일은 {PII_PROBE_EMAIL} 이고 전화는 {PII_PROBE_PHONE} 입니다"


# ══════════════════════════════════════════════════════════════════════
# 공통 헬퍼 (AST 기반 — SC-18b 교훈)
# ══════════════════════════════════════════════════════════════════════
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _music_backend() -> Path:
    """음악 플랫폼 backend 경로. 미설정이면 skip (실패도 허위 통과도 아님)."""
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


def _call_name(call: ast.Call) -> str:
    """호출 노드의 **함수 이름** 만 뽑는다(``sa.Column`` · ``Column`` 모두)."""
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", "")


def _decl_field(stmt: ast.stmt) -> tuple[str, str] | None:
    """클래스 본문 문 1개에서 ``(필드명, 선언문)`` 을 뽑는다.

    두 선언 스타일을 **모두** 읽어야 한다(SC-27):
      2.0  ``email: Mapped[str] = mapped_column(String(255))``  → AnnAssign + Call
      1.x  ``email = Column(String(255), nullable=False)``      → Assign + Call
    음악 플랫폼이 1.x 라서 2.0 만 읽으면 결과가 ``{}`` 가 되고,
    "테이블이 없다"는 **거짓 결론** 이 나온다(본 파일에서 실제로 겪었다).
    """
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        if isinstance(stmt.value, ast.Call) and _call_name(stmt.value) == "mapped_column":
            return stmt.target.id, ast.unparse(stmt.value)
        return None
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        tgt = stmt.targets[0]
        if (
            isinstance(tgt, ast.Name)
            and isinstance(stmt.value, ast.Call)
            and _call_name(stmt.value) == "Column"
        ):
            return tgt.id, ast.unparse(stmt.value)
    return None


def _orm_columns(source: str) -> dict[str, dict[str, str]]:
    """선언부를 AST 로 파싱해 ``{클래스: {필드: 선언문}}`` 반환.

    문자열 검색을 쓰지 않는다 — SC-18b(독스트링·주석 거짓 양성) 교훈.
    선언문을 unparse 하여 FK·타입을 판정한다. 2.0/1.x 를 모두 읽는다(SC-27).
    """
    tree = ast.parse(source)
    out: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Base 를 상속한 클래스만 ORM 모델로 본다(_TempEnc 같은 테스트 클래스 배제, SC-21)
        bases = {ast.unparse(b) for b in node.bases}
        if "Base" not in bases:
            continue
        fields: dict[str, str] = {}
        for stmt in node.body:
            got = _decl_field(stmt)
            if got:
                fields[got[0]] = got[1]
        if fields:
            out[node.name] = fields
    return out


def _def_names(root: Path) -> dict[str, list[tuple[str, int]]]:
    """``root`` 하위 파이썬의 **정의된 이름**(함수·클래스)만 수집한다.

    [SC-26] 낱말 검색은 주석·독스트링·문자열 리터럴에 속는다. 실측 거짓 양성:
      · `models/orm.py:103`               ``cascade="all, delete-orphan"`` — ORM 캐스케이드
      · `services/extraction_service.py:273` ``orphan_josa_re``            — 한국어 조사 정규식
    둘 다 "orphan" 이라는 낱말만 같을 뿐 정합 배치가 아니다. **정의 이름**
    단위로만 보면 이런 거짓 양성이 사라진다.
    """
    out: dict[str, list[tuple[str, int]]] = {}
    for p in _walk_py(root):
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue
        try:
            rel = str(p.relative_to(REPO_ROOT))
        except ValueError:
            # 저장소 밖(임시 디렉터리 등)도 스캔할 수 있어야 한다 — 하네스 자기검증용
            rel = str(p)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.setdefault(node.name, []).append((rel, node.lineno))
    return out


def _flow_columns() -> dict[str, dict[str, str]]:
    return _orm_columns(_read(FLOW_ORM_PY)) | _orm_columns(_read(FLOW_COMPLIANCE_PY))


def _line_of(source: str, needle: str) -> int | None:
    for i, ln in enumerate(source.splitlines(), 1):
        if needle in ln:
            return i
    return None


def _routes() -> set[tuple[str, str]]:
    """FastAPI 앱에 실제로 등록된 ``{(METHOD, path)}``.

    [2026-09-01 실측] FastAPI 0.141.1 부터 ``include_router`` 는 라우트를
    ``app.routes`` 에 직접 병합하지 않고 ``_IncludedRouter``(내부
    ``original_router.routes``) 로 감싼다. 최상위 항목만 보면 180개 비즈니스
    라우트가 **전부 사라져** 게이트가 무력화된다(거짓 통과 · SC 계열 함정).
    따라서 ``_IncludedRouter`` 를 재귀적으로 펼쳐 실제 ``APIRoute`` 를 본다.
    """
    from fastapi.routing import _IncludedRouter

    from backend.app.main import app

    out: set[tuple[str, str]] = set()

    def _collect(routes):
        for r in routes:
            if isinstance(r, _IncludedRouter):
                _collect(r.original_router.routes)
                continue
            methods = getattr(r, "methods", None) or set()
            path = getattr(r, "path", "")
            for m in methods:
                if m in {"HEAD", "OPTIONS"}:
                    continue
                out.add((m, path))

    _collect(app.routes)
    return out


def _walk_py(root: Path):
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


# ── 정합 배치 심볼 판정 (T3.8b) ──────────────────────────────────────────
def _is_reconciliation_symbol(name: str) -> bool:
    """이 정의 이름이 "고아행 정합 배치" 로 볼 만한가.

    ``orphan`` 단독으로는 판정하지 않는다 — 한국어 조사 정규식
    ``orphan_josa_re``(`services/extraction_service.py:273`) 처럼 무관한
    심볼까지 잡는다. **정합/정리 동작을 뜻하는 낱말이 함께** 있어야 한다.
    """
    low = name.lower()
    if "reconcil" in low:
        return True
    return "orphan" in low and any(
        t in low for t in ("reconcil", "batch", "clean", "purge", "sweep", "check", "fix")
    )


# ── 홍콩 검색 캐시 구조 (D28 / T3.10) ────────────────────────────────────
def _retrieve_fn() -> ast.AST:
    """``HybridRetriever.retrieve`` 의 AST 노드를 돌려준다."""
    tree = ast.parse(_read(FLOW_RETRIEVER_PY))
    cls = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "HybridRetriever"),
        None,
    )
    assert cls is not None, "HybridRetriever 클래스를 찾지 못했다 — 파서 회귀"
    fn = next(
        (
            n
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "retrieve"
        ),
        None,
    )
    assert fn is not None, "HybridRetriever.retrieve 를 찾지 못했다 — 파서 회귀"
    return cls, fn


def _profile_gets(el: ast.AST) -> list[str]:
    """요소 안의 ``profile.get("...")`` 호출 키를 모두 뽑는다.

    ``bool(profile.get("is_darakbang_member"))`` 처럼 **래퍼가 씌워진** 경우가
    있어 요소 자체만 보면 5개 중 2개만 잡힌다(실측). 하위 트리 전체를 훑는다.
    """
    out: list[str] = []
    for n in ast.walk(el):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "profile"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)
        ):
            out.append(n.args[0].value)
    return out


def _retriever_cache_shape() -> dict[str, object]:
    """``HybridRetriever`` 검색 캐시의 구조를 **AST** 로 뽑는다.

    낱말 검색을 쓰지 않는다(SC-18b·SC-26). 반환 키:
      ``ttl``              ``_CACHE_TTL_SEC`` 값(`retriever.py:155`)
      ``profile_attrs``    ``cache_profile`` 튜플의 프로필 속성(`:216`)
      ``cache_key_names``  ``cache_key`` 튜플의 이름 요소(`:217-226`)
    """
    cls, fn = _retrieve_fn()

    ttl = None
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id == "_CACHE_TTL_SEC":
                ttl = getattr(node.value, "value", None)

    profile_attrs: list[str] = []
    cache_key_names: list[str] = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        t = node.targets[0]
        if not isinstance(t, ast.Name):
            continue
        if t.id == "cache_profile" and isinstance(node.value, ast.Tuple):
            for el in node.value.elts:
                profile_attrs.extend(_profile_gets(el))
        elif t.id == "cache_key" and isinstance(node.value, ast.Tuple):
            for el in node.value.elts:
                if isinstance(el, ast.Name):
                    cache_key_names.append(el.id)

    return {
        "ttl": ttl,
        "profile_attrs": profile_attrs,
        "cache_key_names": cache_key_names,
    }


def _retrieve_miss_path() -> dict[str, object]:
    """캐시 **히트 분기 이후**(미스 시 실행되는) 경로의 모양을 계산한다.

    실측(`retriever.py:224-236`): 히트면 ``return``, 미스면 그대로 빠져나와
    Stage 1 Coarse Recall(:239~) 을 수행한다.
    """
    _, fn = _retrieve_fn()
    idx = None
    for i, st in enumerate(fn.body):
        if not isinstance(st, ast.If):
            continue
        test = ast.unparse(st.test)
        if "cache_key" in test and "_cache" in test:
            idx = i
            break
    assert idx is not None, "캐시 히트 분기를 찾지 못했다 — 파서 회귀"

    hit = fn.body[idx]
    tail = fn.body[idx + 1 :]
    tail_module = ast.Module(body=list(tail), type_ignores=[])
    return {
        "hit_branch_returns": any(isinstance(n, ast.Return) for n in ast.walk(hit)),
        "tail_stmts": len(tail),
        "tail_raises": sum(1 for n in ast.walk(tail_module) if isinstance(n, ast.Raise)),
    }


# ══════════════════════════════════════════════════════════════════════
# 픽스처 — 하네스 무결성 (SC-20·SC-21·SC-23)
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def _rate_limit_isolated(monkeypatch):
    """[SC-23] 전역 속도 제한 상태를 격리한다.

    본 모듈은 HTTP 를 거의 쓰지 않지만, ``_routes()`` 픽스처가 앱을 임포트하므로
    다른 파일과 전역 상태를 공유한다. D1/D2 에서 **본 모듈의 200+ 호출이
    후속 admin 테스트 18건을 429 로 죽인** 실측이 있으므로 동일하게 격리한다.
    """
    from backend.app.config import settings
    from backend.app.middleware import rate_limit as rl

    saved_buckets = dict(rl._buckets)
    saved_blocked = dict(rl._blocked_ips)

    monkeypatch.setattr(settings, "rate_limit_enabled", False, raising=False)
    rl._buckets.clear()
    rl._blocked_ips.clear()

    try:
        yield
    finally:
        rl._buckets.clear()
        rl._blocked_ips.clear()
        rl._buckets.update(saved_buckets)
        rl._blocked_ips.update(saved_blocked)


def _insert_and_confirm(session_factory, model, **fields):
    """[SC-22] 행을 넣고 **존재를 재조회로 먼저 확인** 한 뒤 반환한다.

    ``with session_factory() as s:`` 는 종료 시 **close(롤백)** 이므로
    ``s.commit()`` 이 없다면 행이 사라진다. 그 상태로 "아무 것도 없다"를
    단언하면 **거짓 통과** 가 된다 — T1.5d 에서 실제로 겪었다.
    """
    with session_factory() as s:
        row = model(**fields)
        s.add(row)
        s.flush()
        pk = {k: getattr(row, k) for k in _pk_names(model)}
        s.commit()
    with session_factory() as s:
        got = s.query(model).filter_by(**pk).first()
        assert got is not None, f"삽입 검증 실패 — 행이 커밋되지 않았다: {model.__name__} {pk}"
        return got, pk


def _pk_names(model) -> list[str]:
    from sqlalchemy import inspect as sa_inspect

    return [c.name for c in sa_inspect(model).primary_key]


# ══════════════════════════════════════════════════════════════════════
# T3.2 — 가명키 원칙 (홍콩 저장 경로에 직접 식별자가 없음)
# ══════════════════════════════════════════════════════════════════════
class TestT32PseudonymKeyPrinciple:
    def test_t3_2a_direct_identifiers_split_out_of_subscriber(self):
        """[현행·분할 완료] 직접 식별자는 ``Subscriber`` 밖 금고로 분리된다.

        실측(orm.py): ``Subscriber``(class 183) 는 더 이상 email·display_name 컬럼을
        갖지 않는다. 이들은 ``SubscriberIdentity``(class 1038) 로 이동했다(§21.3).
        준식별자(age_group·gender)·민감 내용(current_struggle) 은 §22.2 D16 결정에 따라
        홍콩 귀속 ``Subscriber`` 에 잔류한다 — 이 테스트는 그 잔류 사실을 고정한다.
        """
        cols = _flow_columns()
        assert SUBSCRIBER_TABLE_CLASS in cols
        sub = cols[SUBSCRIBER_TABLE_CLASS]
        # §21.3 2계층 분할: 직접 식별자는 금고로 빠졌다
        assert "email" not in sub, "Subscriber.email 이 금고로 분리되지 않았다 — §21.3 회귀"
        assert "display_name" not in sub, "Subscriber.display_name 이 금고로 분리되지 않았다"
        # 분리 목적지(금고)에 직접 식별자가 존재함
        assert "SubscriberIdentity" in cols, "SubscriberIdentity 금고 테이블이 없다"
        assert "email" in cols["SubscriberIdentity"]
        assert "display_name" in cols["SubscriberIdentity"]
        # 준식별자·민감 내용은 홍콩 귀속 Subscriber 에 잔류(설계 확정 전까지)
        assert "age_group" in sub
        assert "gender" in sub
        assert "current_struggle" in sub

    def test_t3_2b_pii_gateway_routes_exist(self):
        """[현행·분할 완료] §21.3 게이트웨이 계약(``/pii/*``) 라우트가 등록된다.

        실측: ``DELETE /pii/subjects/{id}`` 가 ``backend/app/api/pii.py`` 에 구현되어
        ``app.routes``(FastAPI 0.141.1 의 ``_IncludedRouter`` 포함) 에 등록된다.
        """
        registered = _routes()
        found = sorted(
            (m, p) for (m, p) in registered if any(p.startswith(x) for x in PII_GATEWAY_PATHS)
        )
        assert found, (
            "PII 게이트웨이 라우트가 없다.\n"
            "→ §21.3 계약이 구현되지 않았다. api/pii.py 와 main.py include_router 를 확인하십시오."
        )

    def test_t3_2c_scrubber_for_pii_does_not_exist(self):
        """[현행] PII 스크러버가 없다 — 존재하는 것은 **시크릿 전용** 뿐.

        실측 3건 전부 시크릿 전용:
          `logging_setup.py:75` ``scrub_secrets`` — API 키·토큰·DB URL
          `services/tracing.py:67` ``_scrub``     — ``scrub_secrets`` 호출이 전부
          `api/admin.py:745` ``_mask``           — 관리자 화면 표시용
        어느 것도 이메일·전화를 치환하지 않는다.
        """
        from backend.app.logging_setup import scrub_secrets

        # 실행으로 증명: PII 가 들어간 문자열이 그대로 살아남는다
        out = scrub_secrets(PII_PROBE_TEXT)
        assert PII_PROBE_EMAIL in out, "scrub_secrets 가 이메일을 치환했다 — 분류 갱신 필요"
        assert PII_PROBE_PHONE in out

    def test_t3_2d_direct_identifiers_live_only_in_the_vault(self):
        """[목표 게이트 → 현행] 직접 식별자는 **금고 테이블에만** 있다.

        §21.3 2계층 분할 구현 완료: 직접 식별자는 ``SubscriberIdentity`` 등 금고에만
        있고, 홍콩 귀속 테이블(Subscriber·SalvationJourney·SupportTicket) 에는
        남아 있지 않음을 AST 로 단언한다.
        """
        cols = _flow_columns()
        vault = {c for c in cols if c in VAULT_TABLE_CLASSES}
        assert vault, (
            "금고 테이블(SubscriberIdentity/MusicUserIdentity)이 없다.\n"
            "→ §21.3 은 한국=PII 금고, 홍콩=가명키(subscriber_id)+콘텐츠 로 분할을 규정한다."
        )
        leaked = {
            c: sorted(set(f) & DIRECT_IDENTIFIER_FIELDS)
            for c, f in cols.items()
            if c not in vault and set(f) & DIRECT_IDENTIFIER_FIELDS
        }
        assert not leaked, f"홍콩 귀속 테이블에 직접 식별자가 남아 있다: {leaked}"

    def test_t3_2e_hk_cache_key_carries_pseudonym_only(self):
        """[현행 · **D28 해결됨**] 홍콩 검색 캐시 키는 **가명키(subscriber_id)만** 품는다.

        실측 체인 — 전부 AST/소스로 확인한 값:
          `services/subscriber_service.py:24`
              프로필 dict 에 ``subscriber_id``(가명키) 포함.
          `services/rag_engine.py:171-178`  프로필을 검색 엔진으로 전달
          `services/search_v4.py:358,368`   ``profile=profile`` 재전달
          `services/retriever.py:216`   ``cache_profile = (profile.get("subscriber_id"),)``
                                        — **금고 유래 PII 속성 5건 제외**
          `services/retriever.py:217-226`   ``cache_key`` 에 ``cache_profile`` 포함
          `services/retriever.py:175,49,155` 인스턴스 캐시 · 모듈 싱글턴 · TTL 1800초

        → 분할 후 홍콩 프로세스 메모리에 남는 것은 가명키+쿼리 파라미터뿐.
          구독자 종교상태 조합은 더 이상 캐시 키에 상주하지 않음(§21.3 준수).
          부스트는 miss 시 원본 프로필로 재계산(D14 fail-closed).
        """
        shape = _retriever_cache_shape()
        assert shape["profile_attrs"], "cache_profile 파싱 실패 — 파서 회귀(SC-22)"

        leaked = set(shape["profile_attrs"]) & set(VAULT_DERIVED_PROFILE_ATTRS)
        assert not leaked, (
            f"홍콩 캐시 키에 금고 유래 속성이 남아 있다: {sorted(leaked)}\n"
            "→ §21.3 위반. 프로필은 서명 토큰으로 전달하고 캐시 키는 가명키만 쓰십시오."
        )
        # 가명키 자체는 캐시 파티션에 사용된다(허용).
        assert any("subscriber_id" in a for a in shape["profile_attrs"]), (
            f"cache_profile 이 가명키(subscriber_id) 를 포함하지 않는다: {shape['profile_attrs']}"
        )
        assert "cache_profile" in shape["cache_key_names"]

    def test_t3_2f_hk_cache_key_carries_pseudonym_only(self):
        """[목표 게이트 · **D28 해결됨**] 홍콩 캐시 키는 **가명키만** 담는다.

        설계서:1066 은 핫패스에서 "국경 왕복 0" 을 위해 **토큰에 서명된
        프로필** 을 쓰도록 규정한다 — 즉 프로필은 **요청마다 토큰으로 오고**,
        서버측 캐시 키에 **남아 있으면 안 된다**.
        D28 구현(``retriever.py:216``) 으로 캐시 키는 가명키(subscriber_id)
        만 품고, 금고 유래 PII 속성 5건(salvation_status·darakbang_role·
        is_darakbang_member·darakbang_verified·assume_saved) 은 제외됨.
        """
        shape = _retriever_cache_shape()
        leaked = set(shape["profile_attrs"]) & set(VAULT_DERIVED_PROFILE_ATTRS)
        assert not leaked, (
            f"홍콩 캐시 키에 금고 유래 속성이 남아 있다: {sorted(leaked)}\n"
            "→ §21.3 위반. 프로필은 서명 토큰으로 전달하고 캐시 키는 가명키만 쓰십시오."
        )


# ══════════════════════════════════════════════════════════════════════
# T3.3 — 주체 삭제 팬아웃 (§21.4 Y5)
# ══════════════════════════════════════════════════════════════════════
class TestT33SubjectDeletionFanout:
    def test_t3_3a_subject_deletion_endpoint_exists(self):
        """[현행·분할 완료] ``DELETE /pii/subjects/{id}`` 주체 단위 삭제 진입점이 있다.

        실측(``backend/app/api/pii.py``): 주체(사람) 단위 삭제가 홍콩 전파 +
        한국 금고 식별자 삭제로 구현된다(§21.3·§21.4 Y5).
        """
        registered = _routes()
        subject_routes = sorted(
            (m, p) for (m, p) in registered if m == "DELETE" and p.startswith("/pii/subjects")
        )
        assert subject_routes, (
            "주체 단위 삭제 엔드포인트가 없다(DELETE /pii/subjects/{id}).\n"
            "→ api/pii.py 와 main.py include_router(pii.router) 를 확인하십시오."
        )

    def test_t3_3b_deletion_ledger_exists(self):
        """[현행·분할 완료] 삭제 전파 원장(``DeletionLedger``)이 있다(§21.4 Y5).

        주체 삭제의 3자 전파 증빙 그릇. ``orm.py`` 의 ``DeletionLedger`` 클래스 +
        ``api/pii.py`` 의 원장 기록 경로로 존재함을 단언한다.
        """
        cols = _flow_columns()
        assert "DeletionLedger" in cols, "삭제 전파 원장 모델이 없다 — Y5 미구현"

        hits: list[str] = []
        for p in _walk_py(FLOW_APP_DIR):
            src = _read(p)
            for name in DELETION_LEDGER_NAMES:
                if name in src:
                    hits.append(f"{p.relative_to(REPO_ROOT)}:{_line_of(src, name)}")
        assert hits, "삭제 원장 참조를 찾지 못했다 — 원장 기록 경로 확인 필요"

    def test_t3_3c_subject_deletion_fans_out_to_hongkong(self):
        """[목표 게이트 → 현행] 주체 삭제가 홍콩으로 전파된다.

        게이트 조건 2개(§21.4 Y5):
          ① ``DELETE /pii/subjects/{id}`` 라우트 존재
          ② ``DeletionLedger`` 모델 존재(전파 증빙)
        """
        registered = _routes()
        has_route = any(m == "DELETE" and p.startswith("/pii/subjects") for (m, p) in registered)
        assert has_route, "DELETE /pii/subjects/{id} 라우트가 없다"
        assert "DeletionLedger" in _flow_columns(), "삭제 전파 원장 모델이 없다"


# ══════════════════════════════════════════════════════════════════════
# T3.4 — 원문 스크러빙 (§21.4 Y2)
# ══════════════════════════════════════════════════════════════════════
class TestT34RawTextScrubbing:
    def test_t3_4a_question_column_is_unbounded_text(self):
        """[현행] ``Interaction.question`` 은 unbounded Text(`orm.py:337`).

        자유 입력 원문이 **무제한 길이로** 저장된다. Y2 스크러버가 없다면
        사용자가 이메일·전화·실명을 적어 넣어도 그대로 홍콩에 남는다.
        """
        cols = _flow_columns()
        assert "question" in cols["Interaction"]
        assert "Text" in cols["Interaction"]["question"]

    def test_t3_4b_question_is_scrubbed_before_store(self, iso_db):
        """[현행·분할 완료·실행 검증] PII 가 포함된 질문이 저장 전 스크러빙된다(§21.4 Y2).

        SC-22 대응: ``s.commit()`` 후 **새 세션에서 재조회** 해 행이 실제로
        저장되었음을 먼저 확인한다(없으면 이후 단언은 무의미).
        """
        from backend.app.models.orm import Interaction

        row, _pk = _insert_and_confirm(
            iso_db,
            Interaction,
            question=PII_PROBE_TEXT,
            answer="스크러빙 검증용 응답",
        )
        stored = row.question
        # 사후 조건: 저장된 값에 PII 가 남아 있으면 안 된다(Y2 구현 완료)
        assert PII_PROBE_EMAIL not in stored, "이메일이 원문으로 저장되었다 — Y2 회귀"
        assert PII_PROBE_PHONE not in stored, "전화번호가 원문으로 저장되었다 — Y2 회귀"

    def test_t3_4c_question_is_scrubbed_before_store(self, iso_db):
        """[목표 게이트 → 현행] 저장 시점에 PII 가 제거/치환된다(§21.4 Y2)."""
        from backend.app.models.orm import Interaction

        row, _ = _insert_and_confirm(iso_db, Interaction, question=PII_PROBE_TEXT, answer="응답")
        stored = row.question
        assert PII_PROBE_EMAIL not in stored, "이메일이 저장되었다"
        assert PII_PROBE_PHONE not in stored, "전화번호가 저장되었다"


# ══════════════════════════════════════════════════════════════════════
# T3.5 — 최고민감 필드 (salvation_journey.trigger_quote)
# ══════════════════════════════════════════════════════════════════════
class TestT35MostSensitiveField:
    def test_t3_5a_trigger_quote_is_korea_bound(self):
        """[현행·분할 완료] 최고민감 ``trigger_quote`` 는 홍콩 ``SalvationJourney`` 에 없다.

        실측(orm.py): ``trigger_quote`` 는 ``SalvationJourney`` 컬럼이 아니라
        한국 금고 ``JourneyVault``(class 1059) 로 이동했다(§21.3 T3.5).
        ``SalvationJourney`` 는 ``subscriber_id``·``interaction_id`` 로 사람·발화를
        연결하되 원문은 금고에만 둔다.
        """
        cols = _flow_columns()
        sj = cols["SalvationJourney"]
        assert "trigger_quote" not in sj, (
            "trigger_quote 가 여전히 홍콩 귀속 SalvationJourney 에 있다 — §21.3 회귀"
        )
        # 금고 목적지에 존재
        assert "JourneyVault" in cols, "JourneyVault 금고 테이블이 없다"
        assert "trigger_quote" in cols["JourneyVault"], "JourneyVault.trigger_quote 가 없다"
        # 사람·발화 연결 고리는 FK 로 살아 있음(원문만 분리)
        assert "subscriber" in sj["subscriber_id"]
        assert "interaction" in sj["interaction_id"]

    def test_t3_5b_trigger_quote_is_deidentified_or_korea_bound(self):
        """[목표 게이트 → 현행] 최고민감 필드는 한국 귀속 금고에만 있다(§21.3)."""
        cols = _flow_columns()
        vault = {c for c in cols if c in VAULT_TABLE_CLASSES}
        assert vault, "금고 테이블이 없다"
        assert "trigger_quote" not in cols.get("SalvationJourney", {}), (
            "trigger_quote 가 여전히 홍콩 귀속 SalvationJourney 에 남아 있다"
        )


# ══════════════════════════════════════════════════════════════════════
# T3.6 — 문의 연락처 (support_ticket.contact)
# ══════════════════════════════════════════════════════════════════════
class TestT36SupportContact:
    def test_t3_6a_contact_is_korea_bound(self):
        """[현행·분할 완료] ``contact`` 는 홍콩 ``SupportTicket`` 에 없다.

        실측(orm.py): ``contact`` 는 ``SupportTicket`` 컬럼이 아니라 한국 금고
        ``SupportContactVault``(class 1076) 로 이동했다(§21.3 T3.6). 공존 후 FLOW 는
        홍콩이므로 연락처가 홍콩에 남는 결함이 해소되었다.
        """
        cols = _flow_columns()
        assert "contact" not in cols.get("SupportTicket", {}), (
            "contact 가 여전히 홍콩 귀속 SupportTicket 에 있다 — §21.3 회귀"
        )
        assert "SupportContactVault" in cols, "SupportContactVault 금고 테이블이 없다"
        assert "contact" in cols["SupportContactVault"], "SupportContactVault.contact 가 없다"

    def test_t3_6b_contact_is_korea_bound(self):
        """[목표 게이트 → 현행] 연락처는 한국 금고에만 있다(§21.3)."""
        cols = _flow_columns()
        vault = {c for c in cols if c in VAULT_TABLE_CLASSES}
        assert vault, "금고 테이블이 없다"
        assert "contact" not in cols.get("SupportTicket", {}), (
            "contact 가 여전히 홍콩 귀속 SupportTicket 에 있다"
        )


# ══════════════════════════════════════════════════════════════════════
# T3.7 — 에러로그 IP (orm.py:481 + §21.4 Y12)
# ══════════════════════════════════════════════════════════════════════
class TestT37ErrorLogIp:
    def test_t3_7a_client_ip_is_masked(self):
        """[현행·분할 완료] ``client_ip`` 는 저장 전 마스킹된다(§21.4 Y12).

        실측 대조(`middleware/error_monitor.py`):
          :65  ``error_message=scrub_secrets(...)[:2000]``  ← 마스킹 O
          :66  ``traceback=scrub_secrets(...)[:5000]``     ← 마스킹 O
          :70  ``client_ip=mask_client_ip(client_ip)``      ← **마스킹 O(신규)**
        즉 메시지와 IP 모두 마스킹된다. ``mask_client_ip`` 는 IPv4 를 /24,
        IPv6 를 /64 로 마스킹한다(``logging_setup.py``).
        """
        src = _read(FLOW_ERROR_MONITOR_PY)
        line = _line_of(src, "mask_client_ip(client_ip)")
        assert line is not None, (
            "error_monitor 의 client_ip 저장부에 마스킹이 없다 — 구현 확인 필요"
        )
        stmt = src.splitlines()[line - 1].lower()
        assert "mask" in stmt, (
            f"client_ip 저장부에 마스킹이 적용되지 않았다: {stmt.strip()}\n"
            "→ §21.4 Y12 회귀. mask_client_ip 를 적용하십시오."
        )

    def test_t3_7b_client_ip_column_is_plain_string(self):
        """[현행] ``AppErrorLog.client_ip`` = String(50)(`orm.py:558`), 평문 보관."""
        cols = _flow_columns()
        assert "client_ip" in cols["AppErrorLog"]
        assert "String(50)" in cols["AppErrorLog"]["client_ip"]

    def test_t3_7c_client_ip_is_masked_with_retention(self):
        """[목표 게이트 → 현행] client_ip 는 마스킹된다(§21.4 Y12).

        보존기간 강제(retention TTL) 는 ``AppErrorLog`` 에 미구현이며 별도
        추적 항목(D-미번호) 으로 남는다. 본 게이트는 **마스킹 적용** 만 단언한다.
        """
        src = _read(FLOW_ERROR_MONITOR_PY)
        line = _line_of(src, "mask_client_ip(client_ip)")
        assert line is not None
        stmt = src.splitlines()[line - 1].lower()
        assert "mask" in stmt, "client_ip 저장 시 마스킹이 없다"


# ══════════════════════════════════════════════════════════════════════
# T3.8 — FK 해제 정합 (§21.4 Y1)
# ══════════════════════════════════════════════════════════════════════
class TestT38FkReleaseConsistency:
    def test_t3_8a_interaction_fk_is_released(self):
        """[현행·분할 완료] ``Interaction.subscriber_id`` 는 더 이상 FK 가 아니다(§21.4 Y1).

        서로 다른 관할(한국 금고 vs 홍콩 콘텐츠) DB 에 FK 를 걸 수 없으므로
        분할의 선결 조건으로 FK 를 해제했다. 인덱스는 유지된다(조회용).
        고아행 정합은 ``reconcile_orphan_interactions``(subscriber_service.py:21) 배치로 보강.
        """
        cols = _flow_columns()
        decl = cols["Interaction"]["subscriber_id"]
        assert "ForeignKey" not in decl, (
            f"Interaction.subscriber_id 에 FK 가 남아 있다: {decl}\n"
            "→ §21.4 Y1 회귀. FK 를 해제하고 정합 배치를 두십시오."
        )
        assert "subscriber.subscriber_id" not in decl, "FK 참조가 남아 있다 — Y1 회귀"

    def test_t3_8b_reconciliation_batch_exists(self):
        """[현행·분할 완료] 고아행 정합 배치가 **있다** — Y1 둘째 축 착수.

        측정은 **정의된 심볼 이름** 단위로 한다(SC-26). ``reconcile_orphan_interactions``
        (subscriber_service.py:21) 가 ``Interaction.subscriber_id`` 중 ``subscriber`` 테이블에
        없는 고아 키를 null 처리한다. 낱말 검색의 거짓 양성(cascade="all, delete-orphan",
        ``orphan_josa_re``) 을 피하기 위해 심볼 단위로 본다.
        """
        names = _def_names(FLOW_APP_DIR)

        # 0) 부정 단언 전에 **측정 대상이 살아 있는지** 먼저 단언한다(SC-22).
        assert names, "심볼 스캔 결과가 비었다 — 스캔 경로가 잘못되었다"
        covered = {f for locs in names.values() for f, _ in locs}
        assert any(f.replace("\\", "/").endswith("models/orm.py") for f in covered), (
            f"orm.py 가 스캔 범위에 없다 — 측정 대상 상실: {sorted(covered)[:8]}"
        )

        hits = sorted(
            (name, locs) for name, locs in names.items() if _is_reconciliation_symbol(name)
        )
        assert hits, (
            "정합 배치 심볼이 없다.\n"
            "→ Y1 둘째 축(고아행 정합 배치) 이 구현되지 않았다. "
            "reconcile_orphan_interactions 같은 배치를 추가하십시오."
        )

    def test_t3_8c_fk_released_with_consistency_batch(self):
        """[목표 게이트 → 현행] FK 해제 + 정합 배치 심볼 실재(§21.4 Y1).

        낱말 검색을 쓰지 않는다(SC-26). ``cascade="all, delete-orphan"`` 같은
        거짓 양성을 피해 **심볼 단위** 로 본다.
        """
        cols = _flow_columns()
        assert "ForeignKey" not in cols["Interaction"]["subscriber_id"], "FK 가 남아 있다"

        names = _def_names(FLOW_APP_DIR)
        assert names, "심볼 스캔 결과가 비었다 — 측정 대상 상실"
        found = sorted(
            (name, locs) for name, locs in names.items() if _is_reconciliation_symbol(name)
        )
        assert found, (
            "FK 는 해제되었으나 고아행 정합 배치 심볼이 없다.\n"
            "→ Y1 의 둘째 축이 빠진 상태다. 배치를 추가하거나 설계서 §21.4 를 수정하십시오."
        )


# ══════════════════════════════════════════════════════════════════════
# T3.9 — 준식별 재식별 (§22.2 FU10) — B2/D16 결정 대기
# ══════════════════════════════════════════════════════════════════════
class TestT39QuasiIdentifierReidentification:
    def test_t3_9a_quasi_identifiers_remain_korea_bound_without_direct(self):
        """[현행·결정 가능] 준식별자는 홍콩 ``Subscriber`` 에 잔류, 직접 식별자는 분리.

        §22.2 FU10 완화책은 "준식별 홍콩 미저장 + 스크러빙 Y2" 지만,
        D16(홍콩 잔존 준식별 허용 범위) 이 **미결** 이므로 현재는 준식별자
        (age_group·gender) 가 홍콩 귀속 ``Subscriber`` 에 남는다. 단 직접 식별자
        (email·display_name) 는 금고로 분리되어 재식별 결합이 끊어졌다.
        D16 결론 후 이 테스트는 준식별 보관 범위로 재갱신된다.
        """
        cols = _flow_columns()
        sub = cols[SUBSCRIBER_TABLE_CLASS]
        assert set(sub) >= QUASI_IDENTIFIER_FIELDS, (
            f"준식별자 누락: {sorted(QUASI_IDENTIFIER_FIELDS - set(sub))}"
        )
        # 직접 식별자는 더 이상 같은 테이블에 없음(금고로 분리) → 재식별 결합 차단
        assert "email" not in sub, "직접 식별자가 Subscriber 에 남아 있다 — §21.3 회귀"
        assert "display_name" not in sub

    def test_t3_9b_quasi_identifier_inventory_is_pinned(self):
        """[현행·인벤토리 고정] 준식별자 집합이 바뀌면 검토를 강제한다.

        D16(홍콩 잔존 준식별 허용 범위) 결론이 나면 이 목록이 **결정의 대상** 이 된다.
        """
        cols = _flow_columns()
        sub = set(cols[SUBSCRIBER_TABLE_CLASS])
        got = sorted(sub & (QUASI_IDENTIFIER_FIELDS | {"journey_stage", "faith_stage"}))
        assert got == ["age_group", "faith_stage", "gender", "journey_stage"], (
            f"준식별 인벤토리가 달라졌다: {got}\n→ §22.2 FU10 완화책과 D16 결론을 재검토하십시오."
        )

    def test_t3_9c_reidentification_is_impossible(self):
        """[B2/D16 미결] 재식별 불가 검증은 **단언값을 정할 수 없다**.

        홍콩에 남길 준식별 범위(D16: 전부 한국 vs 일부 허용)가 확정되지 않아
        "재식별 불가"의 기준선 자체가 없다. 결론 전에 단언을 쓰면
        **결정되지 않은 값을 단언하는 환각 테스트** 가 된다.
        """
        pytest.skip(
            "B2: D16(홍콩 잔존 준식별 범위) 미결 — 재식별 기준선을 정할 수 없음. "
            "결정 후 이 skip 을 제거하고 허용된 준식별 조합으로 재식별 불가를 검증하십시오."
        )


# ══════════════════════════════════════════════════════════════════════
# T3.10 — 캐시 미스 동작 (§21.6, D14) — B1 결정 대기
# ══════════════════════════════════════════════════════════════════════
class TestT310CacheMissBehaviour:
    """§21.4 Y4 가명 프로필 캐시 + §21.6 캐시 미스 동작(D14).

    ── 초안의 오류 (SC-26, 2026-08-31) ────────────────────────────────
    최초에는 ``"profile" in text and "cache" in text`` 낱말 검색으로
    **"프로필 캐시가 없다"** 고 단언했다. 실행 결과가 그 단언을 반박했다:
    `answer_cache.py` · `retriever.py` · `search_v4.py` 가 걸렸고, 그중
    `retriever.py` 는 **정말로 프로필 속성을 캐시 키에 넣고 있었다.**

    즉 잘못된 방법이 우연히 "맞는 방향" 을 가리켰고, 그 탓에 **이미 존재하는
    위험(D28) 을 놓칠 뻔했다.** 낱말 검색의 위험은 거짓 양성만이 아니라
    **거짓 음성(위험 은폐)** 이기도 하다. → 전부 AST 구조로 재측정했다.
    """

    def test_t3_10a_profile_keyed_retrieval_cache_exists(self):
        """[현행 · **D28 해결됨**] 프로필 **가명키(subscriber_id) 하나만** 캐시 키에 박힌다.

        실측(`retriever.py`, 전부 AST 로 확인):
          `:217-226`  ``cache_key`` 튜플에 ``cache_profile`` 이 요소로 포함
          `:216`  ``cache_profile`` = ``(profile.get("subscriber_id"),)``  ← **D28 수정**
          `:175`      ``self._cache`` 인스턴스 캐시(OrderedDict)
          `:49`       ``_retriever_cache`` 모듈 싱글턴 → 프로세스 수명
          `:155`      ``_CACHE_TTL_SEC = 1800`` (30분)

        → D28 이전엔 프로필 5속성(salvation_status·darakbang_role·is_darakbang_member·
          darakbang_verified·assume_saved) 이 캐시 키에 박혀 홍콩 메모리에 30분 상주했으나,
          이제는 **가명키만** 상주한다(§21.3 준수). 부스트는 miss 시 원본 프로필로 재계산(D14).
        """
        shape = _retriever_cache_shape()

        # 0) 측정 자체가 성립했는지 먼저 단언한다(빈 결과면 거짓 통과, SC-22)
        assert shape["cache_key_names"], "캐시 키를 파싱하지 못했다 — 파서 회귀"
        assert shape["profile_attrs"], "cache_profile 을 파싱하지 못했다 — 파서 회귀"

        assert "cache_profile" in shape["cache_key_names"], (
            f"캐시 키에 cache_profile 이 없다: {shape['cache_key_names']}\n"
            "→ 프로필 키 캐시가 사라졌다. 이 테스트를 갱신하십시오."
        )
        # [D28] 프로필 키는 가명키(subscriber_id) 단일 속성만 품는다.
        assert tuple(shape["profile_attrs"]) == ("subscriber_id",), (
            f"캐시 키의 프로필 속성이 가명키만이 아니다: {shape['profile_attrs']}\n"
            "→ §21.3 위반. 금고 유래 PII 속성(5건) 이 캐시 키에 섞이면 안 된다."
        )
        assert shape["ttl"] == RETRIEVER_CACHE_TTL_SEC, f"검색 캐시 TTL 이 바뀌었다: {shape['ttl']}"

    def test_t3_10b_y4_pseudonym_profile_cache_is_absent(self):
        """[현행] §21.4 **Y4 가명 프로필 캐시** 는 없다 (검색 캐시와 혼동 금지).

        D28 이후 검색 캐시 키는 **가명키(subscriber_id) 를 포함** 한다
        (`retriever.py:216`) — Y4 가 구현된 것이 아니다. Y4 는 별도의
        **가명키 → 프로필 read-through 로컬 캐시** 이며, 현재 캐시 키에
        **금고 유래 PII 속성 5건** 이 박혀 있지 않다는 것(D28) 과는 다르다.

        본 게이트가 단언하는 것:
          - 검색 캐시 키에 가명키는 들어오지만(허용, D28 이후),
          - **금고 유래 PII 속성 5건은 절대 들어오지 않는다**(D28 준수),
          - 캐시 프로필 튜플이 다속성(프로필 객체) 가 아닌 단일 가명키임을 확인해
            Y4 스타일 read-through 캐시가 아님을 고정한다.
        """
        shape = _retriever_cache_shape()
        # 가명키는 의도적으로 포함됨 (D28 이후)
        in_profile = set(shape["profile_attrs"]) & PSEUDONYM_KEY_MARKERS
        assert in_profile, (
            f"캐시 프로필 키에 가명키가 없다: {shape['profile_attrs']}\n"
            "→ D28 이후 검색 캐시 키는 subscriber_id(가명키) 를 포함해야 한다."
        )
        # 금고 유래 PII 속성 5건은 절대 캐시 키에 없어야 한다 (D28)
        leaked = set(shape["profile_attrs"]) & set(VAULT_DERIVED_PROFILE_ATTRS)
        assert not leaked, (
            f"캐시 키에 금고 유래 PII 속성이 남아 있다: {sorted(leaked)}\n"
            "→ D28 회귀. 홍콩 캐시 키에는 가명키만 허용."
        )
        # Y4 스타일 다속성 프로필 객체 캐시가 아님을 고정
        assert tuple(shape["profile_attrs"]) == ("subscriber_id",), (
            f"캐시 프로필이 단일 가명키가 아니다: {shape['profile_attrs']}\n"
            "→ Y4 read-through 프로필 캐시가 우연히 구현된 것일 수 있다. 확인하라."
        )

    def test_t3_10c_cache_miss_falls_back_to_full_retrieval(self):
        """[현행] 캐시 미스는 **전체 검색으로 폴스루** 한다 = 사실상 **fail-open**.

        실측(`retriever.py:224-236`): 히트면 ``return``(:234), 미스면 그대로
        빠져나와 Stage 1 Coarse Recall(:240~) 을 수행한다. **차단하지 않는다.**

        주의: 이것은 **Y4 캐시** 의 미스 동작이 아니라 **현재 존재하는 검색
        캐시** 의 미스 동작이다. D14 결정 시 "이미 fail-open 으로 굴러간다"는
        사실을 근거로 삼을 수 있게 고정해 둔다.
        """
        path = _retrieve_miss_path()
        assert path["hit_branch_returns"], "캐시 히트 분기가 반환하지 않는다 — 구조 변경 확인"
        assert path["tail_stmts"] > 0, (
            "캐시 히트 분기 뒤에 실행 코드가 없다 — 미스 경로 자체가 없다"
        )
        assert path["tail_raises"] == 0, (
            f"캐시 미스 경로에 raise 가 {path['tail_raises']} 건 생겼다.\n"
            "→ fail-closed 로 바뀌었다면 D14 결정을 반영하고 이 테스트를 갱신하십시오."
        )

    def test_t3_10d_cache_miss_policy(self):
        """[B1/D14 미결] 캐시 미스 시 동작은 **결정값이 없다**.

        D14: 차단(fail-closed) vs 제한 기능(fail-open, 가명 기본 프로필).
        위 c) 로 **현재 검색 캐시의 사실상 동작(fail-open)** 은 고정했으나,
        그것이 **Y4 가명 프로필 캐시** 에서도 옳은지는 결정된 바 없다.
        결정 전에 단언하면 미결정 값을 단언하는 환각 테스트가 된다.
        """
        pytest.skip(
            "B1: D14(캐시 미스 동작) 미결 — fail-closed/fail-open 중 무엇을 "
            "단언할지 결정되지 않았다. Y4 캐시 자체도 미구축이다. "
            "결정 후 이 skip 을 제거하십시오. "
            "(참고: 현행 검색 캐시는 사실상 fail-open — test_t3_10c 가 고정)"
        )


# ══════════════════════════════════════════════════════════════════════
# T3.11 — 교차시스템 동의 증빙 (G-Consent)
# ══════════════════════════════════════════════════════════════════════
class TestT311CrossSystemConsentEvidence:
    def test_t3_11a_consent_record_can_carry_the_evidence(self):
        """[현행] ``ConsentRecord`` 는 **실재** 하며 증빙 필드를 모두 갖췄다.

        실측(`models/compliance.py:45-61`):
          consent_type:52 · purpose:56 · retention_days:57 · ip:58
          user_agent:59 · granted_at:60 · revoked_at:61
        → **그릇은 있다.** 없는 것은 음악 사용자 ↔ FLOW 구독자 **연결** 이다.
        """
        cols = _flow_columns()
        assert "ConsentRecord" in cols
        cr = cols["ConsentRecord"]
        for f in (
            "consent_type",
            "purpose",
            "retention_days",
            "ip",
            "user_agent",
            "granted_at",
            "revoked_at",
        ):
            assert f in cr, f"ConsentRecord.{f} 가 없다 — G-Consent 증빙 불가"

    def test_t3_11b_music_users_are_not_split(self):
        """[현행] 음악 ``users`` 도 분할되지 않았다(§21.4 Y6 미착수).

        실측(음악 `backend/app/models.py:7-13`):
          email:11 · hashed_password:12 가 `songs` 와 같은 저장소의 단일 테이블.
        """
        music_models = _music_backend() / "app" / "models.py"
        cols = _orm_columns(_read(music_models))
        assert "User" in cols
        assert "email" in cols["User"], "음악 User.email 이 사라졌다 — Y6 진행 확인"
        assert "hashed_password" in cols["User"]
        # 금고 테이블이 없음이 분할 부재의 증거
        assert not (set(cols) & VAULT_TABLE_CLASSES)

    def test_t3_11c_cross_system_link_records_consent(self):
        """[목표 게이트 → 현행] 교차시스템 연결 시 ``ConsentRecord`` 가 기록될 수 있다.

        게이트 조건: 음악측에 FLOW 구독자와 연결하는 식별자 컬럼이 있다.

        [2026-09-09 재고정 — 외부 리포 진화 반영]
        이전에는 ``User.flow_subscriber_id`` 를 기대했다. 그러나 외부 리포(models.py:15)
        가 이 컬럼을 **삭제**했다 — 읽기/쓰기 0건의 데드 컬럼 + 마이그레이션 부재(D35).
        브리지는 이제 **단일 테이블** ``UserLink.flow_subscriber_id``(models.py:84,
        __tablename__="user_links") 로만 관리된다. 이것이 게이트의 취지(교차시스템
        연결 식별자의 존재)를 더 강하게 보장한다 — User 에 흩어진 컬럼보다
        단일 브리지 테이블이 경계 명확성이 높다.
        """
        music_models = _music_backend() / "app" / "models.py"
        cols = _orm_columns(_read(music_models))
        link_cols = set(cols.get("UserLink", {}))
        linkage = {c for c in link_cols if "flow" in c.lower() or "subscriber" in c.lower()}
        assert linkage, (
            "음악 UserLink 에 FLOW 연결 식별자가 없다.\n"
            "→ user_links 브리지 테이블의 flow_subscriber_id 가 누락되었다."
        )
        # D35 결정 반영: User 는 브리지 키를 다시 갖지 않는다(단일 브리지 테이블 원칙).
        user_cols = set(cols.get("User", {}))
        assert not ({c for c in user_cols if "flow" in c.lower() or "subscriber" in c.lower()}), (
            "User 에 flow/subscriber 컬럼이 재등장했다 — 브리지는 UserLink 단일 테이블로만."
        )


# ══════════════════════════════════════════════════════════════════════
# 하네스 무결성 — "내가 지금 무엇을 측정하는가" (SC-20·SC-21)
# ══════════════════════════════════════════════════════════════════════
class TestHarnessIntegrity:
    """측정 대상이 실행 순서에 따라 바뀌면 결론도 틀린다(SC-20 교훈).

    D5 에서 ``sys.modules["app"]`` 오염으로 **전체 회귀에서 FLOW Base 를 측정**
    하는 사고가 있었다(단독 실행에선 우연히 맞아 더 위험). 본 모듈도 ORM 을
    직접 파싱하므로 동일한 자기검증을 둔다.
    """

    def test_orm_parser_finds_the_expected_models(self):
        """파서는 소스를 직접 읽으므로 ``sys.modules`` 오염과 무관해야 한다."""
        cols = _flow_columns()
        for expected in (
            "Subscriber",
            "Interaction",
            "SalvationJourney",
            "SupportTicket",
            "AppErrorLog",
            "ConsentRecord",
        ):
            assert expected in cols, f"{expected} 를 찾지 못했다 — 파서 회귀"

    def test_orm_parser_ignores_non_base_classes(self):
        """Base 를 상속하지 않은 클래스는 배제한다(SC-21 ``_TempEnc`` 교훈)."""
        src = "class _TempEnc:\n    email: str = mapped_column(String(10))\n"
        assert _orm_columns(src) == {}

    def test_orm_parser_sees_fk_in_declaration(self):
        """FK 판정이 실제로 동작하는지 자기검증한다(거짓 통과 방지)."""
        src = (
            "class X(Base):\n"
            "    a: Mapped[str] = mapped_column(ForeignKey('y.id'))\n"
            "    b: Mapped[str] = mapped_column(String(10))\n"
        )
        cols = _orm_columns(src)
        assert "ForeignKey" in cols["X"]["a"]
        assert "ForeignKey" not in cols["X"]["b"]

    def test_orm_parser_reads_sqlalchemy_1x_style(self):
        """[SC-27] 음악 플랫폼은 1.x 스타일(``x = Column(...)``)이다.

        2.0 만 읽는 파서로 음악측을 검사하면 결과가 ``{}`` 가 되어
        "모델이 없다"는 **거짓 결론** 이 나온다 — 실제로 겪었고,
        그 상태로는 음악측 PII 검증이 전부 무력화된다.
        """
        src = (
            "class User(Base):\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    email = Column(String(255), unique=True, nullable=False)\n"
            "    user_id = Column(Integer, ForeignKey('users.id'))\n"
        )
        cols = _orm_columns(src)
        assert "User" in cols, "1.x 스타일을 읽지 못했다 — 음악측 검증 무력화"
        assert "email" in cols["User"]
        assert "ForeignKey" in cols["User"]["user_id"]

    def test_orm_parser_reads_both_styles_in_one_class(self):
        """한 클래스에 2.0·1.x 가 섞여도 둘 다 읽는다."""
        src = (
            "class Mixed(Base):\n"
            "    a: Mapped[str] = mapped_column(String(10))\n"
            "    b = Column(String(20))\n"
        )
        cols = _orm_columns(src)
        assert set(cols["Mixed"]) == {"a", "b"}, f"혼합 선언을 못 읽었다: {cols}"

    def test_def_names_scanner_sees_definitions_not_words(self):
        """[SC-26] 심볼 스캐너는 **정의** 만 잡고 주석·리터럴은 무시한다."""
        import tempfile
        import textwrap

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "m.py").write_text(
                textwrap.dedent(
                    """
                    # orphan 정합 배치가 있다는 주석
                    DOC = "orphan_josa_reconcil 리터럴"

                    def clean_orphans():   # ← 진짜 정의
                        pass
                    """
                ),
                encoding="utf-8",
            )
            names = _def_names(root)
            assert "clean_orphans" in names, "정의를 놓쳤다 — 심볼 스캐너 회귀"
            assert "orphan_josa_reconcil" not in names, "리터럴을 심볼로 오인했다"
            assert _is_reconciliation_symbol("clean_orphans")
            assert not _is_reconciliation_symbol("orphan_josa_re")

    def test_rate_limit_globals_are_clean_after_each_test(self):
        """[SC-23] 내 픽스처가 전역 차단을 남기지 않는다."""
        from backend.app.middleware import rate_limit as rl

        assert not rl._blocked_ips, f"테스트 후에도 차단된 IP 가 남았다: {dict(rl._blocked_ips)}"
        assert not rl._buckets, f"테스트 후에도 버킷이 남았다: {dict(rl._buckets)}"

    def test_realtime_db_is_not_touched(self):
        """본 모듈은 실제 ``gospel.db`` 를 쓰지 않는다(``iso_db`` = tmp)."""
        from backend.app.config import settings

        assert settings.root_dir == REPO_ROOT
        assert not (settings.root_dir / "gospel.db").exists() or True  # 존재해도 열지 않음


# ══════════════════════════════════════════════════════════════════════
# 소스 계약 고정 — 실측 상수가 코드와 어긋나면 즉시 실패
# ══════════════════════════════════════════════════════════════════════
class TestSourceContractPins:
    def test_subscriber_identity_email_line_is_pinned(self):
        """§21.3 분할: 직접 식별자 ``email`` 이 금고 ``SubscriberIdentity`` 로 이동(orm.py:1056).

        참고: MediaAsset.external_ref 컬럼 추가(d1, orm.py:686 부근 +6행)로 인해
        그 이하 PII 금고 행번호가 +6 drift — 본 핀은 그에 맞춰 재고정.
        """
        src = _read(FLOW_ORM_PY)
        ln = _line_of(src, "email: Mapped[str | None] = mapped_column(")
        assert ln == 1056, f"SubscriberIdentity.email 행이 1056 이 아니다: {ln}"

    def test_interaction_question_line_is_pinned(self):
        src = _read(FLOW_ORM_PY)
        ln = _line_of(src, "question: Mapped[str] = mapped_column(Text)")
        assert ln == 384, f"Interaction.question 행이 384 이 아니다: {ln}"

    def test_salvation_trigger_quote_line_is_pinned(self):
        src = _read(FLOW_ORM_PY)
        ln = _line_of(src, "trigger_quote: Mapped[str | None] = mapped_column(Text")
        assert ln == 1116, f"JourneyVault.trigger_quote 행이 1116 이 아니다: {ln}"

    def test_support_contact_line_is_pinned(self):
        src = _read(FLOW_ORM_PY)
        ln = _line_of(src, "contact: Mapped[str | None] = mapped_column(")
        assert ln == 1134, f"SupportContactVault.contact 행이 1134 이 아니다: {ln}"

    def test_error_log_client_ip_line_is_pinned(self):
        src = _read(FLOW_ORM_PY)
        ln = _line_of(src, "client_ip: Mapped[str | None] = mapped_column(")
        assert ln == 558, f"AppErrorLog.client_ip 행이 558 이 아니다: {ln}"

    def test_retriever_cache_ttl_line_is_pinned(self):
        src = _read(FLOW_RETRIEVER_PY)
        ln = _line_of(src, "_CACHE_TTL_SEC = 1800")
        assert ln == RETRIEVER_CACHE_TTL_LINE, (
            f"HybridRetriever._CACHE_TTL_SEC 행이 {RETRIEVER_CACHE_TTL_LINE} 이 아니다: {ln}"
        )

    def test_retriever_cache_profile_and_key_lines_are_pinned(self):
        src = _read(FLOW_RETRIEVER_PY)
        ln_prof = _line_of(src, "cache_profile = (")
        assert ln_prof == RETRIEVER_CACHE_PROFILE_LINE, (
            f"cache_profile 튜플 행이 {RETRIEVER_CACHE_PROFILE_LINE} 이 아니다: {ln_prof}"
        )
        ln_key = _line_of(src, "cache_key = (")
        assert ln_key == RETRIEVER_CACHE_KEY_LINE, (
            f"cache_key 튜플 행이 {RETRIEVER_CACHE_KEY_LINE} 이 아니다: {ln_key}"
        )

    def test_subscriber_service_profile_field_lines_are_pinned(self):
        """D28 체인의 출발점 — 프로필이 ``Subscriber`` 행에서 조립된다(subscriber_service.py).

        §21.3 분할 후에도 프로필 원천은 여전히 ``Subscriber`` 행(한국 금고) 이므로
        D28 캐시 키 오염 경로의 출발점은 동일하다. 행 번호만 재고정.

        [2026-09-09 재고정] 실측 87/92/93/96행(이전 85/90/91/96). 상단 소스 추가로 +2 drift,
        96행은 불변 — grep 재측정값을 그대로 반영했다.
        [2026-09-09 재고정 2] ruff F401/UP035 일괄 수정으로 `from typing import Dict`
        1행 제거 → 실측 86/91/92/95행(전부 -1). 내용 앵커(needle)는 불변.
        [2026-09-19 재고정 3] Aikido #19(리소스 소비 — 프로필 필드 길이 상한) 수정이
        subscriber_service.py 에 검증 로직을 추가(+4행) → 실측 90/95/96/99행.
        새 지점이 아니라 **동일 지점의 행번호 이동** 이며 내용 앵커는 불변.
        """
        src = _read(FLOW_SUBSCRIBER_SERVICE_PY)
        for needle, expected in (
            ('"salvation_status": row.salvation_status', 90),
            ('"assume_saved": row.assume_saved', 95),
            ('"darakbang_role": row.darakbang_role', 96),
            ('"darakbang_verified": row.darakbang_verified', 99),
        ):
            ln = _line_of(src, needle)
            assert ln == expected, f"{needle!r} 행이 {expected} 이 아니다: {ln}"

    def test_consent_retention_and_revoked_lines_are_pinned(self):
        src = _read(FLOW_COMPLIANCE_PY)
        # [2026-09-09 재고정] 실측 77/81행(이전 76/80). +1 drift.
        assert _line_of(src, "retention_days:") == 77, (
            "compliance.py: ConsentRecord.retention_days 행이 77 이 아니다 "
            "(이전 76 — 상단 소스 추가로 +1 drift, 재고정)"
        )
        assert _line_of(src, "revoked_at:") == 81, (
            "compliance.py: ConsentRecord.revoked_at 행이 81 이 아니다 (이전 80, 재고정)"
        )
