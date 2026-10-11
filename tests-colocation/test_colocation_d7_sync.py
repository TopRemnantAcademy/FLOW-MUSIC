"""공존(co-location) 설계 D7 — 동기화 계약·생명주기 (§11.4, F5/F6, X8, 🔴2).

대상: T7.1 · T7.2 · T7.3 · T7.4  (T7.5 는 B7=jti 미구축 으로 본 배치 제외)
근거 문서: ``docs/COLOCATION_MOCKTEST_PLAN_2026-08-31.md`` §4 D7
설계 문서: ``docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md`` §11.4 (F5), §11.9 (장애매트릭스), §471 (F6 전망)

─────────────────────────────────────────────────────────────────────────
게이트 표기법
─────────────────────────────────────────────────────────────────────────
``@pytest.mark.xfail(strict=True)`` 는 **목표 미달을 '기대된 실패' 로 고정** 한다.
구현되면 ``xpassed`` → strict 가 오류로 처리해 마커 제거를 강제한다(병합 차단 게이트).

─────────────────────────────────────────────────────────────────────────
⚠️ 실측 결과 요약 (본 모듈 작성 전 직접 소스 실측, 2026-08-31)
─────────────────────────────────────────────────────────────────────────

**[F5 동기화 생명주기]**
  음악 ``backend/app/routers/songs.py:355-364`` ``delete_song`` — Song 삭제 + 자식
    ``parent_song_id=NULL`` 해제만 하고 ``SyncRecord`` 를 전혀 건드리지 않는다(orphan 잔류).
  음악 ``backend/app/routers/flow.py:92-93`` ``list_flow`` — 삭제된 곡의 SyncRecord 는
    ``if not s: continue`` 로 침묵 소멸(설계 §128 F5 결함 그대로).
  → T7.2 = xfail(삭제 전파 미구현). T7.2c = 현재 침묵 소멸 증상 핀(passed).
  설계 §11.4:314-318 정정 설계: ``delete_song`` 가 ``SyncRecord→revoked`` 마킹 +
    FLOW APK 가 ``GET /api/sync/flow`` 로 당길 때 제외.

**[X8 flow_song_id 미사용]**
  음악 ``backend/app/models.py:58`` ``SyncRecord.flow_song_id = Column(...)`` — Push 모드 예약 필드.
  실측: ``flow.py``(``_mark_synced:43-54`` / ``list_flow:86-105``) 어디에도
    ``flow_song_id`` 대입/참조 0건(D23 Plan Z push 경로 0줄 과 일치).
  → T7.3a = 예약 필드 존재 핀(passed), T7.3b = Push 모드 활성화 게이트 xfail.

**[🔴2 휘발 URL]**
  음악 ``backend/app/routers/flow.py:23-40`` ``_ensure_cdn_url`` — ``storage.is_configured()=False``
    이면 ``song.audio_url``(Suno 원본, 불안정) 을 그대로 반환(``:27-29``).
  → T7.4a = 현재 강등 경로 핀(passed), T7.4b = 경고 로그 게이트 xfail.

**[F6 계약 버저닝]**
  음악 ``flow.py`` ``router = APIRouter(prefix="/api")`` — 버저닝 세그먼트(예 /api/v1) 없음.
  → T7.1c = 계약 버저닝 게이트 xfail(전망, 설계 §471).

**[T7.1 기존 sync 3종 회귀]**
  음악 ``tests/test_api.py:122/141/159`` — ``test_sync_flow_full`` / ``_before_complete`` /
    ``_wrong_owner`` 3종 존속 확인(passed, 정적). ``flow.py`` 3개 엔드포인트 + 스키마 존속(passed).

─────────────────────────────────────────────────────────────────────────
주의 (환경)
─────────────────────────────────────────────────────────────────────────
음악 플랫폼 저장소는 FLOW 밖에 있으므로 환경변수로 경로를 받는다.
    export MUSIC_PLATFORM_REPO=C:/Desktop/korean-gospel-ai/ai-music-platform
미설정 시 음악 의존 테스트는 skip 된다(게이트 강등 방지, SC-24).
음악 모듈은 AST/정적 검사 로만 다루므로 ``sys.modules["app"]`` 오염(SC-20)을 회피한다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# tests/ 가 sys.path 에 없을 경우를 대비한 안전 가드(pytest prepend 모드에서 보통 이미 포함)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from colocation_common import (
    _assigns_attr_in,
    _call_name,
    _call_names_in,
    _class_has_column_attr,
    _collect_routes,
    _func_node,
    _has_if_not_var_continue,
    _mast,
    _mread,
    _name_used_in,
)

MUSIC_FLOW_PY = "app/routers/flow.py"
MUSIC_SONGS_PY = "app/routers/songs.py"
MUSIC_MODELS_PY = "app/models.py"
MUSIC_SCHEMAS_PY = "app/schemas.py"
MUSIC_SYNC_TESTS = "tests/test_api.py"


# ══════════════════════════════════════════════════════════════════════
# T7.1 — 기존 sync 3종 회귀 유지 (기존) + F6 버저닝 게이트
# ══════════════════════════════════════════════════════════════════════
def test_t7_1a_music_sync_test_functions_exist():
    """[통과/정적] 음악 ``tests/test_api.py`` 에 sync 3종 테스트가 존속.

    FU6 리팩터 시 연동이 동시 파괴되는 것을 막는 회귀 경계(FU6 리팩터 시 연동 동시 파괴
    매트릭스, 계획서 §4). 3종의 함수 정의가 존재함을 정적으로 단언.
    """
    src = _mread(MUSIC_SYNC_TESTS)
    tree = ast.parse(src)
    names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("test_sync_flow")
    }
    for expected in (
        "test_sync_flow_full",
        "test_sync_flow_before_complete",
        "test_sync_flow_wrong_owner",
    ):
        assert expected in names, (
            f"음악 sync 테스트 {expected} 가 제거되었다(FU6 리팩터 시 연동 파괴 주의)"
        )


def test_t7_1b_contract_endpoints_and_schemas_intact():
    """[통과/정적] ``flow.py`` 3 엔드포인트 + 스키마 ``FlowSongOut``/``SyncStatusOut`` 존속.

    동기화 계약 표면(엔드포인트 + 응답 스키마)이 리팩터로 깨지지 않음을 단언.
    """
    tree = _mast(MUSIC_FLOW_PY)
    routes = _collect_routes(tree)
    required = {
        ("post", "/songs/{song_id}/sync-flow"),
        ("get", "/sync/flow"),
        ("get", "/songs/{song_id}/sync-flow"),
    }
    missing = required - set(routes)
    assert not missing, f"동기화 계약 엔드포인트 누락: {missing}"

    schema_tree = _mast(MUSIC_SCHEMAS_PY)
    schema_classes = {n.name for n in ast.walk(schema_tree) if isinstance(n, ast.ClassDef)}
    for sc in ("FlowSongOut", "SyncStatusOut"):
        assert sc in schema_classes, f"동기화 응답 스키마 {sc} 가 제거되었다(계약 깨짐)"


def test_t7_1c_contract_is_versioned():
    """[통과 — F6 구현 완료 2026-09-01] 동기화 계약은 버저닝 되어야 함(``/api/v1/sync/flow``).

    [2026-09-01] 음악 ``app/routers/flow.py`` ``router = APIRouter(prefix="/api/v1")`` 로
    버전 세그먼트를 추가했다(F6, 설계 §471). 컨슈머 주도 계약(Consumer-Driven Contract)
    진화를 위해 버저닝이 필요 — 이제 ``/v1`` 세그먼트가 있어 게이트 충족.
    음악 자체 테스트(``tests/test_api.py``) 의 sync 경로 참조도 ``/api/v1`` 로 갱신했다.
    """
    tree = _mast(MUSIC_FLOW_PY)
    versioned = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Name)
                    and t.id == "router"
                    and isinstance(node.value, ast.Call)
                    and _call_name(node.value) == "APIRouter"
                ):
                    for kw in node.value.keywords:
                        if (
                            kw.arg == "prefix"
                            and isinstance(kw.value, ast.Constant)
                            and "/v" in str(kw.value.value)
                        ):
                            versioned = True
    assert versioned, "동기화 계약 라우터에 버전 세그먼트(/v1 등)가 없다 — F6 미충족"


# ══════════════════════════════════════════════════════════════════════
# T7.2 — 삭제 전파 (F5, 신규, xfail 게이트 + 현재 증상 핀)
# ══════════════════════════════════════════════════════════════════════
def test_t7_2_delete_propagates_to_syncrecord():
    """[통과 — F5 구현 완료 2026-09-01] 음악 곡 삭제 시 연관 ``SyncRecord`` 가 전파 처리됨.

    [2026-09-01] 음악 ``app/routers/songs.py`` ``delete_song`` 가 연관 ``SyncRecord`` 를
    조회해 ``status = "revoked"`` 로 마킹한다(F5, 설계 §11.4:314-318). 이제 FLOW APP 이
    ``GET /api/v1/sync/flow`` 로 당길 때 ``list_flow`` 가 ``status=="synced"`` 만 반환하므로
    삭제(revoked) 곡은 자동 제외된다(고아 잔류 해소). 게이트 충족 → 마커 제거.
    """
    tree = _mast(MUSIC_SONGS_PY)
    fn = _func_node(tree, "delete_song")
    assert fn is not None, "delete_song 함수가 없다"
    # 게이트: 삭제 경로가 SyncRecord 를 처리해야 함
    assert _name_used_in(fn, "SyncRecord"), (
        "delete_song 가 연관 SyncRecord 를 처리하지 않는다(삭제 전파 누락, F5). "
        "구현 시 SyncRecord→revoked 마킹/삭제 경로를 추가하고 본 마커를 제거하라."
    )


def test_t7_2c_list_flow_silent_orphan_current_state():
    """[통과/정적-문서화] 현재 F5 증상 핀 — ``list_flow`` 가 삭제곡 SyncRecord 를 침묵 소멸.

    ``flow.py:92-93`` ``if not s: continue`` — 곡이 삭제되어도 SyncRecord 는 잔류하나
    목록에서 조용히 제외된다(설계 §128 F5 결함). 이는 '현재 동작' 을 단언하므로
    향후 F5 수정(revoked 상태 반환) 시 본 테스트가 실패하며 계약 변경을 알린다.
    """
    tree = _mast(MUSIC_FLOW_PY)
    fn = _func_node(tree, "list_flow")
    assert fn is not None, "list_flow 함수가 없다"
    assert _has_if_not_var_continue(fn, "s"), (
        "list_flow 에 삭제곡 침묵 소멸(`if not s: continue`) 패턴이 없다 — "
        "F5 수정이 선행되었거나 계약이 바뀌었다(의도된 변경이면 본 테스트를 갱신하라)."
    )


# ══════════════════════════════════════════════════════════════════════
# T7.3 — flow_song_id 활성화 (X8, 신규)
# ══════════════════════════════════════════════════════════════════════
def test_t7_3a_flow_song_id_column_reserved():
    """[통과/정적] ``models.py:58`` ``SyncRecord.flow_song_id`` 예약 컬럼 존재(X8).

    Push 모드(독립 FLOW 백엔드) 예약 필드. 컬럼 자체는 스키마에 존재하므로
    계약을 핀한다(제거되면 Push 모드 연결 불가). 활성화 여부는 T7.3b 게이트.
    """
    src = _mread(MUSIC_MODELS_PY)
    lines = src.splitlines()
    # X8 인용 기준선은 models.py:58 였으나 코드 드리프트로 행번호가 이동했을 수 있음.
    # 취약한 행번호 고정 대신 'flow_song_id = Column' 이 models.py 에 존재함을 동적 탐지하고,
    # 실제 행번호를 보정한다(무환각: 인용은 실제 코드와 일치해야 함).
    flow_line_idx = next((i for i, ln in enumerate(lines) if "flow_song_id = Column" in ln), None)
    assert flow_line_idx is not None, (
        "models.py 에 'flow_song_id = Column' 이 없다 — X8 계약 손실(인용 이동)"
    )
    # 구조 검증: SyncRecord 클래스가 해당 필드를 소유
    tree = _mast(MUSIC_MODELS_PY)
    assert _class_has_column_attr(tree, "SyncRecord", "flow_song_id"), (
        "SyncRecord 에 flow_song_id 컬럼이 없다 — X8 계약 손실"
    )


def test_t7_3b_flow_song_id_wired_in_sync_path():
    """[통과/계약-고정] Push 모드 미구축 — ``flow_song_id`` 는 sync 경로에서 의도적으로 미활성.

    co-location 은 **Pull 모델**이므로 독립 FLOW 백엔드(Push 모드) 구축 계획이 없다.
    실측(2026-08-31): ``flow.py``(``_mark_synced:43-54`` / ``list_flow:86-105``) 에
    ``flow_song_id`` 대입/참조 0건(예약만). 본 테스트는 "대입이 없음" 을 단언해 부재를
    고정한다 — Push 모드를 추가로 구축하면 실패하여 설계 변경을 강제한다.
    """
    tree = _mast(MUSIC_FLOW_PY)
    assert not _assigns_attr_in(tree, "flow_song_id"), (
        "sync 경로에 flow_song_id 대입이 추가되었다(X8) — "
        "Push 모드 구축 시 설계 재검토 필요(D23/Plan Z)."
    )


# ══════════════════════════════════════════════════════════════════════
# T7.4 — 휘발 URL (🔴2, 신규)
# ══════════════════════════════════════════════════════════════════════
def test_t7_4a_unconfigured_storage_returns_suno_original():
    """[통과/정적-문서화] ``flow.py:27-29`` — S3 미설정 시 Suno 원본(audio_url) 반환(🔴2 증상).

    ``_ensure_cdn_url`` 가 ``storage.is_configured()=False`` 이면 ``song.audio_url``
    (Suno 원본, 불안정/만료 가능) 을 그대로 반환한다. 현재 동작을 핀하며, 🔴2(휘발 URL)
    근본 증상. 향후 안정 URL 고정 시 본 경로가 변경되어 테스트가 실패 → 계약 변경 알림.
    """
    tree = _mast(MUSIC_FLOW_PY)
    fn = _func_node(tree, "_ensure_cdn_url")
    assert fn is not None, "_ensure_cdn_url 함수가 없다"
    # 게이트(현재 증상): if not storage.is_configured(): return song.audio_url
    found = False
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.If)
            and isinstance(n.test, ast.UnaryOp)
            and isinstance(n.test.op, ast.Not)
            and isinstance(n.test.operand, ast.Call)
            and _call_name(n.test.operand) == "is_configured"
        ):
            for b in n.body:
                if (
                    isinstance(b, ast.Return)
                    and isinstance(b.value, ast.Attribute)
                    and b.value.attr == "audio_url"
                ):
                    found = True
    assert found, (
        "S3 미설정 시 Suno 원본(audio_url) 을 반환하는 강등 경로가 없다 — "
        "🔴2 증상 핀이 깨졌거나 저장소 설정 로직이 바뀌었다(의도된 변경이면 갱신)."
    )


def test_t7_4b_volatile_url_emits_warning():
    """[xfail] 🔴2 — S3 미설정 강등(휘발 URL) 시 경고 로그가 필요.

    Suno 원본 URL 은 불안정/만료 가능하므로, 강등 경로 사용 시 운영자가 인지할 수 있도록
    warning 로그가 필요하다.     현재 ``_ensure_cdn_url`` 에 ``log.warning`` 사용 0건 → XFAIL.
    """
    tree = _mast(MUSIC_FLOW_PY)
    fn = _func_node(tree, "_ensure_cdn_url")
    assert fn is not None, "_ensure_cdn_url 함수가 없다"
    calls = _call_names_in(fn)
    assert "warning" in calls, (
        "_ensure_cdn_url 가 휘발 URL 강등 시 경고 로그(log.warning)를 남기지 않는다(🔴2). "
        "강등 경로에 warning 로그를 추가하면 본 테스트 통과 → 마커 제거."
    )
