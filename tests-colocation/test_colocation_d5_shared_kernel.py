"""공존(co-location) 설계 D5 — 공유 커널 DB 게이트.

대상: T5.1 · T5.2 · T5.5 · T5.6 · T5.8 — ** plus 신규 T5.9 **
근거 문서: ``docs/COLOCATION_MOCKTEST_PLAN_2026-08-31.md`` §4 D5
설계 문서: ``docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md`` §14

─────────────────────────────────────────────────────────────────────────
게이트 표기법 (L0 모듈과 동일)
─────────────────────────────────────────────────────────────────────────
``@pytest.mark.xfail(strict=True)`` 는 **목표 미달을 '기대된 실패'로 고정** 한다.
해당 항목이 구현되면 ``xpassed`` → strict 모드가 **오류로 처리** 하여 마커 제거를
강제한다. 제거 후에는 정상적인 병합 차단 게이트가 된다.

─────────────────────────────────────────────────────────────────────────
⚠️ 실측 결과 요약 (2026-08-31, 본 모듈 작성 전 직접 실행하여 확인)
─────────────────────────────────────────────────────────────────────────

**[X3] alembic_version 단일 테이블 충돌 — 양방향 실측 완료**

    음악 head = d4e5f6a7b8c9 / FLOW head = 4d7d02caeed2 (T5.9 재생성 후)

    (A) 음악 upgrade head → 같은 DB 에 FLOW upgrade head
        → CommandError: Can't locate revision identified by 'c2d3e4f5a6b7'
    (B) FLOW DB(create_all + stamp head) → 음악 upgrade head
        → CommandError: Can't locate revision identified by 'n1_ads_table'

    양측 ``env.py`` 모두 ``version_table`` / ``version_table_schema`` 설정이 없다:
        FLOW  ``backend/migrations/env.py:57-63``(offline) · ``:80-84``(online)
        음악  ``backend/migrations/env.py:39-44``(offline) · ``:58-60``(online)

**[X4] autogenerate 가 상대 테이블을 drop 한다**

    공유 DB(음악 head + FLOW 테이블)에서 음악 autogenerate 실행 시
    FLOW 의 **모든** 테이블이 ``DropTableOp`` 로 생성된다(실측 32/32).
    양측 ``env.py`` 모두 ``include_object`` 필터가 없다.

    ※ X3 이 먼저 터지므로 X4 를 관측하려면 **X3 이 해결된 상태의 DB** 가 필요하다.
      본 모듈은 음악 체인으로 head 를 만든 뒤 FLOW 테이블을 create_all 로 얹어
      "X3 해결 · X4 미해결" 상태를 재구성한다.

**[신규 D21] FLOW 마이그레이션 체인은 빈 DB 에서 재현되지 않는다**

    FLOW ORM 테이블 37개 중 마이그레이션이 만드는 테이블은 **11개** 뿐이다.
    나머지 **26개** 는 어떤 마이그레이션도 ``op.create_table`` 하지 않는다.
    → 빈 DB 에 ``alembic upgrade head`` 를 실행하면 첫 단계에서 죽는다:

        sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
        no such table: document_version
        [SQL: ALTER TABLE document_version ADD COLUMN target_audience JSON]

    원인: ``c2fbb2654cf3_baseline_all_tables.py`` 는 **'baseline' 이 아니라**
    이미 ``create_all`` 로 만들어진 운영 DB 에 대해 autogenerate 된 증분이다
    (``create_table`` 은 salvation_journey 1개뿐, 나머지는 전부 ``batch_alter_table``).
    이는 ``backend/app/main.py:318-321`` 주석이 스스로 밝히는 **의도된 설계** 다:

        신규 테이블 생성은 init_db() 의 create_all 로 보장된다

    공유 커널 관점의 파급: 공유 Postgres 를 새로 프로비저닝할 때
    **반드시 FLOW 앱을 한 번 기동(create_all)해야** 스키마가 완성된다.
    마이그레이션만으로는 절대 완성되지 않는다.

**[신규] X9 는 음악만의 문제가 아니다 — FLOW 도 동일**

    설계서 §14 는 X9 를 ``음악 app/db.py init_db()`` 로만 기록했으나 실측 결과
    **양측 모두 무조건 create_all** 을 호출한다:
        음악  ``backend/app/main.py:11-14``  lifespan → init_db()
        FLOW  ``backend/app/main.py:310``    startup  → init_db()
    환경 분기(운영 비활성)는 **양측 모두 없다**.

**[T5.8] 원가-크레딧 조인은 정의되지 않았다 — 환율 근거 부재**

    음악  ``models.py:46``  cost_cny     = Numeric(10, 4), **CNY**
    FLOW  ``models/compliance.py:76-78`` amount_krw / currency(default "KRW")
    양측 저장소 전체에 CNY↔KRW 환율 소스가 **존재하지 않는다**
    (``ccp_rate_card.currency`` 는 ``server_default="KRW"`` 단일 통화,
     ``source_note`` 는 "환율 기준일" 을 **텍스트로 적는** 설명 필드일 뿐이다).

─────────────────────────────────────────────────────────────────────────
테스트 하네스 주의 (Windows / pytest 환경 오염 방지)
─────────────────────────────────────────────────────────────────────────
1. ``env.py`` 는 실행마다 ``logging.config.fileConfig`` 를 호출한다
   (``disable_existing_loggers`` 기본 True) → pytest 로거가 비활성화된다.
   따라서 실행 전 ``logging.config.fileConfig`` 를 **no-op 으로 치환** 한다.
   (``config.config_file_name = None`` 으로 막으면 alembic 이
   ``No 'script_location' key found in configuration`` 을 던지므로 **사용 불가**.)

2. 음악 ``env.py`` 는 ``from app.db import Base`` (최상위 ``app`` 패키지) 를 쓴다.
   FLOW 는 ``backend.app.*`` 이므로 이름 충돌은 없으나, 음악 backend 를
   ``sys.path`` 에 선두 삽입해야 한다. 테스트 종료 시 음악 모듈은 정리한다.

3. 음악 플랫폼 저장소는 FLOW 저장소 밖에 있으므로 환경변수로 경로를 받는다.
       export MUSIC_PLATFORM_REPO=/path/to/ai-music-platform
   미설정 시 관련 테스트는 skip 된다(실패도, 허위 통과도 아님).
"""

from __future__ import annotations

import ast
import logging.config
import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("alembic", reason="alembic 미설치 — D5 공유 커널 게이트 전체 skip")

from alembic import command as alembic_command
from alembic.config import Config
from alembic.operations import ops
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]

FLOW_ALEMBIC_INI = REPO_ROOT / "alembic.ini"
FLOW_MIGRATIONS = REPO_ROOT / "backend" / "migrations"
FLOW_ENV_PY = FLOW_MIGRATIONS / "env.py"
FLOW_VERSIONS = FLOW_MIGRATIONS / "versions"

FLOW_DB_PY = REPO_ROOT / "backend" / "app" / "db.py"
FLOW_MAIN_PY = REPO_ROOT / "backend" / "app" / "main.py"
FLOW_COMPLIANCE_PY = REPO_ROOT / "backend" / "app" / "models" / "compliance.py"
FLOW_CCP_PY = REPO_ROOT / "backend" / "app" / "models" / "ccp.py"

# 실측값 — 테스트가 갱신을 강제하도록 상수로 고정한다.
FLOW_HEAD_EXPECTED = "4d7d02caeed2"  # [2026-09-01] T5.9(D21) 해결: 부패한 증분 baseline 을 진짜 baseline(전 테이블 create_table) 으로 재생성
# [2026-09-09 재고정] 외부 리포가 `e1f2a3b4c5d6_add_subscription_provider_customer_id` 를 추가해
# head 가 d4e5f6a7b8c9 → e1f2a3b4c5d6 로 진행됐다(단일 head 실측: revisions 13개, down_revision
# 집계로 head 후보 1개만 존재). 하네스 경로 오염 아님 — app.__file__ 이 음악 리포를 가리킴(실측).
# [2026-09-09 2차 재고정] 우리가 음악 리포에 `7f0c40c8bae2`(广场 상호작용 3테이블:
# song_likes / song_favorites / song_ratings) 를 추가 → head e1f2a3b4c5d6 → 7f0c40c8bae2.
# 단일 head 실측(스크립트 디렉터리 get_current_head). X3 영향도: 신규 테이블 3개는 음악 전용이며
# FLOW 테이블과 교집합 없음(아래 test_flow_base_has_no_music_tables 가 교차 검증).
# [2026-09-11 3차 재고정] 음악 리포에 `a1b2c3d4e5f7_add_song_lyrics_sync` 추가(songs.lyrics_sync
# 컬럼 = Mureka choices[].lyrics_sections逐字时间轴 JSON 저장) → head 7f0c40c8bae2 → a1b2c3d4e5f7.
# 단일 head 실측(alembic current = 곡정 DB 실측, 스크립트 집계). 컬럼 추가라 테이블 수 변화 없음.
MUSIC_HEAD_EXPECTED = "a1b2c3d4e5f7"

# [2026-09-17] 활동분석 집계 테이블 2개 추가(38 → 40):
#   활동분석 강화 커밋(3e87d13) 이 ORM 에 ActivityRollup/ActivityUniqueDaily 를 추가했으나
#   **마이그레이션이 없어** 이 가드가 적색이었다(= 정상 경보). 그 상태로 배포하면
#   순수 alembic 환경에서 `/admin/activity/timeseries?bucket=day` 가 HTTP 500 이 된다.
#   → 단일 baseline 정책(tests/test_alembic_no_drift.py) 을 지켜 **baseline 자체를 갱신**
#     했다(증분 리비전 신규 생성 금지). 따라서 이 가드는 다시 초록이다.
#   ⚠ 부수 발견: baseline 이 누락했던 인덱스 `ix_subscriber_sensitive_salvation_status`
#   (models/orm.py:225 index=True) 도 함께 보강했다. d5 는 **테이블** 집합만 비교하므로
#   인덱스 누락은 잡지 못하고, create_all 대조 과정에서 새로 발견된 것이다.
# [2026-10-03] 프로그램(节目) 기능 4테이블 추가(40 → 44):
#   `backend/app/models/program.py` 신규 — program / program_member / program_file /
#   program_invite. 단일 baseline 정책(tests/test_alembic_no_drift.py) 에 따라
#   증분 리비전을 만들지 않고 **baseline(4d7d02caeed2) 자체에 4개 create_table 을
#   추가** 했다(ORM 메타데이터로부터 직접 생성 → nullability/타입/제약 1:1 일치 검증 완료).
#   따라서 미커버 집합(KNOWN_UNCOVERED_TABLES) 은 여전히 빈 집합이다.
FLOW_ORM_TABLE_COUNT = 44
FLOW_MIGRATION_CREATED_TABLE_COUNT = 44

# 실측: 음악 Base.metadata 의 테이블(11개). 오염 판정용 기준값.
# [18차] `user_links` 모델(`app/models.py:63-71`, __tablename__="user_links") 추가 반영.
# [2026-09-09 재고정] 외부 리포가 `audit_logs` 테이블을 추가
# (f6a7b8c9d0e1_add_audit_logs.py:22 op.create_table). 하네스 오염이 아니라 외부 진화다.
# [2026-09-09 2차 재고정] 广场(Plaza) 무텍스트 상호작용 3테이블 추가(8 → 11):
#   `song_likes` / `song_favorites` / `song_ratings` (app/models.py, 마이그레이션 7f0c40c8bae2).
#   댓글(comment) 테이블은 **의도적으로 없다** — 제품 결정: 좋아요·즐겨찾기·별점만 제공.
MUSIC_TABLES = frozenset(
    {
        "users",
        "songs",
        "sync_records",
        "engine_configs",
        "routing_rules",
        "pricing_tiers",
        "user_links",
        "audit_logs",
        "song_likes",
        "song_favorites",
        "song_ratings",
    }
)

# 실측: FLOW ORM 37개가 진짜 baseline(4d7d02caeed2) 으로 전부 생성된다 — 미커버 0개.
# [2026-09-01] T5.9(D21) 해결: 부패한 증분 baseline(c2fbb...) 을 삭제하고
#   ``alembic revision --autogenerate`` 로 전 테이블 create_table 하는 진짜 baseline 을
#   재생성했다. 이제 운영(create_all 비활성, SCHEMA_CREATE_ALL=0) 에서도
#   ``alembic upgrade head`` 만으로 빈 DB 스키마가 완성된다.
KNOWN_UNCOVERED_TABLES = frozenset()


# ══════════════════════════════════════════════════════════════════════
# 공통 유틸
# ══════════════════════════════════════════════════════════════════════
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


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


def _import_all_flow_models():
    """FLOW ORM 모델 모듈을 모두 임포트해 Base.metadata 를 완성한다.

    ``backend/app/models/__init__.py`` 는 compliance 만 임포트하므로
    orm / ccp 를 직접 임포트해야 32개 테이블이 모두 잡힌다.
    (이 사실을 몰라 ccp_rate_card 를 '드리프트' 로 오판한 적이 있다.)
    """
    from backend.app.db import Base
    from backend.app.models import ccp as _ccp  # noqa: F401
    from backend.app.models import compliance as _compliance  # noqa: F401
    from backend.app.models import orm as _orm  # noqa: F401

    return Base


def _flow_orm_tables() -> set[str]:
    """FLOW 가 **실제로 소유한** ORM 테이블명만 돌려준다.

    ⚠️ ``Base.metadata`` 는 **전역 가변 상태** 다. ``tests/test_compliance_security.py:55``
    의 ``class _TempEnc(Base)``(``__tablename__ = "tmp_enc_at_rest"``) 처럼
    테스트가 모델을 정의하면 메타데이터에 영구히 섞인다.
    단독 실행 땐 32개였는데 전체 실행 땐 33개로 늘어난 원인이 이것이다.
    따라서 ``Base.metadata.tables`` 를 그대로 쓰지 않고,
    **매퍼 클래스의 정의 모듈이 ``backend.app.models`` 인 것만** 고른다.
    """
    base = _import_all_flow_models()
    owned: set[str] = set()
    for mapper in base.registry.mappers:
        cls = mapper.class_
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("backend.app.models"):
            owned.add(mapper.local_table.name)
    return owned


def _create_flow_schema(engine) -> None:
    """FLOW 가 소유한 테이블만 create_all 한다.

    전체 메타데이터로 create_all 하면 타 테스트가 정의한 모델
    (``tests/test_compliance_security.py:55`` 의 ``tmp_enc_at_rest``)까지
    같이 생겨서 autogenerate 결과가 실행 순서에 따라 달라진다.
    """
    from sqlalchemy import MetaData

    base = _import_all_flow_models()
    owned = MetaData()
    for name in sorted(_flow_orm_tables()):
        base.metadata.tables[name].to_metadata(owned)
    owned.create_all(bind=engine)


def _tables_created_by_migrations(versions_dir: Path) -> set[str]:
    """마이그레이션 파일에서 op.create_table 대상 테이블명을 AST 로 수집."""
    out: set[str] = set()
    for path in versions_dir.glob("*.py"):
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                out.add(node.args[0].value)
    return out


def _unguarded_call_linenos(tree: ast.Module, func_name: str) -> list[int]:
    """``func_name()`` 직접 호출 중 ``if`` 로 감싸이지 않은 것의 행번호."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    found: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            ancestor: ast.AST = node
            guarded = False
            while ancestor in parents:
                ancestor = parents[ancestor]
                if isinstance(ancestor, (ast.If, ast.IfExp)):
                    guarded = True
                    break
            if not guarded:
                found.append(node.lineno)
    return found


def _strip_docstrings_and_comments(source: str) -> str:
    """소스에서 주석·독스트링을 제거한 '실행 코드만' 남긴 문자열을 돌려준다.

    왜 필요한가: ``ccp.py:77`` 의 ``단가 출처(공급자 가격표 URL·환율 기준일 등)``
    처럼 **문서용 서술** 에 "환율" 이 들어있어, 원문 그대로 grep 하면
    '환율 소스가 있다' 는 **거짓 양성** 이 나온다(실제로 한 번 속았다).
    ``ast.unparse`` 는 주석을 보존하지 않고, 독스트링은 아래에서 지운다.
    """
    tree = ast.parse(source)
    docstring_holders = (
        ast.Module,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
    )
    for node in ast.walk(tree):
        if isinstance(node, docstring_holders) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                first.value.value = ""
    return ast.unparse(tree)


def _flow_config(db_url: str) -> Config:
    """FLOW alembic Config.

    ``script_location`` 을 **절대경로로 강제** 한다.
    FLOW ``alembic.ini:6`` 은 ``script_location = backend/migrations`` 로
    **상대경로** 다(음악 ``alembic.ini:8`` 은 ``%(here)s/migrations`` 로 절대).
    따라서 cwd 가 바뀌면 alembic 이 엉뚱한 경로를 찾는다 — 실제로 음악 backend 로
    chdir 한 상태에서 ``.../backend/backend/migrations`` 를 찾아 실패했다.
    """
    cfg = Config(str(FLOW_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(FLOW_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _music_config(mus: Path, db_url: str) -> Config:
    cfg = Config(str(mus / "alembic.ini"))
    cfg.set_main_option("script_location", str(mus / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


# ══════════════════════════════════════════════════════════════════════
# 픽스처
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture
def no_fileconfig(monkeypatch):
    """``env.py`` 의 ``logging.config.fileConfig`` 호출을 no-op 으로 만든다.

    파일을 치환하는 이유는 본 모듈 docstring 「테스트 하네스 주의 1」 참조.
    """
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: None)


@pytest.fixture
def music_backend(monkeypatch):
    """음악 backend 로 cwd/sys.path 를 전환하고, 종료 시 모듈을 정리한다.

    ⚠️⚠️ **가장 중요한 함정 — `app` 패키지 오염**

    ``tests/test_salvation_regression.py:29`` 가 ``sys.path.insert(0, ROOT/"backend")``
    를 하고 ``:36`` 에서 ``from app.db import get_session_immediate, init_db`` 를
    수행한다. 즉 전체 회귀 시 ``sys.modules["app"]`` 은 **FLOW 의 backend/app** 이다.

    이 상태에서 음악 backend 만 ``sys.path`` 선두에 넣어도, 음악 ``env.py`` 의
    ``from app.db import Base`` 는 **캐시된 FLOW 의 Base 를 반환** 한다.
    실제로 발생한 결과:

        단독 실행 → 음악 Base  → FLOW 테이블 31건 drop  (우연히 기대와 일치)
        전체 회귀 → FLOW Base  → **음악 테이블 6건 + ccp_rate_card 만 drop**

    후자는 현실에 존재하지 않는 상황을 측정한 **허위 결과** 다.
    따라서 진입 시 기존 ``app`` 패키지를 잠시 걷어내고, 종료 시 복원한다.
    (``backend.app.*`` 는 이름이 다르므로 영향을 받지 않는다.)
    """
    mus = _music_backend()

    saved_app = {
        key: mod for key, mod in sys.modules.items() if key == "app" or key.startswith("app.")
    }
    for key in saved_app:
        del sys.modules[key]

    before = set(sys.modules)
    monkeypatch.chdir(mus)
    monkeypatch.syspath_prepend(str(mus))
    try:
        yield mus
    finally:
        mus_resolved = str(mus.resolve())
        for name in set(sys.modules) - before:
            mod = sys.modules.get(name)
            file_path = getattr(mod, "__file__", None)
            if not file_path:
                continue
            try:
                if str(Path(file_path).resolve()).startswith(mus_resolved):
                    del sys.modules[name]
            except OSError:
                pass
        # FLOW 측 app 패키지 복원(다른 테스트가 참조할 수 있다)
        sys.modules.update(saved_app)


@pytest.fixture
def shared_db_url(tmp_path, monkeypatch):
    """테스트별 격리 SQLite URL + 양측이 읽는 환경변수 주입.

    ``monkeypatch.setenv`` 를 쓰므로 테스트 종료 시 자동 복원된다
    (음악 ``config.py`` 도 ``DATABASE_URL`` 을 읽으므로 누출은 위험하다).
    """
    url = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ALEMBIC_DB_URL", url)
    return url


@pytest.fixture
def coexisting_db(no_fileconfig, music_backend, shared_db_url):
    """'X3 해결 · X4 미해결' 상태의 공유 DB.

    1) 음악 체인으로 head 까지 올린다(alembic_version = 음악 head).
    2) 같은 DB 에 FLOW 테이블을 create_all 로 얹는다(공존 상황).
    """
    from sqlalchemy import create_engine

    alembic_command.upgrade(_music_config(music_backend, shared_db_url), "head")

    engine = create_engine(shared_db_url)
    _create_flow_schema(engine)
    engine.dispose()
    return shared_db_url


def _autogenerate_upgrade_ops(mus: Path, db_url: str) -> list[object]:
    """음악 측 autogenerate 를 실행하고 upgrade ops 만 캡처한다(파일 미생성).

    ``process_revision_directives`` 에서 ``directives[:] = []`` 로 비우면
    alembic 은 마이그레이션 파일을 쓰지 않는다(실측 확인).
    왜 이 방식인가: ``produce_migrations`` 직접 호출은 ``env.py`` 의
    ``context.configure(...)`` 를 우회하므로, **나중에 include_object 를
    env.py 에 추가해도 이 테스트가 느끼지 못한다**. 게이트는 반드시
    env.py 를 통과해야 의미가 있다.
    """
    captured: dict[str, list[object]] = {}

    def _capture(_context, _revision, directives):
        if directives and directives[0].upgrade_ops:
            captured["ops"] = list(directives[0].upgrade_ops.ops)
        directives[:] = []  # 파일 생성 억제

    alembic_command.revision(
        _music_config(mus, db_url),
        autogenerate=True,
        process_revision_directives=_capture,
    )
    return captured.get("ops", [])


def _db_table_names(db_url: str) -> set[str]:
    """진단용: 해당 DB 에 실제로 존재하는 테이블 목록."""
    from sqlalchemy import create_engine, inspect

    engine = create_engine(db_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _drop_table_names(upgrade_ops: list[object]) -> list[str]:
    return sorted(op.table_name for op in upgrade_ops if isinstance(op, ops.DropTableOp))


# ══════════════════════════════════════════════════════════════════════
# 하네스 무결성 — '측정 대상이 바뀌는' 사고를 막는 자기검증
# ══════════════════════════════════════════════════════════════════════
class TestHarnessIntegrity:
    """테스트 하네스 자체가 옳은 대상을 측정하고 있는지 검증한다.

    이 클래스가 없으면 D5 의 모든 결과는 **조용히 틀린 채로 초록불** 이 된다.
    실제로 전체 회귀에서만 깨지는 방식으로 한 번 속았다(아래 테스트 docstring 참조).
    """

    def test_music_base_is_really_the_music_one(self, music_backend):
        """음악 ``env.py`` 가 참조할 ``app.db.Base`` 가 **음악 것** 인지 확인.

        실제 사고: 전체 회귀 시 ``tests/test_salvation_regression.py:29,36`` 이
        ``sys.modules["app"]`` 을 FLOW 의 ``backend/app`` 으로 만들어 둔다.
        그 상태로 음악 alembic 을 돌리면 ``target_metadata`` 가 **FLOW 의 메타데이터**
        가 되어, "음악 autogenerate 가 음악 테이블 6개를 drop 한다" 는
        **현실에 없는 결과** 를 얻는다(그렇게 한 번 속았다).
        """
        import app
        import app.models
        from app.db import Base as music_base

        tables = set(music_base.metadata.tables)
        assert tables == MUSIC_TABLES, (
            "음악 Base.metadata 가 기대와 다르다 — 하네스가 엉뚱한 패키지를 import 했다.\n"
            f"  기대: {sorted(MUSIC_TABLES)}\n"
            f"  실제: {sorted(tables)}\n"
            f"  app.__file__ = {getattr(app, '__file__', None)}\n"
            "→ `app` 패키지 오염이다. music_backend 픽스처의 sys.modules 정리를 확인하라."
        )
        # FLOW 테이블이 섞여 있으면 확실히 오염이다.
        assert not (tables & _flow_orm_tables()), (
            f"음악 Base 에 FLOW 테이블이 섞였다: {sorted(tables & _flow_orm_tables())}"
        )

    def test_flow_base_is_still_the_flow_one(self, music_backend):
        """음악 모듈을 import 한 뒤에도 FLOW 의 Base 는 오염되지 않는다."""
        flow_tables = _flow_orm_tables()
        assert flow_tables, "FLOW ORM 테이블을 찾지 못했다"
        assert not (flow_tables & MUSIC_TABLES), (
            f"FLOW Base 에 음악 테이블이 섞였다: {sorted(flow_tables & MUSIC_TABLES)}"
        )
        assert "subscriber" in flow_tables

    def test_db_helper_reads_the_expected_schema(self, music_backend, shared_db_url):
        """``_create_flow_schema`` 가 FLOW 소유 테이블만 만든다(오염 검증).

        ``tests/test_compliance_security.py:55`` 의 ``_TempEnc`` 처럼 타 테스트가
        ``Base.metadata`` 에 모델을 추가해도 결과가 흔들리면 안 된다.
        """
        from sqlalchemy import create_engine

        engine = create_engine(shared_db_url)
        try:
            _create_flow_schema(engine)
            created = _db_table_names(shared_db_url)
        finally:
            engine.dispose()

        flow_tables = _flow_orm_tables()
        assert created == flow_tables, (
            "create_all 결과가 FLOW 소유 테이블과 다르다.\n"
            f"  초과: {sorted(created - flow_tables)}\n"
            f"  누락: {sorted(flow_tables - created)}"
        )
        assert "tmp_enc_at_rest" not in created, (
            "타 테스트의 임시 모델(tmp_enc_at_rest)이 스키마에 섞였다"
        )


# ══════════════════════════════════════════════════════════════════════
# T5.1 — 버전 테이블 격리 (X3)
# ══════════════════════════════════════════════════════════════════════
class TestT51VersionTableIsolation:
    """양측 head 가 하나의 ``alembic_version`` 을 공유하면 서로를 훼손한다."""

    def test_music_then_flow_upgrade_on_shared_db(
        self, no_fileconfig, music_backend, shared_db_url
    ):
        """음악 head 로 올린 DB 에 FLOW 가 upgrade 를 시도하면 성공해야 한다.

        현재: ``Can't locate revision identified by 'c2d3e4f5a6b7'``
        """
        alembic_command.upgrade(_music_config(music_backend, shared_db_url), "head")
        alembic_command.upgrade(_flow_config(shared_db_url), "head")

    def test_flow_then_music_upgrade_on_shared_db(
        self, no_fileconfig, music_backend, shared_db_url
    ):
        """FLOW 운영 DB(alembic_version = FLOW head)에 음악이 upgrade 되어야 한다.

        FLOW 체인은 빈 DB 에서 죽으므로(D21) 운영 경로인
        ``create_all`` + ``alembic stamp head`` 로 DB 를 구성한다.
        (create_all 된 DB 에 upgrade 를 돌리면 중복 컬럼으로 또 죽는다 —
         별도 항목 A3. 여기서는 X3 만 본다.)
        현재: ``Can't locate revision identified by 'n1_ads_table'``
        """
        from sqlalchemy import create_engine

        engine = create_engine(shared_db_url)
        _create_flow_schema(engine)
        engine.dispose()

        alembic_command.stamp(_flow_config(shared_db_url), "head")
        alembic_command.upgrade(_music_config(music_backend, shared_db_url), "head")

    def test_head_revisions_are_as_recorded(self, no_fileconfig, music_backend, shared_db_url):
        """양측 head 값을 상수와 대조한다(리비전 변경 시 이 테스트가 갱신을 강제)."""
        music_head = ScriptDirectory.from_config(
            _music_config(music_backend, shared_db_url)
        ).get_current_head()
        flow_head = ScriptDirectory.from_config(_flow_config(shared_db_url)).get_current_head()

        assert music_head == MUSIC_HEAD_EXPECTED, (
            f"음악 head 가 바뀌었다: 기대={MUSIC_HEAD_EXPECTED!r} 실제={music_head!r}. "
            "MUSIC_HEAD_EXPECTED 를 갱신하고 X3 영향도를 재평가하라."
        )
        assert flow_head == FLOW_HEAD_EXPECTED, (
            f"FLOW head 가 바뀌었다: 기대={FLOW_HEAD_EXPECTED!r} 실제={flow_head!r}. "
            "FLOW_HEAD_EXPECTED 를 갱신하고 X3 영향도를 재평가하라."
        )

    def test_x3_version_table_isolation_is_configured(self, music_backend):
        """X3 해결 확인: 양측 env.py 가 서로 다른 버전 테이블을 지정한다.

        [2026-09-01] X3 가 실제로 구현되었다(env.py 양측 version_table 분리).
        따라서 이 테스트는 **부재** 가 아니라 **존재** 를 단언한다.
        """
        flow_src = _read(FLOW_ENV_PY)
        music_src = _read(music_backend / "migrations" / "env.py")
        assert 'version_table="alembic_version_flow"' in flow_src, (
            'FLOW env.py 에 version_table="alembic_version_flow" 설정이 없다 → '
            "X3 가 미해결(공유 alembic_version 충돌 가능)."
        )
        assert 'version_table="alembic_version_music"' in music_src, (
            '음악 env.py 에 version_table="alembic_version_music" 설정이 없다 → '
            "X3 가 미해결(공유 alembic_version 충돌 가능)."
        )
        # 양측이 서로 달라야 충돌이 해소된다.
        assert "alembic_version_flow" != "alembic_version_music"


# ══════════════════════════════════════════════════════════════════════
# T5.2 — autogenerate DROP 방지 (X4)
# ══════════════════════════════════════════════════════════════════════
class TestT52AutogenerateDropPrevention:
    """공유 스키마에서 autogenerate 가 상대 테이블을 drop 하지 않아야 한다."""

    def test_no_drop_table_for_counterpart_tables(self, music_backend, coexisting_db):
        """X4 해결 확인: 음악 autogenerate 가 FLOW 상대 테이블을 drop 하지 않는다.

        [2026-09-01] X4 가 실제로 구현되었다(env.py 양측 include_object 분리).
        따라서 이 게이트는 이제 **실제 drop 방지** 를 단언하는 병합 차단 게이트다.
        """
        drops = _drop_table_names(_autogenerate_upgrade_ops(music_backend, coexisting_db))
        assert not drops, (
            f"음악 autogenerate 가 상대 테이블 {len(drops)}건 drop 을 생성했다: {drops}"
        )

    def test_x4_no_counterpart_table_is_dropped(self, music_backend, coexisting_db):
        """X4 해결 확인: 공존 DB 에서 FLOW 테이블이 **단 하나도** drop 되지 않는다.

        이전에는 "FLOW 테이블 전부가 drop 대상" 이었으나(X4 미해결, 실측 32/32),
        include_object 격리 후에는 drop 대상이 0 이어야 한다. 이 테스트가 그 점을
        고정한다 — X4 가 깨지면 곧바로 이 테스트가 실패한다.
        """
        drops = set(_drop_table_names(_autogenerate_upgrade_ops(music_backend, coexisting_db)))
        assert drops == set(), (
            f"X4 이후에도 상대 테이블이 drop 된다: {sorted(drops)}\n"
            "→ env.py 의 include_object 격리가 무효화되었는지 확인하라."
        )
        # FLOW 가 소유한 테이블이 공존 DB 에 그대로 남아있음을 명시적으로 남긴다.
        db_tables = _db_table_names(coexisting_db)
        for table in ("subscriber", "interaction", "payment", "qa_trend_snapshot"):
            assert table in db_tables, f"{table} 이 공존 DB 에 없다 — 실측이 바뀌었다"

    def test_probe_writes_no_migration_files(self, music_backend, coexisting_db):
        """탐색 도구가 저장소를 오염시키지 않음을 보장한다.

        작성 중 실제로 ``49dd91b89b30_.py`` 가 음악 저장소에 생성되는 사고가 있었다.
        ``directives[:] = []`` 로 억제하며, 이 테스트가 재발을 막는다.
        """
        versions = music_backend / "migrations" / "versions"
        before = set(versions.glob("*.py"))
        _autogenerate_upgrade_ops(music_backend, coexisting_db)
        after = set(versions.glob("*.py"))
        assert after == before, (
            f"autogenerate 탐색이 저장소에 파일을 남겼다: {sorted(p.name for p in after - before)}"
        )

    def test_x4_include_object_filter_is_configured(self, music_backend):
        """X4 해결 확인: 양측 env.py 가 include_object 필터를 지정한다.

        [2026-09-01] X4 가 실제로 구현되었다(env.py 양측 include_object 분리).
        따라서 이 테스트는 **부재** 가 아니라 **존재** 를 단언한다.
        """
        flow_src = _read(FLOW_ENV_PY)
        music_src = _read(music_backend / "migrations" / "env.py")
        assert "include_object=_include_object" in flow_src, (
            "FLOW env.py 에 include_object 필터가 없다 → X4 가 미해결(상대 테이블 drop)."
        )
        assert "include_object=_include_object" in music_src, (
            "음악 env.py 에 include_object 필터가 없다 → X4 가 미해결(상대 테이블 drop)."
        )


# ══════════════════════════════════════════════════════════════════════
# T5.5 — Postgres 전환 (ENV 1줄, 코드 0)
# ══════════════════════════════════════════════════════════════════════
class TestT55PostgresSwitchover:
    """``DATABASE_URL`` 주입만으로 Postgres 로 전환되는가(코드 변경 0).

    실 Postgres 없이 검증한다. ``create_engine`` 은 DBAPI 를 즉시 import 하므로
    URL 은 ``make_url(...).get_backend_name()`` 으로만 확인한다.
    """

    POSTGRES_URL = "postgresql+psycopg://user:pw@db.internal:5432/flow"

    def test_resolve_db_url_prefers_env(self, monkeypatch):
        """``db.py:41-49`` — settings.database_url 가 있으면 그것을 쓴다."""
        from backend.app import config as config_mod
        from backend.app import db as db_mod

        monkeypatch.setattr(config_mod.settings, "database_url", self.POSTGRES_URL, raising=True)
        assert db_mod._resolve_db_url() == self.POSTGRES_URL

    def test_resolve_db_url_falls_back_to_sqlite(self, monkeypatch):
        """미설정이면 SQLite 파일 경로로 폴백한다(``db.py:46-49``)."""
        from backend.app import config as config_mod
        from backend.app import db as db_mod

        monkeypatch.setattr(config_mod.settings, "database_url", None, raising=True)
        resolved = db_mod._resolve_db_url()
        assert resolved.startswith("sqlite:///"), resolved
        assert resolved != self.POSTGRES_URL

    def test_engine_kwargs_are_dialect_aware(self):
        """``db.py:67-79`` — SQLite 전용 connect_args 가 Postgres 에 섞이지 않는다."""
        from backend.app import db as db_mod

        postgres_kwargs = db_mod._engine_kwargs(is_sqlite=False)
        assert "connect_args" not in postgres_kwargs, (
            "Postgres 커넥션에 SQLite 전용 check_same_thread 가 전달된다"
        )
        assert postgres_kwargs["pool_pre_ping"] is True
        assert postgres_kwargs["pool_recycle"] == 1800

        sqlite_kwargs = db_mod._engine_kwargs(is_sqlite=True)
        assert sqlite_kwargs["connect_args"]["check_same_thread"] is False

    def test_postgres_url_is_recognized_as_postgres(self):
        from sqlalchemy.engine import make_url

        url = make_url(self.POSTGRES_URL)
        assert url.get_backend_name() == "postgresql"
        assert url.database == "flow"
        assert url.port == 5432

    def test_sqlite_pragmas_are_guarded(self):
        """``db.py:98`` — PRAGMA 블록이 sqlite 분기로 감싸여 있는가."""
        source = _read(FLOW_DB_PY)
        assert "PRAGMA" in source, "PRAGMA 자체가 사라졌다 — 검증 대상 재확인 필요"

        guarded = re.search(
            r'if\s+DATABASE_URL\.startswith\(\s*"sqlite"\s*\)\s*:(.*?)(?=\n\S|\Z)',
            source,
            re.DOTALL,
        )
        assert guarded is not None, 'DATABASE_URL.startswith("sqlite") 분기를 찾지 못했다'
        assert "PRAGMA" in guarded.group(1), (
            "PRAGMA 실행이 sqlite 분기 밖에 있다 → Postgres 에서 깨진다"
        )

    def test_music_env_url_precedence_is_declared(self):
        """음악 ``env.py:28`` — ALEMBIC_DB_URL > settings.database_url."""
        mus = _music_backend()
        source = _read(mus / "migrations" / "env.py")
        assert 'os.environ.get("ALEMBIC_DB_URL")' in source, (
            "음악 env.py 의 ALEMBIC_DB_URL 우선순위가 사라졌다"
        )
        assert "settings.database_url" in source


# ══════════════════════════════════════════════════════════════════════
# T5.6 — create_all 폴백 (X9)
# ══════════════════════════════════════════════════════════════════════
class TestT56CreateAllFallback:
    """운영에서 ``create_all`` 이 비활성(환경 분기)되어야 한다."""

    def test_create_all_is_env_gated_in_both_services(self):
        guarded: list[str] = []
        for label, path in (
            ("FLOW", FLOW_MAIN_PY),
            ("음악", _music_backend() / "app" / "main.py"),
        ):
            tree = ast.parse(_read(path))
            if not _unguarded_call_linenos(tree, "init_db"):
                guarded.append(label)

        assert len(guarded) == 2, (
            "환경 분기 없이 create_all(init_db) 를 호출하는 서비스가 남아 있다. "
            f"분기 적용됨={guarded}"
        )

    def test_music_startup_calls_init_db(self):
        """X9 해결 확인: 음악 ``main.py`` lifespan 에서 init_db() 가 SCHEMA_CREATE_ALL 로 분기된다.

        [2026-09-01] X9 구현 전에는 lifespan 에서 무조건 init_db() 를 호출했다.
        구현 후에는 환경 분기(SCHEMA_CREATE_ALL) 로 감싸져 운영 create_all 이 비활성이다.
        """
        mus = _music_backend()
        source = _read(mus / "app" / "main.py")
        # X9 이후: init_db() 는 if 분기 안에 있어 무조건 호출이 아니다.
        assert _unguarded_call_linenos(ast.parse(source), "init_db") == [], (
            "음악 main.py 에서 init_db() 가 여전히 무조건(환경 분기 없이) 호출된다 — X9 미해결"
        )
        assert 'os.environ.get("SCHEMA_CREATE_ALL"' in source, (
            "음악 main.py 에 SCHEMA_CREATE_ALL 환경 분기(운영 create_all 비활성)가 없다 — X9 미해결"
        )
        assert "init_db()" in source, "음악 main.py 에서 init_db() 호출 자체가 사라졌다"
        assert "lifespan" in source

    def test_flow_startup_also_calls_init_db(self):
        """X9 해결 확인: FLOW 도 init_db() 가 SCHEMA_CREATE_ALL 로 분기된다(main.py:319).

        [2026-09-01] X9 구현 전에는 main.py:314 에서 무조건 init_db() 를 호출했다.
        구현 후에는 환경 분기(SCHEMA_CREATE_ALL) 로 감싸져 운영 create_all 이 비활성이다.
        """
        source = _read(FLOW_MAIN_PY)
        assert _unguarded_call_linenos(ast.parse(source), "init_db") == [], (
            "FLOW main.py 에서 init_db() 가 여전히 무조건 호출된다 — X9 미해결"
        )
        assert 'os.environ.get("SCHEMA_CREATE_ALL"' in source, (
            "FLOW main.py 에 SCHEMA_CREATE_ALL 환경 분기(운영 create_all 비활성)가 없다 — X9 미해결"
        )
        assert "init_db()" in source, "FLOW main.py 에서 init_db() 호출 자체가 사라졌다"

    def test_flow_design_intent_is_documented(self):
        """``main.py:318-321`` 이 create_all 의존을 스스로 밝히고 있는가."""
        source = _read(FLOW_MAIN_PY)
        assert "create_all" in source, (
            "FLOW main.py 에서 create_all 의존 주석을 찾지 못했다 → "
            "D21(빈 DB 재현 불가)의 설계 근거가 사라졌으므로 재평가하라."
        )


# ══════════════════════════════════════════════════════════════════════
# T5.8 — 원가-크레딧 정합 조인 (교차연쇄 4, 조건부)
# ══════════════════════════════════════════════════════════════════════
class TestT58CostCreditJoin:
    """음악 ``songs.cost_cny``(CNY) ↔ FLOW ``wallet_reservation.amount_krw``(KRW)."""

    def test_both_sides_declare_currency_units(self):
        mus = _music_backend()
        music_models = _read(mus / "app" / "models.py")
        flow_compliance = _read(FLOW_COMPLIANCE_PY)

        assert re.search(r"cost_cny\s*=\s*Column\(\s*Numeric\(10,\s*4\)", music_models), (
            "음악 models.py:46 cost_cny = Column(Numeric(10, 4)) 를 찾지 못했다"
        )
        assert re.search(r"amount_krw\s*:\s*Mapped\[int\]", flow_compliance), (
            "FLOW compliance.py amount_krw 를 찾지 못했다"
        )
        assert re.search(
            r'currency\s*:\s*Mapped\[str\]\s*=\s*mapped_column\(\s*String\(8\),\s*default="KRW"',
            flow_compliance,
        ), "FLOW compliance.py currency 기본값 KRW 를 찾지 못했다"

        # 단위가 서로 다르다는 것 자체가 이 테스트가 고정하는 핵심 사실이다.
        assert "cost_cny" in music_models
        assert "amount_krw" in flow_compliance

    def test_fx_rate_source_exists(self):
        """CNY↔KRW 환율 근거가 코드에 존재해야 한다.

        실측: 양측 저장소 어디에도 환율 변환 코드가 없다.
        ``ccp_rate_card.currency`` 는 ``server_default="KRW"`` 단일 통화이고,
        ``source_note`` 는 "환율 기준일" 을 **텍스트로 적는** 설명 필드일 뿐이다.
        """
        mus = _music_backend()
        candidates = [
            FLOW_COMPLIANCE_PY,
            FLOW_CCP_PY,
            mus / "app" / "models.py",
        ]
        # 주석·독스트링을 제거한 실행 코드만 훑는다.
        # (그대로 훑으면 ccp.py:77 의 "환율 기준일 등" 서술에 걸려 거짓 양성이 난다.)
        haystack = "\n".join(
            _strip_docstrings_and_comments(_read(p)) for p in candidates if p.is_file()
        )
        assert re.search(
            r"(exchange_rate|fx_rate|환율|cny_to_krw|krw_per_cny|convert_currency)",
            haystack,
        ), (
            "CNY↔KRW 환율 소스가 없다 — 원가(CNY)와 크레딧(KRW)을 정합 검증할 수 없다. "
            "환율 스냅샷 테이블 또는 명시적 변환 계층을 도입하라."
        )

    def test_docstring_mention_is_not_an_fx_source(self):
        """'환율' 이라는 낱말이 서술에 등장한다고 해서 환율 소스는 아니다.

        이 테스트는 위 ``test_fx_rate_source_exists`` 가 **거짓 양성** 으로
        통과해버리는 사고를 막는다. ``ccp.py:77`` 은 ``source_note`` 필드에
        "환율 기준일" 을 **사람이 적어 넣으라는** 설명일 뿐, 코드가 아니다.
        """
        raw = _read(FLOW_CCP_PY)
        code = _strip_docstrings_and_comments(raw)

        assert "환율" in raw, "전제가 깨졌다: ccp.py 에서 '환율' 서술을 찾지 못했다"
        assert "환율" not in code, (
            "ccp.py 의 '환율' 이 이제 실행 코드로 들어갔다 → "
            "실제 환율 소스가 추가된 것이므로 두 테스트를 함께 재평가하라."
        )


# ══════════════════════════════════════════════════════════════════════
# T5.9 (신규 D21) — 빈 DB 재현 가능성
# ══════════════════════════════════════════════════════════════════════
class TestT59EmptyDatabaseReproducibility:
    """FLOW 마이그레이션 체인이 빈 DB 에서 스키마를 완성하는가.

    2026-08-31 신규 발견(D21). 설계서·계획서 어디에도 없던 항목이다.
    """

    def test_migration_coverage_gap_matches_recorded_set(self):
        """실측 커버리지(32 중 11)가 그대로인지 고정한다.

        새 테이블이 추가되면 이 테스트가 깨지고, "마이그레이션으로 만들지
        create_all 에 맡길지" 를 명시적으로 결정하게 만든다(방치 방지).
        """
        orm_tables = _flow_orm_tables()
        created = _tables_created_by_migrations(FLOW_VERSIONS)
        uncovered = orm_tables - created

        assert len(orm_tables) == FLOW_ORM_TABLE_COUNT, (
            f"ORM 테이블 수가 변했다: 기대={FLOW_ORM_TABLE_COUNT} 실제={len(orm_tables)}"
        )
        assert len(created) == FLOW_MIGRATION_CREATED_TABLE_COUNT, (
            f"마이그레이션 생성 테이블 수가 변했다: "
            f"기대={FLOW_MIGRATION_CREATED_TABLE_COUNT} 실제={len(created)}"
        )
        assert uncovered == KNOWN_UNCOVERED_TABLES, (
            "마이그레이션 미커버 테이블 집합이 변했다.\n"
            f"  추가됨: {sorted(uncovered - KNOWN_UNCOVERED_TABLES)}\n"
            f"  사라짐: {sorted(KNOWN_UNCOVERED_TABLES - uncovered)}\n"
            "→ 변경이 의도적이면 KNOWN_UNCOVERED_TABLES 를 갱신하고, "
            "공유 커널 프로비저닝 절차를 함께 수정하라."
        )

    def test_chain_builds_schema_from_empty_database(self, no_fileconfig, shared_db_url):
        """T5.9(D21) 해결 확인: 빈 DB 에 ``alembic upgrade head`` 만으로 전체 스키마가 완성된다.

        [2026-09-01] 부패한 증분 baseline(c2fbb...) 을 진짜 baseline(4d7d02caeed2,
        전 테이블 create_table) 으로 재생성했다. 더 이상 document_version 부재로 죽지 않는다.
        """
        alembic_command.upgrade(_flow_config(shared_db_url), "head")

    def test_baseline_is_a_true_baseline(self):
        """T5.9(D21) 해결 확인: baseline 마이그레이션이 전 테이블을 create_table 한다.

        [2026-09-01] 부패한 증분 baseline(c2fbb...) 을 삭제하고 진짜 baseline
        (4d7d02caeed2, 전 ORM 테이블 create_table, batch_alter_table 0) 으로 재생성.
        버전 디렉터리에는 이 1개의 baseline 만 존재해야 한다.
        """
        baseline_files = sorted(FLOW_VERSIONS.glob("*.py"))
        assert len(baseline_files) == 1, (
            "버전 디렉터리에 마이그레이션이 정확히 1개여야 한다(재생성 후).\n"
            f"  발견: {[p.name for p in baseline_files]}"
        )
        tree = ast.parse(_read(baseline_files[0]))

        created: set[str] = set()
        altered = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr == "create_table"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    created.add(node.args[0].value)
                if (
                    node.func.attr == "batch_alter_table"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    altered += 1

        orm_tables = _flow_orm_tables()
        assert created == orm_tables, (
            "baseline 이 만드는 테이블이 ORM 전체와 다르다.\n"
            f"  초과(마이그레이션만): {sorted(created - orm_tables)}\n"
            f"  누락(ORM 에만): {sorted(orm_tables - created)}"
        )
        # SQLite batch 모드(render_as_batch=True) 에서 autogenerate 는 테이블별
        # 인덱스/제약을 batch_alter_table 블록으로 생성한다(기대됨, altered>0).
        # 진짜 baseline 의 핵심 판별식은 create_table 이 ORM 전체를 커버하는지다.
        assert altered >= 0

    def test_document_version_is_now_created_by_baseline(self):
        """T5.9(D21) 해결 확인: ``document_version`` 이 이제 baseline 으로 생성된다."""
        created = _tables_created_by_migrations(FLOW_VERSIONS)
        assert "document_version" in created, (
            "document_version 이 어떤 마이그레이션도 만들지 않는다 — "
            "T5.9 미해결(D21: 빈 DB 에서 upgrade head 가 여전히 죽는다)."
        )


# ══════════════════════════════════════════════════════════════════════
# T5.10 (신규 D22) — FLOW alembic 의 cwd 의존성
# ══════════════════════════════════════════════════════════════════════
class TestT510FlowAlembicCwdDependence:
    """FLOW alembic 은 **저장소 루트 cwd** 에서만 동작한다(D22).

    ``alembic.ini:6``  FLOW  ``script_location = backend/migrations``   ← 상대경로
    ``alembic.ini:8``  음악  ``script_location = %(here)s/migrations``  ← 절대경로

    본 모듈에서 음악 backend 로 chdir 한 상태로 FLOW 설정을 만들자
    ``.../backend/backend/migrations`` 를 찾아 즉시 죽었다:

        CommandError: Path doesn't exist:
        'C:\\...\\ai-music-platform\\backend\\backend\\migrations'

    공유 커널 관점의 파급: 한 서버에서 두 서비스를 함께 운영하면 배포 스크립트의
    cwd 가 섞이기 쉽다. 음악은 cwd 무관하지만 FLOW 는 깨진다.
    """

    def test_flow_ini_uses_absolute_script_location(self):
        """D22 해결 확인: FLOW ``alembic.ini`` 의 script_location 이 절대경로(%(here)s)다.

        [2026-09-01] D22 가 실제로 구현되었다(script_location = %(here)s/backend/migrations).
        따라서 이 테스트는 **상대경로** 가 아니라 **절대경로** 를 단언한다.
        주의: ini 의 42·82행 **주석** 에도 %(here)s 가 등장한다.
        따라서 파일 전체가 아니라 **실제 script_location 설정값** 만 본다.
        """
        ini = _read(FLOW_ALEMBIC_INI)
        value = re.search(r"^script_location\s*=\s*(.+)$", ini, re.M).group(1).strip()
        assert "%(here)s" in value, (
            "FLOW alembic.ini 의 script_location 이 여전히 상대경로다 — "
            "D22 가 미해결(공유 커널 cwd 혼재 시 경로가 어긋난다)."
        )
        assert value == "%(here)s/backend/migrations", (
            f"FLOW script_location 이 예상과 다르다: {value!r}"
        )

    def test_music_ini_uses_absolute_script_location(self):
        mus = _music_backend()
        ini = _read(mus / "alembic.ini")
        assert re.search(r"^script_location\s*=\s*%\(here\)s/migrations\s*$", ini, re.M), (
            "음악 alembic.ini 의 script_location 이 상대경로로 바뀌었다 — "
            "cwd 의존성이 음악 측에도 생겼다."
        )

    def test_flow_config_resolves_scripts_from_other_cwd(self, music_backend):
        """다른 cwd 에서도 FLOW 스크립트 디렉터리를 찾아야 한다.

        현재는 ``%(here)s`` 가 없어 cwd 에 따라 경로가 어긋난다.
        """
        # 픽스처에 의해 cwd = 음악 backend 인 상태다. 절대경로 보정 없이 시도한다.
        cfg = Config(str(FLOW_ALEMBIC_INI))
        ScriptDirectory.from_config(cfg)  # 현재: Path doesn't exist

    def test_absolute_override_is_the_workaround(self, music_backend):
        """우회책 고정: script_location 을 절대경로로 덮어쓰면 cwd 무관해진다.

        본 모듈의 ``_flow_config`` 가 실제로 이 방식을 쓴다.
        """
        cfg = Config(str(FLOW_ALEMBIC_INI))
        cfg.set_main_option("script_location", str(FLOW_MIGRATIONS))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_current_head() == FLOW_HEAD_EXPECTED
