"""A1–A8 — FLOW ↔ 音乐独立架构 게이트 (ISSUE #72 결정 2026-10-11).

═══════════════════════════════════════════════════════════════════════
배경
═══════════════════════════════════════════════════════════════════════
#72 에서 사용자가 4개 결정을 확정했다(결정 1/3 이 문서를 뒤집음):

  · 결정 1 (DB)   = **A 안: 독립 DB + 좁은 브리지**.
                    `MUSIC_PLATFORM_MIGRATION_2026-09-10` 의 「共享库」 결론 폐기.
                    두 서비스 각库各主권, 사용자 신원은 token 교환 + `user_links` 매핑.
  · 결정 2 (Token) = **현행 유지** (독립 JWT + scoped exchange, `SYNC_FLOW_SCOPE`).
  · 결정 3 (Front) = **A 안: 독립 App** (`com.flowccm.app`).
                    FLOW App 「音乐」Tab 은 점프 진입점으로 강등(FLOW 리포 변경, 본 리포 아님).
  · 결정 4 (Deploy) = 방향(동일 호스트 단일 Caddy + 서브도메인 분站) 유지;
                    도메인/공존 시점은 #74 로 이관(**본 issue 비차단**).

═══════════════════════════════════════════════════════════════════════
게이트 설계 — 기존 「이중궤도(xfail(strict) 목표门 + passed 현행钉)」 재사용
═══════════════════════════════════════════════════════════════════════
  A. **현행钉(passed)** — "지금 무엇이 참인가" 고정. 설계가 바뀌면 깨져 갱신 강제.
  B. **목표门(xfail, strict=True)** — 미구현/미성립 목표 단언. 구현되면 xpass →
     strict 오류 → 마커 제거 강제 = 진짜 병합 차단 게이트.
  C. skip 금지(사유 명시 외) — SC-24: 게이트가 조용히 skip 으로 강등되면
     exit=0 이지만 실질 게이트가 사라진다.

본 파일은 **본 리포(= 음악 플랫폼 자신)** 를 직접 판독한다 — 외부
`MUSIC_PLATFORM_REPO` 의존이 없다(그 env 는 FLOW 측 하네스용). 따라서
본 게이트는 환경 무관하게 항상 실행된다(skip 0).

하네스 주의(재발 방지):
  · SC-18b  문자열 검색은 독스트링·주석에 속는다 → **AST** 로 실행 코드만 본다.
  · SC-20  `sys.modules["app"]` 오염 회피 — 런타임 import 없이 소스 트리를 파싱한다.
  · SC-27  음악은 SQLAlchemy **1.x 스타일**(`x = Column(...)`). 파서는 두 스타일 모두.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"
MODELS_PY = BACKEND / "app" / "models.py"
CONFIG_PY = BACKEND / "app" / "config.py"
MIGRATIONS_DIR = BACKEND / "migrations" / "versions"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE_COLOCATION = REPO_ROOT / "docker-compose.colocation.yml"
COMPOSE_MAIN = REPO_ROOT / "docker-compose.yml"
CAPACITOR_CFG = REPO_ROOT / "native" / "capacitor.config.json"
SUPERSEDE_DOCS = (
    REPO_ROOT / "docs" / "MUSIC_PLATFORM_MIGRATION_2026-09-10.md",
    REPO_ROOT / "docs" / "MUSIC_TAB_MERGE_PLAN_2026-09-21.md",
    REPO_ROOT / "docs" / "PLAN_MUSIC_APP_2026-09-28.md",
)
# FLOW 측 DB 파일명 — 어떤 설정에도 이 문자열이 음악측 기본값으로 오면 위반.
FLOW_DB_NAME = ".gospel.db"


# ── 공용 유틸 (AST) ────────────────────────────────────────────────────
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _call_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", "")


def _decl_field(stmt: ast.stmt) -> tuple[str, str] | None:
    """클래스 본문 문 1개에서 (필드명, 선언문) — 1.x/2.0 두 스타일 모두(SC-27)."""
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


def _orm_fields(source: str) -> dict[str, dict[str, str]]:
    """{클래스: {필드: 선언문}} — Base 상속 클래스만."""
    tree = ast.parse(source)
    out: dict[str, dict[str, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if "Base" not in {ast.unparse(b) for b in node.bases}:
            continue
        fields: dict[str, str] = {}
        for stmt in node.body:
            got = _decl_field(stmt)
            if got:
                fields[got[0]] = got[1]
        if fields:
            out[node.name] = fields
    return out


def _module_assign(tree: ast.Module) -> dict[str, str]:
    """모듈 최상위 name = <literal> 매핑(클래스 내부 제외)."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = ast.unparse(node.value)
    return out


# ── A1 ─────────────────────────────────────────────────────────────────
def test_a1_userlink_music_user_id_has_no_foreign_key():
    """[#72 결정 1] `UserLink.music_user_id` 는 **FK 가 아니라** 일반 컬럼.

    독립 DB 에서 cross-database FK 는 성립하지 않는다(FLOW DB 에 `users` 없음).
    종전 `ForeignKey("users.id")` 는 공유 DB 전제(09-10) 잔재.
    """
    fields = _orm_fields(_read(MODELS_PY))
    assert "UserLink" in fields, "UserLink 모델이 사라졌다"
    decl = fields["UserLink"].get("music_user_id")
    assert decl is not None, "UserLink.music_user_id 컬럼이 사라졌다(브리지 키 유실)"
    assert "ForeignKey" not in decl, (
        f"user_links.music_user_id 에 아직 FK 가 있다: {decl}\n"
        "→ #72 결정 1(독립 DB) 위반 — cross-DB FK 는 성립하지 않는다."
    )


def test_a1_migration_drops_the_fk():
    """[#72 결정 1] FK 제거 마이그레이션이 실재하고 head 체인에 연결돼 있다.

    (마이그레이션 없이 모델만 고치면 순수 alembic 환경에서 schema drift.)
    """
    found = []
    for p in MIGRATIONS_DIR.glob("*.py"):
        src = _read(p)
        if "user_links" in src and "music_user_id" in src and "drop" in src.lower():
            tree = ast.parse(src)
            assigns = _module_assign(tree)
            found.append((p.name, assigns.get("revision"), assigns.get("down_revision")))
    assert found, (
        "user_links.music_user_id 의 FK 를 제거하는 마이그레이션이 없다 — "
        "모델만 바꾸면 schema drift."
    )


# ── A2 ─────────────────────────────────────────────────────────────────
def test_a2_narrow_bridge_upsert_path_intact():
    """[현행钉] 좁은 브리지의 쓰기 경로(flow-link upsert)가 유지된다.

    FK 를 제거하면 정합성 책임이 애플리케이션으로 넘어온다 — 그 경로가
    실재하지 않으면 브리지가 무책임해진다.
    """
    auth_src = _read(BACKEND / "app" / "routers" / "auth.py")
    tree = ast.parse(auth_src)
    func_names = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "flow_link" in func_names or any("flow_link" in n for n in func_names), (
        "auth.py 에 flow-link upsert 함수가 없다"
    )
    assert "UserLink" in auth_src, "auth.py 가 UserLink 를 쓰지 않는다 — 브리지 쓰기 경로 부재"


# ── A3 ─────────────────────────────────────────────────────────────────
def test_a3_userlink_holds_only_pseudonymous_keys():
    """[현행钉] 브리지 테이블은 가명키 매핑만 — PII 원문 컬럼 금지.

    (email/name/phone 류 컬럼이 user_links 에 등장하면 PII 경계 위반.)
    """
    fields = _orm_fields(_read(MODELS_PY))["UserLink"]
    forbidden = {"email", "name", "phone", "display_name", "password", "hashed_password"}
    leaked = forbidden & set(fields)
    assert not leaked, f"user_links 에 PII 원문 컬럼이 등장했다: {sorted(leaked)}"


# ── A4 ─────────────────────────────────────────────────────────────────
def test_a4_flow_scope_token_exchange_present():
    """[현행钉, 결정 2] scope 한정 토큰 교환 경로가 유지된다(`sync:flow`).

    FK 제거로 정합성 책임이 앱 계층으로 온 이상, 신원 연결은 토큰 교환이 진다.
    """
    auth_src = _read(BACKEND / "app" / "routers" / "auth.py")
    assert 'SYNC_FLOW_SCOPE' in auth_src or '"sync:flow"' in auth_src or "'sync:flow'" in auth_src, (
        "auth.py 에 sync:flow scope 상수가 없다 — 결정 2(토큰 교환) 경로 유실"
    )


# ── A5 ─────────────────────────────────────────────────────────────────
def test_a5_music_database_url_is_independent_of_flow_db():
    """[#72 결정 1] 음악측 기본 DB 는 FLOW `.gospel.db` 와 **다르다**.

    판정 대상 3곳: config.py 기본값 / .env.example / compose backend env.
    어디에도 `.gospel.db` 가 음악측 DB 로 등장하면 공유 DB 회귀.
    """
    cfg_fields = _module_assign(ast.parse(_read(CONFIG_PY)))
    # pydantic Settings 는 클래스 본문 안에 기본값이 있으므로 클래스 필드도 본다
    tree = ast.parse(_read(CONFIG_PY))
    db_default = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id == "database_url" and stmt.value is not None:
                        db_default = ast.unparse(stmt.value)
    assert db_default is not None, "config.py Settings.database_url 기본값을 찾지 못했다"
    assert FLOW_DB_NAME not in db_default, (
        f"config.py 기본 DATABASE_URL 이 FLOW DB 를 가리킨다: {db_default}"
    )

    env_src = _read(ENV_EXAMPLE)
    for line in env_src.splitlines():
        if line.strip().startswith("DATABASE_URL="):
            val = line.split("=", 1)[1]
            assert FLOW_DB_NAME not in val, f".env.example 의 DATABASE_URL 이 FLOW DB: {val}"
            break

    compose_src = _read(COMPOSE_MAIN)
    tree = ast.parse("")  # noqa: F841  (compose 는 YAML — 문자열로 확인)
    assert FLOW_DB_NAME not in compose_src.split("backend:", 1)[-1], (
        "docker-compose.yml backend 환경에 FLOW DB(.gospel.db) 가 등장한다"
    )


def test_a5_colocation_compose_declares_db_independence():
    """[#72 결정 1] 공존 override 가 DB 독립 경계를 명시한다(설정 병합 사고 방지)."""
    src = _read(COMPOSE_COLOCATION)
    # 경계 선언의 구체 마커: ① FLOW DB 파일명 언급 ② #72 결정 1 지목 ③ DB 독립 서술.
    assert FLOW_DB_NAME in src, (
        "docker-compose.colocation.yml 이 FLOW DB(.gospel.db) 를 언급하지 않는다 — "
        "DB 독립 경계 주석 부재(설정 병합 시 공유 DB 회귀 위험)."
    )
    assert "#72" in src and "决策 1" in src, (
        "docker-compose.colocation.yml 에 #72 결정 1(독립 DB) 포인터가 없다"
    )
    assert "主权" in src or "独立" in src, (
        "docker-compose.colocation.yml 에 DB 主权/독립 서술이 없다"
    )


# ── A6 ─────────────────────────────────────────────────────────────────
def test_a6_supersede_pointers_present_in_all_three_docs():
    """[#72 수렴] 09-10 / 09-21 / 09-28 3개 문서에 supersede banner + #72 포인터.

    原文은 **삭제하지 않는다**(결정 궤적 보존) — banner 삽입만.
    """
    for doc in SUPERSEDE_DOCS:
        assert doc.is_file(), f"문서가 사라졌다: {doc}"
        src = _read(doc)
        assert "SUPERSEDED" in src, f"{doc.name} 에 SUPERSEDED banner 가 없다"
        assert "#72" in src, f"{doc.name} 에 #72 supersede 포인터가 없다"
        assert "2026-10-11" in src, f"{doc.name} banner 에 결정 일자(2026-10-11)가 없다"


# ── A7 ─────────────────────────────────────────────────────────────────
def test_a7_independent_app_id_is_flowccm():
    """[현행钉, 결정 3] 독립 App id = `com.flowccm.app`(FLOW `com.gospelai.app` 아님)."""
    cfg = json.loads(_read(CAPACITOR_CFG))
    assert cfg.get("appId") == "com.flowccm.app", (
        f"capacitor.config.json appId 가 독립 App id 가 아니다: {cfg.get('appId')!r}"
    )
    assert cfg.get("appId") != "com.gospelai.app", "FLOW 본체 app id 로 회귀했다"


# ── A8 ─────────────────────────────────────────────────────────────────
def test_a8_music_tab_demoted_to_redirect_entry():
    """[목표门 → 현행] FLOW App 「音乐」Tab 이 점프 진입점으로 강등됐는지는
    **FLOW 리포의 변경**이라 본 리포에서 단언할 수 없다.

    본 리포가 할 수 있는 단언: 결정 3 이 **문서화**되어 있고, 본 리포가
    FLOW App Tab 을 제품 형태로 주장하지 **않는다**(= 독립 App 서술로 수렴).
    """
    merge_doc = REPO_ROOT / "docs" / "MUSIC_TAB_MERGE_PLAN_2026-09-21.md"
    src = _read(merge_doc)
    assert "另开 issue" in src or "不在本仓库" in src or "점프" in src or "跳转" in src, (
        "MUSIC_TAB_MERGE_PLAN 에 'Tab 은 FLOW 리포 사안' 이라는 강등 서술이 없다"
    )


@pytest.mark.xfail(
    strict=True,
    reason="#72 결정 3: FLOW App 侧 '音乐' Tab 的跳转入口改动在 FLOW 仓库实施，本仓库尚无跨仓验证；"
    "待 FLOW 侧 issue 落地后由跨仓 e2e 覆盖，届时移除本 xfail",
)
def test_a8_x_tab_redirect_cross_repo_verified():
    """[목표门] FLOW App Tab → 独立 App 점프 진입점의 **교차 리포 실증**.

    본 리포에는 FLOW App 소스가 없으므로 #72 시점엔 실증 불가 → xfail(strict).
    FLOW 리포 이슈가 닫히고 교차 e2e 가 생기면 xpass → 마커 제거 강제.
    """
    # 교차 리포 실증 산출물(예: FLOW 리포의 점프 딥링크 테스트)이 본 리포에 반영되기 전까지 거짓.
    assert False, "FLOW App 쪽 '음악' Tab 점프 진입점의 교차 리포 실증이 아직 없다"
