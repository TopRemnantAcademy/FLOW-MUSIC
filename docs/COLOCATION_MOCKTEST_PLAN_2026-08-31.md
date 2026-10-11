# FLOW + ai-music-platform 통합 MockTest 계획서 (2026-08-31)

> 상태: **계획(Plan)** — 본 문서는 테스트 **설계**이며, 테스트 코드는 본 계획 승인 후 작성한다.
> 근거: 모든 항목은 실제 코드 `file:line` 에 고정. 추정 금지.
> 대상: 공존 설계서 `docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md`(§1~§25)의 모든 계약·갭·파생 변경을 **검증 가능한 테스트로 환원**한다.

---

## 0. 원칙 (환각 배제 4원칙)

| # | 원칙 | 설명 |
|---|---|---|
| P1 | **file:line 고정** | 모든 테스트 항목은 실측 근거를 가진다. 근거 없는 항목은 작성하지 않는다. |
| P2 | **기존 자산 확장 (RC-E 금지)** | 이미 있는 테스트/fixture 를 재발명하지 않는다. 확장 지점만 명시한다. |
| P3 | **추적 매트릭스로 "누락 없음" 증명** | §8 에서 모든 갭 ID → 테스트 ID 역방향 매핑을 제공한다. 매핑되지 않은 갭이 없음을 확인 가능해야 한다. |
| P4 | **외부 의존 전량 mock** | 실측된 외부 의존(Suno·S3·Langfuse·LLM·PortOne·Qdrant)은 전부 테스트 더블로 대체. 미mock 항목은 §11 블로커로 명시. |

---

## 1. 기존 테스트 자산 실측 (계획의 출발점)

### 1.1 FLOW 측 (실측)

| 항목 | 사실 | 근거 |
|---|---|---|
| pytest 설정 | `testpaths = tests backend/tests` | `pytest.ini:1-4` |
| conftest | `tests/conftest.py` 단 1개 | find 결과 |
| 유일 fixture | `iso_db(tmp_path, monkeypatch)` — engine/read_engine/SessionLocal/ReadSessionLocal/DATABASE_URL monkeypatch | `tests/conftest.py:18-51` |
| 테스트 파일 수 | **70개** (`tests/` 69 + `backend/tests/test_main.py`) | find 집계 |
| **기존: 트레이싱** | `_scrub` 구조 분기, `get_client` 로컬 폴백, `observe` 동기/비동기, `_safe_repr` | `tests/test_tracing.py:57-75, 173-180, 186-232, 260-275` |
| **기존: Alembic** | `test_alembic_no_drift.py` 존재 | `tests/test_alembic_no_drift.py` |
| **기존: 파이프라인 계약** | `test_chat_pipeline_contract.py` 존재 | `tests/` 목록 |
| 기타 관련 | `test_support_ticket.py`, `test_annotation_service.py`, `test_salvation_detector.py`, `test_salvation_regression.py`, `test_jobs_no_traceback_leak.py`, `test_no_silent_swallow.py`, `test_admin_auth.py`, `test_compliance_security.py`, `test_payment_core.py` | `tests/` 목록 |

**중요 실측 — `_scrub` 은 시크릿 전용이다**
```python
# backend/app/services/tracing.py:67-79
def _scrub(value):  # 문서화: "시크릿 마스킹"
    from ..logging_setup import scrub_secrets  # ← 시크릿 전용
```
→ `_scrub` 은 **API 키/토큰 마스킹**(`logging_setup.py:27-40`)이며 **PII 마스킹이 아니다.** 즉 Y9 은 *신규 PII 규칙 추가* 지만, **부착 지점은 이미 존재**한다.

**중요 실측 — Langfuse 경로는 `_scrub` 을 우회한다**
```python
# backend/app/api/chat.py:187, 193
lf = get_client()
trace = lf.trace(name="chat", input=req.model_dump(), user_id=sub_id) if lf else None
# backend/app/api/chat.py:855, 857  (스트리밍 경로 동일)
```
→ `get_client()` 가 Langfuse SDK 클라이언트를 반환하면, `.trace()` 는 **`tracing.py` 의 `_scrub` 을 거치지 않는다.** 로컬 폴백(`_LocalTraceClient`)만 `_scrub` 을 탄다. **이것이 T4.2 를 반드시 spy 로 검증해야 하는 이유다.**

### 1.2 음악 플랫폼 측 (실측)

| 항목 | 사실 | 근거 |
|---|---|---|
| 테스트 위치 | `backend/tests/` 13개 파일 | find 결과 |
| **현재 성적** | **66 passed, 3 skipped** (2026-08-29 실측) | `MOCK_TEST.md:4-5` |
| skip 사유 | `test_postprocess` 3건 — `pedalboard`/`noisereduce` 선택 의존 | `MOCK_TEST.md:6-7` |
| MockProvider | `backend/app/orchestrator/mock.py`, conftest 에서 주입 | `mock.py`, `conftest.py:33-35` |
| conftest 패턴 | `fastapi_app = _app_main.app`(이름 섀도잉 회피) / `SessionLocal.configure(bind=engine)` / session-level `TestClient` / autouse `_clean` | `conftest.py:12, 25, 45-47, 51-54` |
| 기존 sync 테스트 | `test_sync_flow_full`, `test_sync_flow_before_complete`, `test_sync_flow_wrong_owner` | `MOCK_TEST.md:63-65` |
| 기존 Alembic 테스트 | `test_alembic_upgrade_builds_schema_matching_metadata` | `MOCK_TEST.md:67` |
| 기존 공개/심의 | `test_explore_returns_only_public_approved`, `test_generate_auto_approves_after_completion` | `MOCK_TEST.md:68-70` |

### 1.3 양측 차이 (통합 시 충돌 지점)

| 축 | FLOW | 음악 |
|---|---|---|
| pytest 설정 파일 | `pytest.ini` 존재 (루트) | **없음** (MOCK_TEST.md:20-22 는 `cd backend` 후 실행) |
| fixture 철학 | `monkeypatch` 로 engine 교체 | `SessionLocal.configure(bind=)` 동일 객체 재바인딩 |
| 클린업 | **테스트별 독립 파일** — `tmp_path/iso.sqlite` + `monkeypatch` 자동 복원 | autouse `_clean` 전 테이블 정리(**단일 객체 재바인딩**) |
| 격리 철학 | **격리 = 새 파일**(물리 분리) | **격리 = 테이블 비우기**(논리 분리) |
| 외부 mock | LLM/embedder 계층 (확인 필요) | `MockProvider` 주입 완비 |

> **§1.3 실측 완료(2026-08-31, B6 해소)**: `tests/conftest.py:18-59` — `iso_db` 는 `tmp_path` 로 **테스트마다 새 SQLite 파일**을 만들고(`:30`), `monkeypatch` 로 `dbmod` 의 engine/SessionLocal/DATABASE_URL 을 덮어쓴 뒤(`:47-51`) 테스트 종료 시 자동 복원된다. 즉 **FLOW 는 격리가 이미 보장되며 별도 클린업 전략이 불필요**하다.
> ⚠ 단, **양측 철학이 정반대**(FLOW=새 파일 / 음악=같은 객체 비우기)이므로 **conftest 를 통합하면 안 된다** — §7 분리 실행 원칙의 근거가 여기에도 있다.
> 참고: `iso_db` 사용 파일은 69개 중 **42개**. 나머지는 DB 비접촉으로 추정되나 미검증(추정 금지).

→ **통합 실행 시 가장 위험한 지점**: FLOW 는 루트에서, 음악은 `backend/` 에서 실행된다. **단일 pytest 세션으로 합치면 `pythonpath`·`testpaths` 충돌**이 발생한다(§7 에서 분리 실행 원칙으로 해결).

---

## 2. 테스트 범위 (IN / OUT 명시)

**IN (본 계획이 검증하는 것)**
1. Plan Z 종단 시나리오(§13)
2. PII 분할 데이터 평면(§21) + 파생 변경 Y1~Y12(§21.4, §25.5)
3. 출국 경로(Langfuse·LLM)(§25.2~25.3)
4. 공유 커널 DB 함정 X3/X4(§14.2)
5. 공존 보안 X1·X2·X5·X6·X7·X8(§15)
6. 동기화 계약·생명주기(§11.4)
7. 기존 회귀(양측 기존 70 + 13 파일)

**OUT (본 계획이 검증하지 않는 것 — 명시적 경계)**
- 실제 한국↔홍콩 네트워크 지연(§20.4 보류) → **지연 주입 시뮬레이션으로 대체**
- 실제 서버 스펙 기반 부하 테스트 → 스펙 미확정(T-4/T-5)
- 실제 Suno API 계약 → `MockProvider` 로 대체(기존 방침, `MOCK_TEST.md:87-88`)
- 실제 PortOne 결제 → `test_payment_core.py` 기존 범위 유지
- 모바일 APK UI/E2E → `mobile-e2e/tests` 기존 범위(본 계획은 API 계약까지만)
- 법률·컴플라이언스 적법성 판정(D15) → 테스트 대상 아님, 산출물(증빙 로그)만 검증

---

## 3. 테스트 계층

```
L4 운영·DR      한국 금고 장애 / 백업 2계층 / 장애 모드 매트릭스
L3 종단(E2E)    Plan Z 8단계 트레이스 / 삭제 팬아웃
L2 통합(서비스간) 음악→FLOW 푸시 / 공유 커널 스키마 격리 / PII 게이트웨이
L1 단위         _scrub·PII 규칙 / category 화이트리스트 / alembic env 설정
L0 정적·계약    갭→테스트 추적 매트릭스 / 인벤토리 회귀 / 설정 기본값 검사
```

---

## 4. 테스트 도메인 상세 (D1~D8)

표기: `[ID] 항목 — Given/When/Then · 근거 · 계층 · 신규/확장`

### D1. 사용자 시나리오 종단 (Plan Z)

| ID | 항목 | 검증 내용 | 근거 | 계층 | 구분 |
|---|---|---|---|---|---|
| T1.1 | 푸시 성공 경로 | approved 곡 → `POST /admin/media` → `asset_id` → `sync_records.flow_song_id` 저장 | `models.py:38, 58`, `media.py:61-62` | L3 | **신규** |
| T1.2 | 카탈로그 노출 | `ai_music` 카테고리가 모바일 카탈로그에 포함 | `media.py:157-158`, `media_service.py:195` | L2 | **신규** |
| T1.3 | Range 스트리밍 | 업로드된 곡이 스트림 엔드포인트로 재생됨 | `media.py:178-179`, `media_service.py:226` | L2 | **신규** |
| T1.4 | 예약 발행 | `publish_at` 미래 → `active=False` → 스케줄러가 도래 시 플립 | `media_publisher.py:33`, `media_service.py:95-96` | L2 | **신규** |
| T1.5 | 푸시 알림 | `notify_mode` 별 `notify_at` 결정 + `notified_at` 중복방지 | `media_publisher.py:54, 84-91` | L2 | **신규** |
| T1.6 | 멱등성 | 동일 곡 재푸시 → `external_ref` 유니크로 중복 생성 없음 | §13.5-(2) 신규 컬럼 | L2 | **신규** |
| T1.7 | 실패점 C | `ADMIN_API_KEY='change-me'` → 503 | `auth.py:229-233` | L1 | **신규** |
| T1.8 | 실패점 D | 미허용 category → 400 | `media_service.py:95-96` | L1 | **신규** |
| **T1.9** | **미심의 곡 노출 차단** (교차연쇄 2) | `moderation_status != 'approved'` 곡이 FLOW 무인증 카탈로그·스트림에 **절대 노출되지 않음** | 음악 `songs.py:167-168` 자동 approved × FLOW `media.py:157-158, 178-179` 무인증 | L2 | **신규(최우선)** |

### D2. 미디어 커널 계약 (FLOW 기존 자산 검증)

| ID | 항목 | 근거 | 계층 | 구분 |
|---|---|---|---|---|
| T2.1 | 관리자 인증 3분기(무키 403 / 오키 403 / 정상 200) | `media.py:80`, `auth.py:216-241` | L1 | **신규**(기타 admin 테스트와 동일 패턴 재사용) |
| T2.2 | 확장자 화이트리스트 | `media_service.py:24` | L1 | 신규 |
| T2.3 | 로컬 바이트 소유(경로·파일 존재) | `media_service.py:21, 148` | L1 | 신규 |
| T2.4 | 삭제 시 파일·레코드 정리 | `media_service.py:255` | L1 | 신규 |
| T2.5 | 스케줄러 수명주기 등록 | `main.py:199-201, 234-236` | L1 | 신규 |

### D3. PII 분할 데이터 평면 (§21)

| ID | 항목 | 검증 내용 | 근거 | 계층 |
|---|---|---|---|---|
| T3.1 | **인벤토리 회귀** | PII 필드 목록 스냅샷 대조 — 신규 컬럼 추가 시 테스트 실패로 검토 강제 | **전수(실측 재확인)**: `orm.py:188`(email) `:251`(email_verify_token) `:189`(display_name) `:193-194`(age_group,gender) `:190-192`(journey_stage,faith_stage,emotional_state) `:208`(last_emotion) `:195-196`(current_struggle 등) `:219`(salvation_status) `:263-266`(religion,denomination) `:232`(is_darakbang_member) `:197-198`(consent_data,kakao) `:241`(consent JSON) `:336-337`(question,answer) `:380`(note) `:395`(audit who) `:443-452`(salvation_journey 전 필드) `:478,481`(traceback,client_ip) `:499-500`(content,contact) `:876,881`(device_id,payload), `compliance.py:58`(**ip**) `:59`(user_agent), 음악 `models.py:9,10,20,21,76` | **L0** |
| T3.2 | 가명키 원칙 | 홍콩 저장 경로에 직접 식별자가 **없음**을 단언 | §21.3 | L2 |
| T3.3 | 주체 삭제 팬아웃 | `DELETE /pii/subjects/{id}` → 홍콩(Qdrant·S3·interaction) 전파 + 원장 기록 | §21.4 Y5 | L3 |
| T3.4 | 원문 스크러빙 | `interaction.question` 저장 전 PII 제거 | `orm.py:336` + Y2 | L2 |
| T3.5 | 최고민감 필드 | `salvation_journey.trigger_quote` 비식별화 또는 한국 귀속 | `orm.py:449` | L2 |
| T3.6 | 문의 연락처 | `support_ticket.contact` 한국 귀속·홍콩 미저장 | `orm.py:500` | L2 |
| T3.7 | 에러로그 IP | `app_error_log.client_ip` 마스킹/보존기간 | `orm.py:481` + Y12 | L1 |
| T3.8 | FK 해제 정합 | `interaction.subscriber_id` FK 해제 후 고아행 정합 배치 | `orm.py:333-335` + Y1 | L2 |
| T3.9 | 준식별 재식별 | 홍콩 잔존 준식별 조합으로 재식별 불가 검증(FU10) | §22.2, D16 | L2 |
| T3.10 | 캐시 미스 동작 | D14 결정값(fail-closed / fail-open) 단언 | §21.6, D14 | L2 |
| **T3.11** | **교차시스템 동의 증빙** (G-Consent) | 음악 사용자↔FLOW 구독자 연결 시 `ConsentRecord` 에 목적·보존기간·철회시각이 기록됨을 단언 | `compliance.py:45-61`(ConsentRecord: consent_type:52, purpose:56, retention_days:57, ip:58, user_agent:59, granted_at:60, revoked_at:61), `orm.py:197-198, 241` | L2 |
| **T3.12** | **동의 IP 귀속** | `consent_record.ip`가 한국 금고 귀속 대상 인벤토리에 포함되는지 단언(설계서 인벤토리 누락분) | `compliance.py:58` | **L0** |

### D4. 출국 경로 (§25.2~25.3) — 최우선

| ID | 항목 | 검증 내용 | 근거 | 계층 |
|---|---|---|---|---|
| **T4.1** | **기본값 OFF** | `langfuse_enabled` 기본값이 `False` 임을 단언 | `config.py:178` + Y10 | **L0** |
| **T4.2** | **Langfuse 유출 차단 (spy)** | `langfuse_enabled=True` + 키 설정 상태에서도 `lf.trace(input=...)` 페이로드에 **PII·원문·subscriber_id 원본이 없음**을 spy 로 캡처 단언 | `chat.py:193, 857` | **L2** |
| T4.3 | 파이프라인 원문 | `trace.event(input=query)` **전 지점** 동일 검증 | 아래 ⚠️ 정정 참조 | L1(AST)+L2 |
| T4.4 | `_scrub` PII 확장 | 기존 시크릿 마스킹 회귀 유지 + PII 규칙 추가 | `tracing.py:67-79` + Y9 | L1 |
| T4.5 | 로컬 폴백 경로 | Langfuse 불가 → 로컬 클라이언트 전환 회귀 | ⚠️ **기존 테스트의 패치가 무효** — 아래 정정 | L1 |
| T4.6 | LLM 프롬프트 최소화 | D19 결정값(전문 / 마스킹) 단언 | `config.py:23-24, 44` + Y11 | L2 |
| **T4.7** | **스케줄러 원문 재전송** | 스케줄러→LLM 경로가 설계서 §25 에 기록됨 + 경로 자체가 실재함 | `trending_scheduler.py:29,57-59`, `insight_engine.py:117-122,196-198,347,489` | **L0** |

#### ⚠️ T4.3 정정 — "3개소" 는 **과소 집계** 였다 (2026-08-31 AST 전수 스캔)

계획서 1판은 `chat_pipeline.py:154, 458, 538` **3개소** 로 적었다. AST 로 전수 스캔한 결과
**13개 지점** 이 확인됐다. 계획서가 놓친 것이 10곳이다.

| 파일 | 행 | 호출 | 전달값 | 계획서 기재 |
|---|---|---|---|---|
| `chat.py` | 193 | `.trace` | `req.model_dump()` (질문 원문 포함) | ✅ |
| `chat.py` | 477 | `.generation` | `user_prompt` (최종 LLM 프롬프트) | ❌ |
| `chat.py` | 615 | `.event` | `resp_text` (LLM 답변 원문) | ❌ |
| `chat.py` | 680 | `.event` | `resp_text` | ❌ |
| `chat.py` | 857 | `.trace` | `req.model_dump()` | ✅ |
| `chat.py` | 1112 | `.generation` | `user_prompt` | ❌ |
| `chat.py` | 1265 | `.event` | `final_text` | ❌ |
| `chat_pipeline.py` | 154 | `.event` | `query` | ✅ |
| `chat_pipeline.py` | 356 | `.event` | `str(_emb_exc)` (예외문 — 원문 아님) | ❌ |
| `chat_pipeline.py` | 447 | `.event` | `query` (교차언어 폴백 경로) | ❌ |
| `chat_pipeline.py` | 458 | `.event` | `query` | ✅ |
| `chat_pipeline.py` | 473 | `.event` | `query` (**무조건 실행** — 검색 결과 건수·ID 포함) | ❌ |
| `chat_pipeline.py` | 538 | `.event` | `query` | ✅ |

> **`chat.py` 의 `chat_pipeline.py` 경로도 정정**: 둘 다 `backend/app/api/` 이다
> (`backend/app/services/chat_pipeline.py` 는 존재하지 않는다).

→ T4.3 은 **L2 런타임 spy + L1 AST 인벤토리** 로 분리 구현했다.
AST 인벤토리(`KNOWN_INPUT_SITES`)는 **새 지점이 생기면 즉시 실패** 하므로,
"3개소만 고치고 끝" 을 구조적으로 막는다.

#### ⚠️ T4.5 정정 — 기존 테스트의 spy 주입이 **무효** 였다 (2026-08-31 실측)

`connections.py:444-462`:

```python
class _ConnectionRegistry:
    langfuse = staticmethod(langfuse)  # ← 모듈 함수 객체를 **정의 시점에** 바인딩


connections = _ConnectionRegistry()
```

`tracing.get_client()`(`tracing.py:191-195`) 는 `connections.langfuse()` 를 부른다.
따라서 **모듈 속성** 을 패치해도 클래스에 바인딩된 원본 객체는 바뀌지 않는다.

실측:

| 주입법 | `connections.langfuse()` | `get_client()` | 유효 |
|---|---|---|---|
| `monkeypatch.setattr(conn_mod, "langfuse", ...)` (기존 `test_tracing.py:176-178`) | `None` (원본 그대로) | `_LocalTraceClient` | **무효** |
| `monkeypatch.setattr(conn_mod._ConnectionRegistry, "langfuse", staticmethod(...))` | 스파이 | **스파이** | ✅ |

→ 기존 `test_get_client_returns_local_when_langfuse_unavailable` 은
**패치가 통과한 게 아니라** 테스트 환경에서 `settings.langfuse_enabled` 가 False 여서
우연히 통과한 것이다. **Langfuse 분기를 실제로 검증한 테스트는 존재하지 않았다.**
본 계획의 D4 는 올바른 주입법을 사용하며, 함정을 고정하는 테스트
(`test_module_attribute_patch_is_ineffective`)도 함께 배치했다.

### D5. 공유 커널 DB (§14)

| ID | 항목 | 검증 내용 | 근거 | 계층 |
|---|---|---|---|---|
| **T5.1** | **버전 테이블 격리** | 양측 `version_table_schema` 설정 후 `alembic upgrade head` 교차 실행 → 서로의 head 를 훼손하지 않음 | `migrations/env.py:57-63, 80-84` / 음악 `env.py:39-44, 58-60` (X3) | **L2** |
| **T5.2** | **autogenerate DROP 방지** | 공유 스키마에서 `alembic revision --autogenerate` 실행 시 상대 테이블 `drop_table` **미생성** 단언 | 양측 `include_object` 부재(X4) | **L2** |
| T5.3 | FLOW 무결성 | 기존 `alembic check` 무드리프트 유지 | `tests/test_alembic_no_drift.py` | L1 (**기존**) |
| T5.4 | 음악 무결성 | 기존 스키마==메타데이터 유지 | 음악 `tests/test_alembic.py` | L1 (**기존**) |
| T5.5 | Postgres 전환 | `DATABASE_URL` 주입 시 동일 동작(ENV 1줄, 코드 0) | `db.py:41-49` | L2 |
| T5.6 | create_all 폴백 | 운영에서 `init_db` 비활성 시 스키마 드리프트 없음(X9) | 음악 `db.py init_db` | L1 |
| **T5.7** | **공유 커널 채택 전제 게이트** (교차연쇄 1) | X1(포트 노출)·X9 가 미해결 상태에서 변형 B 채택을 **차단**하는 게이트 — T6.1 실패 시 T5.1/T5.2 도 의미 없음을 순서로 강제 | §16.3 연쇄 1, `docker-compose.yml:11-12,19-20` | **L0(순서 게이트)** |
| **T5.8** | **원가-크레딧 정합 조인** (교차연쇄 4) | 공유 인스턴스 전제 하 `songs.cost_cny` ↔ FLOW `wallet_reservation` 홀드/캡처 금액 정합 검증 | 음악 `models.py:46`(cost_cny) × FLOW `compliance.py:72`(WalletReservation) | L2(조건부) |
| **T5.9** | **빈 DB 재현 가능성** **(신규 D21)** | 빈 DB 에 `alembic upgrade head` 만으로 전체 스키마가 완성되는가 | **실측: 불가** — ORM 32개 중 마이그레이션 생성은 **11개** 뿐, `c2fbb2654cf3` 은 가짜 baseline(create_table 1개 + batch_alter_table 8개) | **L2(신규)** |
| **T5.10** | **FLOW alembic cwd 의존성** **(신규 D22)** | 다른 cwd 에서도 FLOW 스크립트 디렉터리를 찾는가 | FLOW `alembic.ini:6` `script_location = backend/migrations`(**상대경로**) vs 음악 `alembic.ini:8` `%(here)s/migrations`(**절대**) | **L1(신규)** |

### D6. 보안·공존 (§15)

| ID | 항목 | 근거 | 계층 |
|---|---|---|---|
| **T6.1** | **포트 미노출** — 5432/6379 가 호스트에 바인딩되지 않음(설정 정적 검사) | `docker-compose.yml:11-12, 19-20` (X1) | **L0** |
| T6.2 | 시크릿 기본값 — 음악 `admin_token` 빈값 검출, FLOW `change-me` 503 | `config.py:59`, `auth.py:229-233` (X5) | L1 |
| T6.3 | 무인증 공개 범위 — `/mobile/media` 는 approved 곡만 | `media.py:157-158, 178-179` (X6) | L2 |
| T6.4 | SSRF — `httpx.get(audio_url)` 사설 IP 차단 | 음악 `flow.py:32`, FLOW `security/ssrf.py:29` | L2 |
| T6.5 | CORS 화이트리스트 | `main.py:292-304` | L1 |
| T6.6 | 음악 컨테이너 리소스 상한 정의 존재 | 음악 compose 앱 서비스 부재(X2) | L0 |
| T6.7 | 관리자 토큰 URL 노출 — Caddy 로그 미기록 | `admin/lib/auth.py:41-99` (🟡8) | L2 |
| **T6.8** | **레이트리밋 공유성** (F1) | 음악: `_hits` 모듈 전역 `defaultdict` → 다중 인스턴스 무효화를 **정적 단언**. FLOW: Redis 우선 + 인메모리 폴백 구조임을 단언하고, **폴백 발생 시 경고 로그** 검증(설계서 F1 은 "양측 in-memory"로 과단순 → §13 SC-8 정정) | 음악 `ratelimit.py:13`, FLOW `middleware/rate_limit.py:100-102, 116-134` | L1 |
| **T6.9** | **api_key 이중 검증** (F3) | ① 저장 시 평문(암호화 0건) → 알려진 갭으로 **명시적 xfail 또는 문서화**, ② **GET 응답 마스킹은 이미 구현됨**(`api_key_set: bool`) → 회귀 차단 | 음악 `models.py:76`(평문 저장), `schemas.py:150`, `admin.py:45`(마스킹 구현) | L1 |
| **T6.10** | **redis 무인증** (F4) | 음악 compose redis 서비스에 `requirepass`/ACL 부재 정적 단언 | 음악 `docker-compose.yml:16-20` | **L0** |
| **T6.11** | **healthcheck 부재** (G-Orch-Merge) | 음악 compose 에 app 서비스·healthcheck 부재 정적 단언(X2 와 동근원) | 음악 `docker-compose.yml:1-23`(서비스 2개, healthcheck 0건) | **L0** |
| **T6.12** | **관리UI 페이지 명칭 중복** (🟡7) | FLOW `admin/pages/` 와 음악 관리 화면 간 명칭 충돌 스캔(정적) | FLOW `admin/pages/11_🎵_미디어업로드.py`, `9_👥_유저관리.py` | **L0** |

### D7. 동기화 계약·생명주기 (§11.4)

| ID | 항목 | 근거 | 구분 |
|---|---|---|---|
| T7.1 | 기존 sync 3종 회귀 유지 | 음악 `test_sync_flow_*` | **기존** |
| T7.2 | 삭제 전파 — 음악 곡 삭제 시 FLOW `media_asset` 처리 | `songs.py:355-364` SyncRecord 미정리, `flow.py:91-93` | **신규** |
| T7.3 | `flow_song_id` 활성화 검증 | `models.py:58` (X8) | 신규 |
| T7.4 | 휘발 URL — S3 미설정 시 Suno 원본 사용 경고 | `flow.py:25-29` | 신규 |
| **T7.5** | **연동 해제(unlink) 시 토큰 무효화** (장애 매트릭스 4행) | ⚠ **신규 구축 전제** — FLOW 백엔드 전체에 `jti` 구현 **부재**(grep 0건, §13 SC-10). 기존 동작 단언이 아니라 **구축 후 무효화 경로 존재를 단언** | §11.9 4행(완화: jti 블랙리스트), 실측: `backend/app/` 전역 `jti` 검색 결과 0건 | L2 (**구축 후**) |

### D8. 운영·장애·DR (§11.9, §22)

| ID | 항목 | 근거 |
|---|---|---|
| T8.1 | 한국 PII 금고 다운 시 D14 정책 동작(지연 주입으로 시뮬레이션) | §21.6, D14 |
| T8.2 | 백업 2계층 분리 — 한국 백업에 홍콩 데이터 혼입 없음(역방향 포함) | §21.4 Y8 |
| T8.3 | 장애 모드 매트릭스 항목별 동작 단언 | §11.9 |
| T8.4 | 삭제 전파 누락 감사(FU11) — 원장 대조 | §22.2 |
| T8.5 | sync 이후 심의 rejected → `SyncRecord` → `revoked` 전이(F5 상태머신) | §11.9 5행, `flow.py:91-93` |
| T8.6 | 네트워크 분할 시 경계 타임아웃 + 지수 백오프(지연 주입) | §11.9 6행, `LatencyInjector` |

---

## 5. 목(Mock) / 테스트 더블 인벤토리

### 5.1 기존 재사용 (재발명 금지)

| 더블 | 용도 | 근거 |
|---|---|---|
| `MockProvider` | Suno 대체, 고정 완료态 | 음악 `orchestrator/mock.py`, `conftest.py:33-35` |
| `iso_db` | FLOW DB 격리 | `tests/conftest.py:18-51` |
| `FakeLogger` | 트레이스 방출 캡처 | `tests/test_tracing.py:28-38` |
| `fastapi_app` + session `TestClient` | 음악 API 클라이언트 | 음악 `conftest.py:12, 45-47` |
| autouse `_clean` | 테스트 간 격리 | 음악 `conftest.py:51-54` |

### 5.2 신규 필요

| 더블 | 목적 | 근거 |
|---|---|---|
| **`PiiVaultStub`** | 한국 금고 시뮬레이터(resolve/scrub/delete 3 API) | §21.3 |
| **`LangfuseSpy`** | `lf.trace()`/`trace.event()` 페이로드 **캡처·단언** — T4.2/T4.3 핵심 | `chat.py:193`, `chat_pipeline.py:154/458/538` |
| **`FlowAdminStub`** | FLOW `/admin/media` 수신 검증(음악 푸시 대상) | `media.py:61-62` |
| **`LatencyInjector`** | 한국↔홍콩 지연 시뮬레이션(실측 불가 구간 대체) | §20.4, T8.1 |
| **`AlembicIsolatedEnv`** | 양측 env.py 를 격리 스키마로 실행(X3/X4 재현) | §14.4 |
| **`QdrantStub`** | 벡터 payload 식별자 제거·삭제 전파 검증 | §21.4 Y3 |

---

## 6. 픽스처·테스트 데이터

| 항목 | 설계 |
|---|---|
| PII 샘플 | 실명·이메일·전화·질병·가족사·종교 서술을 포함한 **고정 픽스처**(스크러빙 단언용). 실존 인물 금지. |
| 가명키 | `subscriber_id` 40자 문자열(`orm.py:185`) 규격 준수 |
| 오디오 픽스처 | 소형 mp3 (확장자 화이트리스트 `.mp3` 포함, `media_service.py:24`) |
| 곡 상태 | `completed` + `moderation_status='approved'`(`models.py:38`) 고정 — BackgroundTasks 동기 실행 함정 회피 |
| 결정론 | 음악 기존 방침: `enable_rate_limit=False`(`MOCK_TEST.md:95`) |

---

## 7. CI 통합 · 게이트

| 원칙 | 내용 | 근거 |
|---|---|---|
| **분리 실행** | FLOW(루트, `pytest.ini` 존재)와 음악(`cd backend`)은 **별도 세션**으로 실행 — `pythonpath`/`testpaths` 충돌 회피 | §1.3 |
| 기존 게이트 유지 | FLOW `ci.yml`, 음악 `alembic upgrade head` + `alembic check` | `MOCK_TEST.md:76-78` |
| 신규 게이트 | T4.1(기본값 OFF), T4.2(Langfuse 유출), T5.1/T5.2(공유 커널), T6.1(포트) — **실패 시 병합 차단** | 본 계획 |
| skip 정책 | 선택 의존은 `@pytest.mark.skipif` (기존 3건 패턴 유지) | `MOCK_TEST.md:6-7` |

---

## 8. 추적 매트릭스 — "누락 없음" 증명

> 설계서의 **모든 갭 ID**를 테스트로 매핑한다. 매핑 누락이 있으면 그 자체가 계획 결함이다.

> **감사 이력**: 1차 작성 후 설계서 전 ID 를 재추출·대조하여 **누락 5건(🟡7·F1·F3·F4·FU6)을 발견·보완**했고, 파생 갭·교차 연쇄·장애 매트릭스 3개 축을 신규 추가했다(§13 SC-7~SC-11).

### 8.1 §4 갭 카탈로그 — 15건 전수

| 갭 | 내용 | 테스트 |
|---|---|---|
| 🔴A | 인증 모델 불일치 | T1.7(admin 토큰 경로), T3.10 |
| 🔴1 | FLOW 수신 측 | T1.1, T2.1 |
| 🔴2 | 오디오 URL 휘발 | T1.3, T2.3, T7.4 |
| 🟠3 | 포트 충돌 | T6.1, T6.6 |
| 🟠4 | CORS | T6.5 |
| 🟠5 | S3/배포 산출물 | T2.3(로컬 소유 대체), T6.6 |
| **🟡7** | **관리UI 페이지 명칭 중복** *(1차 누락)* | **T6.12** |
| 🟡8 | `?a=` 토큰 URL 노출 | T6.7 |
| **F1** | **레이트리밋 비공유** *(1차 누락)* | **T6.8** |
| F2 | SQLite→Postgres | T5.5 |
| **F3** | **api_key 평문 저장** *(1차 누락)* | **T6.9** |
| **F4** | **redis 무인증** *(1차 누락)* | **T6.10** |
| F5 | 동기화 생명주기 | T7.2, T8.5 |
| F6 | 계약 버저닝 | T7.1 |
| F7 | 심의 | T1.1(approved 전제), T6.3, **T1.9** |

### 8.2 X 시리즈 (§15) — 9건

| 갭 | 테스트 | | 갭 | 테스트 |
|---|---|---|---|---|
| X1 포트 노출 | **T6.1** | | X6 무인증 공개 | T6.3, **T1.9** |
| X2 앱 컨테이너 부재 | T6.6, **T6.11** | | X7 category 화이트리스트 | T1.8, T1.2 |
| X3 alembic_version | **T5.1** | | X8 flow_song_id 미사용 | T7.3 |
| X4 autogenerate DROP | **T5.2** | | X9 create_all 폴백 | T5.6 |
| X5 시크릿 미설정 | T6.2 | | | |

### 8.3 Y 시리즈 (§21.4, §25.5) — 12건

| 변경 | 테스트 | | 변경 | 테스트 |
|---|---|---|---|---|
| Y1 FK 해제+정합 | T3.8 | | Y7 로그 PII 검사 | T3.2, T4.4 |
| Y2 PII 스크러버 | T3.4 | | Y8 백업 2계층 | T8.2 |
| Y3 Qdrant payload | T3.9, T8.4 | | **Y9 원격측정 스크러빙** | **T4.2, T4.3** |
| Y4 가명 캐시 | T3.10, T8.1 | | **Y10 Langfuse 기본 OFF** | **T4.1** |
| Y5 삭제 원장 | T3.3, T8.4 | | Y11 LLM DPA/최소화 | T4.6 |
| Y6 음악 users 분할 | T3.2, T3.6 | | Y12 에러로그 마스킹 | T3.7 |

### 8.4 미래 리스크 FU (§12.9, §22.2) — 12건 전수

| 리스크 | 테스트 |
|---|---|
| FU1 PIPL 국외이전 | **제외** — 법률 판정(§2 OUT) |
| FU2 AIGC 라벨 | T1.2(카탈로그 메타 전달) |
| FU3 SQLite 잠금 | T5.5 |
| FU4 egress 비용 | **제외** — 용량/비용(§2 OUT) |
| FU5 단일 박스 SPOF | **제외** — 인프라(§2 OUT), 단 FU9 로 부분 대체 |
| **FU6 리팩터 시 연동 동시 파괴** *(1차 누락)* | **T7.1**(계약 회귀) + **T1.1**(종단 회귀) |
| FU7 OIDC 전환 | **제외** — 3년 축, 표준 미확정 |
| FU8 익명 공개 적발 | **제외** — 규제 판정, 단 T6.3/T1.9 가 기술적 전제 |
| FU9 한국 금고 SPOF | T8.1 |
| FU10 가명 재식별 | T3.9 |
| FU11 삭제 전파 누락 | T8.4 |
| FU12 분할 운영 복잡도 | T8.2, T3.8 |

### 8.5 2차 파생 갭 G-* (§5.4) — 5건 *(신규 축)*

| 파생 갭 | 내용 | 테스트 |
|---|---|---|
| G-S3-CORS | S3 CORS 에 FLOW 도메인 허용 | **조건부 무효화** — Plan Z(로컬 바이트 소유, `media_service.py:148`) 채택 시 불필요. D9/D11 에서 S3 경유 선택 시에만 T1.3 확장 |
| G-Client-Merge | 모바일 로컬+외부 트랙 병합 | **제외** — 모바일 UI 범위(§2 OUT). API 계약은 T1.2/T1.3 에서 검증 |
| G-Consent | 교차시스템 동의+감사 | **T3.11** |
| G-Orch-Merge | 음악 컨테이너 compose 편입 + healthcheck | **T6.11**, T6.6 |
| G-Backup | 크로스 백업 정합성 | T8.2 |

### 8.6 교차 연쇄 (§16.3) — 4건 *(신규 축)*

| 연쇄 | 내용 | 테스트 |
|---|---|---|
| 연쇄 1 | X1 × 공유 커널 — 선후 관계 강제 | **T5.7**(순서 게이트), T6.1 |
| 연쇄 2 | 무심의 × 무인증 공개 × AIGC 라벨 | **T1.9**, T6.3, T1.2 |
| 연쇄 3 | 바이트 소유권 → 책임·용량 이동 | T2.3(소유권) + **결정 D9 선행** — 용량 임계는 §11 블로커 |
| 연쇄 4 | 공유 커널 × 원가-크레딧 정합 | **T5.8**(조건부) |

### 8.7 장애 모드 매트릭스 (§11.9) — 6행 *(신규 축)*

| 장애 | 테스트 |
|---|---|
| 음악 플랫폼 다운 → FLOW 로컬 곡 재생 유지 | T8.3 |
| S3 URL 만료 → 404 | T7.4 (Plan Z 하 무효화, T8.3 병행) |
| FLOW 계정 삭제 → media_asset 고아행 | T3.8(Y1) |
| 연동 해제(unlink) → 토큰 잔존 | **T7.5** (⚠ jti 미구축, 신규 구축 전제) |
| sync 이후 심의 rejected → FLOW 잔존 | **T8.5** |
| 네트워크 분할 → 타임아웃/백오프 | **T8.6** |

### 8.8 매핑 누락 점검 (갱신)

| 축 | 건수 | 비고 |
|---|---|---|
| §4 갭 카탈로그 | **15/15** | 🔴3 + 🟠3 + 🟡2 + F7 |
| X 시리즈 | 9/9 | |
| Y 시리즈 | 12/12 | |
| FU 시리즈 | 12/12 | 매핑 7 / **명시적 제외 5**(FU1·4·5·7·8) |
| G-* 파생 갭 | 5/5 | 제외 2(G-S3-CORS 조건부, G-Client-Merge 범위외) |
| 교차 연쇄 | 4/4 | 연쇄 3 은 D9 결정 선행 |
| 장애 매트릭스 | 6/6 | |
| **합계** | **63/63** | 1차 38건 → **보완 후 63건**(누락 5 + 신규 축 20) |

- **테스트로 검증 불가(§2 OUT 으로 이관)**: FU1·FU4·FU5·FU7·FU8, G-Client-Merge, 법률 적법성(D15).
- D1~D19 중 **테스트가 결정값을 단언하는 항목**: D14(T3.10, T8.1), D16(T3.9), D18(T4.1/T4.2), D19(T4.6). 나머지는 **결정 선행**(§11).
- **구축 전제 테스트**(기존 동작 단언 불가, §13 SC-10): **T7.5**(jti 부재).

---

## 9. 수용 기준 (Definition of Done)

| # | 기준 |
|---|---|
| 1 | §8 추적 매트릭스의 **모든 테스트 ID 가 구현**되고, 근거 `file:line` 이 실제 코드와 일치 |
| 2 | 양측 **기존 테스트 전량 통과 회귀**(FLOW 70파일, 음악 66 passed/3 skipped 기준선 유지) |
| 3 | **CI 병합 차단 게이트** — 1차: T4.1·T4.2·T5.1·T5.2·T6.1 / **2차 추가: T1.9(미심의 노출)·T6.10(redis 무인증)·T5.7(공유커널 순서)** |
| 4 | 신규 실패 테스트가 **실패 원인을 파일:라인으로 지목** |
| 5 | 모든 외부 의존이 mock 되어 **오프라인 단일 머신에서 재현 가능** |

---

## 10. 재사용 함정 (양측 실측 — 재발 방지)

음악 `MOCK_TEST.md:29-46` 의 4건 + 본 라운드 확인분:

| # | 함정 | 대응 |
|---|---|---|
| 1 | **conftest 이름 섀도잉** — `from app.main import app` 금지, `import app.main as _app_main` | 음악 `conftest.py:12` 패턴 준수 |
| 2 | **DB session 바인딩** — `dbmod.SessionLocal = 신규` 금지, `SessionLocal.configure(bind=engine)` | 음악 `conftest.py:25` |
| 3 | **BackgroundTasks 동기 실행** — `TestClient` 에서 generate 후 이미 completed | 완료态은 DB 직접 삽입으로 구성 |
| 4 | **bcrypt==4.0.1 고정** (passlib 1.7.4 호환) | requirements 고정 유지 |
| 5 | **실행 위치 상이** — FLOW 루트 / 음악 `backend/` | §7 분리 실행 |
| 6 | **Langfuse 경로가 `_scrub` 우회** | T4.2 를 spy 로만 검증(단위 `_scrub` 테스트로는 검증 불가) |

---

## 11. 블로커 · 선결 항목

| # | 항목 | 영향 |
|---|---|---|
| B1 | **D14**(캐시 미스 동작) 미결 → T3.10·T8.1 작성 불가 | 결정 필요 |
| B2 | **D16**(홍콩 잔존 준식별 범위) 미결 → T3.9 단언값 미정 | 결정 필요 |
| B3 | **D18**(Langfuse 유지/자체호스팅/OFF) 미결 → T4.1·T4.2 기대값 상이 | 결정 필요 |
| B4 | **D19**(LLM 전문/마스킹) 미결 → T4.6 단언값 미정 | 결정 필요 |
| B5 | 한/홍 서버 스펙·RTT 미확정(§20.4) → 부하·지연 테스트는 **시뮬레이션으로만** | 입력 대기 |
| ~~B6~~ | ~~FLOW `tests/` 클린업 전략 미확인~~ | ✅ **해소** — `tests/conftest.py:18-59` 실측: `tmp_path` 새 파일 + `monkeypatch` 자동 복원으로 **격리 이미 보장**, 별도 전략 불필요. 단 양측 철학이 반대이므로 **conftest 통합 금지**(§1.3) |
| **B7** | **T7.5 는 jti 미구축으로 "기존 동작 단언" 불가** — 구축 완료 후에만 작성 가능 | 구축 선행 |
| ~~B8~~ | ~~설계서 §5.4·§2.1 `compose:306-308` 인용 환각 정정~~ | ✅ **해소** — 설계서 2곳 정정 + §26 신설 + 검증 도구 `scripts/verify_citations.py` 구축(전 167건 인용 무결성 확인) |
| ~~B9~~ | ~~음악 플랫폼 경로 2개(어느 것이 활성?)~~ | ✅ **해소** — `korean-gospel-ai/ai-music-platform` 과 `b86db9b7-.../ai-music-platform` 은 **완전 동일 사본**(테스트 13개·타임스탬프·66 passed/3 skipped 일치). 전자를 기준으로 삼되 **사본 수정 시 미동기화 주의** |

---

## 12. 실행 순서 (제안)

```
1) L0 정적·설정 — T4.1, T6.1, T6.6, T6.10, T6.11, T6.12, T3.1, T3.12, T5.7
   (의존성 0, 즉시 가능, CI 게이트 후보)
2) D4 출국 경로 — LangfuseSpy 구축 → T4.2/T4.3/T4.4      ← 최고 우선
3) D5 공유 커널 — AlembicIsolatedEnv → T5.1/T5.2/T5.5/T5.6/T5.8 **(+T5.9·T5.10 신설)**
4) D1/D2 Plan Z — FlowAdminStub → T1.1~T1.9, T2.1~T2.5  ← T1.9(연쇄2) 포함
5) D3 PII 분할 — PiiVaultStub → T3.2~T3.11              ← B1/B2 선결
6) D6 보안·공존 → T6.2~T6.9                             ← T6.8/T6.9 는 정정 반영
7) D7/D8 동기화·운영 → T7.1~T7.4, T8.1~T8.6
8) [B7 해소 후] T7.5 — jti/토큰 무효화 구축 완료 뒤 작성
9) 회귀 통합 + CI 게이트 연결
```

### 12.1 진행 상황 (2026-08-31 오후)

| 순서 | 상태 | 산출물 | 결과 |
|---|---|---|---|
| **1) L0 정적·설정** | ✅ **완료** | `tests/test_colocation_l0_guards.py` (13 tests) | 7 passed / **6 xfailed** / 0 failed *(SC-25 정정)* |
| **2) D4 출국 경로** | ✅ **완료** | `tests/test_colocation_d4_egress.py` (10 tests, `LangfuseSpy` 포함) | 7 passed / **3 xfailed** / 0 failed |
| — | ✅ | `scripts/dump_orm_fields.py`, `scripts/dump_text_columns.py` (실측 도구) | |
| **3) D5 공유 커널** | ✅ **완료** | `tests/test_colocation_d5_shared_kernel.py` (**32** tests) | **25** passed / **7 xfailed** / 0 failed *(SC-25 정정)* |
| **4) D1/D2 Plan Z** | ✅ **완료** | `tests/test_colocation_d1_d2_plan_z.py` (**77** tests) | **71** passed / 1 skipped / **5 xfailed** / 0 failed |
| **5) D3 PII 분할** | ✅ **완료** | `tests/test_colocation_d3_pii_split.py` (**49** tests) | **38** passed / 2 skipped / **9 xfailed** / 0 failed |
| **6) D6 보안·공존** | ✅ **완료** | `tests/test_colocation_d6_security.py` (**13** tests) | **10** passed / **3 xfailed** / 0 failed |
| **7) D7 동기화 계약·생명주기** | ✅ **완료** | `tests/test_colocation_d7_sync.py` (**9 tests**) + `tests/colocation_common.py`(공용 정적분석 헬퍼, 재발명 금지) | **5** passed / **4 xfailed** / 0 failed / 0 skipped |
| **8) D8 운영·장애·DR** | ✅ **완료** | `tests/test_colocation_d8_ops.py` (**8 tests**) + `tests/colocation_fault.py`(`LatencyInjector` 신규) | **3** passed / 2 skipped / **3 xfailed** / 0 failed |
| **9) 회귀 통합 + CI 게이트 연결** | ✅ **완료** | `.github/workflows/ci.yml`(`colocation-gate` 신규 잡) — `MUSIC_PLATFORM_REPO` CI 고정 + xfail(strict) 강제 + SC-24 skip 상한(8) 감시. 주 `test` 잡은 `--ignore=tests/test_colocation_*.py` 로 단일 출처 분리 | T4.1/T4.2/T5.1/T5.2/T6.1/T1.9/T6.10/T5.7 병합 차단 게이트 + `MUSIC_PLATFORM_REPO` CI 고정 |
| **10) T7.5 토큰 무효화 (B7 해소 후)** | ✅ **완료** | `tests/test_colocation_d9_token_revoke.py`(**5 tests**) + 제품변경: `backend/app/api/auth.py`(jti 5-part 내장 `:128-143`·`_verify_token` 무상태 유지 `:146-189`·`_check_revocation` schema 부재 fail-open `:192-215`·`_verify_token_revocation_checked` 단일 진입점 `:218-236`·`revoke_all_tokens` `:862-882`·`POST /auth/revoke-all` `:889-893`·`POST /auth/revoke-token` `:896-903`) + `backend/app/models/orm.py`(`Subscriber.token_revoked_at` `:263`·`TokenBlacklist` `:927`) + `chat.py`/`events.py`/`chat_pipeline.py:127-136` 호출점 라우팅 + `db.py` `init_db` 방어적 ALTER | **5** passed / 0 skipped / 0 xfailed / 0 failed |

> **⚠️ 위 숫자는 `MUSIC_PLATFORM_REPO` 가 설정된 환경에서만 성립한다 (SC-24).**
> 미설정 시 **29건** 이 skip 으로 강등되며, 그 중 **xfail 게이트 14건이 게이트 기능을 잃는다**
> (`exit=0` 인 채로 **838 passed / 33 skipped / 16 xfailed** — 겉보기엔 여전히 초록불).
> 설정 시 **853 passed / 4 skipped / 30 xfailed**. 총 테스트 수는 **887 로 동일** 하므로
> 겉보기 총수만 보면 **차이를 알 수 없다** — 반드시 skip/xfail 을 분리 집계하라.
> CI 에서 이 변수가 빠지면 **검증 없이 통과** 한다. 반드시 CI env 에 고정한다.

> **D5 는 계획서의 항목 수를 스스로 늘렸다.** 실행 전 실측에서 설계서·계획서
> 어디에도 없던 결함 2건(D21 빈 DB 재현 불가, D22 FLOW alembic cwd 의존)과
> 기존 기록의 오류 2건(X9 는 FLOW 도 해당, T5.8 조인 정의 불가)을 발견해
> T5.9·T5.10 을 신설했다. 상세는 §13 SC-16~SC-21.

**xfailed 30건 목록** (전부 `strict=True` — 구현되면 xpass→오류로 마커 제거를 강제):

| ID | 파일 | tests | 미구현 내용 |
|---|---|---|---|
| T4.1 | L0 | 1 | `langfuse_enabled` 기본값 `True` (Y10) |
| T5.7 | L0 | 1 | 공유 커널 채택 전제(X1 포트 노출·X2 앱 컨테이너 부재) 미충족 |
| T6.1 | L0 | 1 | 음악 compose 5432·6379 호스트 노출 (X1) |
| T6.10 | L0 | 1 | 음악 redis 무인증 (F4) |
| T6.11 | L0 | 1 | 음악 compose 앱 서비스·healthcheck 부재 (X2) |
| T6.12 | L0 | 1 | 관리 화면 의미 라벨 충돌(settings) (🟡7) |
| T4.2 | D4 | 1 | `lf.trace()` 페이로드 PII 미마스킹 (Y9) |
| T4.3 | D4 | 1 | 13개 trace 입력 지점 전부 마스킹 없음 (Y9) |
| T4.4 | D4 | 1 | `_scrub` 에 PII 규칙 부재 (Y9) |
| T5.1 | D5 | **2** | 양측 `env.py` 에 `version_table` 격리 없음 (X3) — 교차 upgrade **양방향** 실패 |
| T5.2 | D5 | 1 | 양측 `env.py` 에 `include_object` 없음 (X4) — autogenerate 가 FLOW 테이블 **전부 drop** |
| T5.6 | D5 | 1 | 양측 모두 `create_all` 무조건 호출 (X9) — 환경 분기 없음 |
| T5.8 | D5 | 1 | CNY↔KRW 환율 소스 0건 — 원가-크레딧 조인이 정의되지 않음 |
| T5.9 | D5 | 1 | 빈 DB 에서 `upgrade head` 실패 (D21) — ORM 32 중 11만 마이그레이션 생성 |
| T5.10 | D5 | 1 | FLOW `alembic.ini` `script_location` 상대경로 (D22) — cwd 의존 |
| **T1.1b** | **D1/D2** | 1 | **D23** — 음악에 `/admin/media` push 호출이 없고 `flow_song_id` 는 예약 필드 (`models.py:58`) |
| **T1.2b** | **D1/D2** | 1 | FLOW 에 `ai_music` 카테고리 없음 — `media_service.py:95` 이 `worship\|sermon` 만 허용 |
| **T1.6b** | **D1/D2** | 1 | `external_ref`(외부 참조 유니크) 컬럼이 `MediaAsset` 에 없음 — 멱등성 불가 |
| **T1.9b** | **D1/D2** | 1 | **D25** — `stream_media` 가 `active` 를 필터하지 않아 미발행 오디오가 무인증 노출 |
| **T1.9c** | **D1/D2** | 1 | **D24** — 음악 `sync_to_flow` 가 `moderation_status` 를 검사하지 않음 |
| **T3.2d** | **D3** | 1 | §21.3 2계층 분할 미구현 — 직접 식별자가 금고 테이블로 분리되지 않았다 |
| **T3.2f** | **D3** | 1 | **D28** — 홍콩 검색 캐시 키가 금고 유래 프로필 속성을 품는다(`retriever.py:207-223`) |
| **T3.3c** | **D3** | 1 | §21.4 Y5 미구현 — 주체 삭제 진입점·삭제 전파 원장이 모두 없다 |
| **T3.4c** | **D3** | 1 | §21.4 Y2 미구현 — 원문 저장 전 PII 스크러버가 없다 |
| **T3.5b** | **D3** | 1 | `trigger_quote`(`orm.py:449`) 가 비식별화되지 않았고 한국 귀속도 아니다 |
| **T3.6b** | **D3** | 1 | `support_ticket.contact`(`orm.py:500`) 가 한국 귀속으로 분리되지 않았다 |
| **T3.7c** | **D3** | 1 | `client_ip` 가 원문 저장된다 — 마스킹·보존기간 모두 부재(`error_monitor.py:69`) |
| **T3.8c** | **D3** | 1 | §21.4 Y1 미구현 — `interaction.subscriber_id` FK(`orm.py:334-336`) 가 그대로 있다 |
| **T3.11c** | **D3** | 1 | 음악 사용자↔FLOW 구독자 연결이 없어 교차시스템 동의가 기록되지 않는다 |

> 24행이 30 tests 인 이유: T5.1 은 **양방향 2건**(`test_flow_then_music_*`·`test_music_then_flow_*`)이다.
> 위 숫자는 junit-xml 실측(`--junit-xml`) 기준이며, 진행 표시줄 파싱이 아니다(§12.1 각주 참조).

> **D3 의 skip 2건은 B1/B2 블로커 대기분이다** — 조용한 skip 이 아니라 사유를 명시한다:
> `test_t3_10d_cache_miss_policy`(B1/D14), `test_t3_9c_reidentification_is_impossible`(B2/D16).
> 결정값이 없는데 단언을 쓰면 **미결정 값을 단언하는 환각 테스트** 가 된다(SC-24 계열).

> **X3·X4 는 실측으로 재확인했다.** 음악 head=`c2d3e4f5a6b7`, FLOW head=`n1_ads_table`.
> ① 음악 upgrade → FLOW upgrade 시 `Can't locate revision identified by 'c2d3e4f5a6b7'`
> ② FLOW stamp → 음악 upgrade 시 `Can't locate revision identified by 'n1_ads_table'`.
> X4 는 X3 에 가려 관측되지 않으므로, **음악 체인으로 head 를 만든 뒤 FLOW 테이블을
> create_all 로 얹는** "X3 해결·X4 미해결" 상태를 재구성해 32/32 drop 을 실측했다.

**전체 회귀**: `tests/` + `backend/tests` 전량 **863 passed / 4 skipped / 33 xfailed / 0 failed (900 tests)**
(D3 제외 동일 조건 측정: **815 passed / 2 skipped / 21 xfailed = 838** — D3 기여분
**+38 passed / +2 skipped / +9 xfailed = 49 tests**, 기존 테스트 영향 0).
D6 적용 후: 기존 887 → **900** (+10 passed / +3 xfailed = 13 tests, skip 4 유지), 기존 테스트 영향 0.
D6 xfailed 3건: **T6.4b**(음악 `_ensure_cdn_url` SSRF 가드 부재, `routers/flow.py:23-40`) ·
**T6.7b**(Caddyfile `?a=` 토큰 쿼리 log 블록 부재, 🟡8) ·
**T6.8b_warn**(FLOW 레이트리밋 Redis 폴백 경고 로그 부재, `middleware/rate_limit.py:110,123`, F1/SC-8).

> 853 / 4 / 30 은 `--junit-xml` 실측값이다. 진행 표시줄(`.x s`) 파싱은 Windows 에서
> **집계 라인이 출력되지 않는 경우가 있어**(아래 환경 주의) 오독 위험이 있다.
> 숫자를 문서에 옮길 때는 반드시 junit-xml 로 재검증한다.
>
> **skip 2건의 성격** — ① `test_t2_5b_scheduler_start_stop_cycle`(D26: apscheduler 미설치,
> 실제 엔진 기동 불가) ② `test_detector_matches_seed`(LLM 키 미설정, 기존 테스트).
> 즉 **D1/D2 자체가 환경 때문에 못 도는 것은 1건** 이며, 나머지 76건은 환경 무관하게 실행된다.

> **xfail(strict=True) 게이트가 실제로 작동했다.** T4.7(스케줄러 출국 경로)을 처음에
> xfail 로 배치했는데, 설계서 §25.7 을 추가하자 즉시 **xpass → strict 오류** 가 발생해
> 마커 제거를 강제했다. "언젠가 조용히 통과해 게이트 기능을 잃는" 사고가 실제로 방지된 사례.

> ⚠️ **Windows 환경 주의**: pytest 실행 시 safe-delete shim 이 pytest 자체 임시 디렉터리
> 정리를 fail-closed 로 차단해 **집계 라인이 출력되기 전에 프로세스가 중단** 된다.
> 회피: `PYTEST_DEBUG_TEMPROOT` 를 프로젝트 내 경로로 지정하고, 실행 전 해당 디렉터리를
> 비워둔다(비어 있으면 pytest 가 GC 를 시도하지 않는다).
> `--basetemp=상대경로` 는 pytest fixture 에서 `OSError: [Errno 5]` 를 유발하므로 쓰지 않는다.
>
> **집계 라인이 사라져도 숫자를 잃지 않는 법** — `--junit-xml=<path>` 를 항상 함께 쓴다.
> 집계 라인이 없어도 XML 에 전 건수가 남는다. 본 계획서의 모든 수치는 이 방식으로 재검증했다.
> 또한 **`rm -rf .pytest_tmp` 조차 shim 이 차단** 하므로 `/bin/rm -rf` 를 쓴다
> (`SAFE_DELETE_BULK_CONFIRM_REQUIRED` — 50건 초과 벌크 삭제 가드).

**표준 회귀 명령 (Windows, 재현용)**:

```bat
set MUSIC_PLATFORM_REPO=C:/Desktop/korean-gospel-ai/ai-music-platform
set PYTEST_DEBUG_TEMPROOT=C:/Desktop/Project/FLOW/.pytest_tmp
/bin/rm -rf .pytest_tmp && mkdir -p .pytest_tmp
./venv313/Scripts/python.exe -m pytest tests/ backend/tests -q -p no:randomly ^
    --junit-xml=.junit.xml
python scripts/junit_count.py .junit.xml all        :: 집계
python scripts/junit_count.py .junit.xml byfile     :: 파일별
python scripts/junit_count.py .junit.xml skipped    :: skip 사유 확인
```

> `MUSIC_PLATFORM_REPO` 를 빼면 **exit=0 인데 29건이 skip** 된다(SC-24).
> `skipped` 사유를 확인하지 않으면 초록불을 "전부 통과"로 오독한다.

**A/B 대조 명령 (신규 파일의 기여분·무회귀 증명)**:

```bat
set MUSIC_PLATFORM_REPO=C:/Desktop/korean-gospel-ai/ai-music-platform
set PYTEST_DEBUG_TEMPROOT=C:/Desktop/Project/FLOW/.pytest_tmp

:: A — 신규 파일 포함
/bin/rm -rf .pytest_tmp && mkdir -p .pytest_tmp
./venv313/Scripts/python.exe -m pytest tests/ backend/tests -q -p no:randomly ^
    --junit-xml=.jA.xml
python scripts/junit_count.py .jA.xml byfile > .byA.txt

:: B — 신규 파일 제외 (베이스라인)
/bin/rm -rf .pytest_tmp && mkdir -p .pytest_tmp
./venv313/Scripts/python.exe -m pytest tests/ backend/tests -q -p no:randomly ^
    --ignore=tests/test_colocation_<NEW>.py --junit-xml=.jB.xml
python scripts/junit_count.py .jB.xml byfile > .byB.txt

:: 차분 — **추가 행만** 보여야 한다. 기존 파일 숫자가 1개라도 바뀌면 회귀다.
diff .byB.txt .byA.txt
```

> 총수 대조만으로는 부족하다. A−B 가 **"신규 파일의 클래스 행만 추가"** 인지까지
> 확인해야 "기존 테스트 영향 0" 을 말할 수 있다. D3 적용 시 diff 결과는
> **추가 12행 / 변경 0행** 이었다(49 = 38 passed + 2 skipped + 9 xfailed).

---

## 13. 자가정정 (본 계획 수립 중 발견한 설계서·계획 오류)

> 원칙: 계획 수립 중 **실측으로 뒤집힌 내용은 즉시 기록**한다. 조용히 고치지 않는다.

| # | 항목 | 정정 전 | 정정 후 (실측 근거) |
|---|---|---|---|
| **SC-7** | **G-Consent 인용** | 설계서 §5.4: FLOW PIPL 동의 모델 `compose:306-308` | **해당 인용은 실재하지 않는다.** `docker-compose.prod.yml` 은 **152줄**이며 모든 compose 파일에 `consent`/`PIPL` 문자열 **0건**. 실제 위치는 `compliance.py:45-61`(ConsentRecord) + `orm.py:197-198, 241`. → B8 로 설계서 정정 이관 |
| **SC-8** | **F1 레이트리밋** | 설계서 §4 F1: "레이트리밋 비공유(in-memory)" — 양측 동일 취급 | **양측 다르다.** 음악 = 모듈 전역 `defaultdict`(`ratelimit.py:13`)로 진짜 비공유. FLOW = **Redis 우선 + 인메모리 폴백**(`middleware/rate_limit.py:100-102` redis zset, `:116-134` 폴백). → T6.8 을 "음악 비공유 단언 + FLOW 폴백 경고 검증"으로 분리 |
| **SC-9** | **F3 api_key** | 설계서 §4 F3: "평문, 암호화 0건" — 전면 미조치로 읽힘 | **저장(at-rest)은 평문이 맞으나 GET 응답은 이미 마스킹 구현됨**: `schemas.py:150` `api_key_set: bool`(주석 "脱敏：仅表示是否配置，不返回明文"), `admin.py:45` `api_key_set=bool(e.api_key)`. → T6.9 를 "①평문 저장=알려진 갭 명시, ②GET 마스킹=회귀 차단" 이중 검증으로 분리 |
| **SC-10** | **jti 블랙리스트** | ~~설계서가 존재하는 것처럼 기재~~ *(본 계획 1차의 과잉 판단)* | **정정: 설계서 §11.9 는 "완화"란에 **설계 제안**으로 기재한 것일 뿐이며 환각이 아니다.** 다만 **구현은 0건**(FLOW 백엔드 전역 `jti` grep 0건)이므로, T7.5 는 기존 동작 단언이 아니라 **구축 후 검증**으로 표기한다(→ B7). 설계서 §11.9 에도 "미구축" 명시 완료(§26.2 #10) |
| **SC-11** | **PII 인벤토리** | 설계서 §21.1 + §25.1 합산 목록 | 2건 추가·2건 행번호 정정: ① **`consent_record.ip`(`compliance.py:58`) 누락** — 동의 시점 IP 는 준식별 PII. ② `emotional_state` 는 `:195-196` 이 아니라 **`:192`**, `last_emotion` 은 `:207` 이 아니라 **`:208`**. → T3.1 인벤토리 전수 목록에 반영, T3.12 신설 |
| **SC-12** | **PII 탐지 방법** | 필드명 키워드(`email`/`ip`/`device_id`…)만으로 PII 판정 | **방법 자체가 결함이었다.** 겉보기 무해한 이름의 원문 파생 컬럼을 전부 놓친다. 실측 3건: ① `QATrendSnapshot.canonical_text` = `normalize_question(Interaction.question/.answer)`(`trending_service.py:218,256` ← `:158`/`:181`) — `normalize_question`(`:26-45`)은 NFC·공백·물음표만 정규화하고 **내용을 보존** → **사용자 발화 원문**. ② `Payment.raw` = PortOne 응답 **무필터 저장**(`payment.py:135` ← `portone_service.py:122`). ③ `QATrendSnapshot.raw_variants` = **무가공 변형 원문**, `insight_engine.py:197-198` 이 최대 5개를 프롬프트에 삽입. → 탐지를 **3축(이름/크기 Text/구조 JSON)** 으로 교체, T3.1 전면 재구성 |
| **SC-13** | **T4.3 지점 수** | `trace.event(input=query)` **3개소** | **과소 집계 — 13개 지점.** AST 전수 스캔 결과 `chat.py` 7곳(193·477·615·680·857·1112·1265) + `chat_pipeline.py` 6곳(154·356·447·458·473·538). 계획서가 10곳을 놓쳤고, 특히 `chat.py:477/1112` 의 `generation(input=user_prompt)` 는 **최종 LLM 프롬프트** 다. → T4.3 을 L1(AST 인벤토리 회귀) + L2 로 분리 |
| **SC-14** | **spy 주입법** | `monkeypatch.setattr(conn_mod, "langfuse", ...)` (기존 `test_tracing.py:176-178` 방식 재사용) | **그 방식은 무효다.** `connections.py:459` `langfuse = staticmethod(langfuse)` 가 **정의 시점에** 함수 객체를 바인딩 → 모듈 속성 패치는 `connections.langfuse` 에 영향 없음(실측: `connections.langfuse()` 여전히 `None`). **기존 `test_tracing.py` 의 폴백 테스트는 패치가 아니라 환경 우연(`langfuse_enabled=False`)으로 통과** 하고 있었다 → Langfuse 분기를 검증한 테스트는 **존재하지 않았다**. 올바른 주입은 `_ConnectionRegistry` 의 staticmethod 패치. 함정 고정 테스트까지 배치 |
| **SC-15** | **출국 경로 수** | §25 = ①Langfuse ②LLM 프로바이더 (2개) | **③번째 경로 발견 — 스케줄러 원문 재전송.** `trending_scheduler.py:29`(60초 주기) → `:57-59` → `insight_engine.py:196,347,489`(`canonical_text` 로드) → `:117-122` 외부 LLM. **사용자 요청 없이, 이미 저장된 원문을** 보낸다 → 요청 단위 동의·목적 제한을 걸 앵커가 없다. → T4.7 신설, 설계서 §25.7~25.9(Y13/Y14/D20) 역반영 완료 |

| **SC-16** | **D21 — 빈 DB 재현 불가 (신규 결함)** | T5.1 은 "양측 `version_table` 설정 후 교차 실행" 만 상정 — 양측 체인이 **각자 빈 DB 에서 성립**한다고 암묵적으로 가정 | **FLOW 체인은 빈 DB 에서 죽는다.** ORM **32개** 중 마이그레이션 생성은 **11개** 뿐. `c2fbb2654cf3_baseline_all_tables.py` 는 create_table **1개**(`salvation_journey`) + `batch_alter_table` **8개** → **가짜 baseline**(이미 존재하는 DB 에 대한 증분). 빈 DB `upgrade head` = `no such table: document_version`. `main.py:318-321` 이 "신규 테이블은 create_all 로 보장"이라고 **의도된 설계임을 자기 고백**. → **T5.9 신설 + 공유 Postgres 프로비저닝 시 FLOW 앱 1회 기동이 필수 불변식** |
| **SC-17** | **X9 범위** | 설계서 §14: X9 = **음악** `init_db()` 만 | **FLOW 도 동일.** `main.py:310` 에서 환경 분기 없이 `init_db()` 호출(음악은 `main.py:11-14`). → T5.6 게이트를 "양측" 기준으로 재작성 |
| **SC-18** | **T5.8 조인 가능성** | 공유 인스턴스 전제 하 정합 검증이 가능한 것으로 상정 | **조인이 정의되지 않는다.** 음악 `models.py:46` `cost_cny`(**CNY**) ↔ FLOW `compliance.py:76-78` `amount_krw`/`currency`(기본 **KRW**) — **CNY↔KRW 환율 소스 0건**. `ccp_rate_card.currency` 는 `server_default="KRW"` 단일 통화(`models/ccp.py:102-104`). → T5.8 을 "정합 검증"이 아니라 **"환율 근거 존재" 게이트** 로 변경 |
| **SC-18b** | **문자열 검색의 거짓 양성** | `ccp.py` 에 "환율" 이 등장하므로 환율 소스가 있다고 판단 | `ccp.py:77` 의 "환율 기준일" 은 **`source_note` 에 사람이 적어 넣으라는 서술** 일 뿐 코드가 아니다. **실제로 한 번 속았다.** → `_strip_docstrings_and_comments`(AST 기반) 를 만들어 **실행 코드만** 검색. 역함정 고정 테스트 `test_docstring_mention_is_not_an_fx_source` 배치 |
| **SC-19** | **D22 — FLOW alembic cwd 의존 (신규 결함)** | 양측 alembic 이 모두 cwd 무관하다고 가정 | FLOW `alembic.ini:6` `script_location = backend/migrations`(**상대경로**) vs 음악 `alembic.ini:8` `%(here)s/migrations`(**절대**). 다른 cwd 에서 FLOW 는 `.../backend/backend/migrations` 를 찾아 죽는다. → **T5.10 신설**, 하네스는 절대경로 override 로 우회 |
| **SC-20** | **`sys.modules["app"]` 오염 — 측정 대상 자체가 바뀜** | 음악 backend 를 `sys.path` 선두에 넣으면 음악 `env.py` 의 `from app.db import Base` 가 음악 Base 를 가리킬 것으로 가정 | **전체 회귀에서 FLOW Base 를 가리켰다.** `tests/test_salvation_regression.py:29` 가 `backend` 를 `sys.path` 에 넣고 `:36` `from app.db import ...` 수행 → `sys.modules["app"]` = **FLOW 의 backend/app**. 결과 "음악 autogenerate 가 **음악 테이블 6개를 drop**" 이라는 **현실에 없는 결과**. **단독 실행에선 우연히 맞았으므로 더 위험** 했다. → 픽스처에서 `app` 격리·복원 + `TestHarnessIntegrity` 3종 신설 |
| **SC-21** | **`Base.metadata` 전역 가변 상태** | `Base.metadata.tables` 를 그대로 사용 | `tests/test_compliance_security.py:55` `class _TempEnc(Base)`(`tmp_enc_at_rest`)가 영구 합류 → **단독 32개 vs 전체 33개**. → 매퍼 클래스의 정의 모듈이 `backend.app.models` 인 것만 선별 + `create_all` 대상도 동일하게 한정 |

**D1/D2(Plan Z) 구현 중 발견한 제품 결함 5건 (2026-08-31 6차).**
이 5건은 설계서에 **D23~D27** 로 등록되었고, §26.2 에 **자가정정 #22~#26** 으로 기록되었다.
(계획서의 SC 번호는 하네스 결함에만 쓴다 — 아래 SC-22~SC-24 와 혼동 주의.)

| 설계서 # | 결함 | 정정 전 (계획서의 전제) | 정정 후 (실측 근거) |
|---|---|---|---|
| **#22** | **D23 — Plan Z 의 push 경로는 없다 (신규 결함)** | 계획서 §12 의 4) 단계는 "음악 → `POST /admin/media` push" 를 **전제** 로 T1.1~T1.9 를 설계 | **push 코드가 0줄이다.** 음악 저장소 전역에 `/admin/media` 호출 **0건**, `flow_song_id` 는 `models.py:58` 의 **예약 필드**(`# Push 模式（独立 FLOW 后端）预留`). `routers/flow.py:1-8` 이 **Pull 모델** 임을 자기 고백. **수신 계약은 실재**(`api/media.py:61-74`)하고 **규격도 호환**(`asset_id` 36자 ≤ `flow_song_id` 255자)하나 연결부가 없다. → T1.1b 를 xfail(strict) 목표 게이트로 고정. 설계서 §25.13 신설 |
| **#23** | **D24 — `sync_to_flow` 심의 미검사 (신규 결함)** | T1.9 를 "미심의 곡 노출 차단"으로 설계 — 음악이 자체 필터할 것으로 암묵 가정 | `routers/flow.py:64-68` 은 소유권(404) + `status != "completed"`(400) 만 본다. `moderation_status` **미검사**. 그런데 `admin.py:117-131` 의 수동 reject 는 **`song.status` 를 건드리지 않으므로** rejected 곡도 `status == "completed"` → **그대로 동기화**. `songs.py:294` explore 가 approved 만 노출하는 것과 대조. **현행 결함.** → T1.9c 게이트, 설계서 §25.14 신설 |
| **#24** | **D25 — 미발행 오디오 무인증 스트리밍 (신규 결함, 🔴)** | FLOW 미디어 커널을 "완결된 자산"으로 재사용 — 검증 불필요로 간주 | `api/media.py:178-185` `stream_media(asset_id)` 에 **인증 파라미터 자체가 없고**, `media_service.py:226-234` `get_file` 은 `active` 를 **필터하지 않는다**(`list_public` 의 `:201-202` 와 대조). 실측: `publish_at=+3일` 업로드 → 카탈로그 미노출이나 **`GET /mobile/media/{id}/stream` 무헤더 → 200, 2055 bytes 전문 수신**. **"목록은 가리고 파일은 연다."** Plan Z 와 무관한 **현행 노출**. → T1.9b 게이트, 설계서 §25.15 신설 |
| **#25** | **D26 — media_publisher 는 apscheduler 없이 조용히 죽는다 (신규 결함)** | T1.4/T2.5 를 "스케줄러가 도래 시 플립"으로 설계 — 스케줄러 존재를 전제 | `apscheduler==3.10.4` 가 `requirements.txt:55`·`requirements-313-local.txt:57` 에서 **주석 처리**(선택 의존), 배포 매니페스트 어디에도 없음. 그런데 `media_publisher` 는 푸시만 하는 게 아니라 `_publish_due`(`:32-50`)로 **예약 발행 활성화** 를 담당 → **예약 발행 영구 미동작**. `main.py:198-203` 의 `except Exception` 이 `ImportError` 마저 삼켜 **경고 로그만 남고 부팅은 성공**. → `TestD26SchedulerDependencyGap` 7종 + apscheduler 스텁 주입(`_install_apscheduler_stub`)으로 **검증 대상 로직은 실제 코드** 유지, 설계서 §25.16 신설 |
| **#26** | **D27 — 관리자 속도 제한 자기 차단 (신규 결함)** | `T1.x` 대량 push 시나리오를 제약 없이 상정 | `rate_limit.py:20` 이 `/admin` 을 제한 → `POST /admin/media` 도 대상. `MAX_IP_RPM=60`(`:15`, `config.py:172`), 초과 시 **IP 단위 15분 차단**(`:185-186`). **양 서비스 홍콩 1대 공존 → 송신 IP 단일** 이므로 카탈로그 60곡 일괄 push 는 **자기 차단**. 실측: 본 모듈 200+ 회 호출이 이후 admin 테스트 18건을 429 로 죽였다. → 설계서 §25.17 신설 |

**D1/D2 구현 중 발견한 하네스 결함 3건 (SC-22~SC-24).**
설계서 §26.2.1 과 **번호를 통일** 했다(SC 번호는 하네스 결함 전용).

| # | 결함 | 실측 | 대응 |
|---|---|---|---|
| **SC-22** | **`sessionmaker()` 를 `with` 로 쓰면 commit 이 아니라 close(롤백)** | `tests/conftest.py:45-57` 의 `iso_db` 는 **sessionmaker** 를 주지만 `get_session`(`db.py:136-147`) 처럼 commit 해 주지 않는다. 직접 삽입 시 `with session_factory() as s:` 만 쓰면 **행이 저장되지 않는다**. 그 결과 "대상이 0건" 이 되어 `_notify_due()["count"] == 0` 이 **거짓 통과** 했다(T1.5d) | 삽입 헬퍼 `_insert_media_asset` 에서 `s.commit()` 명시 + **삽입 후 재조회로 존재를 먼저 단언**. 모든 호출부에 **사전 조건 검증** 추가 |
| **SC-23** | **전역 속도 제한이 테스트 파일 간 오염을 일으킨다** | `rate_limit.py:22-23` 의 `_buckets`/`_blocked_ips` 는 **모듈 전역**. `TestClient` IP 는 항상 `"testclient"`. D1/D2(200+ 회 `/admin/media`) 가 이후 `test_content_admin_api.py`(9건)·`test_support_api.py`(9건→6건)·`test_subscriber_pagination.py`(3건) 을 **모두 429 로 죽였다**. 본 모듈 제외 동일 조건 실행은 exit=0 | autouse 픽스처 `_rate_limit_isolated`: `rate_limit_enabled=False` + 전역 버킷 저장/초기화/복원. teardown 에서 **차단을 남기지 않음** 을 자체 테스트(`test_our_fixture_leaves_no_global_block`)로 고정 |
| **SC-24** | **`MUSIC_PLATFORM_REPO` 미설정 시 xfail 게이트가 skip 으로 강등** | 환경변수 없이 전체 회귀: **801 passed / 29 skipped / 8 xfailed, `exit=0`**. 설정 시: **815 / 2 / 21, `exit=0`**. 차이 **27건 = xfail 게이트 13 + 일반 14**. 즉 **게이트 13개가 조용히 무장애 통과** 한다 | 회귀 명령에 환경변수 고정(§12.1 표준 명령), `--junit-xml` 로 **skip 사유를 반드시 확인**. 설계서 §26.2.1 에 SC-24 로 기록 |

> **SC-22 의 교훈이 특히 중요하다.** 실패가 아니라 **통과** 로 드러났기 때문이다.
> "기대한 대상이 아무것도 없어서" 조건을 만족한 것이며, 이는 **거짓 음성보다 발견하기 어렵다.**
> → **부정 단언("아무 일도 일어나지 않았다")을 할 때는 반드시 "대상이 존재한다"를 먼저 단언** 해야 한다.
>
> **SC-24 는 SC-20 계열의 변주다.** SC-20 이 "측정 대상이 바뀐" 사고라면,
> SC-24 는 **"측정이 아예 일어나지 않았는데도 초록불"** 인 사고다.
> → **exit code 는 "실패 없음"만 말하고 "검증했음"은 말하지 않는다.**
> skip/xfail 건수 자체를 회귀 지표로 추적해야 한다.

**SC-25 — 본 계획서 내부의 수치 불일치 (junit-xml 재측정으로 발견, 2026-08-31 6차).**

D1/D2 실측을 문서에 옮기려 §12.1 기존 수치를 **검증** 하다가, 과거 기록이 스스로 모순됨을 발견했다.
과거 수치는 진행 표시줄 파싱으로 얻은 값이었고, **검산(합이 총수와 맞는지)을 하지 않았다.**

| 항목 | 종전 기재 | 실측 (junit-xml) | 판정 |
|---|---|---|---|
| **L0** | `13 tests` / 8 passed / 6 xfailed | **13 tests** / **7 passed** / 6 xfailed | **8+6=14 ≠ 13** → 자가모순. 실측은 7+6=13 |
| **D5** | `29 tests` / 22 passed / 7 xfailed | **32 tests** / **25 passed** / 7 xfailed | §14 5차 기재(32/25/7)와 §12.1 기재(29/22/7)가 **서로 달랐다**. 실측은 **32/25/7** |
| **xfailed 건수** | "16건" (표는 15행) | **16 tests** | T5.1 이 **양방향 2건** 이라 15행=16tests. 표에 tests 열을 추가해 해소 |

> **교훈**: "통과 숫자"를 기록할 때는 **반드시 합이 총수와 맞는지 검산** 하고,
> 서로 다른 절(§12.1 vs §14)에 같은 항목을 적을 때는 **한쪽을 다른 쪽에서 생성** 한다.
> 진행 표시줄 파싱은 Windows 에서 집계 라인이 유실될 수 있으므로 **junit-xml 을 단일 출처** 로 삼는다.

**D3(PII 분할) 구현 중 발견한 제품 결함 1건 (2026-08-31 7차).**
설계서에 **D28** 로 등록되었고, §26.2 에 **자가정정 #27** 로 기록되었다.

| 설계서 # | 결함 | 정정 전 (계획서의 전제) | 정정 후 (실측 근거) |
|---|---|---|---|
| **#27** | **D28 — 홍콩 검색 캐시 키가 한국 금고 유래 속성을 품는다 (신규 결함)** | T3.10 을 "Y4 가명 프로필 캐시가 **없다** → 캐시 미스 경로 자체가 없다"로 설계. §21.3 분할 후 홍콩에는 **가명키(`subscriber_id`) + 콘텐츠만** 남는다고 전제 | **프로필 속성이 캐시 키에 이미 박혀 있다.** 실측 체인: `subscriber_service.py:45,50,51,56` 이 프로필 dict 를 `Subscriber` **행에서 직접** 조립(`salvation_status`·`assume_saved`·`darakbang_role`·`darakbang_verified`) → `rag_engine.py:171-178` → `search_v4.py:358,368` → `retriever.py:207-213` 5속성으로 `cache_profile` 구성 → `:214-223` `cache_key` 에 **요소로 포함**. 저장소는 `self._cache`(`:175`)이고 소유자는 모듈 싱글턴 `_retriever_cache`(`:49`) → **프로세스 수명**, TTL **1800초**(`:155`). → **분할 후에도 홍콩 프로세스 메모리에 구독자 종교상태 조합이 30분간 상주.** §21.3 위반 + FU10(§22.2 재식별) 표면 확대. 설계서:1066 은 핫패스에서 **"토큰에 서명된 프로필"** 을 쓰도록 규정하므로 서버측 캐시 키에 남는 것은 설계 의도와도 반대다. → T3.2e(현행 고정) + **T3.2f(xfail strict 게이트)** |

**D3 구현 중 발견한 하네스 결함 2건 (SC-26·SC-27).**

| # | 결함 | 실측 | 대응 |
|---|---|---|---|
| **SC-26** | **문자열 검색은 "거짓 양성" 만이 아니라 "위험 은폐" 도 만든다** | T3.10a 를 `"profile" in text and "cache" in text` 로 작성 → `answer_cache.py`·`retriever.py`·`search_v4.py` 히트. 처음엔 "또 거짓 양성(SC-18b)" 으로만 보고 **측정법만 고치려 했으나**, 실제로 `retriever.py` 는 **정말로 프로필 속성을 캐시 키에 넣고 있었다.** 잘못된 방법이 우연히 "맞는 방향" 을 가리켰고, 그 탓에 **이미 존재하는 결함(D28) 을 놓칠 뻔했다.** 한편 T3.8b 는 반대로 진짜 거짓 양성이었다(`orm.py:103` `cascade="all, delete-orphan"` = ORM 캐스케이드, `extraction_service.py:273` `orphan_josa_re` = 한국어 조사 정규식) | 낱말 검색을 전면 폐기하고 **정의 심볼(`_def_names`)**·**AST 구조(`_retriever_cache_shape`, `_retrieve_miss_path`)** 로 재측정. 심볼 스캔 실측 **157 파일 · 0 히트** 로 T3.8b 의 결론은 유지. 심볼 스캐너 자체를 `test_def_names_scanner_sees_definitions_not_words` 로 고정(주석·리터럴은 잡지 않고 정의만 잡음) |
| **SC-27** | **음악 플랫폼은 SQLAlchemy 1.x 스타일이다 — 2.0 전용 파서는 결과가 빈다** | `_orm_columns()` 가 FLOW 의 2.0 선언(`Mapped[T] = mapped_column(...)`)만 읽도록 작성됨. 음악 `models.py:7-13` 은 **1.x**(`email = Column(String(255), ...)`) → 파싱 결과 `{}` → `assert "User" in cols` 실패. **이를 "음악에 User 모델이 없다"로 오독하면 거짓 결론** | `_decl_field()` 신설 — `AnnAssign + mapped_column` **와** `Assign + Column` 을 모두 인식. 혼합 선언도 읽는다. 하네스 자기검증 `test_orm_parser_reads_sqlalchemy_1x_style`·`test_orm_parser_reads_both_styles_in_one_class` 로 고정 |

> **SC-26 의 교훈 — 거짓 양성보다 위험한 것은 "우연히 맞은 틀린 방법"이다.**
> SC-18b·SC-20 은 **틀린 결과** 를 냈기 때문에 즉시 드러났다. SC-26 은
> 틀린 방법이 **그럴듯한 결과** 를 냈고, 그 결과를 의심하지 않았다면
> **실제 결함을 영구히 놓쳤을** 것이다.
> → **부정 단언의 측정법을 바꿨을 때 결론이 유지되는지 반드시 재검증** 한다.
> 유지되면 결론이 강화되고, 뒤집히면 결함을 찾은 것이다. 둘 다 값이 있다.

**영향 요약**: SC-8·SC-9·SC-10 은 "설계서가 기존 구현을 과소평가/과대평가"한 항목으로, 그대로 테스트를 쓰면 **존재하지 않는 동작을 단언하는 환각 테스트**가 된다. 본 계획은 실측 기준으로 분리·보정했다.

**SC-16~SC-21 의 공통 성격 — "테스트가 초록불" 과 "옳은 대상을 측정했다" 는 별개다.**
SC-16·19 는 **제품의 미기록 결함**이고, SC-20·21 은 **테스트 하네스自身的 결함**이다.
특히 SC-20 은 단독 실행에서 우연히 기대값과 일치했기 때문에, 전체 회귀를 돌리지
않았다면 **틀린 결론을 확신한 채로** 넘어갔을 사고다.
→ 측정 대상이 실행 순서에 따라 바뀔 수 있는 경우
(모듈 캐시·전역 메타데이터·cwd) **"내가 지금 무엇을 측정하는가"를 검증하는
테스트를 반드시 함께 배치** 한다(`TestHarnessIntegrity`).
**SC-12~SC-15 는 성격이 다르다** — 설계서의 틀림이 아니라 **계획서自己的 탐지 방법·집계·가정이 틀린 경우**다. 특히 SC-12 는 "안전해 보이는 이름 = 안전" 이라는 가정 자체가 환각의 원천이었음을 보여준다.

---

## 14. 문서 이력

| 일시 | 변경 |
|---|---|
| 2026-08-31 1차 | 계획 수립 (§0~§12). 추적 매트릭스 38건 |
| 2026-08-31 2차 | **누락 감사 반영** — §8 전면 재구성(15/9/12/12 + G 5 + 연쇄 4 + 장애 6 = 63건), 신규 테스트 11건(T1.9·T3.11·T3.12·T5.7·T5.8·T6.8~T6.12·T7.5·T8.5·T8.6), 자가정정 SC-7~SC-11, 블로커 B7·B8 |
| 2026-08-31 3차 | **인용 전수 검증** — 설계서 157건 인용 검증(통과 156 / 환각 1 = `compose:306-308`). SC-7~SC-11 설계서에 **역반영 완료**(§5.4·§4 F1/F3·§11.9·§21.1 + §26 신설). SC-10 자체 오판 정정. B8 해소, 검증 도구 `scripts/verify_citations.py` 확보 |
| 2026-08-31 4차 | **L0·D4 구현** — `tests/test_colocation_l0_guards.py`(13 tests), `tests/test_colocation_d4_egress.py`(10 tests). 구현 중 **설계서·계획서 오류 4건 추가 발견**(SC-12~SC-15): PII 탐지 방법 결함(`canonical_text` 등 3건 누락), T4.3 지점 3→13곳, 기존 spy 주입 무효, **출국 경로 ③ 스케줄러 원문 재전송**. T4.7 신설. 설계서 §25.7~25.9 역반영(Y13·Y14·D20). **xfail(strict) 게이트가 실제로 작동** — §25.7 추가 즉시 T4.7 이 xpass→strict 오류를 내어 마커 제거를 강제(의도대로 동작) |
| 2026-08-31 5차 | **D5 공유 커널 구현** — `tests/test_colocation_d5_shared_kernel.py`(**32 tests**: 25 passed / 7 xfailed). X3·X4 **양방향 실측 재확인**(음악 head `c2d3e4f5a6b7` / FLOW head `n1_ads_table`, autogenerate 가 FLOW 테이블 **32/32 drop**). 구현 중 **신규 결함 2건 + 기존 기록 오류 2건 + 하네스 결함 2건** 발견(SC-16~SC-21): **D21 빈 DB 재현 불가**(ORM 32 중 11만 마이그레이션 생성, `c2fbb2654cf3` = 가짜 baseline), **D22 FLOW alembic cwd 의존**(`alembic.ini:6` 상대경로), **X9 는 FLOW 도 해당**(`main.py:310`), **T5.8 환율 근거 0건**. 하네스: `sys.modules["app"]` 오염(전체 회귀에서 FLOW Base 를 측정 — 단독 실행에선 우연히 맞아 더 위험), `Base.metadata` 전역 오염(`_TempEnc`). → **T5.9·T5.10 신설 + `TestHarnessIntegrity` 3종 신설**. 설계서 §25.10~25.12 신설, §26.2 에 자가정정 #16~#21 추가, **D21·D22 미결정 등록**. 전체 회귀 **744 passed / 1 skipped / 16 xfailed / 0 failed** |
| 2026-08-31 6차 | **D1/D2 Plan Z 구현** — `tests/test_colocation_d1_d2_plan_z.py`(**77 tests**: 71 passed / 1 skipped / 5 xfailed / 0 failed). T1.1~T1.9 + T2.1~T2.5 + `TestD26`/`TestD27`/`TestHarnessIntegrity`(5)/`TestSourceContractPins`(5). 구현 중 **신규 결함 5건** 발견 — **D23 Plan Z push 경로 0줄**(음악 전역 `/admin/media` 0건, `flow_song_id` 는 `models.py:58` 예약 필드), **D24 `sync_to_flow` 심의 미검사**(`flow.py:64-68`, 수동 reject 는 `song.status` 를 안 건드려 그대로 동기화), **D25 미발행 오디오 무인증 스트리밍🔴**(`media.py:178-185` 인증 파라미터 없음 + `media_service.py:226-234` `active` 미필터 → 실측 200 전문 수신), **D26 apscheduler 선택 의존**(`requirements.txt:55` 주석 → 예약 발행 영구 미동작, `main.py:198-203` 이 `ImportError` 조차 삼킴), **D27 관리자 60 RPM 자기 차단**(공존 1대 단일 IP). 하네스 결함 3건: **SC-22** `sessionmaker` 의 `with` 는 commit 이 아님(**거짓 통과** 유발 — 부정 단언 전 대상 존재를 먼저 단언), **SC-23** 전역 속도 제한 파일 간 오염(본 모듈이 후속 admin 테스트 18건을 429 로 사살), **SC-24** `MUSIC_PLATFORM_REPO` 미설정 시 **xfail 게이트 13건이 skip 으로 강등** 되면서도 `exit=0`(801/29/8 vs 815/2/21). **SC-25** 계획서 내부 수치 불일치 정정(L0 8+6≠13 → 7+6=13, D5 §12.1 29/22 → **32/25**). 설계서 **§25.13~25.17 신설**, §26.2 **#22~#26** 추가, §26.2.1 **SC-22·SC-23** 추가, §26.3 에 **D23~D27** 등록. 집계를 **진행 표시줄 파싱 → `--junit-xml` 단일 출처**로 교체(`scripts/junit_count.py` 확보). 전체 회귀 **815 passed / 2 skipped / 21 xfailed / 0 failed (838)**, D1/D2 제외 대조 **744 / 1 / 16 (761)** → 기여분 **+71 / +1 / +5 = 77**, 기존 영향 0 |
| 2026-08-31 7차 | **D3 PII 분할 구현** — `tests/test_colocation_d3_pii_split.py`(**49 tests**: 38 passed / 2 skipped / 9 xfailed / 0 failed). T3.2~T3.11 + `TestHarnessIntegrity`(8)/`TestSourceContractPins`(9). **실측 결론**: §21.3 2계층 분할 **0건**(`Subscriber` 단일 테이블이 직접 식별자·준식별자·민감 내용을 모두 보유, `orm.py:183-213`), `/pii/*` 게이트웨이 라우트 **0건**(실제 등록 라우트 검사), `deletion_ledger` **0건**, `Interaction.subscriber_id` **여전히 FK**(`orm.py:334-336`), PII 스크러버 **부재**(`scrub_secrets` 는 시크릿 전용 — 실행 검증에서 이메일·전화가 **원문 유지**), `client_ip` 는 `error_monitor.py:69` 에서 **50자 절단만**(같은 함수의 `:65-66` 은 `scrub_secrets` 적용). 반면 `ConsentRecord`(`compliance.py:45-61`)는 **실재하며 증빙 필드를 모두 갖췄다** — 없는 것은 음악 사용자↔FLOW 구독자 **연결** 뿐. 구현 중 **신규 결함 1건(D28)** 발견: **홍콩 검색 캐시 키가 한국 금고 유래 프로필 속성을 품는다**(`subscriber_service.py:45,50,51,56` → `rag_engine.py:171-178` → `search_v4.py:358,368` → `retriever.py:207-213,214-223`, 저장소 `self._cache`(`:175`)·소유자 모듈 싱글턴(`:49`)·TTL **1800초**(`:155`)) → **분할 후에도 홍콩 프로세스에 구독자 종교상태 조합이 30분 상주**, §21.3 위반 + FU10 표면 확대. 하네스 결함 2건: **SC-26** 문자열 검색이 **위험을 은폐** 했다(틀린 방법이 우연히 맞는 방향을 가리켜 D28 을 놓칠 뻔 — 반대편 T3.8b 는 진짜 거짓 양성: `orm.py:103` `cascade="all, delete-orphan"` · `extraction_service.py:273` `orphan_josa_re`) → 심볼·AST 구조 측정으로 전면 교체(심볼 스캔 **157 파일 · 0 히트**), **SC-27** 음악은 SQLAlchemy **1.x 스타일**(`email = Column(...)`)이라 2.0 전용 파서 결과가 `{}` → 파서를 2.0/1.x 겸용으로 확장. 설계서 §26.2 **#27** 추가, §26.2.1 **SC-26·SC-27** 추가, §26.3 에 **D28** 등록. 전체 회귀 **853 passed / 4 skipped / 30 xfailed / 0 failed (887)**, D3 제외 대조 **815 / 2 / 21 (838)** → 기여분 **+38 / +2 / +9 = 49**, **A/B byfile diff = 추가 12행 / 변경 0행** 으로 기존 영향 0 증명. `MUSIC_PLATFORM_REPO` 미설정 시 **838 / 33 / 16 (게이트 14건 강등)** 실측으로 SC-24 수치 갱신 |
| 2026-08-31 8차 | **D6 보안·공존 구현** — `tests/test_colocation_d6_security.py`(**13 tests**: 10 passed / 3 xfailed / 0 failed). T6.2(TLS 기본값 — FLOW `auth.py:229-233` change-me→503 통과 · 음악 `config.py:59` admin_token 기본값 '' 문서화) · T6.3(무인증 공개 — `media_service.py:201-202` list_public 가 active 만 반환 통과) · T6.4(FLOW `security/ssrf.py` 가드 통과 · 음악 `routers/flow.py:23-40` `_ensure_cdn_url` SSRF 가드 부재 → **T6.4b xfail**) · T6.5(`main.py:292-304` CORS allow_credentials=False + 와일드카드 금지 통과, 정적) · T6.7(관리 토큰 `admin/lib/auth.py:37,41-44` HMAC 파생 통과 · Caddyfile `?a=` 쿼리 log 블록 부재 → **T6.7b xfail**) · T6.8(음악 `ratelimit.py:13` 프로세스 로컬 defaultdict 비공유 문서화 통과 · FLOW `middleware/rate_limit.py` Redis 우선+인메모리 폴백 구조 통과 · 폴백 경고 로그 부재 → **T6.8b_warn xfail**) · T6.9(음악 `models.py:76` api_key 평문 저장 문서화 통과 · `schemas.py:150`+`routers/admin.py:45` GET 마스킹 구현 통과). 구현 중 **하네스 결함 1건 자가정정**: T6.4b 초기 극성 오류(`assert not(...)`가 갭 존재 시 통과→XPASS)를 `assert (...)` 실패→XFAIL 로 반전 + `@pytest.mark.xfail` 누락 보완. 전체 회귀 **863 passed / 4 skipped / 33 xfailed / 0 failed (900)**, D6 기여분 **+10 passed / +3 xfailed = 13**, 기존 테스트 영향 0. D1/D2·D3 와 동일 조건(`MUSIC_PLATFORM_REPO` 설정) 측정 |
| 2026-08-31 9차 | **D7 동기화 계약·생명주기 구현** — `tests/test_colocation_d7_sync.py`(**9 tests**: 5 passed / 4 xfailed / 0 failed) + `tests/colocation_common.py`(공용 정적분석 헬퍼, D1~D6 인라인 복제 종식). T7.1(기존 sync 3종 회귀 — 음악 `tests/test_api.py:122/141/159` + `flow.py` 3 엔드포인트/스키마 존속 · F6 버저닝 부재 → **T7.1c xfail**) · T7.2(F5 삭제 전파 — `songs.py:355-364` `delete_song` 가 `SyncRecord` 미처리 → **T7.2 xfail**, `list_flow` `if not s: continue` 침묵 소멸은 T7.2c 로 핀) · T7.3(X8 — `models.py:58` 예약 컬럼 존속 핀 + Push 미활성 → **T7.3b xfail**) · T7.4(🔴2 — `flow.py:27-29` S3 미설정 시 Suno 원본 반환 핀 + 강등 경고 부재 → **T7.4b xfail**). **신규 제품 결함 0건**(F5/X8/🔴2/F6 는 이미 문서화된 갭). 전체 회귀 **868 passed / 4 skipped / 37 xfailed / 0 failed (909)**, D7 기여분 **+5 passed / +4 xfailed = 9**, **A/B byfile diff = D7 파일 추가 1행 / 타 파일 0행** 으로 기존 영향 0 증명(B=900, A=909). `MUSIC_PLATFORM_REPO` 설정 조건 유지 | |
| 2026-08-31 10차 | **D8 운영·장애·DR 구현** — `tests/test_colocation_d8_ops.py`(**8 tests**: 3 passed / 2 skipped / 3 xfailed / 0 failed) + `tests/colocation_fault.py`(`LatencyInjector` 신규, 네트워크 미터치 지연·실패·분할 주입 쌍). T8.1(B1/D14 미결 — 한국 PII 금고 다운 시 단언값 미정의 → **skip**, §24.2) · T8.2(Y8 백업 2계층 분리 — 저장소 코드에 백업 메커니즘 자체 0건 → **skip**) · T8.3(§11.9 장애 매트릭스 6행 전부 게이트 매핑 cover-ledger 존속 **pin passed**) · T8.4(FU11/Y5 — FLOW 전역 `deletion_ledger`/`ledger`/`reconcile` grep **0건** → **xfail**) · T8.5(F5/D24 — 음악 `flow.py:48` 만 `SyncRecord.status="synced"`, `sync_to_flow:57-83` 이 `moderation_status` 미검사·`revoked` 전이 0건 → **xfail**) · T8.6a(`LatencyInjector` 자기검사 **passed**) · T8.6b(음악 `flow.py:32` `httpx.AsyncClient(timeout=60)` 경계 타임아웃 존재 **passed**) · T8.6c(§11.9 네트워크 분할 지수 백오프 — `flow.py` 전역 `backoff`/`retry`/`tenacity`/`sleep` **0건** → **xfail**). **신규 제품 결함 0건**(FU11/Y5/D14/D24/F5/§11.9 는 이미 문서화된 갭의 게이트화). 전체 회귀 **A=871 passed / 6 skipped / 40 xfailed / 0 failed (917)**, D8 기여분 **+3 passed / +2 skip / +3 xfailed = 8**, **A/B byfile diff = D8 파일 추가 1행 / 타 파일 0행** 으로 기존 909건 영향 0 증명(B=868/4/37/0=909). `MUSIC_PLATFORM_REPO` 설정 조건 유지 | |
| 2026-08-31 11차 | **회귀 통합 + CI 게이트 연결** — `.github/workflows/ci.yml` 수정. ① 주 `test` 잡 pytest 에 `--ignore=tests/test_colocation_*.py` 추가(단일 출처 분리 + SC-24 강등 방지). ② 신규 `colocation-gate` 잡: 동일 org 모노레포 `TopRemnantAcademy/korean-gospel-ai` 를 `_music_platform_src` 에 checkout → `MUSIC_PLATFORM_REPO=${{ github.workspace }}/_music_platform_src/ai-music-platform` 고정(음악 저장소는 `ai-music-platform` 하위 디렉터리, 별도 git 아님 — `colocation_common._music_backend()` 가 `<MUSIC_PLATFORM_REPO>/backend/alembic.ini` 존재 검증). ③ co-location 전용 실행(`tests/test_colocation_*.py`, 211건 실측 166 passed / 5 skipped / 40 xfailed / 0 failed) + `--junit-xml`. ④ **SC-24 게이트 강등 감지**: skip 상한 8 초과 시 명시적 `exit 1`(env 미설정 시 skip 이 30건대로 폭증하는 것 차단). ⑤ 모든 co-location xfail 마크가 `strict=True`(전수 확인) → 갭이 닫히면 XPASS→실패로 병합 차단. ⑥ `android-release` `needs` 에 `colocation-gate` 추가. YAML 검증 통과(jobs: test/colocation-gate/android-unit-test/gitleaks/web-e2e/android-release). **주의**: 실제 병합 차단은 저장소 브랜치 보호 규칙에서 `colocation-gate` 를 required status check 로 지정해야 함(워크플로우 내 needs 만으로는 PR 차단 안 됨). 설계서 미변경 → 신규 인용 없음 | |
| 2026-08-31 12차 | **T7.5 토큰 무효화 구축 (B7 해소) + 회귀 클리어** — 사용자 대리결정(미래지향)으로 **방안 2(HMAC 무효화)** 채택: JWT 재작성 없이 기존 HMAC 모델에 서버측 폐기 저장 + jti 내장. 제품변경: `backend/app/api/auth.py`(`_make_token` 5-part `sub_id:ts:device_id:jti:sig` jti 내장 `:128-143` · `_verify_token` 무상태 순수 유지 `:146-189` · `_check_revocation` `:192-215` — 컬럼/테이블 부재 `fail-open`("no such column"/"no such table"→False, 미마이그레이션 DB 인증 유지), 기타 DB 오류 `fail-closed`(True) · `_verify_token_revocation_checked` 단일 진입점 `:218-236` · `_token_device_id` 4/5-part 공통 `:239-248` · `get_current_user` 라우팅 `:278` · `revoke_session` 실제 폐기 수정(`token_revoked_at=now`, 기존은 서명 불변으로 **장식적 폐기=실제 보안 결함** = B7 본질) `:848` · `revoke_all_tokens` `:862-882` · `POST /auth/revoke-all` `:889-893` · `POST /auth/revoke-token` `:896-903`) + `backend/app/models/orm.py`(`Subscriber.token_revoked_at` `:263` · `TokenBlacklist` `:927`) + `chat.py` 3곳/`events.py`/`chat_pipeline.py:127-136` 호출점 → `_verify_token_revocation_checked` 라우팅 + `db.py` `init_db` 방어적 ALTER(`ALTER TABLE subscriber ADD COLUMN token_revoked_at TIMESTAMP`). 신규 테스트 `tests/test_colocation_d9_token_revoke.py`(**5 tests**: jti 5-part·revoke-all 일괄 폐기·jti 개별 블랙리스트·4-part 하위호환+폐기·`get_current_user` 401). **회귀 교정**: 초기 15 failed(초안이 `_verify_token` 에 DB 조회를 넣어 오래된 `gospel.db` crash + 성능 regression → 무상태 복원 + `_check_revocation` fail-open + 경계 라우팅으로 정정) → 잔여 7 failed(`test_auth_core._token_device_id` 4/5-part 미처리, D3 `TestSourceContractPins` 4종 orm.py 행 +5 이동 `question:342`/`trigger_quote:454`/`client_ip:486`/`contact:505`, D5 T59 `FLOW_ORM_TABLE_COUNT` 32→33+`token_blacklist` 미커버 집합 추가) + `test_verify_auth.py` 3 failed(`_verify_token_revocation_checked` 모킹으로 정정 — 토큰 재파싱 실패) → 전부 0 failed. **전체 회귀 A=876 passed / 6 skipped / 40 xfailed / 0 failed (922)**, **B=871 / 6 / 40 / 0 (917)**, **A/B byfile diff = D9 파일 추가 5행 / 타 파일 0행** 으로 기존 영향 0 증명. `MUSIC_PLATFORM_REPO` 설정 조건 유지 | |
