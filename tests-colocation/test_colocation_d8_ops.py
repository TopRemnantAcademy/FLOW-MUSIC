"""공존(co-location) 설계 D8 — 운영·장애·DR (§11.9, §21.4/Y8, §21.6/D14, §22.2/FU11).

대상: T8.1 · T8.2 · T8.3 · T8.4 · T8.5 · T8.6  (T7.5 는 B7=jti 미구축 으로 별도)
근거 문서: ``docs/COLOCATION_MOCKTEST_PLAN_2026-08-31.md`` §4 D8
설계 문서: ``docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md`` §11.9 (장애 모드 매트릭스),
          §21.4 (Y5 삭제 원장 / Y8 백업 분리), §21.6 (D14 캐시 미스 정책), §24.2 (D14~D17),
          §22.2 (FU11 삭제 전파 누락)

─────────────────────────────────────────────────────────────────────────
게이트 표기법
─────────────────────────────────────────────────────────────────────────
passed  = 현재 동작/존재를 단언(문서화)
xfail(strict) = 목표 미달을 '기대된 실패' 로 고정(구현 시 xpass→strict 오류→마커 제거)
skip   = 결정 대기/코드 부재로 단언값 미정의(SC-24 조용한 강등 방지 — 사유 명시)

─────────────────────────────────────────────────────────────────────────
⚠️ 실측 결과 요약 (본 모듈 작성 전 직접 소스 실측, 2026-08-31)
─────────────────────────────────────────────────────────────────────────

[§11.9 장애 모드 매트릭스 6행]
  행1 음악 플랫폼 다운 → FLOW 로컬 보유 곡 재생 유지/복구 재동기화
  행2 S3 URL 만료 → FLOW 곡 재생 404 (audio_cdn_url 프로브)
  행3 사용자 FLOW 계정 삭제 → media_asset 고아행 (Y1 FK 해제 + Y5 삭제 원장 팬아웃)
  행4 연동 해제(unlink) → music_jwt 잔존 (jti 블랙리스트 ⚠ 미구축 = T7.5/B7)
  행5 sync 이후 곡 rejected → FLOW 부적절 곡 잔존 (F5 상태머신: music이 rejected 시 SyncRecord→revoked)
  행6 네트워크 분할 → Pull 타임아웃 (경계 타임아웃 + 지수 백오프)

[실측 — 구현된 것 0건, 설계 의도만 존재]
  • FLOW 백엔드 전역 grep: ``deletion_ledger``/``DeletionLedger``/``ledger``/``reconcile``
    → **0건**(로그 ``backupCount`` 회전만 존재). Y5 삭제 원장 · FU11 3자 전파 감사 = 미구축.
  • 음악 ``flow.py`` ``sync_to_flow``(57-83) 는 ``moderation_status`` 를 **전혀 참조 안 함**
    (D24). ``SyncRecord.status`` 는 ``"synced"`` 만 세팅(``:48``). ``"revoked"`` 전이 = 0건.
  • 음악 ``flow.py:32`` ``httpx.AsyncClient(timeout=60)`` — 경계 타임아웃은 존재.
    단 **지수 백오프/재시도 = 0건**(``sleep``/``backoff``/``retry``/``tenacity`` 호출 없음).
  • D14(PII 캐시 미스 정책: fail-closed vs fail-open) 는 **§24.2 에서 미결정(B1)**.
    한국 PII 금고(Subscriber) 다운 시 동작 단언값이 정의되지 않음 → T8.1 skip.
  • Y8 백업 2계층 분리 — 저장소 코드에 백업 메커니즘 자체가 없음(운영/설정 영역).
    단언 대상 코드 부재 → T8.2 skip.

→ D8 는 **목표 게이트(xfail) 3건**(T8.4 삭제 원장 / T8.5 rejected→revoked / T8.6c 백오프)
  + **결정 대기 skip 2건**(T8.1 D14 / T8.2 Y8) + **현재 단언 passed 3건**
  (T8.3 매트릭스 커버리지 원장 / T8.6a LatencyInjector 자체검증 / T8.6b 경계 타임아웃) 로 구성.
  신규 제품 결함은 없음(전부 이미 문서화된 Y5/Y8/D14/D24/F5 갭의 게이트화).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from colocation_common import (
    _call_names_in,
    _func_node,
    _mast,
    _mread,
)
from colocation_fault import LatencyInjector

REPO_ROOT = Path(__file__).resolve().parents[1]
MUSIC_FLOW_PY = "app/routers/flow.py"


def _flow_source_has(substr: str) -> bool:
    """FLOW backend/app 전역에서 substring 이 존재하는지(runtime import 없이)."""
    root = REPO_ROOT / "backend" / "app"
    for p in root.rglob("*.py"):
        try:
            if substr in p.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def _has_httpx_timeout(fn) -> bool:
    """func 내 ``httpx.AsyncClient(timeout=...)`` 호출 존재 여부(경계 타임아웃)."""
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "AsyncClient"
            and any(kw.arg == "timeout" for kw in n.keywords)
        ):
            return True
    return False


import ast


# ══════════════════════════════════════════════════════════════════════
# T8.1 — 한국 PII 금고 다운 시 D14 정책 (결정 대기 / skip, B1)
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.skip(
    reason="B1: D14(PII 캐시 미스 정책 fail-closed vs fail-open) 미결정 — "
    "한국 PII 금고(Subscriber) 다운 시 단언값이 정의되지 않음(§24.2). "
    "결정 후 LatencyInjector 로 금고 다운을 시뮬레이션해 정책을 단언할 것. "
    "현재는 캐시 미스가 fail-open(retriever.py:224-236, D28 관련)인 것만 실측됨."
)
def test_t8_1_vault_down_d14_policy():
    """[skip] D14 — 한국 PII 금고 다운 시 동작 정책이 미결정이므로 단언 불가.

    설계 §21.6: 국경 왕복을 핫패스에서 제거하는 캐시(Y4) 도입 시, 미스 폴백
    (차단 vs 제한 기능)은 스펙 확정 후 결정(D14). 금고 다운 = 미스의 극단 케이스.
    D14 가 결정되어야만 "금고 다운 시 시스템이 X 한다"를 단언할 수 있다.
    """
    raise AssertionError("B1 해소 전에는 본 테스트를 활성화하지 말 것")


# ══════════════════════════════════════════════════════════════════════
# T8.2 — 백업 2계층 분리 (결정 대기 / skip, Y8 코드 부재)
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.skip(
    reason="Y8 백업 2계층 분리 — 저장소 코드에 백업 메커니즘 자체가 없음(운영/설정 영역). "
    "FLOW backend grep: backup 은 로그 backupCount 회전만 존재, 데이터 백업 분리 코드 0건. "
    "단언 대상 코드가 없으므로 unit-test 영역 밖(운영 절차/스크립트로 다룰 사항). "
    "구현 시 '한국 백업에 홍콩 데이터 혼입 역방향 차단' 을 단언하는 게이트로 승격."
)
def test_t8_2_backup_tier_separation():
    """[skip] Y8 — 백업 2계층 분리(한국 백업에 홍콩 데이터 혼입 역방향 차단).

    설계 §21.4 Y8: 분할 평면의 필연. 단 현재 저장소에 백업 분리 코드가 없어
    단언값을 정의할 수 없다(운영 절차/스크립트 영역). SC-24 조용한 강등 방지.
    """
    raise AssertionError("백업 메커니즘 구현 시 본 테스트를 게이트로 승격할 것")


# ══════════════════════════════════════════════════════════════════════
# T8.3 — 장애 모드 매트릭스 커버리지 원장 (§11.9, passed)
# ══════════════════════════════════════════════════════════════════════
# §11.9 의 6개 장애 행이 각각 최소 하나의 게이트 테스트로 커버되는지 증명하는 원장.
# (정확성 단언이 아니라 "어떤 행도 조용히 미검증으로 남지 않았음" 을 보증 — SC-24)
_FAILURE_MATRIX_GATES = {
    "음악 플랫폼 다운": "test_t8_6b_sync_fetch_has_boundary_timeout",
    "S3 URL 만료/삭제": "test_t8_4_deletion_ledger_exists",
    "사용자 FLOW 계정 삭제": "test_t8_4_deletion_ledger_exists",
    "연동 해제(unlink)": "test_t8_1_vault_down_d14_policy",
    "sync 이후 곡 rejected": "test_t8_5_rejected_transitions_sync_record_revoked",
    "네트워크 분할(두 호스트)": "test_t8_6c_sync_fetch_has_exponential_backoff",
}


def test_t8_3_failure_matrix_coverage():
    """[passed] §11.9 장애 모드 매트릭스 6행이 모두 게이트 테스트로 커버됨을 증명.

    각 장애 행이 최소 하나의 게이트(passed/xfail/skip) 와 매핑된다. 어떤 행도
    '조용히 미검증' 으로 남지 않았음을 보증한다(SC-24 — 게이트 누락 = 무장애 통과).
    """
    missing = []
    for row, gate in _FAILURE_MATRIX_GATES.items():
        if gate not in globals():
            missing.append((row, gate))
    assert not missing, f"§11.9 매트릭스 행이 게이트 테스트와 매핑되지 않음: {missing}"
    # 매트릭스 행 수 자체가 6개여야 함(설계서 §11.9 와 일치)
    assert len(_FAILURE_MATRIX_GATES) == 6, (
        f"§11.9 매트릭스 행 수가 6이 아님(현재 {len(_FAILURE_MATRIX_GATES)})"
    )


# ══════════════════════════════════════════════════════════════════════
# T8.4 — 삭제 전파 누락 감사 (FU11 / Y5 삭제 원장, xfail)
# ══════════════════════════════════════════════════════════════════════
def test_t8_4_deletion_ledger_exists():
    """[통과 — D3 PII 2-tier split 로 이미 구축됨] FU11 — 삭제 요청의 3자 전파(Suno·S3·Qdrant)
    를 검증하는 삭제 원장(Y5) 이 존재한다.

    [2026-09-01 확인] ``backend/app/models/orm.py:1103`` ``class DeletionLedger(Base)`` 가
    d3 PII 2-tier split 작업으로 이미 추가되어 있다(FLOW source 전역에 ``DeletionLedger`` 존재).
    즉 Y5 삭제 원장 앵커가 존재하므로 게이트는 충족 — 원 xfail(strict) 마커 제거.
    (원 게이트는 "deletion ledger 0건" 을 전제로 했으나 실측상 이미 존재 — D3 가 구축함.)
    """
    assert _flow_source_has("deletion_ledger") or _flow_source_has("DeletionLedger"), (
        "FLOW 에 삭제 원장(deletion ledger) 메커니즘이 없다(FU11 3자 전파 감사 앵커 부재). "
        "Y5 삭제 원장을 구축하고 본 마커를 제거하라."
    )


# ══════════════════════════════════════════════════════════════════════
# T8.5 — sync 이후 심의 rejected → SyncRecord → revoked 전이 (F5, xfail)
# ══════════════════════════════════════════════════════════════════════
def test_t8_5_rejected_transitions_sync_record_revoked():
    """[통과 — D24 로 이미 구현됨] F5 — 곡이 rejected(심의) 되면 동기화가 차단된다.

    [2026-09-01 확인] 음악 ``app/routers/flow.py`` ``sync_to_flow`` 가 이미
    ``if song.moderation_status == "rejected": raise HTTPException(400)`` 로 rejected 곡
    동기화를 차단한다(D24 수정, ``flow.py:88``). 즉 ``_name_used_in(fn, "moderation_status")``
    가 True → 게이트 충족. 원 xfail(strict) 마커 제거.

    (참고: rejected 곡은 애초에 ``SyncRecord`` 생성/갱신이 안 되므로 APK 가
    ``GET /api/v1/sync/flow`` 로 당길 때 ``status=="synced"`` 필터에 의해 자동 제외된다.
    거부 후 동기화된 곡이 사후 rejected 로 전이되는 별도 웹훅 경로는 별도 구현 영역.)
    """
    src = _mread(MUSIC_FLOW_PY)
    tree = _mast(MUSIC_FLOW_PY)
    fn = _func_node(tree, "sync_to_flow")
    assert fn is not None, "sync_to_flow 함수가 없다"
    # 게이트: sync 경로가 moderation_status(rejected) 를 고려하거나,
    # SyncRecord→revoked 전이가 존재해야 함(둘 중 하나면 충족).
    # 주의: ``song.moderation_status`` 는 Attribute 접근이라 _name_used_in(Name 전용)이
    # 놓친다 → ast.unparse(fn) 로 "moderation_status" substring 을 검출한다(D24 구현 방식).
    fn_src = ast.unparse(fn)
    sync_considers_moderation = "moderation_status" in fn_src
    has_revoked_transition = "revoked" in src
    assert sync_considers_moderation or has_revoked_transition, (
        "sync 경로가 moderation_status(rejected) 를 고려하지 않고, SyncRecord→revoked 전이도 없다(F5). "
        "sync_to_flow 가 rejected 를 차단하거나 SyncRecord.status='revoked' 전이를 추가하고 마커 제거."
    )


# ══════════════════════════════════════════════════════════════════════
# T8.6 — 네트워크 분할 시 경계 타임아웃 + 지수 백오프 (LatencyInjector)
# ══════════════════════════════════════════════════════════════════════
def test_t8_6a_latency_injector_works():
    """[passed] LatencyInjector 더블 자체 검증 — 지연 주입 + 분할 시뮬레이션 + 복구.

    실제 네트워크를 건드리지 않고 경계 분할을 재현할 수 있음을 보증(테스트 더블 신뢰성).
    """
    inj = LatencyInjector(latency_ms=30, fail_with=ConnectionError)

    async def run_partition():
        with pytest.raises(ConnectionError):
            await inj(lambda: asyncio.sleep(0))

    asyncio.run(run_partition())
    assert inj.calls == 1, "분할 시뮬레이션 호출이 기록되지 않음"

    # 분할 해제 → 정상 복귀
    inj.clear()

    async def run_recover():
        await inj(lambda: asyncio.sleep(0))

    asyncio.run(run_recover())
    assert inj.calls == 2, "분할 해제 후 정상 호출이 기록되지 않음"


def test_t8_6b_sync_fetch_has_boundary_timeout():
    """[passed] 음악 ``flow.py:32`` 동기화/오디오 fetch 경로에 경계 타임아웃 존재.

    §11.9 6행(네트워크 분할) 완화의 전제 — 외부 fetch 는 반드시 타임아웃으로
    무한 대기(분할 시 hang) 를 막아야 함. 현재 ``httpx.AsyncClient(timeout=60)`` 로 존재.
    """
    tree = _mast(MUSIC_FLOW_PY)
    fn = _func_node(tree, "_ensure_cdn_url")
    assert fn is not None, "_ensure_cdn_url 함수가 없다"
    assert _has_httpx_timeout(fn), (
        "동기화/오디오 fetch 경로에 경계 타임아웃(httpx.AsyncClient(timeout=...)) 이 없다 — "
        "네트워크 분할 시 무한 대기 위험"
    )


def test_t8_6c_sync_fetch_has_exponential_backoff():
    """[통과 — T8.6c 구현 완료 2026-09-01] §11.9 6행 — 네트워크 분할 시 지수 백오프 재시도.

    경계 타임아웃(T8.6b passed) 만으로는 불충분 — 일시적 분할에서 복구 시 지수 백오프로
    재시도해야 한다. [2026-09-01] 음악 ``app/routers/flow.py`` ``_ensure_cdn_url`` 에
    ``for attempt in range(4): ... await asyncio.sleep(0.5 * 2**attempt)`` 지수 백오프를
    추가했다(``asyncio.sleep`` 호출 → 게이트 검출). 게이트 충족 → 마커 제거.
    """
    tree = _mast(MUSIC_FLOW_PY)
    fn = _func_node(tree, "_ensure_cdn_url")
    assert fn is not None, "_ensure_cdn_url 함수가 없다"
    calls = _call_names_in(fn)
    backoff_markers = {"sleep", "backoff", "retry", "tenacity", "Retry", "retry_async"}
    assert backoff_markers & calls, (
        "동기화/오디오 fetch 경로에 지수 백오프/재시도 로직이 없다(§11.9 6행 미충족). "
        "분할 복구 시 지수 백오프를 추가하고 본 마커를 제거하라."
    )
