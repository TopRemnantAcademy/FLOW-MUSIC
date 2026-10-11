"""D1/D2 — Plan Z (음악 → FLOW 미디어 커널 푸시) 계약 검증.

═══════════════════════════════════════════════════════════════════════════
이 모듈의 모든 수치는 2026-08-31 실측이다. 추정치가 단 하나도 없음을 보장한다.
═══════════════════════════════════════════════════════════════════════════

─────────────────────────────────────────────────────────────────────────
[실측 1] FLOW 수신 계약 — POST /admin/media
─────────────────────────────────────────────────────────────────────────
    라우트   ``api/media.py:28``  admin_router = APIRouter(prefix="/admin/media")
    등록     ``main.py:364-365``  media.admin_router / media.public_router
    핸들러   ``api/media.py:61-74``  multipart form 필드:
                 file(필수) title(필수) category(기본 worship)
                 artist description scripture_refs order_index(0)
                 active(True) publish_at(None) notify_mode("immediate")
             + ``authorization: str | None = Header(default=None)``
    인증     ``api/media.py:80``   ``_check_admin(authorization)``
    크기     ``api/media.py:83-88``  max_upload_size_mb(기본 100, ``config.py:249``) → 413
    반환     ``api/media.py:100-112`` → ``media_service.save_upload`` dict
             (``asset_id`` 포함, ``media_service.py:272-291``)

─────────────────────────────────────────────────────────────────────────
[실측 2] asset_id 규격 (T1.1 의 접합부)
─────────────────────────────────────────────────────────────────────────
    FLOW   ``models/orm.py:600``  asset_id = String(40), PK, default=_uuid
           ``models/orm.py:39-40`` _uuid() = str(uuid.uuid4()) → **36자**
    음악   ``models.py:58``       flow_song_id = String(255), default=""
    → 36 ≤ 255 이므로 **규격은 호환된다** (T1.1a 통과).

─────────────────────────────────────────────────────────────────────────
[신규 발견 D23] Plan Z 의 push 경로는 **존재하지 않는다**
─────────────────────────────────────────────────────────────────────────
    음악 저장소 전체(``.py``) grep 결과:
        ``/admin/media``   → **0건** (호출 자체가 없다)
        ``flow_song_id``   → **2건** (``models.py:58`` 정의 + 마이그레이션 1건)
                             대입/갱신 코드는 **0건**
    음악 ``routers/flow.py:1-8`` 독스트링이 스스로 밝힌다:
        "MVP 采用 Pull:本接口负责「准备」… APK 端用同一 JWT 调
         GET /api/sync/flow 拉取自己的歌单"
    → 즉 현재 음악 플랫폼은 **Pull 모델**이고, Plan Z 의 push 는
      ``models.py:58`` 의 주석 "Push 模式(独立 FLOW 后端)预留" 대로
      **예약 필드로만 남아 있다.**
    → T1.1 은 "양쪽 규격이 맞는가"까지만 단언하고(T1.1a),
      "실제로 저장되는가"는 xfail(strict) 목표 게이트(T1.1b)로 분리한다.

─────────────────────────────────────────────────────────────────────────
[신규 발견 D24] 음악 sync_to_flow 는 심의 상태를 **보지 않는다**
─────────────────────────────────────────────────────────────────────────
    ``routers/flow.py:64-68``
        song = db.get(Song, song_id)
        if not song or song.user_id != user.id: 404
        if song.status != "completed": 400
        (moderation_status 검사 **없음**)
    반면 심의 결과는 status 와 무관하게 별도 필드다:
        ``routers/songs.py:164``  기심사 탈락 → moderation_status = "rejected"
        ``routers/admin.py:127``  관리자 수동 reject → moderation_status = "rejected"
                                  (**song.status 는 건드리지 않는다**)
    → 즉 **rejected 곡도 status="completed" 이므로 그대로 동기화된다.**
    ``routers/songs.py:294`` 가 explore(발견 페이지)는 approved 만 노출하는 것과
    대조적이다 — 동기화 경로에만 동일한 필터가 없다.

─────────────────────────────────────────────────────────────────────────
[신규 발견 D25] FLOW: **미발행 오디오가 무인증으로 스트리밍된다**
─────────────────────────────────────────────────────────────────────────
    공개 스트림 ``api/media.py:178-185``
        def stream_media(asset_id):        # ← 인증 파라미터 자체가 없다
            result = media_service.get_file(asset_id)
    ``media_service.py:226-234`` get_file 은 **asset_id 단일 조회**이며
    ``active`` 를 필터하지 않는다(``list_public`` 의 ``:201-202`` 와 대조).

    실측 (2026-08-31, TestClient):
        publish_at=+3일 로 업로드 → active=False, 카탈로그 미노출 확인
        GET /mobile/media/{asset_id}/stream  (헤더 없음)
            → **200 OK, 2055 bytes 전문 수신**
    → 예약 발행 전(미발행) 오디오가 asset_id 만 알면 누구나 내려받을 수 있다.
      Plan Z 와 무관하게 **현재 이미 성립하는 노출**이다.

─────────────────────────────────────────────────────────────────────────
[실측 3] 카테고리·확장자 화이트리스트
─────────────────────────────────────────────────────────────────────────
    ``media_service.py:95-96``  category ∈ {"worship","sermon","ai_music"} 아니면 ValueError → 400
    ``media_service.py:24``     _ALLOWED_EXTS = .mp3 .wav .m4a .ogg .aac .flac
    → **[2026-09-01] D1/T1.2b 구현**: "ai_music" 카테고리가 허용목록에 추가되어
      Plan Z 의 "ai_music 카탈로그 노출"(T1.2)이 가능해졌다. external_ref(멱등 키)도
      ``MediaAsset``(orm.py) 에 신규 컬럼으로 추가됨(T1.6).

─────────────────────────────────────────────────────────────────────────
[실측 4] 예약 발행 · 알림 (T1.4 / T1.5)
─────────────────────────────────────────────────────────────────────────
    ``media_service.py:114-115``  publish_at > now → active 강제 False
    ``media_service.py:118-133``  notify_at 결정표
    ``media_service.py:132-133``  순수 드래프트(비활성+예약없음) → notify_at None
    ``media_service.py:60-71``    _next_kst_evening — KST ``config.py:271`` 기본 21시
    ``media_publisher.py:32-50``  _publish_due → active=True 플립
    ``media_publisher.py:53-103`` _notify_due — notified_at None + active True 만
                                  발송 후 notified_at 기록(중복 방지, ``:84-95``)
    ``media_publisher.py:119-139`` start/stop_scheduler (60초 interval, ``:124-129``)
    ``main.py:199-201`` 등록 / ``main.py:234-236`` 해제

─────────────────────────────────────────────────────────────────────────
[실측 5] 멱등성 키는 **없다** (T1.6)
─────────────────────────────────────────────────────────────────────────
    ``external_ref`` — FLOW 저장소 전체 grep **0건**.
    ``MediaAsset`` (``orm.py:590-623``) 에 외부 참조 유니크 컬럼이 없다.
    → 동일 곡 재푸시 시 중복 레코드가 그대로 생성된다(T1.6a 로 실증).

─────────────────────────────────────────────────────────────────────────
[신규 발견 D26] media_publisher 는 **apscheduler 미설치 시 조용히 죽는다**
─────────────────────────────────────────────────────────────────────────
    의존 선언 상태 (2026-08-31 실측):
        ``requirements.txt:53-56`` / ``requirements-313-local.txt:55-58``
            # ----- Push notifications (선택 — APK 푸시 알림 활성화 시 설치) -----
            # apscheduler==3.10.4  # 매일 말씀 발송 스케줄러   ← **주석 처리**
        ``requirements-deploy.txt``  → 없음
        ``Dockerfile`` / ``Dockerfile.ui`` / ``docker-compose.*.yml`` → 없음
    사용처: ``media_publisher.py:20-21``, ``push_scheduler.py`` (**2개 파일**)
    실측: 테스트 venv(``venv313``) 및 ``venv`` 모두 ``ModuleNotFoundError``.

    왜 심각한가 — media_publisher 는 "푸시"만 하는 모듈이 아니다:
        ``media_publisher.py:32-50``  _publish_due  → **예약 발행 활성화 플립**
        ``media_publisher.py:53-103`` _notify_due   → 푸시 발송
    즉 apscheduler 가 없으면 **예약 발행(publish_at) 자체가 영구 미동작**이다.

    왜 조용한가 — ``main.py:198-203``:
        try:
            from .services.media_publisher import start_scheduler as _start_media_pub
            _start_media_pub()
        except Exception as _mpub_err:
            log.warning("[STARTUP] 미디어 발행 스케줄러 시작 실패 (무시): ...")
    → ImportError 조차 **경고 로그로만 남고 부팅은 성공**한다.

    결합 리스크 (D25 x D26):
        운영자가 "예약 발행" 했다고 믿은 오디오는
          · 스케줄러가 없어 **카탈로그에 영구 미노출**(운영자는 발행됐다고 오해)
          · 그러나 D25 로 인해 **이미 무인증 스트리밍 가능**
        → "안 보이지만 누구나 내려받을 수 있는" 상태가 기본 구성으로 성립한다.

─────────────────────────────────────────────────────────────────────────
테스트 하네스 주의 (SC-20 / SC-21 재발 방지)
─────────────────────────────────────────────────────────────────────────
1. ``media_service.MEDIA_DIR`` 은 **모듈 임포트 시점 상수**다
   (``media_service.py:21`` = ``settings.root_dir / "data" / "media"``).
   settings 를 패치해도 바뀌지 않으므로 **속성 자체를 tmp 로 교체**해야 한다.
   교체하지 않으면 실제 ``data/media/`` 에 파일이 써진다.

2. 위 1 이 놓치기 쉬운 정적 오염이므로, 본 모듈은 **autouse 가드 픽스처**로
   모든 테스트 전후에 실제 ``data/media`` 스냅샷을 대조한다.

3. ``media_service`` 는 모듈 레벨에서 ``from ..db import get_session`` 한다
   (``media_service.py:16``). ``get_session`` 은 호출 시점에 ``SessionLocal()``
   전역을 참조하므로(``db.py:136-138``) ``iso_db`` 의 전역 패치가 적용된다.

4. ``TestClient(app)`` 는 컨텍스트 매니저로 쓰지 않으면 **lifespan 을 실행하지
   않는다** → 스케줄러가 기동되지 않아 테스트가 격리된다.
   (기존 ``tests/test_admin_auth.py:21`` 과 동일한 패턴 재사용)

5. 음악 저장소는 환경변수 ``MUSIC_PLATFORM_REPO`` 로 받는다(없으면 skip).
"""

from __future__ import annotations

import ast
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

FLOW_MAIN_PY = REPO_ROOT / "backend" / "app" / "main.py"
FLOW_ORM_PY = REPO_ROOT / "backend" / "app" / "models" / "orm.py"
FLOW_MEDIA_API = REPO_ROOT / "backend" / "app" / "api" / "media.py"
FLOW_MEDIA_SERVICE = REPO_ROOT / "backend" / "app" / "services" / "media_service.py"
FLOW_MEDIA_PUBLISHER = REPO_ROOT / "backend" / "app" / "services" / "media_publisher.py"

# ── 실측 상수 (값이 바뀌면 테스트가 실패하도록 고정) ──────────────────────
ADMIN_KEY_SENTINEL = "d1d2-probe-key"  # 실제 운영 키가 아님 (테스트 전용)

FLOW_ASSET_ID_COLUMN_LENGTH = 40  # orm.py:600  String(40)
FLOW_ASSET_ID_ACTUAL_LENGTH = 36  # orm.py:39-40  str(uuid.uuid4())
MUSIC_FLOW_SONG_ID_COLUMN_LENGTH = 255  # 음악 models.py:58  String(255)

ALLOWED_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac")  # media_service.py:24
ALLOWED_CATEGORIES = ("worship", "sermon", "ai_music")  # media_service.py:95 (D1/T1.2b)
MIME_MP3 = "audio/mpeg"  # media_service.py:26

MEDIA_NOTIFY_EVENING_HOUR_KST = 21  # config.py:271
SCHEDULER_JOB_ID = "media_publisher_tick"  # media_publisher.py:127
SCHEDULER_INTERVAL_SECONDS = 60  # media_publisher.py:126

# 최소 mp3 바이트 (확장자만 검사하므로 내용은 무의미 — 크기만 실측에 영향)
_MP3_BYTES = b"ID3\x03\x00\x00\x00" + b"\x00" * 2048  # 2055 bytes


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


def _music_app_dir() -> Path:
    return _music_backend() / "app"


def _iter_py(root: Path):
    """root 하위 모든 .py 경로 (``__pycache__`` 제외)."""
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


# ── apscheduler 가용성 (D26) ────────────────────────────────────────────
# 실측(2026-08-31): venv313 / venv 모두 미설치. requirements 에는 **주석 처리**
# (requirements.txt:55) 되어 있어 "선택 의존" 으로 취급된다.
try:  # pragma: no cover - 환경 의존
    import apscheduler  # noqa: F401

    HAS_APSCHEDULER = True
except ImportError:  # pragma: no cover
    HAS_APSCHEDULER = False

_MP_MODULE_KEY = "backend.app.services.media_publisher"
_STUB_MODULE_KEYS = (
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.schedulers.background",
    "apscheduler.triggers",
    "apscheduler.triggers.interval",
)


def _install_apscheduler_stub(monkeypatch) -> None:
    """외부 의존(apscheduler) 테스트 더블 주입 (계획서 §0-P4).

    D26 으로 apscheduler 가 선택 의존이라, 실제 스케줄러 엔진은 스텁으로 대체하고
    **검증 대상인 FLOW 로직(``_publish_due`` / ``_notify_due``) 은 실제 코드** 를
    그대로 사용한다. 스텁은 최소 표면만 제공한다:
        BackgroundScheduler(timezone=) → add_job/start/shutdown/get_job
        IntervalTrigger(seconds=)      → .interval (timedelta)

    환경을 오염시키지 않도록 ``sys.modules`` 를 저장·복원한다(SC-20 교훈).
    """
    import sys
    import types
    from datetime import timedelta

    saved = {k: sys.modules.get(k) for k in _STUB_MODULE_KEYS}
    saved[_MP_MODULE_KEY] = sys.modules.get(_MP_MODULE_KEY)

    class IntervalTrigger:
        def __init__(self, seconds: int = 0, **_kw):
            self.interval = timedelta(seconds=seconds)

    class _Job:
        def __init__(self, fn, trigger, job_id):
            self.func = fn
            self.trigger = trigger
            self.id = job_id

    class BackgroundScheduler:
        def __init__(self, timezone=None, **_kw):
            self.timezone = timezone
            self._jobs: dict[str, _Job] = {}
            self.running = False

        def add_job(self, fn, trigger=None, id=None, replace_existing=False, **_kw):
            self._jobs[id] = _Job(fn, trigger, id)
            return self._jobs[id]

        def get_job(self, job_id):
            return self._jobs.get(job_id)

        def start(self):
            self.running = True

        def shutdown(self, wait=False):
            self.running = False
            self._jobs.clear()

    def _mod(name: str, **attrs) -> types.ModuleType:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    pkg = _mod("apscheduler")
    sched_pkg = _mod("apscheduler.schedulers")
    bg = _mod("apscheduler.schedulers.background", BackgroundScheduler=BackgroundScheduler)
    trig_pkg = _mod("apscheduler.triggers")
    interval = _mod("apscheduler.triggers.interval", IntervalTrigger=IntervalTrigger)

    pkg.schedulers = sched_pkg
    pkg.triggers = trig_pkg
    sched_pkg.background = bg
    trig_pkg.interval = interval

    for key, module in (
        ("apscheduler", pkg),
        ("apscheduler.schedulers", sched_pkg),
        ("apscheduler.schedulers.background", bg),
        ("apscheduler.triggers", trig_pkg),
        ("apscheduler.triggers.interval", interval),
    ):
        monkeypatch.setitem(sys.modules, key, module)

    # 스텁에 묶인 media_publisher 모듈이 캐시되지 않도록 정리
    sys.modules.pop(_MP_MODULE_KEY, None)


# ══════════════════════════════════════════════════════════════════════
# 픽스처
# ══════════════════════════════════════════════════════════════════════
def _real_media_dir() -> Path:
    from backend.app.config import settings

    return settings.root_dir / "data" / "media"


@pytest.fixture(autouse=True)
def _rate_limit_isolated(monkeypatch):
    """[하네스 무결성] 전역 속도 제한 상태를 격리한다 (SC-23).

    ── 실측한 전역 오염 경로 ─────────────────────────────────────────────
    ``middleware/rate_limit.py``
        :20  _RATE_PREFIXES = ("/chat","/auth","/rag","/admin","/documents","/support")
        :22  _buckets: dict        ← **모듈 전역**
        :23  _blocked_ips: dict    ← **모듈 전역**
        :185-186  분당 60회 초과 시 ``_blocked_ips[ip] = now + 15분``
        :172 (config)  MAX_IP_RPM = 60

    ``TestClient`` 의 클라이언트 IP 는 항상 ``"testclient"`` 단일 값이므로,
    본 모듈(72 tests x 수 회 = 200+ 회의 ``/admin/media`` 호출)이
    **이후 모든 ``/admin*`` · ``/support*`` 테스트를 15분간 429 로 죽였다.**

    실측 피해(2026-08-31, 전체 회귀): 본 모듈 추가 후
    ``test_content_admin_api.py``(9건) · ``test_support_api.py``(6건) ·
    ``test_subscriber_pagination.py``(3건) 전부 ``assert 429 == 20x``.
    본 모듈을 제외한 동일 조건 실행은 **전부 통과**.

    ── 대응 ─────────────────────────────────────────────────────────────
    속도 제한은 본 모듈의 검증 대상이 **아니다**(``tests/test_rate_limit.py`` 담당).
    따라서 (a) 비활성화로 내 요청이 차단되지 않게 하고,
           (b) teardown 에서 전역을 비워 **뒤따르는 파일을 오염시키지 않게** 한다.
    사전 상태는 복원한다(다른 파일이 의도적으로 만든 상태를 파괴하지 않음).
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
        # 내 테스트가 만든 어떤 차단도 남기지 않는다
        rl._buckets.clear()
        rl._blocked_ips.clear()
        # 사전 상태 복원(없었다면 빈 상태로 유지)
        rl._buckets.update(saved_buckets)
        rl._blocked_ips.update(saved_blocked)


@pytest.fixture(autouse=True)
def _media_dir_guard():
    """[하네스 무결성] 실제 ``data/media`` 가 이 모듈 때문에 오염되지 않음을 보장.

    모든 테스트 전후로 재귀 스냅샷을 대조한다. 테스트 실행 순서와 무관하게
    적용되도록 autouse 로 걸었다(정렬 의존 제거).
    """
    real = _real_media_dir()
    before = _snapshot_tree(real)
    yield
    after = _snapshot_tree(real)
    assert before == after, (
        f"실제 미디어 디렉터리가 변경되었다: {real}\n"
        f"  추가: {sorted(set(after) - set(before))}\n"
        f"  삭제: {sorted(set(before) - set(after))}\n"
        "→ media_service.MEDIA_DIR 이 tmp 로 교체되지 않았다."
    )


def _snapshot_tree(root: Path) -> frozenset[tuple[str, int]]:
    if not root.exists():
        return frozenset()
    return frozenset(
        (str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file()
    )


@pytest.fixture
def media_env(tmp_path, monkeypatch, iso_db):
    """격리된 미디어 커널 환경.

    - ``media_service.MEDIA_DIR`` → tmp (모듈 임포트 시점 상수라 속성 교체 필요)
    - ``settings.admin_api_key``  → 센티넬(운영 키가 아님)
    - DB                          → ``iso_db``(기존 자산 재사용)

    반환: ``{"client": TestClient, "media_dir": Path, "key": str}``
    """
    from fastapi.testclient import TestClient

    from backend.app.config import settings
    from backend.app.main import app
    from backend.app.services import media_service

    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(media_service, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(settings, "admin_api_key", ADMIN_KEY_SENTINEL)

    # 컨텍스트 매니저로 쓰지 않음 → lifespan 미실행 → 스케줄러 미기동 (격리)
    client = TestClient(app)

    return {
        "client": client,
        "media_dir": media_dir,
        "key": ADMIN_KEY_SENTINEL,
        "session": iso_db,
    }


@pytest.fixture
def mp_module(monkeypatch):
    """``backend.app.services.media_publisher`` — apscheduler 를 스텁으로 대체해 임포트.

    D26: apscheduler 는 **선택 의존**(``requirements.txt:55`` 주석)이라 없으면
    모듈 임포트 자체가 실패한다. 여기서는 스케줄러 엔진만 스텁으로 대체하고
    **검증 대상인 ``_publish_due`` / ``_notify_due`` 는 실제 FLOW 코드** 를 쓴다.

    teardown 에서 스텁과 ``media_publisher`` 모듈을 ``sys.modules`` 에서 걷어내
    다른 테스트에 스텁이 묶인 모듈이 캐시되지 않게 한다(SC-20).
    """
    import importlib
    import sys

    if not HAS_APSCHEDULER:
        _install_apscheduler_stub(monkeypatch)
    try:
        yield importlib.import_module(_MP_MODULE_KEY)
    finally:
        sys.modules.pop(_MP_MODULE_KEY, None)


def _insert_media_asset(session_factory, media_dir: Path, **fields) -> str:
    """MediaAsset 을 **커밋까지** 보장해 삽입하고 asset_id 를 반환.

    ── 함정 (SC-22) ──────────────────────────────────────────────────────
    ``with sessionmaker()() as s:`` 는 SQLAlchemy 에서 **commit 이 아니라
    close(→ 미커밋 트랜잭션 롤백)** 이다. ``iso_db`` 는 ``get_session`` 이
    아닌 ``sessionmaker`` 를 주므로, 직접 삽입할 때 ``s.commit()`` 을
    명시하지 않으면 행이 **저장되지 않는다**.

    이 실수는 "대상이 없어서 count==0" 을 만들어 **거짓 통과** 를 낳는다
    (T1.5d 가 실제로 그렇게 통과했다). 그래서 삽입 후 재조회로 존재를
    먼저 단언하고, 호출부에서도 사전 조건을 검증한다.
    """
    from backend.app.models.orm import MediaAsset

    defaults = {
        "category": "worship",
        "mime_type": MIME_MP3,
        "size_bytes": len(_MP3_BYTES),
        "file_name": "x.mp3",
    }
    defaults.update(fields)
    if "storage_path" not in defaults:
        defaults["storage_path"] = str(media_dir / defaults["category"] / defaults["file_name"])

    with session_factory() as s:
        a = MediaAsset(**defaults)
        s.add(a)
        s.flush()
        asset_id = a.asset_id
        s.commit()  # ← 필수: with 블록 종료는 close(롤백) 이다

    # 삽입 검증 — 여기서 못 찾으면 이후 단언은 전부 무의미하다
    with session_factory() as s:
        assert s.query(MediaAsset).filter_by(asset_id=asset_id).first() is not None, (
            f"삽입 검증 실패 — 행이 커밋되지 않았다: {asset_id}"
        )
    return asset_id


def _upload(
    client,
    *,
    title="곡",
    category="worship",
    filename="a.mp3",
    data: bytes = _MP3_BYTES,
    key: str = ADMIN_KEY_SENTINEL,
    **extra,
):
    """``POST /admin/media`` 헬퍼 (``api/media.py:61-74`` 계약)."""
    form = {"title": title, "category": category, **extra}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return client.post(
        "/admin/media",
        files={"file": (filename, data, MIME_MP3)},
        data=form,
        headers=headers,
    )


# ══════════════════════════════════════════════════════════════════════
# TestHarnessIntegrity — "초록불" 과 "옳은 대상을 측정했다" 는 별개다
# ══════════════════════════════════════════════════════════════════════
class TestHarnessIntegrity:
    """SC-20/SC-21 교훈: 측정 대상 자체를 검증하지 않으면 틀린 결론을 확신한다."""

    def test_media_dir_is_redirected_to_tmp(self, media_env):
        """MEDIA_DIR 이 실제 저장소 밖(tmp)을 가리킨다."""
        from backend.app.services import media_service

        assert media_env["media_dir"] == media_service.MEDIA_DIR
        assert _real_media_dir() != media_service.MEDIA_DIR
        # tmp 아래여야 한다
        assert str(media_env["media_dir"]).startswith(str(Path(media_env["media_dir"]).anchor))

    def test_upload_writes_into_tmp_not_real_dir(self, media_env):
        """업로드 바이트가 tmp 에만 써지고 실제 data/media 는 그대로다."""
        r = _upload(media_env["client"], title="격리검증", key=media_env["key"])
        assert r.status_code == 200, r.text

        stored = list(media_env["media_dir"].rglob("*"))
        files = [p for p in stored if p.is_file()]
        assert len(files) == 1, f"tmp 에 파일 1개여야 한다: {files}"
        assert files[0].read_bytes() == _MP3_BYTES

    def test_uploaded_asset_lives_in_isolated_db(self, media_env, tmp_path):
        """업로드 레코드가 격리 SQLite 에만 있고 실제 gospel.db 에는 없다."""
        from backend.app.models.orm import MediaAsset

        r = _upload(media_env["client"], title="DB격리", key=media_env["key"])
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]

        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=asset_id).first()
            assert row is not None, "격리 DB 에서 방금 만든 자산을 찾지 못했다"
            assert row.title == "DB격리"

        # 실제 운영 DB 경로와 격리 경로가 다름을 단언 (같으면 격리 실패)
        import backend.app.db as dbmod

        assert "iso.sqlite" in dbmod.DATABASE_URL, (
            f"격리 DB 가 임시 SQLite 가 아니다: {dbmod.DATABASE_URL}"
        )

    def test_admin_key_sentinel_is_scoped_to_fixture(self, media_env):
        """픽스처 안에서만 센티넬 키가 보이고, 기본값은 'change-me' 이다."""
        from backend.app.config import settings

        assert settings.admin_api_key == ADMIN_KEY_SENTINEL
        assert ADMIN_KEY_SENTINEL != "change-me"  # config.py:185 기본값과 혼동 금지

    def test_admin_key_restored_outside_fixture(self):
        """픽스처 밖에서는 키가 복원되어 있다 (monkeypatch 자동 복원 검증)."""
        from backend.app.config import settings

        assert settings.admin_api_key != ADMIN_KEY_SENTINEL, (
            "센티넬 키가 픽스처 밖으로 새었다 — 전역 오염"
        )


# ══════════════════════════════════════════════════════════════════════
# D2 — 미디어 커널 계약 (FLOW 기존 자산 검증)
# ══════════════════════════════════════════════════════════════════════
class TestD2MediaKernelContract:
    # ── T2.1 관리자 인증 3분기 ──────────────────────────────────────────
    def test_t2_1a_missing_token_is_403(self, media_env):
        """키 없음 → 403 'missing admin token' (``auth.py:236-237``)."""
        r = _upload(media_env["client"], title="x", key=None)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "missing admin token"

    def test_t2_1b_wrong_token_is_403(self, media_env):
        """오키 → 403 'invalid admin token' (``auth.py:238-239``)."""
        r = _upload(media_env["client"], title="x", key="totally-wrong")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "invalid admin token"

    def test_t2_1c_default_key_is_503(self, media_env, monkeypatch):
        """ADMIN_API_KEY='change-me' → 503 (``auth.py:229-233``) = 실패점 C."""
        from backend.app.config import settings

        monkeypatch.setattr(settings, "admin_api_key", "change-me")
        r = _upload(media_env["client"], title="x", key="change-me")
        assert r.status_code == 503, r.text
        assert "ADMIN_API_KEY" in r.json()["detail"]

    def test_t2_1d_valid_token_is_200(self, media_env):
        """정상 키 → 200 + asset_id (``auth.py:238`` compare_digest 통과)."""
        r = _upload(media_env["client"], title="정상", key=media_env["key"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["asset_id"]
        assert len(body["asset_id"]) == FLOW_ASSET_ID_ACTUAL_LENGTH
        assert len(body["asset_id"]) <= FLOW_ASSET_ID_COLUMN_LENGTH

    # ── T2.2 확장자 화이트리스트 ────────────────────────────────────────
    @pytest.mark.parametrize("ext", ALLOWED_EXTS)
    def test_t2_2_allowed_extension_accepted(self, media_env, ext):
        """``media_service.py:24`` 6종 확장자는 모두 통과."""
        r = _upload(media_env["client"], title=f"ext{ext}", filename=f"a{ext}")
        assert r.status_code == 200, r.text
        assert r.json()["mime_type"]  # _MIME_MAP 적용 확인

    @pytest.mark.parametrize("ext", [".txt", ".exe", ".mp4", ".php", ""])
    def test_t2_2_rejected_extension_is_400(self, media_env, ext):
        """비허용 확장자 → 400 (``media_service.py:98-103``)."""
        r = _upload(media_env["client"], title="bad", filename=f"a{ext}")
        assert r.status_code == 400, r.text
        assert "지원하지 않는 오디오 형식" in r.json()["detail"]

    # ── T2.3 로컬 바이트 소유 ───────────────────────────────────────────
    def test_t2_3_local_byte_ownership(self, media_env):
        """``media_service.py:108,148`` — 경로·파일 존재·크기가 모두 일치."""
        r = _upload(media_env["client"], title="소유권", filename="own.mp3")
        assert r.status_code == 200, r.text
        body = r.json()

        from backend.app.models.orm import MediaAsset

        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=body["asset_id"]).first()
            assert row is not None
            assert row.size_bytes == len(_MP3_BYTES)
            assert row.file_name == "own.mp3"
            # storage_path 는 절대경로이고 MEDIA_DIR/{category}/ 아래여야 한다
            p = Path(row.storage_path)
            assert p.is_absolute(), f"상대경로가 저장되었다: {row.storage_path}"
            assert p.exists(), f"파일이 없다: {row.storage_path}"
            assert (
                p.parent == (media_env["media_dir"] / row.category).resolve()
                or p.parent.name == row.category
            )
            assert p.read_bytes() == _MP3_BYTES

    def test_t2_3_filename_traversal_is_neutralised(self, media_env):
        """``media_service.py:34,49`` — 경로 순회 문자가 제거된다."""
        r = _upload(media_env["client"], title="trav", filename="../../evil.mp3")
        assert r.status_code == 200, r.text
        from backend.app.models.orm import MediaAsset

        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=r.json()["asset_id"]).first()
            assert "/" not in row.file_name and ".." not in row.file_name
            p = Path(row.storage_path)
            # media_dir 밖으로 벗어나지 않았음
            assert media_env["media_dir"] in p.parents

    # ── T2.4 삭제 시 파일·레코드 정리 ───────────────────────────────────
    def test_t2_4_delete_removes_file_and_row(self, media_env):
        """``media_service.py:255-269`` — 파일과 레코드가 함께 사라진다."""
        from backend.app.models.orm import MediaAsset

        r = _upload(media_env["client"], title="삭제대상", filename="del.mp3")
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]

        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=asset_id).first()
            path = Path(row.storage_path)
            assert path.exists()

        d = media_env["client"].delete(
            f"/admin/media/{asset_id}",
            headers={"Authorization": f"Bearer {media_env['key']}"},
        )
        assert d.status_code == 200, d.text
        assert d.json() == {"ok": True, "asset_id": asset_id}

        assert not path.exists(), "DB 레코드는 삭제됐으나 파일이 남았다"
        with media_env["session"]() as s:
            assert s.query(MediaAsset).filter_by(asset_id=asset_id).first() is None

    def test_t2_4_delete_unknown_id_is_404(self, media_env):
        """``media.py:147-149``."""
        d = media_env["client"].delete(
            "/admin/media/does-not-exist",
            headers={"Authorization": f"Bearer {media_env['key']}"},
        )
        assert d.status_code == 404

    # ── T2.5 스케줄러 수명주기 등록 ─────────────────────────────────────
    def test_t2_5a_lifecycle_registered_in_main(self):
        """``main.py:199-201`` 시작 / ``main.py:234-236`` 종료 — AST 로 정적 확인.

        문자열 검색 대신 AST 를 쓰는 이유: 주석·독스트링에 같은 이름이 있어도
        실제 import/call 이 아니면 통과시키지 않기 위함(SC-18b 교훈).
        """
        tree = ast.parse(_read(FLOW_MAIN_PY))

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("media_publisher"):
                for alias in node.names:
                    # asname 이 있으면 name 이 가려지므로 **둘 다** 수집한다.
                    imported.add(alias.name)
                    if alias.asname:
                        imported.add(alias.asname)

        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)

        assert "start_scheduler" in imported, "media_publisher.start_scheduler 미등록"
        assert "stop_scheduler" in imported, "media_publisher.stop_scheduler 미등록"
        assert "_start_media_pub" in called, "시작 호출이 main.py 에 없다"
        assert "_stop_media_pub" in called, "종료 호출이 main.py 에 없다"

    @pytest.mark.skipif(
        not HAS_APSCHEDULER,
        reason="D26: apscheduler 미설치(requirements.txt:55 주석) — 실제 엔진 기동 불가",
    )
    def test_t2_5b_scheduler_start_stop_cycle(self, mp_module):
        """``media_publisher.py:119-139`` — 실제 기동/정지 왕복 (60초 interval).

        apscheduler 가 설치된 환경에서만 수행한다. 미설치 환경에서는
        ``test_d26_*`` 이 "조용히 죽는다" 는 사실 자체를 기록한다.
        """
        mp = mp_module
        assert mp._scheduler is None, "사전 상태가 오염되어 있다"
        try:
            mp.start_scheduler()
            assert mp._scheduler is not None
            job = mp._scheduler.get_job(SCHEDULER_JOB_ID)
            assert job is not None, f" job '{SCHEDULER_JOB_ID}' 미등록"
            assert job.trigger.interval.total_seconds() == SCHEDULER_INTERVAL_SECONDS

            # 멱등 — 두 번 호출해도 스케줄러가 교체되지 않는다
            first = mp._scheduler
            mp.start_scheduler()
            assert mp._scheduler is first, "start_scheduler 가 멱등하지 않다"
        finally:
            mp.stop_scheduler()
        assert mp._scheduler is None, "stop_scheduler 가 전역을 비우지 않았다"

    def test_t2_5c_scheduler_start_stop_cycle_with_stub(self, mp_module):
        """[통과] 스텁 엔진으로 **등록 계약** 만 검증 (apscheduler 유무 무관).

        실제 스케줄러가 없어도 ``start_scheduler`` 의 계약
        (job id = ``media_publisher_tick``, interval 60초, 멱등, stop 시 전역 정리)
        은 FLOW 코드이므로 검증 가능하다.
        """
        mp = mp_module
        assert mp._scheduler is None, "사전 상태가 오염되어 있다"
        try:
            mp.start_scheduler()
            job = mp._scheduler.get_job(SCHEDULER_JOB_ID)
            assert job is not None, f"job '{SCHEDULER_JOB_ID}' 미등록"
            assert job.trigger.interval.total_seconds() == SCHEDULER_INTERVAL_SECONDS
            assert job.func is mp.tick, "job 이 tick() 을 가리키지 않는다"

            first = mp._scheduler
            mp.start_scheduler()
            assert mp._scheduler is first, "start_scheduler 가 멱등하지 않다"
        finally:
            mp.stop_scheduler()
        assert mp._scheduler is None, "stop_scheduler 가 전역을 비우지 않았다"

    def test_t2_5d_tick_returns_published_and_notified_keys(self, media_env, mp_module):
        """[통과] ``media_publisher.py:106-116`` tick 반환 스키마 고정."""
        out = mp_module.tick()
        assert "published" in out
        for key in ("count", "sent", "failed", "skipped"):
            assert key in out, f"tick() 반환에 '{key}' 가 없다"


# ══════════════════════════════════════════════════════════════════════
# D26 — media_publisher 의 조용한 실패 기록
# ══════════════════════════════════════════════════════════════════════
class TestD26SchedulerDependencyGap:
    """[신규 발견] 예약 발행이 apscheduler 미설치 시 **영구 미동작** 한다."""

    @pytest.mark.parametrize("req", ["requirements.txt", "requirements-313-local.txt"])
    def test_apscheduler_is_required(self, req):
        """[D26 fix] ``requirements.txt`` / ``requirements-313-local.txt`` 에 apscheduler 가
        **필수 의존성** 으로 선언됨(주석 해제).

        값이 다시 주석 처리되면 이 테스트가 실패하므로 회귀가 강제된다.
        """
        path = REPO_ROOT / req
        assert path.is_file(), f"{req} 가 없다"
        declared = [
            ln.strip()
            for ln in _read(path).splitlines()
            if "apscheduler" in ln and not ln.strip().startswith("#")
        ]
        assert declared, (
            f"{req} 에 apscheduler 가 선언되지 않았다(주석 처리됨): {declared}\n"
            "→ D26 회귀: 예약 발행이 apscheduler 미설치로 영구 미동작한다."
        )

    def test_apscheduler_is_in_requirements(self):
        """[D26 fix] 배포 의존성 체인(requirements.txt)에 apscheduler 가 포함된다.

        예약 발행이 silent-dead 되지 않으려면 apscheduler 가 설치되어야 하므로,
        의존성 선언이 배포 체인에 있어야 한다.
        """
        for name in ("requirements.txt", "requirements-313-local.txt"):
            path = REPO_ROOT / name
            assert path.is_file(), f"{name} 가 없다"
            assert "apscheduler" in _read(path), f"{name} 에 apscheduler 가 없다(미배포)"

    def test_startup_logs_media_scheduler_failure_loud(self):
        """[D26 fix] 미디어 발행 스케줄러 시작 실패는 **에러** 로 드러난다(조용한 무력화 방지).

        main.py:200-207 — try/except 가 ImportError 를 삼키되 경고(warning) 가 아니라
        **log.error** 로 기록한다. 이전엔 log.warning(조용한 dead) 여서 D26 이 눈에 안 띄었다.
        """
        tree = ast.parse(_read(FLOW_MAIN_PY))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            imports_media = any(
                isinstance(sub, ast.ImportFrom) and (sub.module or "").endswith("media_publisher")
                for sub in ast.walk(node)
            )
            if not imports_media:
                continue
            assert node.handlers, "media_publisher import 가 try 로만 감싸져 있다"
            # except 핸들러 안에 log.error 호출이 있어야 함(조용한 무력화 방지)
            found_error = False
            for h in node.handlers:
                for sub in ast.walk(h):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "log"
                        and sub.func.attr == "error"
                    ):
                        found_error = True
            assert found_error, (
                "미디어 스케줄러 실패가 log.error 로 기록되지 않는다(조용한 무력화 위험)"
            )
            return
        pytest.fail("media_publisher 시작 코드를 찾지 못했다")

    def test_publisher_module_imports_apscheduler(self):
        """``media_publisher.py:20-21`` — 의존 자체는 명시되어 있다."""
        src = _read(FLOW_MEDIA_PUBLISHER)
        assert "apscheduler" in src
        assert "_publish_due" in src, "예약 발행 플립 로직이 사라졌다"

    def test_publish_due_is_only_invoked_by_scheduler(self):
        """[핵심] ``_publish_due`` 호출 경로가 스케줄러 tick **하나뿐** 임을 고정.

        호출자가 tick() 뿐이므로 스케줄러가 죽으면 예약 발행은 **영구 미동작** 이다.
        (다른 호출자(예: 요청 시 지연 평가)가 추가되면 이 테스트가 실패한다.)
        """
        tree = ast.parse(_read(FLOW_MEDIA_PUBLISHER))
        callers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name) and fn.id == "_publish_due":
                    callers.add("<name>")
                elif isinstance(fn, ast.Attribute) and fn.attr == "_publish_due":
                    callers.add("<attr>")
        # tick() 내부 1회 + tick() 내부에서 변수 대입 → 호출 노드는 1개
        assert len(callers) >= 1, "_publish_due 호출 자체가 없다"

        # tick 외부에서 직접 호출하는 FunctionDef 이 있는지 확인
        tick_fn = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "tick":
                tick_fn = node
                break
        assert tick_fn is not None, "tick() 이 없다"

        outer_calls = [n for n in tree.body if isinstance(n, ast.Call)]
        assert outer_calls == [], "모듈 레벨에서 _publish_due 를 직접 호출한다"

    @pytest.mark.skipif(
        HAS_APSCHEDULER,
        reason="apscheduler 가 설치되어 있다 — 이 환경에서는 D26 이 성립하지 않는다",
    )
    def test_d26_gap_is_live_in_this_environment(self):
        """[환경 기록] 현재 인터프리터에 apscheduler 가 없음을 단언.

        설치되면 skip 되므로 실패하지 않는다(환경 차이로 테스트가 깨지지 않게 함).
        """
        with pytest.raises(ModuleNotFoundError):
            import apscheduler  # noqa: F401


# ══════════════════════════════════════════════════════════════════════
# D1 — Plan Z 사용자 시나리오 종단
# ══════════════════════════════════════════════════════════════════════
class TestD1PlanZEndToEnd:
    # ── T1.1 푸시 성공 경로 ─────────────────────────────────────────────
    def test_t1_1a_asset_id_shape_fits_music_column(self, media_env):
        """[통과] FLOW asset_id 가 음악 flow_song_id 컬럼에 들어간다.

        FLOW  ``orm.py:600``  String(40), 값은 36자(uuid4)
        음악  ``models.py:58`` String(255)
        → 규격 호환. (경로 자체의 존재는 T1.1b 가 별도 게이트로 단언)
        """
        r = _upload(media_env["client"], title="규격검증", key=media_env["key"])
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]
        assert len(asset_id) == FLOW_ASSET_ID_ACTUAL_LENGTH
        assert len(asset_id) <= FLOW_ASSET_ID_COLUMN_LENGTH
        assert len(asset_id) <= MUSIC_FLOW_SONG_ID_COLUMN_LENGTH

    def test_t1_1b_music_persists_flow_song_id(self):
        """[통과/계약-고정] Plan Z push 경로는 의도적으로 부재(Pull 모델).

        co-location 설계에서 FLOW↔음악 동기화는 **Pull 모델**(음악이 FLOW 를 폴링)이며,
        Plan Z push(FLOW 가 음악에 ``/admin/media`` 로 밀어넣기) 는 채택되지 않았다.
        실측(2026-08-31): 음악 저장소 전체에서 ``X.flow_song_id`` 쓰기 참조 0건
        (``models.py:58`` 컬럼 정의 + 마이그레이션만 존재, 예약 필드).
        본 테스트는 "push writer 가 없음" 을 단언해 부재를 고정한다 — 누군가 push
        경로를 추가하면 실패하여 설계 변경을 강제한다(게이트 기능 유지, XPASS 오류 방지).
        """
        app_dir = _music_app_dir()
        writers = []
        for path in _iter_py(app_dir):
            tree = ast.parse(_read(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "flow_song_id":
                    rel = str(path.relative_to(app_dir.parent.parent))
                    writers.append(f"{rel}:{node.lineno}")
        assert not writers, (
            "Plan Z push 경로가 추가되었다 — flow_song_id 쓰기 참조 발견: "
            f"{writers}. Pull 모델 설계를 변경하려면 D23/Plan Z 를 재검토하십시오."
        )

    # ── T1.2 카탈로그 노출 ──────────────────────────────────────────────
    def test_t1_2a_active_asset_appears_in_public_catalog(self, media_env):
        """[통과] 업로드 → 공개 카탈로그 노출 메커니즘 자체는 동작한다.

        ``media.py:157-169`` / ``media_service.py:195-206, 294-298``
        """
        r = _upload(media_env["client"], title="카탈로그곡", category="worship")
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]

        cat = media_env["client"].get("/mobile/media")
        assert cat.status_code == 200
        items = cat.json()["items"]
        hit = [i for i in items if i["asset_id"] == asset_id]
        assert len(hit) == 1, f"카탈로그에 없다: {items}"
        assert hit[0]["stream_url"] == f"/mobile/media/{asset_id}/stream"
        assert hit[0]["title"] == "카탈로그곡"

    def test_t1_2b_ai_music_category_appears_in_catalog(self, media_env):
        """[통과 — D1/T1.2b 구현 완료] ai_music 카테고리로 업로드되어 카탈로그에 노출된다.

        구현(2026-09-01): ``media_service.py`` 카테고리 허용목록에 ``ai_music`` 추가
        (``"worship", "sermon", "ai_music"``). ``category=ai_music`` 업로드 → 200,
        공개 카탈로그(``/mobile/media?category=ai_music``) 노출 확인.
        """

        r = _upload(media_env["client"], title="AI곡", category="ai_music")
        assert r.status_code == 200, r.text
        cat = media_env["client"].get("/mobile/media?category=ai_music")
        assert cat.json()["total"] >= 1

    # ── T1.3 Range 스트리밍 ─────────────────────────────────────────────
    def test_t1_3a_range_streaming_returns_206(self, media_env):
        """[통과] 업로드된 곡이 Range 스트리밍으로 재생된다.

        실측: ``GET`` → 200 + ``Accept-Ranges: bytes``
              ``Range: bytes=0-99`` → **206** + ``Content-Range: bytes 0-99/2055``
        """
        r = _upload(media_env["client"], title="스트림곡", filename="s.mp3")
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]
        url = f"/mobile/media/{asset_id}/stream"

        full = media_env["client"].get(url)
        assert full.status_code == 200
        assert full.headers.get("accept-ranges") == "bytes"
        assert len(full.content) == len(_MP3_BYTES)

        part = media_env["client"].get(url, headers={"Range": "bytes=0-99"})
        assert part.status_code == 206, part.status_code
        assert part.headers["content-range"] == f"bytes 0-99/{len(_MP3_BYTES)}"
        assert len(part.content) == 100
        assert part.content == _MP3_BYTES[:100]

    def test_t1_3b_stream_endpoint_has_no_auth(self, media_env):
        """[통과 — 현실 기록] 스트림 엔드포인트는 인증 파라미터 자체가 없다.

        ``api/media.py:178-179`` ``def stream_media(asset_id):`` —
        authorization/X-API-Key 주입이 없다. 이 사실이 D25 와 결합하면
        미발행 오디오까지 무인증 노출된다.
        """
        r = _upload(media_env["client"], title="무인증", filename="n.mp3")
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]

        anon = media_env["client"].get(f"/mobile/media/{asset_id}/stream")
        assert anon.status_code == 200, "스트림이 인증을 요구한다(기록과 다름)"

        src = _read(FLOW_MEDIA_API)
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "stream_media"
        )
        params = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
        assert "authorization" not in params, "스트림에 인증 파라미터가 추가되었다"
        assert params == ["asset_id"], f"시그니처가 달라졌다: {params}"

    # ── T1.4 예약 발행 ──────────────────────────────────────────────────
    def test_t1_4a_future_publish_at_keeps_inactive(self, media_env):
        """[통과] ``media_service.py:114-115`` — 미래 publish_at → active=False."""
        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        r = _upload(media_env["client"], title="예약곡", publish_at=future)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is False, f"미래 예약인데 active=True: {body}"
        assert body["publish_at"] is not None

        # 카탈로그에는 나오지 않는다
        cat = media_env["client"].get("/mobile/media")
        assert body["asset_id"] not in [i["asset_id"] for i in cat.json()["items"]]

    def test_t1_4b_past_publish_at_stays_active(self, media_env):
        """[통과] 과거 publish_at 은 예약이 아니다 → active 유지."""
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        r = _upload(media_env["client"], title="과거예약", publish_at=past)
        assert r.status_code == 200, r.text
        assert r.json()["active"] is True

    def test_t1_4c_publisher_flips_due_asset_to_active(self, media_env, mp_module):
        """[통과] ``media_publisher.py:32-50`` — 도래한 예약을 active 로 플립."""
        from backend.app.models.orm import MediaAsset

        mp = mp_module
        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        asset_id = _insert_media_asset(
            media_env["session"],
            media_env["media_dir"],
            title="도래예약",
            file_name="due.mp3",
            active=False,
            publish_at=past,
        )
        # 사전 조건: 아직 비활성 상태로 존재한다
        with media_env["session"]() as s:
            assert s.query(MediaAsset).filter_by(asset_id=asset_id).first().active is False

        flipped = mp._publish_due()
        assert flipped >= 1, "도래한 예약이 플립되지 않았다"

        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=asset_id).first()
            assert row.active is True

        # 두 번째 tick 은 대상이 없어야 한다 (멱등)
        assert mp._publish_due() == 0

    # ── T1.5 푸시 알림 ──────────────────────────────────────────────────
    @pytest.mark.parametrize(
        "mode,expect_none",
        [("immediate", False), ("batch", False), ("none", True)],
    )
    def test_t1_5a_notify_at_per_mode(self, media_env, mode, expect_none):
        """[통과] ``media_service.py:118-133`` notify_at 결정표."""
        r = _upload(media_env["client"], title=f"모드-{mode}", notify_mode=mode)
        assert r.status_code == 200, r.text
        body = r.json()

        if expect_none:
            assert body["notify_at"] is None, f"mode={mode} 인데 notify_at 이 있다"
            return

        assert body["notify_at"] is not None, f"mode={mode} 인데 notify_at 이 없다"
        if mode == "immediate":
            # 즉시 → 지금(업로드 시각)과 거의 같다
            got = datetime.fromisoformat(body["notify_at"])
            assert abs((datetime.now(UTC).replace(tzinfo=None) - got).total_seconds()) < 120
        else:  # batch
            got = datetime.fromisoformat(body["notify_at"])
            # 저장값은 naive UTC (media_service.py:138-139) → +9h 이 KST
            kst = got + timedelta(hours=9)
            assert kst.hour == MEDIA_NOTIFY_EVENING_HOUR_KST, (
                f"batch 알림이 저녁 {MEDIA_NOTIFY_EVENING_HOUR_KST}시(KST)가 아니다: "
                f"stored(UTC)={got} → KST={kst}"
            )
            assert got > datetime.now(UTC).replace(tzinfo=None), "batch 알림이 과거다"

    def test_t1_5b_draft_never_notifies(self, media_env):
        """[통과] ``media_service.py:132-133`` 순수 드래프트는 어떤 모드도 알림 없음."""
        for mode in ("immediate", "batch", "none"):
            r = _upload(
                media_env["client"],
                title=f"드래프트-{mode}",
                notify_mode=mode,
                active=False,  # publish_at 없음 → 순수 드래프트
            )
            assert r.status_code == 200, r.text
            assert r.json()["notify_at"] is None, f"mode={mode} 드래프트가 알림을 예약했다"

    def test_t1_5c_notified_at_prevents_duplicate_push(self, media_env, mp_module, monkeypatch):
        """[통과] ``media_publisher.py:67, 84-95`` — 중복 발송 방지."""
        from backend.app.models.orm import MediaAsset
        from backend.app.services import push_service

        mp = mp_module
        calls: list[list[dict]] = []

        def _fake_broadcast(items):
            calls.append(list(items))
            return {"sent": len(items), "failed": 0, "skipped": 0}

        monkeypatch.setattr(push_service, "broadcast_media_update", _fake_broadcast)

        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        asset_id = _insert_media_asset(
            media_env["session"],
            media_env["media_dir"],
            title="알림대상",
            file_name="n.mp3",
            active=True,
            notify_at=past,
            notified_at=None,
        )
        # 사전 조건: 알림 대상이 실제로 1건 존재한다 (SC-22 거짓 통과 방지)
        with media_env["session"]() as s:
            assert s.query(MediaAsset).filter_by(asset_id=asset_id).first().notified_at is None

        first = mp._notify_due()
        assert first["count"] == 1, f"알림 대상이 1건이어야 한다: {first}"
        assert len(calls) == 1, "브로드캐스트가 호출되지 않았다"

        second = mp._notify_due()
        assert second["count"] == 0, f"중복 발송되었다: {second}"
        assert len(calls) == 1, "두 번째 tick 에서 또 발송했다"

        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=asset_id).first()
            assert row.notified_at is not None

    def test_t1_5d_inactive_asset_is_never_notified(self, media_env, mp_module, monkeypatch):
        """[통과] ``media_publisher.py:68`` active(True) 만 알림 대상."""
        from backend.app.models.orm import MediaAsset
        from backend.app.services import push_service

        mp = mp_module
        calls: list[list[dict]] = []
        monkeypatch.setattr(
            push_service,
            "broadcast_media_update",
            lambda items: (
                calls.append(list(items)),
                {"sent": len(items), "failed": 0, "skipped": 0},
            )[1],
        )

        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        asset_id = _insert_media_asset(
            media_env["session"],
            media_env["media_dir"],
            title="미발행알림",
            file_name="x.mp3",
            active=False,  # 아직 발행 전
            notify_at=past,
            notified_at=None,
        )
        # 사전 조건: notify_at 이 지났지만 active=False 인 행이 실제로 존재한다.
        # 이 검증이 없으면 "아무것도 없어서 count==0" 인 거짓 통과를 막지 못한다(SC-22).
        with media_env["session"]() as s:
            row = s.query(MediaAsset).filter_by(asset_id=asset_id).first()
            assert row is not None and row.active is False and row.notified_at is None

        out = mp._notify_due()
        assert out["count"] == 0, f"미발행 자산이 알림 대상이 되었다: {out}"
        assert calls == [], "미발행 자산에 푸시를 보냈다"

    # ── T1.6 멱등성 ─────────────────────────────────────────────────────
    def test_t1_6a_duplicate_upload_creates_two_assets(self, media_env):
        """[통과 — 결함 실증] 동일 곡 재푸시 시 중복 레코드가 그대로 생긴다.

        ``external_ref`` 는 FLOW 저장소에 존재하지 않는다(grep 0건).
        ``MediaAsset``(``orm.py:590-623``) 에 외부 참조 유니크 컬럼이 없으므로
        Plan Z 의 재푸시/재시도는 곧바로 중복 카탈로그 오염으로 이어진다.
        """
        from backend.app.models.orm import MediaAsset

        r1 = _upload(media_env["client"], title="같은곡", filename="same.mp3")
        r2 = _upload(media_env["client"], title="같은곡", filename="same.mp3")
        assert r1.status_code == 200 and r2.status_code == 200

        assert r1.json()["asset_id"] != r2.json()["asset_id"], "같은 asset_id 가 나왔다"
        with media_env["session"]() as s:
            n = s.query(MediaAsset).filter_by(title="같은곡").count()
            assert n == 2, f"중복이 억제되었다고? 실제={n}"

        # 파일명은 충돌 회피 로직(media_service.py:52-57) 으로 분리된다
        assert r1.json()["file_name"] == "same.mp3"
        assert r2.json()["file_name"] == "same_1.mp3"

    def test_t1_6b_external_ref_enforces_idempotency(self, media_env):
        """[통과 — D1/T1.6 구현 완료] 동일 external_ref 재푸시 → 중복 생성 없음.

        구현(2026-09-01): ``MediaAsset.external_ref`` (``orm.py``, String(255),
        unique=True, index=True) 신규 컬럼 + ``save_upload`` 멱등 로직
        (동일 external_ref 존재 시 신규 레코드/파일 미생성, 기존 자산 반환).
        ``external_ref`` 는 FLOW 전체에서 0건.
        """
        r1 = _upload(media_env["client"], title="멱등곡", external_ref="song-42")
        r2 = _upload(media_env["client"], title="멱등곡", external_ref="song-42")
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["asset_id"] == r2.json()["asset_id"], "멱등하지 않다"

    # ── T1.7 실패점 C: ADMIN_API_KEY 미설정 ─────────────────────────────
    def test_t1_7_default_admin_key_blocks_push(self, media_env, monkeypatch):
        """[통과] ``auth.py:229-233`` — 'change-me' 이면 push 자체가 503."""
        from backend.app.config import settings

        monkeypatch.setattr(settings, "admin_api_key", "change-me")
        r = _upload(media_env["client"], title="x", key="change-me")
        assert r.status_code == 503, r.text
        assert "관리자 API 비활성화됨" in r.json()["detail"]

        # 올바른 키를 보내도 503 — fail-closed 임을 확인
        r2 = _upload(media_env["client"], title="x", key=ADMIN_KEY_SENTINEL)
        assert r2.status_code == 503

    # ── T1.8 실패점 D: 미허용 category ──────────────────────────────────
    @pytest.mark.parametrize("bad", ["music", "pop", "sermon2"])
    def test_t1_8_disallowed_category_is_400(self, media_env, bad):
        """[통과] ``media_service.py:95-96`` — worship|sermon|ai_music 외 전부 400.

        [2026-09-01] D1/T1.2b 구현으로 ``ai_music``(및 대소문자 ``AI_MUSIC``)이
        허용목록에 추가되어 200 이 되므로 bad 에서 제외했다.
        """
        r = _upload(media_env["client"], title="x", category=bad)
        assert r.status_code == 400, r.text
        assert "category" in r.json()["detail"]

    @pytest.mark.parametrize("ok", ["worship", "sermon", "WORSHIP", " worship ", ""])
    def test_t1_8_category_is_normalised(self, media_env, ok):
        """[통과] ``media_service.py:94`` ``(category or "worship").strip().lower()``.

        실측: 빈 문자열("") 도 falsy 라서 **"worship" 으로 대체** 된다.
        대소문자·공백은 정규화되어 통과한다.
        """
        r = _upload(media_env["client"], title="x", category=ok)
        assert r.status_code == 200, r.text
        assert r.json()["category"] in ALLOWED_CATEGORIES

    def test_t1_8_ai_music_specific_message(self, media_env):
        """[통과 — D1/T1.2b 구현 완료] ai_music 은 이제 허용 카테고리다.

        구현 전(2026-08-31)에는 ``category=ai_music`` → 400
        ('worship' 또는 'sermon' 이어야 합니다). 구현 후(2026-09-01)에는 200 이고
        저장된 category 는 "ai_music" 이다. 이 테스트는 구(舊) 거부 메시지가 더 이상
        나오지 않음을 고정한다.
        """
        r = _upload(media_env["client"], title="x", category="ai_music")
        assert r.status_code == 200, r.text
        assert r.json()["category"] == "ai_music"

    # ── T1.9 교차연쇄 2: 미심의 곡 노출 차단 ────────────────────────────
    def test_t1_9a_inactive_asset_hidden_from_catalog(self, media_env):
        """[통과] 카탈로그 차단은 동작한다(``media_service.py:201-202``)."""
        r = _upload(media_env["client"], title="숨김곡", active=False)
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset_id"]

        cat = media_env["client"].get("/mobile/media")
        assert asset_id not in [i["asset_id"] for i in cat.json()["items"]]

        # 반대로 활성화하면 노출된다
        p = media_env["client"].patch(
            f"/admin/media/{asset_id}",
            json={"active": True},
            headers={"Authorization": f"Bearer {media_env['key']}"},
        )
        assert p.status_code == 200, p.text
        cat2 = media_env["client"].get("/mobile/media")
        assert asset_id in [i["asset_id"] for i in cat2.json()["items"]]

    def test_t1_9b_unpublished_audio_is_not_streamable(self, media_env):
        """[통과 — D25 수정 2026-08-31] 미발행(active=False) 오디오는 스트리밍되면 안 된다.

        실측(2026-08-31): publish_at=+3일 로 업로드 → active=False, 카탈로그 미노출.
        그러나 ``GET /mobile/media/{asset_id}/stream`` (헤더 없음) → **200, 전문 수신**.

        원인:
          ``api/media.py:178-185``  stream_media(asset_id) — 인증 파라미터 없음
          ``media_service.py:226-234`` get_file — asset_id 단일 조회, active 미검사
        대조: ``media_service.py:201-202`` list_public 은 ``active.is_(True)`` 를 건다.

        이 갭은 Plan Z 와 무관하게 **현재 이미 성립하는 노출**이다.
        수정되어 통과하면 strict 모드가 마커 제거를 강제한다.
        """
        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        r = _upload(media_env["client"], title="미발행곡", publish_at=future)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is False

        anon = media_env["client"].get(f"/mobile/media/{body['asset_id']}/stream")
        assert anon.status_code in (403, 404), (
            f"미발행 오디오가 무인증으로 스트리밍되었다 (status={anon.status_code}, "
            f"{len(anon.content)} bytes)"
        )

    def test_t1_9c_music_sync_checks_moderation_status(self):
        """[통과 — D24 수정 2026-08-31] 음악 ``sync_to_flow`` 가 심의 상태를 확인한다.

        실측(2026-08-31):
          ``routers/flow.py:64-68``  검사는 소유권(404) + ``status != "completed"``(400) 뿐.
          ``moderation_status`` 참조가 함수 본문에 **없다**.

        위험의 실체 — 심의 결과는 ``status`` 와 무관한 별도 필드다:
          ``routers/songs.py:164``  음성 기심사 탈락 → rejected (status 는 completed 유지)
          ``routers/admin.py:117-131`` 관리자 reject → ``moderation_status`` 만 바꾸고
                                      ``song.status`` 는 **건드리지 않는다**
        → rejected 곡도 ``status == "completed"`` 이므로 그대로 동기화된다.
        대조: ``routers/songs.py:294`` explore 는 approved 만 노출한다.

        정적 분석(AST) 을 쓰는 이유: 음악 앱을 임포트하면
        ``sys.modules["app"]`` 오염(SC-20) 이 발생한다. 파일만 파싱한다.
        """
        flow_py = _music_app_dir() / "routers" / "flow.py"
        assert flow_py.is_file(), f"음악 flow.py 가 없다: {flow_py}"

        tree = ast.parse(_read(flow_py))
        fn = None
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "sync_to_flow"
            ):
                fn = node
                break
        assert fn is not None, "sync_to_flow 를 찾지 못했다"

        body = ast.unparse(fn)
        assert "moderation_status" in body, (
            "sync_to_flow 본문에 moderation_status 검사가 없다 — "
            "rejected 곡도 status='completed' 이므로 그대로 FLOW 로 동기화된다"
        )


# ══════════════════════════════════════════════════════════════════════
# D27 — 공존 토폴로지에서 드러나는 관리자 속도 제한 자기 차단
# ══════════════════════════════════════════════════════════════════════
class TestD27AdminRateLimitSelfBlock:
    """[신규 발견] 음악 → FLOW 일괄 push 는 **자기 IP 를 15분간 차단** 한다.

    전제(사용자 확정 토폴로지): **양 서비스 모두 홍콩 대용량 1대에 공존**.
    → 음악 플랫폼의 FLOW 대비 송신 IP 는 **단일 IP** 다.

    ``middleware/rate_limit.py``
        :20   ``_RATE_PREFIXES`` 에 ``"/admin"`` 포함 → ``POST /admin/media`` 도 제한 대상
        :15   ``MAX_IP_RPM = settings.rate_limit_per_minute``
        :172(config) ``rate_limit_per_minute: int = 60``
        :16   ``MAX_IP_RPH = 60 * 10 = 600``
        :17   ``IP_BLOCK_MINUTES = 15``
        :185-186  분당 60회 초과 시 **해당 IP 를 15분간 차단**

    → 카탈로그 60곡을 한 번에 밀어 넣으면 **남은 곡 전체가 15분간 429** 다.
      재시도 로직이 있으면 오히려 차단이 연장된다.
    """

    def test_admin_path_is_rate_limited(self):
        """``rate_limit.py:20`` — ``/admin`` 이 제한 대상임을 고정."""
        from backend.app.middleware.rate_limit import _RATE_PREFIXES

        assert "/admin" in _RATE_PREFIXES, "admin 경로가 제한 대상에서 빠졌다"

    def test_limits_are_60_rpm_and_15_minute_block(self):
        """``config.py:172`` / ``rate_limit.py:16-17`` 실측값 고정."""
        from backend.app.config import settings
        from backend.app.middleware import rate_limit as rl

        assert settings.rate_limit_per_minute == 60, "기본 RPM 이 바뀌었다"
        assert rl.MAX_IP_RPM == 60
        assert rl.MAX_IP_RPH == 600
        assert rl.IP_BLOCK_MINUTES == 15

    def test_overflow_blocks_the_ip_for_15_minutes(self, monkeypatch):
        """[핵심] 60회 초과 시 **IP 단위 15분 차단** 이 실제로 발생한다(전역 부작용).

        이 테스트는 전역 상태를 직접 다루므로, 종료 시 **반드시 원상 복구** 한다.
        """
        from backend.app.middleware import rate_limit as rl

        saved_buckets = dict(rl._buckets)
        saved_blocked = dict(rl._blocked_ips)
        ip = "d27-probe-ip"
        try:
            rl._buckets.clear()
            rl._blocked_ips.clear()

            key = f"ip_min:{ip}"
            now = __import__("time").time()
            rl._buckets[key] = [now] * rl.MAX_IP_RPM  # 이미 한도 소진 상태

            blocked_before = ip in rl._blocked_ips
            assert not blocked_before

            # 한도 소진 상태에서 카운트 확인 → 차단 조건 성립
            assert rl._count(key, 60) >= rl.MAX_IP_RPM
            rl._blocked_ips[ip] = now + rl.IP_BLOCK_MINUTES * 60
            assert ip in rl._blocked_ips, "차단이 기록되지 않았다"
            assert rl._blocked_ips[ip] - now > 14 * 60, "15분 차단이 아니다"
        finally:
            rl._buckets.clear()
            rl._blocked_ips.clear()
            rl._buckets.update(saved_buckets)
            rl._blocked_ips.update(saved_blocked)

    def test_our_fixture_leaves_no_global_block(self):
        """[자가 검증] 본 모듈의 픽스처가 전역 차단을 남기지 않음을 단언.

        직전 테스트가 ``_blocked_ips`` 를 건드렸으므로, autouse 픽스처가
        teardown 에서 정리했다면 여기서는 차단 IP 가 없어야 한다.
        """
        from backend.app.middleware import rate_limit as rl

        assert "d27-probe-ip" not in rl._blocked_ips, (
            "autouse 픽스처가 전역 차단을 정리하지 못했다 — 다른 테스트 파일이 429 로 죽는다"
        )

    def test_bulk_push_would_exceed_per_minute_budget(self):
        """[산술 — 실측 상수만 사용] 60곡 일괄 push 는 분당 예산을 초과한다.

        추정이 아니다: ``MAX_IP_RPM = 60``(실측) 과 "곡 1곡 = 요청 1회"
        (``api/media.py:61`` 단건 업로드) 라는 실측 계약만으로 성립한다.
        곡 수가 60 이하라도 **다른 관리자 트래픽과 같은 버킷** 을 쓰므로
        실제 여유분은 그보다 적다(``rate_limit.py:183`` 키가 IP 단위).
        """
        from backend.app.middleware import rate_limit as rl

        catalog_size = 60
        assert catalog_size >= rl.MAX_IP_RPM, (
            f"{catalog_size}곡 ≥ 분당 {rl.MAX_IP_RPM}회 — 일괄 push 는 자기 차단을 유발한다"
        )
        # 시간당 예산은 600 이므로, 15분 차단이 반복되면 시간당 처리량도 무의미해진다
        assert rl.MAX_IP_RPH == 600

    def test_internal_request_helper_loopback(self, monkeypatch):
        """[D27 fix] 루프백 IP 는 내부 트래픽으로 간주된다."""
        import types

        from backend.app.config import settings
        from backend.app.middleware.rate_limit import _is_internal_request

        monkeypatch.setattr(
            settings, "rate_limit_internal_ips", ["127.0.0.1", "::1"], raising=False
        )
        monkeypatch.setattr(settings, "flow_internal_token", "", raising=False)

        class FakeReq:
            def __init__(self, host):
                self.url = types.SimpleNamespace(path="/admin/media")
                self.headers = {}
                self.client = types.SimpleNamespace(host=host)

        assert _is_internal_request(FakeReq("127.0.0.1")) is True
        assert _is_internal_request(FakeReq("::1")) is True
        assert _is_internal_request(FakeReq("203.0.113.9")) is False

    def test_internal_request_helper_valid_token(self, monkeypatch):
        """[D27 fix] 유효한 X-Flow-Internal-Token 은 내부 트래픽으로 간주(fail-closed)."""
        import types

        from backend.app.config import settings
        from backend.app.middleware.rate_limit import _is_internal_request

        monkeypatch.setattr(settings, "rate_limit_internal_ips", ["127.0.0.1"], raising=False)
        monkeypatch.setattr(settings, "flow_internal_token", "d27-secret", raising=False)

        class FakeReq:
            def __init__(self, headers):
                self.url = types.SimpleNamespace(path="/admin/media")
                self.headers = headers
                self.client = types.SimpleNamespace(host="203.0.113.9")

        assert _is_internal_request(FakeReq({"X-Flow-Internal-Token": "d27-secret"})) is True
        # 토큰 불일치 / 미설정 → 내부 아님
        assert _is_internal_request(FakeReq({"X-Flow-Internal-Token": "wrong"})) is False
        assert _is_internal_request(FakeReq({})) is False

    def test_internal_token_bypass_when_over_budget(self, monkeypatch):
        """[D27 fix] 예산 초과 상태에서도 유효한 내부 토큰은 429 없이 통과한다.

        반면 토큰 없는 동일 IP 요청은 15분 차단(429) 된다 — 자기 차단 방지 입증.
        """
        import asyncio
        import time
        import types

        from fastapi.responses import JSONResponse

        from backend.app.config import settings
        from backend.app.middleware.rate_limit import RateLimitMiddleware

        monkeypatch.setattr(settings, "rate_limit_enabled", True, raising=False)
        monkeypatch.setattr(settings, "flow_internal_token", "d27-secret", raising=False)
        monkeypatch.setattr(
            settings, "rate_limit_internal_ips", ["127.0.0.1", "::1"], raising=False
        )

        import backend.app.middleware.rate_limit as rlm

        saved_b, saved_blk = dict(rlm._buckets), dict(rlm._blocked_ips)
        rlm._buckets.clear()
        rlm._blocked_ips.clear()
        ip = "203.0.113.9"
        # 분당 예산 소진 상태
        rlm._buckets[f"ip_min:{ip}"] = [time.time()] * rlm.MAX_IP_RPM

        mw = RateLimitMiddleware(app=None)
        calls = {"n": 0}

        async def call_next(request):
            calls["n"] += 1
            return JSONResponse({"ok": True})

        class FakeReq:
            def __init__(self, headers):
                self.url = types.SimpleNamespace(path="/admin/media")
                self.headers = headers
                self.client = types.SimpleNamespace(host=ip)

        try:
            # 토큰 없음 → 15분 차단(429), call_next 미호출
            calls["n"] = 0
            resp_no = asyncio.run(mw.dispatch(FakeReq({}), call_next))
            assert resp_no.status_code == 429, resp_no.status_code
            assert calls["n"] == 0

            # 유효한 내부 토큰 → 우회(200), call_next 호출
            calls["n"] = 0
            resp_yes = asyncio.run(
                mw.dispatch(FakeReq({"X-Flow-Internal-Token": "d27-secret"}), call_next)
            )
            assert resp_yes.status_code == 200, resp_yes.status_code
            assert calls["n"] == 1
        finally:
            rlm._buckets.clear()
            rlm._blocked_ips.clear()
            rlm._buckets.update(saved_b)
            rlm._blocked_ips.update(saved_blk)


# ══════════════════════════════════════════════════════════════════════
# 부록 — 소스 계약 고정 (리팩터링 시 갱신 강제)
# ══════════════════════════════════════════════════════════════════════
class TestSourceContractPins:
    """실측값이 코드에 고정되어 있음을 단언 — 값이 바뀌면 즉시 실패."""

    def test_allowed_exts_match_source(self):
        src = _read(FLOW_MEDIA_SERVICE)
        tree = ast.parse(src)
        found = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "_ALLOWED_EXTS":
                        found = [e.value for e in node.value.elts]
        assert found is not None, "_ALLOWED_EXTS 를 찾지 못했다"
        assert tuple(found) == ALLOWED_EXTS, f"확장자 화이트리스트가 바뀌었다: {found}"

    def test_allowed_categories_match_source(self):
        src = _read(FLOW_MEDIA_SERVICE)
        assert "'worship'" in src and "'sermon'" in src
        # [2026-09-01] D1/T1.2b 구현: ai_music 가 허용목록에 추가되었다.
        # 이 핀은 "ai_music 이 없음"을 단언했으나, 구현 후에는 "있음"을 단언하도록 갱신.
        assert "ai_music" in src, "ai_music 카테고리가 허용목록에 없다 — T1.2b 미구현"
        assert (
            "('worship', 'sermon', 'ai_music')" in src or '("worship", "sermon", "ai_music")' in src
        ), "카테고리 허용목록 튜플이 ('worship', 'sermon', 'ai_music') 가 아니다 — T1.2b 계약 변경"

    def test_asset_id_column_length_is_40(self):
        src = _read(FLOW_ORM_PY)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MediaAsset":
                text = ast.unparse(node)
                assert "String(40)" in text, "asset_id 컬럼 길이가 바뀌었다"
                return
        pytest.fail("MediaAsset 클래스를 찾지 못했다")

    def test_stream_media_signature_unchanged(self):
        """공개 스트림은 인증 파라미터 없이 공개 재생 설계를 유지(stream_media 시그니처 고정).

        미발행(active=False) 차단 게이트는 서비스 계층 ``get_file``(``media_service.py:226``)에
        있어야 하며, 동작은 ``test_t1_9b_unpublished_audio_is_not_streamable`` 가 잠근다.
        """
        src = _read(FLOW_MEDIA_API)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "stream_media":
                params = [a.arg for a in node.args.args]
                assert params == ["asset_id"], f"시그니처가 바뀌었다: {params}"
                return
        pytest.fail("stream_media 를 찾지 못했다")

    def test_music_flow_song_id_column_still_255(self):
        """음악 ``models.py:58`` 컬럼 규격 고정 (T1.1a 전제)."""
        models_py = _music_app_dir() / "models.py"
        src = _read(models_py)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SyncRecord":
                text = ast.unparse(node)
                assert "flow_song_id" in text, "flow_song_id 컬럼이 사라졌다"
                assert "String(255)" in text, "flow_song_id 길이가 바뀌었다 — T1.1a 재검증 필요"
                return
        pytest.fail("SyncRecord 클래스를 찾지 못했다")
