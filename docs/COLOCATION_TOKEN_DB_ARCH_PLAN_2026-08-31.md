# FLOW ↔ ai-music-platform 토큰·DB 분리 아키텍처 기획 (2026-08-31)

> 상태: **기획서(구현 전)**. 사용자 요청 "기획먼저해" — 본 문서는 결정안 + 근거 + 구현 대상만 정리.
> 모든 결론은 실제 코드 `file:line` 실증 기반(환각 배제 원칙).

---

## 0. 결론 (Executive Summary)

| 항목 | 사용자 직관 | 실측 결론 | 결정 |
|---|---|---|---|
| **토큰** | 음악(구독/SaaS) ↔ FLOW(채팅상담 토큰) 공유하면 안 됨 | 양측 토큰은 **이미 다른 메커니즘+다른 secret+다른 식별자**(🔴A). 음악 `flow.py` 의 "동일 JWT 재사용"은 **거짓 전제** | **공유 금지** → 별도 발급/검증 + `flow-exchange`(scope 한정) + `user_link` 매핑 |
| **DB** | 공유하지 않는 게 더 실용적 아닌가 | 기본값은 **이미 비공유**(각 sqlite). 공유 커널(변형 B)은 감사에서 **X3/X4 🔴 + PII 잔류(D28)** 로 정정 축소됨 | **공유 커널(전체) 채택 금지** → 서비스별 독립 DB + 좁은 브리지(매핑/원장)만 공유 |

**핵심**: 두 플랫폼은 비즈니스 모델(구독 SaaS vs 채팅상담 토큰)이 다르므로 **권한 경계(토큰)와 데이터 경계(DB)를 모두 분리**하는 게 정답. 공유는 "회계 정합성" 하나의 이득뿐이고, 이는 좁은 브리지(원장)로 충분히 얻는다.

---

## 1. 현행 실측 (code = fact)

### 1.1 양측 토큰 모델 — 이미 분리되어 있음

**음악 (JWT)**
- `backend/app/routers/auth.py:30-46` — `create_token(user_id)`: JWT `HS256`, payload=`{"sub": user_id, "exp": ...}`, `settings.jwt_secret`(`config.py:11`, 기본값 `"change-me-please"` ⚠️), `access_token_expire_minutes` 기본 1440(24h).
- **scope/tier 클레임 없음** — 토큰만으로 구독 등급을 구분 불가.
- 구독 모델: `models.py:101-105` `pricing_tiers`(`free|standard|sacred|enterprise`) + `config.py:44` `per_user_daily_quota=3`. → 구독 상태는 **토큰이 아니라 생성 시 DB 조회**로 확인.

**FLOW (HMAC, JWT 아님)**
- `backend/app/api/auth.py:128-140` — `_make_token(sub_id, device_id, jti)`: 5-part `sub_id:ts:device_id:jti:sig`, `AUTH_SECRET` HMAC 서명.
- `get_current_user` → `subscriber_id`(문자열) 반환. 폐기: `token_revoked_at`(일괄) + `TokenBlacklist`(jti 개별, `:927`).
- 식별자 = `Subscriber.subscriber_id`(`orm.py:183-213`).

**식별 모델 자체가 다름**: 음악 `User.id` vs FLOW `Subscriber.subscriber_id`. FLOW `orm.py` 에 음악 연결 필드 0건(grep `music_user_id|music_id|platform_user|external_id` 무매칭).

### 1.2 🔴A — "FLOW APP 동일 JWT 재사용"은 거짓 전제 (이미 문서화됨)

- 음악 `routers/flow.py:1-8` docstring: *"FLOW APP가 동일 JWT 재사용"* 가정.
- 그러나 FLOW 백엔드는 HMAC이라 **음악 JWT를 검증할 수 없음** → `sync_to_flow`/`list_flow` 는 그 가정 하에서만 동작, 실제 FLOW에선 인증 불가.
- 설계서 이미 식별: 🔴A(`COLOCATION_PLAN_VERIFIED_2026-08-30.md:104`), 연쇄(`:139`, `:792`), 그런데 `flow.py:4` 주석은 여전히 "사실이 아닌 전제"로 기술(`:792`).
- → **코드·주석과 설계서가 불일치**. 이 틈이 사용자 우려의 정확한 지점.

### 1.3 DB 현행 — 기본값은 비공유, 공유 커널은 "제안 + 정정"

- 기본값: 음악 `sqlite:///./app.db`(`config.py:9`), FLOW `./gospel.db`(sqlite) → **기본적으로 공유 안 함**.
- 설계서 "공유 커널 DB(변형 B, §14)" 제안했으나 감사로 리스크 발견:
  - **X3** `alembic_version` 충돌 — 양측 `env.py` 모두 `version_table` 부재 → head 충돌(`:773`).
  - **X4** autogenerate 가 상대 테이블 **DROP 생성** → 운영 데이터 전삭제(`:669`, `:774`).
  - **D28** 홍콩 공유 메모리/캐시 키에 한국 금고 유래 PII 속성 잔류(§25.18).
- 설계서 정정(`:1109`, `:1113`): 공유 커널 대상에서 **PII 테이블(subscriber 식별부/payment/consent_record/음악 users 식별부) 제외** → 사실상 "비식별 데이터만" 으로 축소.

---

## 2. 토큰 아키텍처 결정

### 2.1 원칙
1. 토큰은 **서비스별로 발급·검증**(별도 secret, 별도 식별자). 공유 secret/토큰 금지.
2. 음악(구독 SaaS)과 FLOW(채팅상담 토큰)는 **별개 권한 경계** → 한쪽 토큰이 다른쪽 자원을 개방하면 안 됨.
3. FLOW APP 은 두 토큰을 별도 보유:
   - (a) **FLOW HMAC 토큰** — 채팅/오디오(API 자체 검증).
   - (b) **sync 전용 단기·scope 한정 음악 JWT** — `flow-exchange` 로 교환 발급, `GET /api/sync/flow` 전용.

### 2.2 토큰 교환 (설계서 `:210` 제안을 채택·구체화)
- 음악 신규 `POST /api/auth/flow-exchange`(내부 `FLOW_INTERNAL_TOKEN` 으로 보호):
  - 입력: FLOW `subscriber_id`(+ 기기/세션 증명).
  - 처리: `user_link` 매핑 조회/생성 → **단기(예: 1h)·scope 한정 JWT** 발급.
- **`create_token` 에 `scope` 클레임 추가**(현재 `sub` 만): `{"sub": user_id, "scope": "sync:flow", "exp": short}`.
  - → 이 JWT 로는 sync 외 권한(음악 생성/관리/결제) 부여 불가. 최소 권한 원칙.
- FLOW APP 은 이 scoped JWT 로만 sync 호출. FLOW 채팅은 자체 HMAC 토큰(구독 상태와 무관).

### 2.3 식별 연동 (`user_link`)
- `user_link` 테이블(`song_user_id ↔ flow_subscriber_id`, 설계서 `:378`): 매핑만 보관.
- 위치: 각 서비스가 자기 DB에 copy 보관 **또는** 좁은 브리지 DB 1개(§3.2).
- **구독 상태는 음악 도메인 only**. FLOW 는 자기 토큰으로 음악 구독을 추론/요구하지 않음.

---

## 3. DB 토폴로지 결정

### 3.1 공유 커널(전체 테이블) 채택 불가
- X3/X4 🔴(마이그레이션 파괴) + D28(PII 홍콩 잔류) + 블라스트 레이더스(X1 노출 × 공유커널 연쇄 1, `:810`).
- 공유 커널의 유일 실질 이득은 "회계 정합성"(§11.5 비용 귀속, `:844`) — 이는 좁은 원장으로 얻음.

### 3.2 권장: 서비스별 독립 DB + 좁은 브리지
홍콩 대형 1대에:
- **음악 전용 DB** + **FLOW 전용 DB** — 별도 Postgres *database*(또는 schema), 동일 인스턴스 가능 → 인프라 2배 아님.
- **PII**(구독자 식별부/결제/동의)는 **한국 금고(별도 소용량)** — 홍콩 DB 에 PII 테이블 0(§14 정정 반영).
- **브리지(공유 항목 최소)**: `user_link` 매핑 + **비용 귀속 원장(cost ledger)** 만. 둘 다 각 서비스가 자기 migration 체인으로 소유 → X3/X4 회피.
- 교차 접근은 **API pull**(`flow.py` 동기화 모델) — DB join 불필요.

### 3.3 이득
- X3/X4 **완전 소멸**(각 DB 고유 `alembic_version`).
- PII 한국 금고 격리 → D28 캐시 키 잔류 리스크 축소.
- 블라스트 레이더스 축소(한쪽 migration/장애가 다른쪽에 영향 0).
- 스키마 독립 진화(음악 pricing_tiers 변경이 FLOW 에 영향 0).

---

## 4. 구현 대상 (사용자 확정 후 실행)

- [ ] 음악 `create_token`(`routers/auth.py:30-33`) 에 `scope` 클레임 추가 + `flow-exchange` endpoint(`FLOW_INTERNAL_TOKEN` 보호) 구현.
- [ ] `user_link` 테이블 + 매핑 로직(음악/브리지 DB).
- [ ] 음악 `flow.py:1-8` docstring "동일 JWT 재사용" → "scoped exchange JWT" 로 정정 + `sync_to_flow`/`list_flow` 이 scoped JWT 검증(🔴A 폐소).
- [ ] DB 토폴로지: 각 서비스 전용 DB, PII 홍콩 비보관 확인, 브리지(매핑+원장) 최소화.
- [ ] 게이트 전환: X3/X4 회피를 전제로 한 "독립 DB" 게이트(T5.1 개정) 로 전환, shared-kernel 게이트 폐기.

## 5. 결정 (사용자 "전부 해결해" 위임 → 대리결정, 2026-08-31)

사용자가 "전부 해결해" 로 위임함(12차 T7.5 와 동일 패턴: 미래 지향적·안전 기본값). 다음을 확정하고 구현 진행.

- **Q1 장기 identity**: **별도 identity + `user_link` 매핑**(현재 권장) 으로 고정. Phase Z OIDC/SSO 로의 진화는 `user_link` 가 브로커 역할을 하므로 열어둠. 전면 SSO 는 미적용(블라스트 레이더스 축소).
- **Q2 번들 여부**: **완전 독립**. 음악 구독 상태는 FLOW 접근을 부여하지 않음, 그 반대도 동일. 각 토큰은 자기 도메인 권한만 표현(scope 한정).
- **Q3 브리지 DB**: **동일 HK 인스턴스 내 별도 Postgres database**(schema 분리와 동등한 격리, 인프라 2배 아님). 각 DB 고유 `alembic_version` → X3/X4 소멸.
- **D14 캐시 미스 정책**: **fail-closed** — 미스 시 한국 금고 원본에서 재계산, HK 캐시 키에 PII 파생 속성 미포함(§25.18/D28 연동).
- **D16 재식별 범위**: 최소화 — HK 는 가명키(subscriber_id) + 콘텐츠/벡터/미디어만, subscriber PII 원문은 한국 금고 전용.
- **D18 Langfuse**: **기본 OFF + 명시적 opt-in + 송신 전 스크러빙**(Y9/Y10). 안전 기본값.
- **D19 LLM 프롬프트**: **최소화 전송**(필요 텍스트만, 송신 전 PII 스크럽, Y11). DPA 별도.

→ 모든 결정은 설계서 §15 X 시리즈·§25 Y 시리즈 권장과 일치(안전 기본값 우선).

---

## 부록: 관련 기존 실측 인용
- 🔴A `COLOCATION_PLAN_VERIFIED_2026-08-30.md:104,139,210,378,466,792`
- 공유 커널 X3/X4/D28: 동 문서 `:638-849, 669, 773-774, 1109, 1113, 1626`
- 음악 토큰: `ai-music-platform/backend/app/routers/auth.py:30-46`, `config.py:11,44`
- 음악 구독: `ai-music-platform/backend/app/models.py:101-105`
- FLOW 토큰: `FLOW/backend/app/api/auth.py:128-140, 192-236, 277, 866-905`
