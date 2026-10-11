# 공존 설계 결함 감사 — 6차 라운드 (D29~D39)

> 작성: 2026-09-01 · 대상: `docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md` (D1~D28 누적) 이후 **미등록 결함**
> 방법: **문자열 grep 금지 — AST + 소스 대조**. 모든 결론은 `file:line` 실측. 추정치는 `보류` 표기.
> 코드베이스: FLOW = `C:\Desktop\Project\FLOW`, 음악 = `C:\Desktop\korean-gospel-ai\ai-music-platform`

---

## 0. 요약 — 이번 라운드의 단일 주제

이번 라운드에서 발견된 11건은 서로 다른 파일에 흩어져 있지만 **하나의 구조적 원인**으로 수렴한다.

> **수정 코드는 설치되었으나 기본값이 그것을 무력화한다.**
> — 또는 —
> **수정 코드가 환경(타임존 · 컨테이너 네트워크 · DB 엔진)에 암묵적으로 의존한다.**

| 유형 | 해당 결함 | 공통 구조 |
|---|---|---|
| **① 기본값 무력화형** | D30, D31, D33 | fail-closed 가 "안전"이 아니라 **기능 영구 정지**로 작동 |
| **② 환경 의존형** | D29, D38, D39 | 로컬 개발 환경에서는 맞고 **공존 토폴로지(홍콩 · Postgres · 컨테이너)에서 틀리다** |
| **③ 공유 자원 경합형** | D32, D36 | 단일 호스트 1대 공존으로 **blast radius 가 교차 확대** |
| **④ 경계 불일치형** | D34, D35, D37 | 같은 값 · 같은 개념인데 **두 서비스 / 두 코드 경로가 다르게 취급** |

⚠ **D27·D24 의 기존 수정은 옳다.** 다만 둘 다 "설치"까지 했고 "작동 조건"을 남겼다.
본 문서는 그 **작동 조건이 기본 상태에서 충족되지 않는다**는 것을 실증한다.

---

## 1. 감사 범위와 방법

| 단계 | 내용 |
|---|---|
| 인벤토리 | `scripts/ast_inventory.py` 신규 작성 — 양측 백엔드 전수 AST 파싱(클래스·함수·데코레이터·전역 가변 상태·위험 호출) |
| 비교 축 | 인증 브로커 / 동기화 계약 / 미디어 파이프라인 / 스케줄러 / 설정·시크릿 / 캐시·쿼터 |
| 실증 | 타임존 결함은 **실행으로 측정**(`TZ` 3종 대조), 설정 파싱 결함은 **import 실패 재현** |
| 배제 | D1~D28 과 중복되는 항목, `file:line` 로 못 박을 수 없는 항목 |

**이번 라운드에서 새로 확인된 미감사 면** (기존 설계서에 `flow_link`·`get_db`·`async def`·`astimezone`·`max_upload_size_mb`·`BackgroundTasks` 언급 **0건**):
음악 측 **인증·백그라운드 실행 모델**, FLOW 측 **업로드 경로**, 양측 **시간/일 경계 처리**.

---

## 2. 신규 결함 레지스터

| ID | 심각 | 한 줄 요약 | 위치 | 상태 |
|---|---|---|---|---|
| **D29** | 🔴 | 예약 발행 시각이 **호스트 로컬 타임존** 에 따라 최대 9시간 어긋난다 | `FLOW backend/app/api/media.py:55-71`(수정 후) / 구 `:52-58` | ✅ **해결** (2026-09-02) |
| **D30** | 🔴 | D27 수정(내부 트래픽限速 제외)은 **기본 설정에서 무동작** | `FLOW config.py:180-181`, `middleware/rate_limit.py:181-188` | ⚙️ **운영 조치만 잔존** (토큰 발급·양측 배포) |
| **D31** | 🔴 | D30 의 해법(`RATE_LIMIT_INTERNAL_IPS`)을 자연스러운 형식으로 쓰면 **서비스 부팅 실패** | `FLOW backend/app/config.py:186-207`(수정 후) / 구 `:180` | ✅ **해결** (2026-09-02) |
| **D32** | 🔴 | 생성 백그라운드 태스크가 **이벤트 루프에서 블로킹 I/O** 수행 | 음악 `routers/songs.py:157` (수정) / 구 `:107,232` | ✅ **해결** (2026-09-02) |
| **D33** | 🟠 | 심의가 **기본 무효** — D24 수정은 아무것도 걸러내지 못한다 | 음악 `config.py:51`, `routers/songs.py:175` (수정) | ✅ **해결** (2026-09-02) |
| **D34** | 🟠 | SSRF 가드가 **같은 값의 두 출구 중 하나만** 지킨다 | 음악 `storage/s3.py:79` (수정) / `routers/flow.py:51` | ✅ **해결** (2026-09-02) |
| **D35** | 🟠 | 신원 브리지 키 이중화(데드 컬럼) **삭제로 단일화** | 음악 `models.py:15,77` (`User.flow_subscriber_id` 삭제) | ✅ **해결** (2026-09-02) |
| **D36** | 🟠 | 업로드 크기 검증이 **전체 메모리 적재 후** — 같은 리포의 다른 경로와 불일치 | `FLOW api/media.py:97-113`(수정 후) / 구 `:84-90` | ✅ **해결** (2026-09-02) |
| **D37** | 🟡 | 음악 JWT 서명 키에 **기본값 방어 없음** (FLOW 는 있음) | `음악 auth.py` `_assert_jwt_secret_configured` (수정) | ✅ **해결** (2026-09-02) |
| **D38** | 🟡 | 일일 쿼터 "하루" 경계가 **DB 엔진 · 세션 TimeZone** 에 좌우 | 음악 `quota.py:22,28` (수정) / 구 `:18-23` | ✅ **해결** (2026-09-02) |
| **D39** | 🟡 | `synced_at` 응답 형식이 **SQLite / Postgres 에서 달라진다** | 음악 `routers/flow.py:148` (수정) / 구 `:144` | ✅ **해결** (2026-09-02) |
| **D44** | 🔴 | `requirements-deploy.txt` 가 **실제 import 의존성을 누락** → 컨테이너 기동 실패 | `requirements-deploy.txt`, `services/media_publisher.py:20`, `security/encryption.py:19` | ✅ **해결** (2026-09-02, docker 실증) |

> **D32~D39 · D44 의 상태 표기**: ✅ 해결 / ⚙️ 코드 완료·운영 잔존 / ⬜ 미착수.
> D44 는 2026-09-02 출시 준비 점검 중 **신규 발견**된 결함으로, 6차 감사(D29~D39) 범위 밖이었다.

---

### 2.1 🔴 D29 — 예약 발행 시각은 호스트 로컬 타임존에 따라 어긋난다

> **✅ 2026-09-02 해결.** 수정 후 코드는 `backend/app/api/media.py:55-71`.
> 회귀 테스트: `tests/test_media_publish_guards.py` (D29 10 케이스).

**수정 전 코드** (구 `FLOW backend/app/api/media.py:46-58`):

```python
def _parse_publish_at(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    try:
        dt = datetime.fromisoformat(s)  # 구 :52  — tz 없으면 naive
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt - timedelta(hours=9)  # 구 :57  — KST 가정, 여전히 naive
    return dt.astimezone(UTC)  # 구 :58  — ⚠ naive 를 "시스템 로컬 타임존" 으로 해석
```

**수정 후 코드** (`FLOW backend/app/api/media.py:55-71`):

```python
_KST = timezone(timedelta(hours=9))  # :52  — 고정 오프셋 상수


def _parse_publish_at(raw: str | None) -> datetime | None:  # :55
    ...
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_KST)  # :70  — 명시 부착(뺄셈 아님)
    return dt.astimezone(UTC)  # :71  — 이제 항상 tz-aware
```

**왜 `ZoneInfo("Asia/Seoul")` 이 아니라 고정 오프셋인가** — 실측 후 재검증한 사유:

| 후보 | 실측 결과 | 판단 |
|---|---|---|
| `ZoneInfo("Asia/Seoul")` | ✅ **정상 동작**. `python:3.11-slim` 에 `/usr/share/zoneinfo` 가 존재(Debian 기본 포함). `tzdata` pip 패키지는 미설치이나 stdlib `zoneinfo` 가 시스템 디렉터리를 읽어 성공 — `2026-09-01 12:00:00+09:00` | 동작은 하나 채택하지 않음 |
| `timezone(timedelta(hours=9))` | ✅ 정상. 외부 데이터 0건 | **채택** |

채택 사유는 **가용성이 아니라 계약의 명시성** 이다.
(⚠️ 초안에서 "slim 이미지에 tzdata 가 없어 `ZoneInfo` 가 실패한다" 고 적었으나 **실측 결과 거짓**
— 컨테이너에서 직접 실행해 정상 동작을 확인했고, 위 표로 정정한다. 근거 없는 단정은 환각이다.)

1. 이 엔드포인트의 계약은 **"관리자가 입력하는 벽시계 값은 KST 로 해석한다"** 다.
   고정 오프셋은 그 계약을 **문자 그대로** 인코딩한다.
2. `ZoneInfo` 는 **런타임에 시스템 tz 데이터베이스를 요구**한다. 지금 이미지에는 있지만
   distroless·scratch·tzdata 미포함 커스텀 이미지로 바꾸면 그때 `ZoneInfoNotFoundError` 로
   **기동 실패**한다. 고정 오프셋은 배포 대상 이미지와 무관하다.
3. 서머타임: 한국은 1988년 이후 미시행이나, `ZoneInfo` 는 **미래에 DST 가 도입되면 해석이 바뀐다**.
   계약문서("KST 기준")와 어긋나는 자동 변경은 이 필드에서 원치 않는 동작이다.

테스트 `test_d29_kst_constant_is_fixed_offset_not_system_local` 이 `_KST` 의 고정 오프셋 성질을 잠근다.

**원리**: Python 에서 `astimezone()` 을 **naive datetime** 에 호출하면 그 naive 값을
**시스템 로컬 타임존으로 이미 표현된 것** 으로 간주한다. `:57` 에서 KST→UTC 로 9시간을 이미 뺐으므로,
`:58` 은 **로컬 타임존이 UTC 일 때만** 정답이 된다.

**실측** (동일 입력, 호스트 TZ 만 변경):

| 호스트 로컬 UTC 오프셋 | 입력 `2026-09-05T09:00` (KST 의도) | 결과 (UTC) | 드리프트 |
|---|---|---|---|
| `+0.0h` | 09:00 KST | `2026-09-05T00:00Z` | **0h** ✅ |
| `+9.0h` | 09:00 KST | `2026-09-04T15:00Z` | **−9.0h** ❌ |

→ **드리프트 = −(호스트 로컬 UTC 오프셋)**. 표본 1개가 아니라 **법칙**으로 확인됨.
→ tz 를 붙인 입력(`2026-09-05T09:00+09:00`)은 두 경우 모두 정상(`:57` 분기 미진입) —
즉 **naive 입력 경로만** 오동작하며, 관리자 UI 는 ISO 문자열을 그대로 넘기므로 이 경로가 기본이다.

**왜 공존 토폴로지에서 악화되는가**:

- 서버를 **홍콩(UTC+8)** 으로 옮기면(사용자 확정 토폴로지) 모든 `publish_at` 이 **8시간 일찍 발행**된다.
- **어떤 Dockerfile / compose 도 `TZ` 를 고정하지 않았다** — 조치 전 `TZ` grep 결과 0건
  (`docker-compose.prod.yml`, `docker-compose.local.yml`, `Dockerfile`, `Dockerfile.ui`).
  → 컨테이너는 기본 UTC, 베어메탈 홍콩 호스트는 UTC+8. **배포 형태에 따라 결과가 달라졌다.**
  → ✅ 2026-09-02 조치: `Dockerfile` 이미지 `ENV TZ=UTC` + compose backend·ui `TZ=${TZ:-UTC}` 로 고정.
  (코드가 로컬 타임존에 비의존하게 되었으므로 **2중 방어** — 하나가 새도 다른 하나가 막는다.)
- D26(스케줄러 미설치) 과 결합하면 증상이 **"조용히 틀린 시각에 발행"** 이 되어 사후 감지가 더 어렵다.

**같은 리포의 올바른 대조군** (media.py 만 틀렸음을 증명):

| 위치 | 방식 | 판정 |
|---|---|---|
| `services/quota_service.py:34` | `datetime.datetime.now(datetime.UTC).astimezone(KST)` | ✅ tz-aware 기준 |
| `services/greeting_service.py:29` | `datetime.fromisoformat(...).replace(tzinfo=UTC)` | ✅ 명시 부착 |
| `services/ccp/rate_service.py:91` | `if moment.tzinfo is not None: return moment.astimezone(UTC)...` | ✅ 가드 후 변환 |
| **`api/media.py:58`** | **`dt.astimezone(UTC)` (naive)** | ❌ **유일한 오용** |

**권고** → **조치 완료 (2026-09-02)**:

1. ✅ `media.py:55-71` — `dt - 9h` 후 `astimezone(UTC)` → `dt.replace(tzinfo=_KST).astimezone(UTC)` 로 교체.
2. ✅ `TZ` 명시 고정 — `Dockerfile:36-40`(이미지 `ENV TZ=UTC`),
   `docker-compose.prod.yml` backend·ui 양 서비스(`TZ=${TZ:-UTC}`), 양측 `.env.example`.
   (조치 전 `TZ` grep 결과 0건 이었음 → 컨테이너는 UTC, 베어메탈 홍콩 호스트는 +8 로 **배포 형태에 따라 결과가 달라졌다**.)
3. ✅ 회귀 테스트 `tests/test_media_publish_guards.py` — D29 10 케이스 추가.
   A/B 대조로 "구버전에서는 이 테스트가 깨진다" 를 확인:
   동일 입력에 대해 구현 로직 차이가 **드리프트 −9.0h**(호스트 +9) / **−8.0h**(홍콩 +8) 를 만든다.
4. ✅ 컨테이너 실증: `TZ=UTC` 이미지에서 naive 정오 → `12:00Z` (드리프트 0).

보류: 관리자 UI 가 실제로 naive 를 보내는지 — §3 미결정 참조.
(수정 후에는 **naive 든 tz-aware 든 둘 다 정확**해졌으므로 이 미결정의 위험도는 낮아졌다.)

---

### 2.2 🔴 D30 — D27 수정(내부 트래픽限速 제외)은 기본 설정에서 무동작

D27 은 "공존 1대에서 음악→FLOW 일괄 push 가 자기 차단된다" 는 결함이고,
`middleware/rate_limit.py:168-188` 의 `_is_internal_request` 가 그 수정이다.

```python
def _is_internal_request(request: Request) -> bool:
    if _real_client_ip(request) in settings.rate_limit_internal_ips:  # :181
        return True
    token = settings.flow_internal_token  # :183
    if token:
        sent = request.headers.get("X-Flow-Internal-Token")
        if sent and hmac.compare_digest(sent, token):  # :186
            return True
    return False
```

**두 분기가 모두 기본 상태에서 닫혀 있다.**

| 분기 | 기본값 | 결과 |
|---|---|---|
| IP allowlist | FLOW `backend/app/config.py:180` `rate_limit_internal_ips: list[str] = ["127.0.0.1", "::1"]` | ❌ **컨테이너 네트워크에서 음악 컨테이너 IP 는 브리지 대역(172.x)이며 loopback 이 아니다** |
| 토큰 | FLOW `backend/app/config.py:181` `flow_internal_token: str = ""` | ❌ `if token:` 미진입 — **fail-closed 로 인해 우회 자체가 비활성** |

**`FLOW_INTERNAL_TOKEN` 은 그 어디에도 없다** — 실측:
`docker-compose.prod.yml`, `docker-compose.local.yml`, `Dockerfile`, `Dockerfile.ui`,
`backend/.env.example` 전수 grep → **0건**.

→ **결론: D27 의 자기 차단은 수정 코드가 있어도 기본 배포에서 그대로 발생한다.**
fail-closed 는 여기서 "안전"이 아니라 **"기능 영구 정지"** 다.

**부수 위험 — 내부 판정이 클라이언트可控 헤더에 묶여 있다**:
`_is_internal_request` 는 `_real_client_ip()`(`rate_limit.py:64-82`)에 의존하며,
이 함수는 FLOW `backend/app/config.py:173-175` `rate_limit_trust_proxy: bool = True` 일 때
**XFF 의 가장 오른쪽 hop** 을 쓴다(`:78`). 즉 내부 트래픽 판정 근거가 **TCP peer 가 아니라 헤더** 다.

- 현재는 `Caddyfile:66` `header_up X-Forwarded-For {remote_host}` 가 XFF 를 **덮어쓰기** 때문에 외부 조작이 불가하다.
- 그러나 `docker-compose.prod.yml:128-130` 은 `443/8501` 만 publish 하고 **backend:8000 은 노출하지 않으므로**
  이 안전성이 **Caddy 단일 경유에만 의존**한다. backend:8000 이 직접 노출되는 순간
  `X-Forwarded-For: 127.0.0.1` 한 줄로 **60 RPM · 15분 차단 전부 우회** 된다.
- 권고: allowlist 판정은 `request.client.host`(실제 TCP peer)로 하고, XFF 는 "프록시 뒤 실제 IP" 용도로만 쓴다.

---

### 2.3 🔴 D31 — D30 의 해법을 자연스러운 형식으로 쓰면 서비스가 부팅 실패한다

`rate_limit_internal_ips: list[str]` 는 pydantic-settings 필드다.
컨테이너 브리지 IP 를 추가하려는 운영자가 통상 쓰는 형식은 **쉼표 구분**이다.

**실측 재현**:

```
RATE_LIMIT_INTERNAL_IPS=172.18.0.5
→ SettingsError: error parsing value for field "rate_limit_internal_ips"
   from source "EnvSettingsSource"
```

→ **import 시점 예외 = 프로세스 기동 실패.** 헬스체크 이전에 죽는다.

**의미**: D30 을 고치려는 시도 자체가 **장애 유발 경로** 다.
문서화되지 않은 JSON 배열 형식(`["127.0.0.1","172.18.0.5"]`)만 허용되며,
이 형식 제약은 `.env.example` 에 **존재하지 않는다**(해당 키 자체가 없음).

**권고**: `field_validator(mode="before")` 로 `str` 입력을 쉼표 분할 허용 + `.env.example` 에 키와 형식 명시.

#### ✅ 조치 완료 (2026-09-02) — 그리고 **첫 번째 수정은 효과가 없었다**

**1차 시도(실패)**: `@field_validator("rate_limit_internal_ips", mode="before")` 만 추가.
→ **여전히 동일한 `SettingsError` 로 기동 실패.**

```python
# ❌ 효과 없음 — validator 가 호출되기 전에 죽는다
@field_validator("rate_limit_internal_ips", mode="before")
def _split_internal_ips(cls, v): ...
```

원인은 pydantic-settings 의 **소스 단계 디코딩**이다. 스택 트레이스가 이를 명시한다:

```
config.py:406  settings = Settings()
  → pydantic_settings/main.py  _settings_build_values: source_state = source()
    → sources/base.py:608  field_value = self.prepare_field_value(...)
      → sources/providers/env.py:150  value = self.decode_complex_value(...)
        → sources/base.py:208  return json.loads(value)   ← 여기서 이미 죽는다
```

즉 **`list`/`dict` 타입은 validator 에 도달하기 전에 `json.loads` 된다**.
`mode="before"` validator 는 그 *다음* 단계다.
→ **"코드에 수정이 있다" 와 "수정이 작동한다" 는 별개** 라는 결함감사 원칙 1 의 재확인 사례.

**2차 시도(성공)**: `Annotated[list[str], NoDecode]` + validator 조합.
`NoDecode` 는 소스 단계 JSON 디코딩을 **건너뛰게** 해 원시 문자열을 validator 로 넘긴다.

```python
# ✅ config.py:193
rate_limit_internal_ips: Annotated[list[str], NoDecode] = list(_DEFAULT_INTERNAL_IPS)


# ✅ config.py:198-207
@field_validator("rate_limit_internal_ips", mode="before")
def _split_internal_ips(cls, v): ...  # JSON 배열 · 쉼표 구분 · list 모두 허용
```

**실측 (케이스별 subprocess 6종, 전부 OK)**:

| 입력 | 결과 |
|---|---|
| `172.18.0.5` | `['172.18.0.5']` ← **기존엔 기동 실패** |
| `172.18.0.5, 172.18.0.6` | `['172.18.0.5', '172.18.0.6']` |
| `["172.18.0.5"]` | `['172.18.0.5']` ← 레거시 JSON 배열 하위호환 |
| `""` (빈 문자열) | `['127.0.0.1', '::1']` ← **기본값으로 되돌림** |
| 미설정 | `['127.0.0.1', '::1']` |

⚠ **빈 문자열을 `[]` 로 해석하지 않은 이유**: `.env` 템플릿 placeholder
(`RATE_LIMIT_INTERNAL_IPS=`) 로 **루프백 우회까지 사라지면 D27 수정이 다시 no-op** 이 된다.
빈값은 "명시적 기본값" 으로 되돌린다(`_DEFAULT_INTERNAL_IPS` 모듈 상수로 단일 정의).

**회귀 테스트**: `tests/test_config.py` D31 6 케이스.
`test_d31_nodecode_annotation_is_present` 는 `NoDecode` 메타데이터 존재를 **구조적으로** 검문해
1차 시도의 실패가 반복되지 않게 막는다.

---

### 2.4 🔴 D32 — 생성 백그라운드 태스크가 이벤트 루프에서 블로킹 I/O 를 수행한다

**마운트 지점 (9곳, 동일 패턴)**:

| 라인 | 호출 |
|---|---|
| `routers/songs.py:232` | `bg.add_task(_process_song, song.id)` |
| `:247` | `bg.add_task(_process_song, song.id)` |
| `:262` | `bg.add_task(_process_song, song.id)` |
| `:277` | `bg.add_task(_process_song, song.id)` |
| `:331` | `bg.add_task(_process_sound, song.id, req)` |
| `:481` / `:495` / `:509` / `:522` | `bg.add_task(_process_post, ...)` |

**`_process_song` 은 `async def` 이다** (`songs.py:107`).
Starlette 의 `BackgroundTasks` 는 async callable 을 **이벤트 루프에서 `await`** 한다.
그 안에서 다음이 실행된다:

| 블로킹 작업 | 위치 |
|---|---|
| `SessionLocal()` + 동기 `db.get` / `db.commit` | `songs.py:108-111` 등 |
| `download_to_temp` → **동기** `httpx.get(timeout=60)` | `s3.py:74` |
| `postprocess.run_postprocess` → **ffmpeg 서브프로세스** | `songs.py:98` |
| `moderation.moderate_audio` | `songs.py:161` |

→ 음악 파일 1곡의 다운로드(최대 60s) + ffmpeg 처리 동안 **음악 API 전체가 멈춘다**.
`/health`, `/api/auth/login`, `/api/v1/sync/flow` 포함.

**왜 공존 토폴로지에서 악화되는가**: 홍콩 1대에 양 서비스가 올라가므로
음악 측 이벤트 루프 정체는 같은 호스트의 **CPU · I/O 경합** 으로 FLOW 응답 지연에까지 전이된다.
또한 `songs.py:107` 의 `except Exception: db.commit()`(`:172-174`) 까지 블로킹 구간이다.

**권고**: `_process_song` 을 `run_in_threadpool`(또는 전용 executor)로 이관하거나,
생성 파이프라인을 별도 워커(Celery/RQ/Arq)로 분리. 최소한 `postprocess` · `download_to_temp` 는 스레드 오프로드.

> **✅ 2026-09-02 해결.** 수정 후 코드는 `routers/songs.py:8,157`.
> 회귀 테스트: `tests/test_d32_postprocess_offloaded.py` (1 건, 행위 검증).

**수정 전 코드** (`routers/songs.py:154`):

```python
if settings.enable_audio_postprocess and song.audio_url:
    song.audio_url = _apply_postprocess(song.audio_url)  # 구 :154 — 이벤트루프에서 동기 블로킹
```

**수정 후 코드** (`routers/songs.py:155-157`):

```python
if settings.enable_audio_postprocess and song.audio_url:
    # [D32] 동기 블로킹 후처리(ffmpeg + 파일 IO) 를 이벤트루프 밖 스레드풀로.
    # 단일 호스트 공존 1대에서 블라스트 레이디어스 확대 차단(§21.3).
    song.audio_url = await run_in_threadpool(_apply_postprocess, song.audio_url)
```

**범위 한계(정직 기록)**: 본 수정은 `_apply_postprocess` 가 들고 있는 **가장 무거운 블로킹 작업**
(ffmpeg 서브프로세스 + `download_to_temp`/`upload` 의 동기 `httpx`)를 이벤트 루프 밖으로 옮긴다.
`_process_song` 본체의 동기 `db.commit()` 등 경량 DB 호출은 여전히 루프 안에 남는다
(단, D34 로 `download_to_temp` 출구가 가드되므로 이 경로의 SSRF 노출도 함께 막힌다).
생성 파이프라인을 별도 워커로 분리하는 것은 별도 작업으로 잔류 — 공존 1대에서
이벤트 루프 정체의 **주된 원인(ffmpeg/외부 fetch)은 제거** 되었으므로 가용성 리스크는 대폭 낮아짐.

**검증**: `test_d32_postprocess_offloaded_to_threadpool` — `enable_audio_postprocess=True` 에서
`_apply_postprocess` 가 실제 호출되고, 곡이 `completed`/`approved` 로 전이됨을 행위 확인.

---

### 2.5 🟠 D33 — 심의는 기본 무효이며, 따라서 D24 수정은 아무것도 걸러내지 못한다

**기본값**: 음악 `config.py:48` `enable_moderation: bool = False`, `:51` `enable_audio_moderation: bool = False`.

**자동 승인 경로** (`songs.py:155-168`):

```python
if (
    result.status == "completed"
    and settings.enable_audio_moderation  # :157
    and song.moderation_status == "pending"
    and song.audio_url
    and not moderation.moderate_audio(song.audio_url)
):
    song.moderation_status = "rejected"  # :164
elif result.status == "completed" and song.moderation_status == "pending":  # :167
    song.moderation_status = "approved"  # :168  ← 기본값에서는 항상 여기
```

→ 두 스위치가 모두 꺼진 기본 상태에서 **생성 완료 = 즉시 `approved`**. `rejected` 는 관리자 수동 조작 외에 불가.

**파급**:

| 경로 | 필터 | 기본 상태에서의 실제 동작 |
|---|---|---|
| `songs.py:294` `explore_songs` | `Song.moderation_status == "approved"` | **모든 공개 완성곡이 무심의 노출** |
| `routers/flow.py:109` `sync_to_flow` | `if song.moderation_status == "rejected"` | **기본값에서는永不 차단** |

→ **D24 의 수정(`flow.py:109`)은 논리적으로 옳지만, `rejected` 가 만들어지지 않으면 no-op 이다.**
D30 과 동일한 "수정 설치 · 조건 미충족" 패턴.

**권고**: ① `enable_moderation` 기본 ON 전환, 또는 ② 기본 OFF 를 유지한다면
`moderation_status` 를 `approved` 로 자동 올리지 말고 **`unreviewed` 상태를 별도 값으로 두어**
`explore`·`sync` 양쪽이 `approved` 명시값만 통과시키도록 변경. ② 가 안전 기본값 원칙(§5)에 부합.

> **✅ 2026-09-02 해결 (① 채택).** 수정 후 코드는 `config.py:47-51`, `routers/songs.py:171-178`.
> 회귀 테스트: `tests/test_d33_moderation_default.py` (4 건).

**수정 전 코드** (`config.py:48,51`):

```python
enable_moderation: bool = False  # 구 :48 (심의 기본 OFF)
enable_audio_moderation: bool = False  # 구 :51
```

**수정 후 코드** (`config.py:47-51`):

```python
# 启用：enable_moderation=True 开文本机审（blocklist.txt 实际词库，见 moderation._blocklist_path）；
# 关闭则歌曲停留 pending 且不进入 explore/同步（协同部署面向中国目标用户，默认开启文本机审）。
enable_moderation: bool = True  # :51
enable_audio_moderation: bool = False  # :52 (오디오 심의는 기본 OFF 유지 — 외부 의존)
```

**자동 승인 분기 게이팅** (`routers/songs.py:171-178`):

```python
# [D33] 심의가 설정된 경로일 때만 자동 approved 로 승격.
# enable_moderation=False(미설정) 면 미심의 상태(pending) 로 유지 —
# explore/sync 가 명시적 approved 만 통과시키므로 무심의 노출을 막는다.
elif (
    result.status == "completed"
    and settings.enable_moderation
    and song.moderation_status == "pending"
):
    song.moderation_status = "approved"
```

**결과**:
- 기본 배포에서 **생성 완료 = `approved`** 가 되어 `explore_songs`(`songs.py:294`) 노출 ·
  `sync_to_flow`(`flow.py:109`) D24 수정(`rejected` 차단) 이 **실제로 발화하는 경로** 가 열린다.
- `enable_moderation` 을 다시 `False` 로 두면(제품 정책 D43) 곡은 `pending` 으로 남아
  `explore`/`sync` 에서 제외 — "수정 무효" 패턴 재발 방지.
- `moderation.py:6` 독스트링도 새 기본값에 맞춰 정정(기존 "기본 미설정" 오기록 → "기본 설정").
- conftest 가 이미 `settings.enable_moderation = True` 를 세팅하므로 기존 87 건 테스트는 영향 0.

**검증**: `test_moderation_default_on_auto_approves` / `test_moderation_off_keeps_unreviewed` /
`test_blocklist_loads_real_terms_package_relative` / `test_check_lyric_rejects_blocked_term`.

---

### 2.6 🟠 D34 — SSRF 가드가 같은 값의 두 출구 중 하나만 지킨다

같은 사용자 영향 값 `song.audio_url` 이 **두 곳에서 외부로 나간다**.

| 출구 | 위치 | 가드 |
|---|---|---|
| `_ensure_cdn_url` | `routers/flow.py:50` `ssrf_guard.guard(song.audio_url)` | ✅ 존재 |
| `download_to_temp` | `storage/s3.py:72-74` `httpx.get(url, timeout=60, follow_redirects=True)` | ❌ **없음** |

**`download_to_temp` 의 유일한 호출 경로**: `songs.py:97` `download_to_temp(remote_url)` ←
`_apply_postprocess(remote_url)`(`:88`) ← **`songs.py:154` `song.audio_url`**.

→ 즉 **동일한 `song.audio_url` 이 `flow.py:50` 에서는 가드되고 `s3.py:74` 에서는 가드되지 않는다.**
`ssrf_guard` 의 전역 사용처는 `flow.py:50` **1곳뿐**이다(전수 grep).

**왜 지금은 잠재적인가**: `enable_audio_postprocess = False`(음악 `config.py:54`)가 기본이므로
`songs.py:154` 분기가 기본으로는 실행되지 않는다. → **D33 과 동일한 "스위치 OFF 라서 안 터진다" 유형.**
후처리를 켜는 순간(음질 P1 요구) 바로 유효해진다.

**권고**: `download_to_temp` 진입부에 `ssrf_guard.guard(url)` 추가 — 출구 단위 방어(§25.4 의 egress 원칙)와 일치.

> **✅ 2026-09-02 해결.** 수정 후 코드는 `storage/s3.py:73-86` (`ssrf_guard.guard` 진입부 추가).
> 회귀 테스트: `tests/test_d34_ssrf_egress.py` (3 건).

**수정 후 코드** (`storage/s3.py:73-86`):

```python
def download_to_temp(url: str) -> str:
    """..."""
    ssrf_guard.guard(url)  # :79 — [SEC] D34: routers/flow.py:50 와 동일 게이트
    resp = httpx.get(url, timeout=60, follow_redirects=True)
    ...
```

**효과**: 동일한 `song.audio_url` 이 `flow.py:51`(이미 가드됨) 과 `s3.py:79`(신규 가드) **양쪽 출구에서
동일하게 차단**된다. `_apply_postprocess`(`songs.py:98`) 가 이 `download_to_temp` 를 호출하므로,
D32 로 스레드 오프로드된 후처리 경로까지 SSRF 방어가 미친다.

**검증**: `test_d34_download_to_temp_blocks_loopback` /
`test_d34_download_to_temp_blocks_cloud_metadata` (loopback·클라우드 메타데이터 IP 는 `ValueError`) /
`test_d34_download_to_temp_proceeds_when_guard_allows` (가드放行 시 httpx.get 까지 도달 — 게이트 위치 확정).

---

### 2.7 🟠 D35 — 신원 브리지 키가 이중화되었고, 한쪽은 마이그레이션이 없다

**두 개의 `flow_subscriber_id`**:

| 위치 | 선언 | 제약 |
|---|---|---|
| `models.py:15` | `User.flow_subscriber_id = Column(String(40), nullable=True, unique=True, index=True)` | unique |
| `models.py:77` | `UserLink.flow_subscriber_id = Column(String(255), unique=True, index=True, nullable=False)` | unique |

**실제 읽기/쓰기 (전수 grep)**:

| 파일:라인 | 대상 |
|---|---|
| `routers/auth.py:154` | `UserLink` 조회 |
| `routers/auth.py:158` | `UserLink` 생성 |
| `routers/auth.py:181` | `UserLink` 조회 |

→ **`User.flow_subscriber_id` 는 읽기·쓰기 0건.** 완전한 데드 컬럼이며,
`models.py:13-14` 주석은 이것을 "교차시스템 동의(ConsentRecord) 브리지 키"로 설명하고 있어
**의도와 구현이 어긋나 있다**. 동의 주체 매핑을 이 컬럼으로 읽는 코드가 생기면 항상 `None` 이다.

**마이그레이션 부재 (더 심각)**:

음악 측 마이그레이션 6개 전수 확인 — `flow_subscriber_id` 를 포함하는 것은
`d4e5f6a7b8c9_add_user_links.py:25,29` **1개뿐이며 이는 `user_links` 테이블용**이다.
**`users.flow_subscriber_id` 를 추가하는 마이그레이션은 존재하지 않는다.**

→ 공유 커널 DB 변형 B(`main.py:20-21` `SCHEMA_CREATE_ALL=0` + `alembic upgrade head`)에서는
ORM 이 선언한 컬럼이 **DB 에 존재하지 않는 drift** 가 된다. D21/D22(마이그레이션 체인)와 동일 계열.

**권고**: `User.flow_subscriber_id` 삭제 또는 마이그레이션 추가 중 **택1** — 양립 불가.
브리지 단일화 원칙상 삭제 권장(`UserLink` 가 이미 유일한 경계).

> **✅ 2026-09-02 해결 (삭제 채택).** 수정 후 코드는 `models.py:7-16` (`User.flow_subscriber_id` 데드 컬럼 삭제).
> 회귀 테스트: `tests/test_d35_userlink_single_bridge.py` (2 건).

**수정 전 코드** (`models.py:13-15`):

```python
# §21.4 G-Consent: ... UserLink 단일 브리지 테이블 로만 관리된다.
User.flow_subscriber_id = Column(String(40), nullable=True, unique=True, index=True)  # 데드 컬럼
created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**수정 후 코드** (`models.py:13-16`):

```python
# §21.4 G-Consent (T3.11c): FLOW 구독자(subscriber_id) ↔ 음악 사용자 연결은
# **UserLink 단일 브리지 테이블** 로만 관리된다(models.py:UserLink).
# [D35] User.flow_subscriber_id 는 읽기/쓰기 0건의 데드 컬럼 + 마이그레이션 부재여서 삭제.
created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**마이그레이션 드리프트 없음 (중요)**: `User.flow_subscriber_id` 는 **순수 Python ORM 선언** 이었고
추가 마이그레이션이 존재하지 않았다(증: 음악 마이그레이션 6개 전수 — `flow_subscriber_id` 를 포함하는
것은 `user_links` 용 `d4e5f6a7b8c9` 1개뿐). 따라서 이 컬럼 삭제는 **마이그레이션 체인에 영향 0** 이며,
삭제 후 `test_alembic_upgrade_builds_schema_matching_metadata` 가 여전히 통과한다(7 업무 테이블 정합 유지,
`alembic_version_music` 명칭 불일치 1건은 별도 정정 완료).

**브리지 단일화 확인**: 실제 읽기/쓰기 경로(`routers/auth.py:185,189,212`)는 모두 `UserLink.flow_subscriber_id`
(`models.py:77`) 를 사용 — 삭제 후에도 유일한 경계가 그대로 동작.

**검증**: `test_d35_user_has_no_dead_flow_subscriber_id_column` / `test_d35_userlink_remains_single_bridge`.

---

### 2.8 🟠 D36 — 업로드 크기 검증이 전체 메모리 적재 후에 일어난다

> **✅ 2026-09-02 해결.** 수정 후 코드는 `backend/app/api/media.py:97-113`.
> 회귀 테스트: `tests/test_media_publish_guards.py::test_d36_*` (2 케이스).

**수정 전 코드** (구 `FLOW backend/app/api/media.py:84-90`):

```python
data = await file.read()  # 구 :84  ← 전부 적재
max_bytes = settings.max_upload_size_mb * 1024 * 1024  # 구 :85
if len(data) > max_bytes:  # 구 :86  ← 그 다음에야 판정
    raise HTTPException(413, ...)
```

**수정 후 코드** (`FLOW backend/app/api/media.py:97-113`):

```python
max_bytes = settings.max_upload_size_mb * 1024 * 1024      # :101
if file.size and file.size > max_bytes:                    # :102  ← 선(先) 판정
    raise HTTPException(status_code=413, ...)              # :103-106

data = await file.read()                                   # :108  ← 그 뒤에야 적재
if len(data) > max_bytes:                                  # :109  ← 사후 검증은 유지
    raise HTTPException(status_code=413, ...)              # :110-113
```

사후 검증(`:109`)을 **제거하지 않고 유지**한 이유: `file.size` 는 `Content-Length` 헤더에서 오며
프록시/청크 전송에서 **없거나 거짓일 수 있다**. 선검증만 남기면 그 우회로가 열린다.
→ **선검증(공격/대용량 차단) + 사후검증(헤더 미신뢰 대비) 의 2단** 이며,
`test_d36_upload_without_content_length_falls_through_to_read` 가 "size 없을 때도 500 이 아닌 정상 진행" 을 잠근다.

**같은 리포의 올바른 대조군** — `api/documents.py:114-117` 은 `await file.read()` **전에** `file.size` 를 먼저 본다:

```python
max_size = settings.max_upload_size_mb * 1024 * 1024  # :115
if file.size and file.size > max_size:  # :116  ← 선(先) 판정
    raise HTTPException(413, ...)  # :117
raw = await file.read()  # :121
```

→ `media.py` 는 **사전 검증 누락**. `Content-Length` 가 없거나 거짓인 경우까지 막지는 못하지만,
정상적인 대형 업로드에 대해 **100MB(FLOW `backend/app/config.py:258`)를 먼저 메모리에 올린 뒤 거절**한다.

**왜 공존에서 중요한가**: 이 경로(`POST /admin/media`)가 **Plan Z 의 음악→FLOW 푸시가 쓰는 유일한 진입점**이며
(`api/media.py:61`, 단건 업로드 전용), 컨테이너 상한 합계는 **≈6.75G** 이다.
동시 업로드 N건이면 `N × 100MB` 가 그대로 상주한다.

**권고** → **조치 완료 (2026-09-02)**: `documents.py:114-117` 과 동일한 `file.size` 선검증 추가
(`media.py:97-106`), 사후 검증은 유지(`:108-113`). 회귀 테스트 2건 추가.
스트리밍 상한(디스크 스풀)은 **미적용** — 컨테이너 상한 3G 와 단건 업로드 전용 진입점이라는
특성을 감안할 때 2단 검증으로 충분하며, 스트리밍 도입은 별도 작업으로 남긴다.

---

### 2.9 🟡 D37 — 음악 JWT 서명 키에는 기본값 방어가 없다 (FLOW 와 비대칭)

| 서비스 | 키 | 기본값 | 방어 |
|---|---|---|---|
| 음악 | `config.py:11` `jwt_secret` | `"change-me-please"` | ❌ **없음** — 그대로 서명에 사용 |
| FLOW | `backend/app/config.py:194` `admin_api_key` | `"change-me"` | ✅ `api/auth.py:301-305` → **503** 반환 |
| 음악 | `config.py:59` `admin_token` | `""` | ✅ `routers/auth.py:130-131` → **503** 반환 (같은 파일에 이미 fail-closed 패턴 존재) |

→ 음악 측은 **같은 파일(`auth.py`) 안에 이미 503 fail-closed 패턴이 있음에도**(`require_admin`)
`jwt_secret` 에는 적용하지 않았다. 기본 배포 시 **누구나 임의 `sub` 의 JWT 를 발급** 할 수 있다.

**권고**: `main.py` lifespan 또는 `create_token` 진입에서 기본값 검사 후 503 / 예외.

> **✅ 2026-09-02 해결.** 수정 후 코드는 `routers/auth.py:42-60,79,86,113`.
> 회귀 테스트: `tests/test_d37_jwt_secret_guard.py` (5 건).

**수정 후 코드** (`routers/auth.py:42-60`):

```python
# 같은 파일의 `require_admin`(admin_token 미설정 → 503) 과 동일한 fail-closed 패턴을 따른다.
_INSECURE_JWT_SECRETS = frozenset({"change-me-please", "change-me", "secret", ""})
_JWT_MISCONFIG_DETAIL = "..."


def _assert_jwt_secret_configured() -> None:
    """서명 키가 공개 기본값이면 503(fail-closed)."""
    if not settings.jwt_secret or settings.jwt_secret.strip().lower() in _INSECURE_JWT_SECRETS:
        raise HTTPException(status_code=503, detail=_JWT_MISCONFIG_DETAIL)
```

**호출 지점 (3 곳 — 서명·검증 양 경로 모두 차단)**:

| 위치 | 역할 |
|---|---|
| `auth.py:79` | `create_token` 진입 — 잘못된 키로 서명 자체를 막음 |
| `auth.py:86` | `get_current_user` 진입 — 검증 차단 |
| `auth.py:113` | `get_current_user_scoped` 진입 — scoped 검증 차단 |

**효과**: FLOW(`api/auth.py:301` 503 패턴) 와 **비대칭 해소**. 기본 배포(`jwt_secret="change-me-please"`)
에서 누구나 임의 `sub` 의 JWT 를 발급하던 취약점이 503 으로 막힌다. `docker-compose.yml` 은
이미 `JWT_SECRET` 을 `${JWT_SECRET:?...}` 필수 주입으로 강제하므로, 운영 배포에서는 안전 키로 기동.

**검증**: `test_create_token_rejects_insecure_secret`(503) /
`test_create_token_works_with_secure_secret` /
`test_get_current_user_rejects_insecure_secret`(503) /
`test_get_current_user_scoped_rejects_insecure_secret`(503) /
`test_forged_token_with_insecure_secret_rejected_when_secure_set`(안전 키 설정 하 위조 토큰 401 거부).

---

### 2.10 🟡 D38 — 일일 쿼터의 "하루" 경계가 DB 엔진과 세션 TimeZone 에 좌우된다

```python
# 음악 quota.py:18-23
today = func.date(func.now())
db.query(Song).filter(Song.user_id == user_id, func.date(Song.created_at) == today)
```

| DB | `func.now()` | `func.date(...)` 기준 |
|---|---|---|
| SQLite (개발 기본, 음악 `config.py:9`) | UTC `'YYYY-MM-DD HH:MM:SS'` 문자열 | UTC |
| **Postgres (공유 커널 변형 B)** | `timestamptz` | **세션 `TimeZone` 파라미터** |

→ **같은 코드가 SQLite → Postgres 전환 시 "하루" 의 경계가 바뀐다.**
`Song.created_at` 은 `DateTime(timezone=True)` + `server_default=func.now()`(`models.py:50`)이지만
SQLite 는 tz 를 저장하지 않으므로 개발·운영 간 동작이 다르다.

**양측 불일치**: FLOW 는 **KST 비즈니스 데이** 를 쓴다 —
`services/quota_service.py:34` `datetime.datetime.now(datetime.UTC).astimezone(KST)`.
→ 공존 후 **같은 사용자가 두 서비스에서 서로 다른 시각에 "일일 한도" 가 리셋** 된다.

**권고**: 양측 모두 단일 기준(권장: KST 비즈니스 데이)으로 통일하고, 경계 계산을 **DB 함수가 아닌 애플리케이션** 으로 이동.

> **✅ 2026-09-02 해결.** 수정 후 코드는 `quota.py:7,19-35`.
> 회귀 테스트: `tests/test_d38_d39_contract.py::test_kst_day_boundary_counts_only_today` (D38).

**수정 전 코드** (`quota.py:18-23`):

```python
today = func.date(func.now())
db.query(Song).filter(Song.user_id == user_id, func.date(Song.created_at) == today)
```

**수정 후 코드** (`quota.py:7,19-35`):

```python
from datetime import datetime, timedelta, timezone  # func 제거

_KST = timezone(timedelta(hours=9))


def _kst_day_start_naive_utc() -> datetime:
    now_kst = datetime.now(_KST)
    day_start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start_kst.astimezone(timezone.utc).replace(tzinfo=None)


def daily_generated_count(db: Session, user_id: int) -> int:
    day_start = _kst_day_start_naive_utc()
    return db.query(Song).filter(Song.user_id == user_id, Song.created_at >= day_start).count()
```

**효과**: "하루" 경계가 **DB 엔진·세션 TimeZone 에 무의존** 해진다. SQLite(UTC naive) 와
Postgres(timestamptz, 세션 TZ) 양쪽에서 동일한 KST 비즈니스 데이 기준으로 계산 — FLOW
(`services/quota_service.py:34` 동일 기준) 와 공존 후에도 같은 사용자가 양측에서 **동일 시각에 일일 한도 리셋**.
`>= day_start` 비교로 경계 inclusive 처리(자정 정각 생성곡도 당일 카운트).

**검증**: `test_kst_day_boundary_counts_only_today` — `day_start + 3h` 곡 카운트, `day_start - 1h` 곡 미카운트 → `== 1`.

---

### 2.11 🟡 D39 — `synced_at` 응답 형식이 DB 엔진에 따라 달라진다

```python
# 음악 routers/flow.py:144
synced_at = (r.updated_at.isoformat() if r.updated_at else "",)
```

`SyncRecord.updated_at` 은 `DateTime(timezone=True)`(`models.py:63`).

- SQLite → naive datetime → `"2026-09-01T12:00:00"`
- Postgres → tz-aware (UTC) → `"2026-09-01T12:00:00+00:00"`

→ **응답 스키마가 배포 환경에 따라 변한다.** FLOW APK 가 naive 를 전제로 파싱하면
공유 커널 DB(변형 B) 전환 시 런타임 오류 또는 조용한 오파싱이 발생한다.
`FlowSongOut.synced_at` 은 `str` 이므로(음악 `schemas.py:247-254`, `synced_at: str` = `:254`) **타입 검사로도 잡히지 않는다.**

**권고**: `.isoformat()` 대신 UTC 고정 정규화(`.astimezone(UTC).replace(tzinfo=None).isoformat() + "Z"`) 후 계약 테스트 고정.

> **✅ 2026-09-02 해결.** 수정 후 코드는 `routers/flow.py:148`.
> 회귀 테스트: `tests/test_d38_d39_contract.py::test_synced_at_normalized_to_utc_z` (D39).

**수정 전 코드** (`routers/flow.py:144`):

```python
synced_at = (r.updated_at.isoformat() if r.updated_at else "",)
```

**수정 후 코드** (`routers/flow.py:148`):

```python
synced_at = (
    (
        r.updated_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        if r.updated_at
        else ""
    ),
)
```

**효과**: SQLite(naive → `"2026-09-01T12:00:00"`) 와 Postgres(tz-aware UTC → `"2026-09-01T12:00:00+00:00"`)
**양쪽에서 동일하게 `...Z` 형식** 으로 정규화된다. FLOW APK 가 naive 를 전제로 파싱하더라도
공유 커널 DB(변형 B) 전환 시 런타임 오류/조용한 오파싱이 발생하지 않는다.
`FlowSongOut.synced_at` 은 `str` 이므로 타입 검사로 잡히지 않던 계약 드리프트를 **런타임 정규화 + 계약 테스트** 로 고정.

**검증**: `test_synced_at_normalized_to_utc_z` — `GET /api/v1/sync/flow` 응답 `synced_at` 이 `endswith("Z")` 고 `+`/`-` 오프셋 미포함.

---



### 2.12 🔴 D44 — `requirements-deploy.txt` 가 실제 import 의존성을 누락해 컨테이너 기동이 실패한다

> **발견 경로**: 2026-09-02 출시 준비 점검(6차 감사 범위 외) 중 신규 발견.
> D29~D39 처럼 "수정이 기본값에서 무효" 유형이 아니라, **배포 경로 자체가 부서져 있는** 유형이다.

**사실 관계** — `Dockerfile:21-22` 는 **오직** `requirements-deploy.txt` 만 설치한다:

```dockerfile
COPY requirements-deploy.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
```

반면 개발 환경은 `requirements.txt`(31 패키지) 를 쓴다. 조치 전 `requirements-deploy.txt` 는
**20 패키지** 였고, `backend/app` 의 실제 서드파티 import(28 모듈) 와 대조하면 다음이 누락되어 있었다.

**기동 실패(HARD — 최상위 import, `ImportError` 가드 없음, `main.py` 에서 도달 가능)**

| 누락 패키지 | import 위치 | 도달 경로 (`scripts/check_import_reach.py`) |
|---|---|---|
| `apscheduler` | `services/media_publisher.py:20` | `main → media_publisher` |
| `cryptography` | `security/encryption.py:19` | `main → security → encryption` |

**실측 A/B** (동일 이미지, 패키지만 제거):

```
$ docker run --rm flow-backend:launch-verify python -c "import backend.app.main"
ModuleNotFoundError: No module named 'cryptography'
  File "/app/backend/app/security/encryption.py", line 19, in <module>
    from cryptography.fernet import Fernet
```

→ **`apscheduler` 누락은 D26(예약 발행 silent-dead) 의 배포 재발** 이고,
**`cryptography` 누락은 PII 필드 암호화 계층 전체의 부재** 다.
둘 다 **기동 즉시** 실패하므로 인지는 빠르지만, "배포만 하면 서버가 안 뜨는" 상태였다.

**조치 (2026-09-02, docker 빌드로 실증 완료)**

추가: `apscheduler==3.10.4`, `cryptography==50.0.1`, `starlette==1.6.0`(직접 import 를 명시 선언),
`redis==8.1.0`, `pywebpush==2.5.0`, `firebase-admin==6.6.0`.

**의도적 제외(사유 명시)** — 근거 없이 빠진 것이 아니다:

| 패키지 | 제외 사유 (실측 근거) |
|---|---|
| `rank-bm25`, `kiwipiepy` | 운영 RAG 의 BM25 는 `services/enhanced_rag/bm25.py` 의 **자체 구현**(순수 표준라이브러리). 이 둘을 쓰는 `services/sparse_index.py` 는 `FLOW backend/app/main.py:131` **시작 워밍업 전용** 이며 이미 `try/except` 로 경고 후 degrade. 게다가 `kiwipiepy` 는 **wheel 이 없고 sdist 만 제공** → slim 이미지에서 C++ 툴체인 필요해 **빌드가 깨진다**. |
| `psutil` | `services/reranker.py:130` lazy import, 미설치 시 `/proc/meminfo` 폴백 존재 → Linux 컨테이너 무영향. |
| `plyer` | `logging_setup.py:260` 의 **Windows 전용** 데스크톱 토스트. `sys.platform.startswith("win")` 분기로 Linux 에서 진입 불가. |
| `anthropic`/`cohere`/`fastembed`/`sentence-transformers` | 파일 상단 설계대로 API 기반 임베딩·선택적 reranker 를 쓰는 배포 프로파일. |

**재발 방지**: `scripts/check_deploy_deps.py` (HARD/SOFT 분류 전수 대조) 와
`scripts/check_import_reach.py` (`main.py` 기동 도달성) 를 추가했다.
현재 `[HARD] 누락 0건` 이며, CI 에 물리면 이 유형의 회귀를 자동 차단할 수 있다.

---

## 3. 미결정 (사용자 입력 대기)

| ID | 내용 | 필요한 입력 |
|---|---|---|
| **D40** | D29 실제 영향 범위 — 관리자 UI 가 `publish_at` 을 **naive** 로 보내는가? | Streamlit 관리자 폼의 값 형식 확인 |
| **D41** | D30 의 실제 토폴로지 — 음악→FLOW 호출이 **Caddy 경유** 인가 **컨테이너 간 직접** 인가? | 확정 시 allowlist 판정 방식이 결정됨(Caddy 경유면 TCP peer = caddy 컨테이너) |
| **D42** | D32 허용치 — 생성 1곡당 이벤트 루프 정체 허용 시간(초) | 제품 SLO |
| ~~**D43**~~ | ~~D33 방향 — 심의 기본 ON vs `unreviewed` 상태 신설~~ | ✅ **2026-09-02 결정**: ① 채택(`enable_moderation=True` 기본 ON). `tests/test_d33_moderation_default.py` 로 고정. |

---

## 4. 권장 처리 순서

| 순위 | 결함 | 이유 |
|---|---|---|
| 1 | **D29** | 잘못된 시각에 발행 = 사용자에게 직접 보이는 오작동. 홍콩 이전 전 반드시 수정 |
| 2 | **D30 + D31** | D27 수정을 실제로 작동하게 만드는 작업. D31 은 그 과정의 장애 유발 경로 |
| 3 | **D32** | 공존 1대에서 blast radius 가 가장 큰 가용성 결함 |
| 4 | **D33 + D34** | 둘 다 "스위치 OFF 라서 안 터지는" 잠재 결함 — 스위치를 켜는 순간 유효화 |
| 5 | **D35** | 마이그레이션 drift. 공유 커널 DB 채택 여부와 무관하게 정리 필요 |
| 6 | **D36** | Plan Z 구현 시 반드시 지나는 경로 |
| 7 | **D37 / D38 / D39** | 기본값 방어 · 계약 일관성 |

---

## 5. 이번 라운드의 방법론적 교훈

1. **"수정이 코드에 있다" 와 "수정이 작동한다" 는 별개다.**
   D24(`flow.py:109`) · D27(`rate_limit.py:168-188`) 모두 옳은 수정이지만,
   **작동 조건이 기본 설정에서 충족되지 않는다**(D33 · D30).
   → 앞으로는 수정 등록 시 **"기본값에서 실제로 발화하는가"** 를 별도 검증 항목으로 넣는다.

2. **fail-closed 는 항상 안전하지 않다.** `flow_internal_token = ""` 은
   "우회 불가"라는 안전한 동작이 아니라 **"우회 기능 영구 정지"** 다(D30).
   → 기본값이 "안전"인지 "무효"인지 **의도 명세** 가 필요하다.

3. **환경 의존은 로컬에서는 보이지 않는다.** 타임존(D29)·DB 엔진(D38/D39)·컨테이너 네트워크(D30) 는
   모두 **공존 토폴로지로 옮기는 순간에만** 드러난다.
   → 설계서의 모든 시간·경계 로직에 "이 코드는 호스트 TZ / DB 엔진에 의존하는가?" 를 묻는다.

4. **같은 리포 안의 대조군이 가장 강한 증거다.** D29(`quota_service.py:34`) · D36(`documents.py:114-117`) ·
   D37(음악 `routers/auth.py:130-131`) 은 모두 **"같은 코드베이스에 올바른写法이 이미 있다"** 는 사실로 증명되었다.
   → 외부 모범 사례를 인용하는 것보다 강하다.

---

## 6. 라운드 완료 요약 (2026-09-02)

### 6.1 상태 집계

| 분류 | 건수 | 항목 |
|---|---|---|
| ✅ 코드 수정 + 회귀 테스트 완료 | **11** | D29, D31, D32, D33, D34, D35, D36, D37, D38, D39, D44 |
| ⚙️ 코드 완료 · 운영 조치만 잔존 | **1** | D30 (`FLOW_INTERNAL_TOKEN` 양측 배포 — §6.3) |
| ⬜ 미결정 (사용자 입력 대기) | **3** | D40, D41, D42 |

→ 6차 감사 대상 11건(D29~D39) 중 **10건 해결**, D30 은 운영 조치 잔존, D44 는 범위 외 신규 발견 후 즉시 해결.

### 6.2 음악 측 회귀 테스트 결과 (출시 게이트)

```
$ cd backend && PYTHONPATH=backend python -m pytest -c <FLOW>/scripts/_music_pytest.ini --rootdir=. tests/ -q -p no:randomly
94 passed, 3 skipped, 0 failed   (49.29s)
```

- 신규 회귀 테스트 **17건**: `test_d32_postprocess_offloaded`(1) · `test_d33_moderation_default`(4) ·
  `test_d34_ssrf_egress`(3) · `test_d35_userlink_single_bridge`(2) · `test_d37_jwt_secret_guard`(5) ·
  `test_d38_d39_contract`(2) + `test_alembic`(1, 버전 테이블 명칭 불일치 정정).
- `3 skipped`: 외부 의존(결제/실제 엔진) 스킵 — 게이트 무력화 아님.
- 0 failed — **출시 게이트 통과**.

> ⚠️ pytest 호출 시 **부모 `korean-gospel-ai/pytest.ini` 상속 금지** 필수: 해당 ini 가 git root 를
> rootdir 로 잡아 `korean-gospel-ai/app.py`(Streamlit UI) 를 `backend/app` 앞에 삽입해
> `ModuleNotFoundError: No module named 'streamlit'` 로 마스킹된다. 반드시
> `-c <FLOW>/scripts/_music_pytest.ini --rootdir=.` 로 격리 실행.

### 6.3 출시 전 잔류 운영 조치 (D30)

D30(`FLOW_INTERNAL_TOKEN` 미설정 → D27 자기 차단 수정이 기본 배포에서 no-op)는
**코드 수정은 완료**(토큰 우회 분기 + docker-compose 필수 주입 `${FLOW_INTERNAL_TOKEN:?...}`)
되었으나, **실제 토큰 생성·양측 `.env.production` 배포는 운영 액션** 이다.

| 단계 | 조치 | 상태 |
|---|---|---|
| 1 | 동일한 `FLOW_INTERNAL_TOKEN` 값 생성(고엔트로피, e.g. `openssl rand -hex 32`) | ⬜ 사용자 |
| 2 | FLOW `.env.production` 에 `FLOW_INTERNAL_TOKEN=<값>` 기입 | ⬜ 사용자 |
| 3 | 음악 `.env.production` 에 동일 값 기입(`auth.py:208-209` 토큰 교환 검증 503 해소) | ⬜ 사용자 |
| 4 | 재기동 후 음악→FLOW 일괄 push 가 60RPM 자기 차단 없이 통과 확인 | ⬜ 사용자 |

→ 이 4단계가 끝나야 D27 수정이 **실제 동작** 한다("수정 설치 ≠ 작동" 원칙 1). 미완료 시
음악→FLOW push 는 여전히 자기 차단되나, **보안상 fail-closed 이므로 서비스 외부 노출 리스크는 없음**.

### 6.4 이번 라운드가 남긴 설계 원칙 (이후 변경 시 준수)

1. 모든 시간/일 경계는 **애플리케이션에서 KST 비즈니스 데이** 로 계산(DB 함수 금지).
2. 모든 대외 응답 시각은 **UTC `.isoformat()+"Z"`** 로 정규화(엔진 무의존).
3. 사용자 영향 URL 의 **모든 egress 출구** 는 `ssrf_guard` 게이트(출구 단위 방어).
4. 신원 브리지는 **`UserLink` 단일 경계** 만(데드 컬럼 금지).
5. 서명/시크릿 기본값은 **부팅/서명 시 503 fail-closed**(FLOW · 음악 대칭).
6. "수정 설치" 후 반드시 **"기본값에서 실제 발화하는가"** 를 회귀 테스트로 고정.
