# FLOW + ai-music-platform 공존 통합 설계서 (실증·연관성 분석 버전)

> **[2026-09-10 실행 착수 갱신]** 본 설계서의 공존 아키텍처가 **부분 착수**되었다:
> ① `ai-music-platform` 은 외부 repo(`korean-gospel-ai`)에서 `FLOW/ai-music-platform` 내부 독립 repo 로 이관.
> ② 음악 11 테이블이 FLOW `.gospel.db` 에 합일(공유 DB, `alembic_version_music` 격리 — X3 유지).
> ③ 음악 `backend/.env` 의 `DATABASE_URL` 이 공유 DB 를 가리킨다. 본문의 `korean-gospel-ai` 경로는
>    역사적 근거로 보존하되, 현재 유효 경로는 `C:/Desktop/Project/FLOW/ai-music-platform` 이다.
>    → `docs/MUSIC_PLATFORM_MIGRATION_2026-09-10.md`, `tests/test_colocation_d5_shared_kernel.py` (32/32 passed).
>
> **[2026-09-10 sync 사슬 "Web 자가발급 다리" 착수 — 음악 repo b16c608]**
> 본문 14.2 의 3단계 사슬(APK → FLOW backend(HMAC) → flow-exchange → scope JWT → APK pull) 중
> **4번째 빠진 다리 = Web 프런트의 자가발급** 을 추가 착수했다:
> - 신규 `POST /api/auth/flow-token`(auth.py): 전권한 JWT 로 **자기 계정** scope 한정(sync:flow)
>   단기 JWT 자가발급 → Player「FLOW APP 에 동기화」버튼이 종단 동작(401 사슬 해소).
> - 🔴A fail-closed 계약 **불변**: sync 엔드포인트는 여전히 전권한 JWT 를 거부(401) —
>   test_flow_sync_rejects_full_token 이 계약 잠금. 자가발급은 sub=자기 자신이라 권한 상승이 없다.
> - `SYNC_FLOW_SCOPE` 상수 단일원(auth.py) — flow.py 3곳 + flow-exchange + flow-token 공유.
> - 실측(8001 라이브): 무인증 발급 401 / 발급 200(scope=sync:flow) / 전권한 JWT→sync 401 유지 /
>   **scope 토큰→sync 404('歌曲不存在', 인증 관통+소유 검증 도달)** / GET /sync/flow 200 [].
>   pytest 221/221, tsc 0 오류.
> - **잔여 다리(변경 없음)**: FLOW backend 의 flow-exchange 프록시(서버측) + FLOW APK pull UI.
>   착수 조건 = FLOW_INTERNAL_TOKEN 설정(음악/양측 동일값, `.env.example:47`) + APK UI 작업.
>   원격 백업: `github.com/TopRemnantAcademy/ai-music-platform`(private, default=feature/company-quality-2026-05-26).

> 작성일: 2026-08-30 · 상태: 기획(Plan) · 근거: 모든 결론은 실제 코드 `file:line` 기반(환각 배제)
> 대상: **홍콩 대용량 서버 1대**에 FLOW(한국어 복음/성경 오디오 앱)와 ai-music-platform(웹 음악 생성)을 공존시키고,
> 사용자 시나리오("웹에서 생성한 음악을 FLOW 앱 음악단에 추가")를 달성하기 위한 엔터프라이즈 통합 설계.
>
> ### 확정 전제 (사용자 확정, 2026-08-30)
> | # | 전제 | 근거 |
> |---|---|---|
> | T-1 | **한국 서버 = 소용량 / 홍콩 서버 = 대용량** | 사용자 확정 |
> | T-2 | **양 서비스(앱·콘텐츠·미디어) = 홍콩 대용량 1대에 공존** | 사용자 확정(선택지 "양 서비스 모두 홍콩 대용량 1대") |
> | T-3 | **개인정보(PII) = 한국 서버에만 보관** | 사용자 확정("개인정보만 한국서버에 놓을거야") |
> | T-4 | 한국 서버의 구체 스펙·용량은 **미확정(사용자 입력 대기)** | 코드 근거 없음 → §20 표기 |
> | T-5 | 홍콩 대용량 서버의 구체 RAM/CPU/디스크는 **미확정** | 코드 근거 없음 → §20 표기 |
>
> ⚠ T-3는 본 문서의 **데이터 평면 전체를 재설계**하게 만든다(§21). T-4/T-5는 §11.8·§21.6 수치의 입력값이므로 확정 전 정량 결론은 보류한다.

---

## TL;DR — 제2부 재검증으로 뒤집힌 3가지 결론 (2026-08-30 추가)

1. **FLOW에는 이미 오디오 종단 파이프라인이 있다.** 업로드(`app/api/media.py:61-62`) → 로컬 바이트 소유(`services/media_service.py:148`) → 예약 발행(`services/media_publisher.py:33`) → 푸시 알림(`:54`) → 공개 카탈로그/Range 스트리밍(`media.py:157-158, 178-179`)이 전부 운영 중이다. 따라서 **신규 수신 API·신규 테이블은 필요 없다**(제1부 제안 철회 → §17).
2. **사용자 제안("기존 DB에 두 서비스 항목을 추가하여 구분")은 성립한다** — 테이블명 충돌 0건, FLOW는 환경변수 1줄로 Postgres 전환 가능(`app/db.py:41-49`), 이미 `workspace_id` 테넌트 축 보유(`models/compliance.py:75`). 단 **단일 스키마 직역은 금지** — Alembic `alembic_version` 충돌(X3)과 **autogenerate가 상대 테이블 DROP을 생성**(X4)하는 치명 함정이 실증됨. → 권장은 **스키마 분리(변형 B)**, 지금 채택은 **레코드 수준 출처 태깅(③)**(§14).
3. **최소 성공 경로가 1/3로 줄었다.** 브로커+S3+수신 API가 아니라 **P0(포트/시크릿/심의) → P1(카테고리 1줄 + 컬럼 2개 + 푸시 워커)** 만으로 사용자 시나리오가 종단 달성된다(§18).

⚠ 동시에 **최상급 신규 리스크**를 발견: 음악 compose가 Postgres 5432·Redis 6379를 호스트에 공개하며 비밀번호가 `music123`이다(`docker-compose.yml:9, 11-12, 19-20`). 이는 공유 커널 채택의 **절대 선결 조건**이다(X1, §16.3 연쇄 1). **홍콩 단일 대형 서버가 인터넷에 직접 면한다는 점에서 이 리스크는 더 커진다**(§20.2 정정 3·4).

> **제3부(§20~§24) 갱신 — 토폴로지 확정 + 개인정보 한국 분리**
> 4. **토폴로지 재확정**: 양 서비스는 **홍콩 대용량 1대** 공존(사용자 확정). 기존 기획의 "8G 단일 박스 포화 → 호스트 분리" 논리는 **폐기**하고, 홍콩 내부 수평 분할로 대체한다(§20.2 정정 2). Plan Z의 localhost 전송(egress 0) 이점은 **유효**하다.
> 5. **"개인정보만 한국"은 테이블 이전이 아니라 가명화 경계 재설계다.** `subscriber`(`orm.py:184`)의 이메일·구원상태·신앙단계·현재고민과 `interaction.question`(`orm.py:336`)의 **원문 질문**이 PII이며, 음악 `users.email`(`models.py:9`)도 같다 → **한국=PII 금고 / 홍콩=가명 콘텐츠** 2계층으로 분할한다(§21).
> 6. **환각 이중감사 결과**: 전 주장 30건을 재검증해 **4건 정정**(음악 Alembic 5개·홍콩 경량 서버 기술·홍콩 단일 박스·PII 위치)과 **미검증 2건을 명시 보류**(한/홍 서버 스펙)했다. 추정 수치는 전부 `보류` 표기로 강등했다(§20).
>
> **제4부(§25) 갱신 — PII 경계는 DB 가 아니라 출구(egress)에 있다**
> 7. **§21.1 인벤토리가 불완전했다**(자가정정 #6). PII 는 3개 테이블이 아니라 **최소 8개 테이블**에 분산: `salvation_journey.trigger_quote`(`orm.py:1116`, 구원 결단 순간 원문 = 최고 민감), `support_ticket.contact`(`orm.py:1134`, 직접 식별자), `app_error_log.client_ip`(`orm.py:481`, 자동 기록), `verse_annotation.note`(`orm.py:380`), `activity_event.device_id/payload`(`orm.py:876, 881`) 등.
> 8. **최고 심각 신규 발견 — Langfuse 원격측정이 기본값 ON 이다.** `config.py:178` `langfuse_enabled: bool = True`, 목적지는 **EU 클라우드** `config.py:181`, 전송 내용은 **요청 전문 + `subscriber_id`** `chat.py:193` `lf.trace(input=req.model_dump(), user_id=sub_id)` + 파이프라인 원문 재전송(`chat_pipeline.py:154, 458, 538`). 운영자가 키만 채우면 **사용자 질문 원문이 EU 로 유출**되며, 이는 **§21 의 DB 분할로는 전혀 막히지 않는다.** → **스크러버는 저장 지점이 아니라 모든 출구 앞에** 두어야 한다(Y9~Y10, §25.4).
> 9. **LLM 프로바이더는 PII 금고로 덮을 수 없는 유일한 예외 경로**다(`config.py:23-24, 44`). 프롬프트는 추론을 위해 반드시 외부로 나간다 → 은폐하지 말고 **처리위탁(DPA) + 프롬프트 최소화**로 관리(Y11, D19).

---

## 0. 문서 목적 및 범위

본 문서는 두 프로젝트를 **코드 레벨에서 실증**하고, 발견된 갭들을 **단독 결함이 아닌 상호 연관(루트커즈·의존·블라스트 레이디어스·파생 갭)** 으로 분석한다.
"공존"의 진정한 목표는 "같은 박스에 두 앱을 올리는 것"이 아니라 **"사용자 시나리오의 종단 달성 + 공존 품질(보안/확장/운영) 유지"** 이다.
따라서 갭을 (A) 시나리오 블로커 / (B) 공존 품질 로 명확히 분리하고, 무엇이 무엇을 막는지 의존 그래프로 제시한다.

---

## 1. 실증 방법론 (환각 배제 원칙)

- 모든 주장은 `Read`/`Grep`/`Explore`로 추출한 `file:line`에 고정. 추측 표현(∼일 것, 아마)은 실코드 확인 후에만 사용.
- 검증 경로: 양측 `main.py`(CORS/라우팅), `routers/flow.py`(동기화), `models.py`(SyncRecord), `docker-compose*`, `Caddyfile`, `mobile/utils/state.js`, `config.py`, `ratelimit.py`, `logging_setup.py`, `moderation.py` 직독.
- 2건의 자가정정(기존 기획 오판)을 명시적으로 포함: `GET /api/sync/flow` 인증 정상 / 음악 alembic migrations 존재.

---

## 2. 현재 상태 실증 팩트베이스

### 2.1 FLOW 스택 (실증)
| 계층 | 사실 | 근거 |
|---|---|---|
| 관리 UI | Streamlit `admin/app.py`+`admin/pages/*`(22페이지), 포트 8501(운영), 8502는 **구동 안 됨** | `Dockerfile.ui:21`, `docker-compose.prod.yml` 서비스=qdrant/backend/ui/caddy(8502 없음) |
| UI 게이트 | `APP_PASSWORD`+HMAC SHA256(`kg_admin_gate`) 토큰을 URL `?a=`에 보관(F5 유지용) | `admin/lib/auth.py:41-99` |
| UI→백엔드 | `Authorization: Bearer {ADMIN_API_KEY}` | `admin/lib/api_client.py:25,43` |
| 백엔드 인증 | `require_admin`: `X-API-Key`/`Bearer` 수용, `hmac.compare_digest` vs `admin_api_key`(기본 change-me→503) | `enhanced_rag.py:92-111` |
| 사용자 인증 | HMAC `AUTH_SECRET`(JWT 아님), 자체 `Subscriber` 테이블 | `backend/app/api/auth.py`, `orm.py` |
| CORS | 화이트리스트, `allow_credentials=False`, 명시적 메서드/헤더 | `main.py:292-304` |
| 프로덕션 DB | **SQLite 파일**(`./gospel.db` 볼륨) — Postgres 아님 | `docker-compose.prod.yml:69` |
| 미디어 저장 | 로컬 디스크 `./data`(media/music) | `compose:67-71` |
| 시크릿 로그 | `api_key/secret/token/password/bearer/...` 마스킹 필터 존재(성숙) | `logging_setup.py:27-40,98-111` |
| 컴플라이언스 | PIPL 동의/크레딧 지갑/워크스페이스 모델 `Base.metadata` 등록(성숙) | `models/compliance.py:64`(ConsentRecord) `:84`(WalletReservation) `:94,108,119`(Workspace 계열) — ⚠ 종전 인용 ~~`docker-compose.prod.yml:306-308`~~ 은 **실재하지 않음**(§26.1 SC-7) |
| 모바일 플레이리스트 | **서버측 엔티티 없음**, `localStorage` 키 `gospel_pl`(`[{id,name,cover,ids:[trackId]}]`) | `mobile/utils/state.js:26` |
| 모바일 인증 | 토큰 메모리만(XSS 방지), localStorage 미저장 | `state.js:34` |
| 단일 진입 | Caddy `:443` → `/api/*`+다수 루트=`backend:8000`, 나머지=`ui:8501` | `Caddyfile:29-91` |
| 리소스 | qdrant 2G/1cpu, backend 3G/2cpu(workers=2), ui 1.5G/1cpu, caddy 256M → **컨테이너 상한 합 ≈ 6.75G/4.5cpu** (⚠ 종전 "8G 박스 중 6.5G"는 근거 없는 추정 → §20.2 정정 2로 폐기) | `docker-compose.prod.yml:40-42, 73-75, 110-112, 138-140` |

### 2.2 ai-music-platform 스택 (실증)
| 계층 | 사실 | 근거 |
|---|---|---|
| 관리 UI | Next.js `frontend/app/admin/page.tsx`(심의)+`config/page.tsx`(엔진/라우팅/요금). 토큰 `localStorage["admin_token"]`→`X-Admin-Token` | `frontend/lib/api.ts:109-120` |
| 백엔드 인증 | `require_admin`: `X-Admin-Token` vs `admin_token`(기본 ""→503) 상수시간비교 | `routers/auth.py:75-84` |
| 사용자 인증 | JWT HS256 `JWT_SECRET`(별개) | `auth.py:30,36` |
| CORS | 화이트리스트, **`allow_credentials=True`** | `main.py:20-26` |
| DB | Postgres(compose `music`/`music123`), alembic `migrations/`(6버전, 비표준 디렉터리명) | `docker-compose.yml:4-14`, `backend/migrations/` |
| 레이트리밋 | **프로세스 내 `deque`(in-memory)**, Redis 선언만·未접속 | `ratelimit.py:13`, grep `redis`→`config.py:10`만 |
| 오디오 저장 | S3/COS **선택적** — 미설정 시 Suno 원격 URL 직접 반환(휘발) | `flow.py:23-40`, `s3.py:16-22` |
| EngineConfig.api_key | **평문 저장**, KMS/암호화 0건 | `models.py:76`(주석 66-68 자인) |
| redis | **비밀번호 없음** | `docker-compose.yml:16-20` |
| 콘텐츠 심의 | `enable_moderation=False` 기본, `moderate_audio`는 항상 `True` 플레이스홀더, 블랙리스트 예시단어만 | `config.py:48,51`, `moderation.py:19,59-75` |
| 배포산출물 | Dockerfile/nginx/Caddy/systemd **없음**, CI=pytest+alembic뿐 | 전체 탐색 결과 |
| 포트 | 백엔드 8000, 프론트 3000, postgres 5432, redis 6379 | `api.ts:2`, `package.json`, `compose` |

### 2.3 공존 현황 요약
- 실제 포트 충돌: **백엔드 `:8000` 1곳**(FLOW `main.py:3` ↔ 음악 `api.ts:2`).
- FLOW는 이미 Caddy 단일 진입+리소스 거버넌스를 갖춤. 음악은 컨테이너/프록시 정의가 없어 **FLOW의 오케스트레이션에 편입**해야 함.
- 양측 관리자 인증은 겉보기만 비슷(공유시크릿+상수시간비교)하고 env명/헤더명/값이 다름 → 시크릿 공유 금지.

---

## 3. 사용자 시나리오와 근본 블로커

**시나리오**: "웹 음악 플랫폼에서 음악을 생성 → FLOW 앱의 음악단(playlist)에 추가하여 재생."

**🔴 근본 블로커 3종 (실증)**:
1. **🔴A 인증 모델 불일치**: 음악은 "FLOW APP가 동일 JWT 재사용"을 가정(`flow.py:4`)하나, FLOW는 HMAC `AUTH_SECRET`+자체 `Subscriber`. 기존 sync endpoint는 이 가정 위에서만 동작 → 실제 FLOW에선 인증 불가.
2. **🔴B 수신 측 부재**: FLOW 백엔드에 외부 음악 수신 endpoint 없음(`api/` grep → 자체 `data/music`·`/admin/media`만), 서버측 playlist 엔티티 없음(`gospel_pl`=localStorage). 음악이 Push를 안 하고, FLOW도 Pull할 곳이 없음.
3. **🔴C 오디오 안정 URL 부재**: 음악 `STORAGE_*` 미설정 시 `audio_cdn_url`이 휘발성 Suno URL(`flow.py:27-29`). FLOW가 신뢰 재생하려면 안정 공개 URL 필수.

→ 3종 모두 **설정만으로 해결 안 됨**. 🔴A는 ID 매핑(브로커), 🔴B는 수신 측 개발, 🔴C는 저장소 설정이 각각 필요.

---

## 4. 갭 카탈로그 (전부 실증)

| ID | 영역 | 심각도 | 설명 | 근거 |
|---|---|---|---|---|
| 🔴1 | 동기화 | Block | 음악 sync=FLOW outbound 0건·로컬 플래그만; FLOW 수신 측 부재 | `flow.py:32-83`, `state.js:26` |
| 🔴2 | 오디오 | Block | 미설정 시 휘발 Suno URL | `flow.py:27-29` |
| 🔴A | 인증 | Block | JWT 가정 vs HMAC 실제(불일치) | `flow.py:4`, FLOW auth |
| 🟠3 | 포트 | High | 백엔드 `:8000` 충돌 | `main.py:3` vs `api.ts:2` |
| 🟠4 | CORS | High | 양측 화이트리스트 — 교차 호출 시 양방향 origin 등록 | `main.py:20-26`(음악), `main.py:292-304`(FLOW) |
| 🟠5 | 배포 | High | 음악 배포산출물 부재 → FLOW Caddy/compose 편입 | 탐색 결과 |
| 🟡7 | 관리UI | Med | 페이지 명칭 중복(media/users) | FLOW `11_미디어업로드`/`9_유저관리` |
| 🟡8 | 보안 | Med | FLOW `?a=` 토큰 URL 기록(로그 노출) | `auth.py:79-86` |
| F1 | 확장 | High | 레이트리밋 비공유 — 음악은 **모듈 전역 `defaultdict`** 로 다중 인스턴스 시 무력화. FLOW 는 **Redis 우선 + 인메모리 폴백**(구조 상이) | 음악 `ratelimit.py:13`, FLOW `middleware/rate_limit.py:100-102`(redis zset) `:116-134`(폴백) — ⚠ 양측 동일 취급 금지, §26.1 SC-8 |
| F2 | DB | High | FLOW 프로덕션 SQLite → 수평확장/HA 한계 | `compose:69` |
| F3 | 시크릿 | High | 음악 EngineConfig.api_key **저장 시** 평문, 암호화 0건. 단 **GET 응답은 이미 마스킹 구현됨**(`api_key_set: bool`) — 따라서 갭은 "at-rest 암호화 부재"로 한정 | `models.py:76`(평문 저장), `schemas.py:150`, `admin.py:45`(마스킹 구현) — §26.1 SC-9 |
| F4 | 시크릿 | Med | 음악 redis 비밀번호 없음 | `compose:16-20` |
| F5 | 동기화생명주기 | High | 삭제곡 침묵 소멸(`if not s: continue`), 재편집 시 stale URL 유지 | `flow.py:91-93,25-26,70-72` |
| F6 | 계약 | Med | 양측 API 비버저닝(`/api/v1` 없음) | `flow.py:20`, FLOW `auth.py:37` |
| F7 | 컴플라이언스 | High | 음악 콘텐츠 심의 스텁(자동 ASR 0건) | `moderation.py:59-75` |

---

## 5. 연관성 분석 (본 문서 핵심)

### 5.1 루트커즈 클러스터 — 갭은 따로 떨어진 게 아니라 4개 원인에서 파생된다

**RC-A. "통합은 희망으로 설계되고 구현되지 않음"** → 🔴1, 🔴2, 🔴A, F5
- 음악 `flow.py` docstring이 "FLOW APP 동일 JWT 재사용"(4), "Push는 이후 FLOW 독립 백엔드 있으면"(7)이라고 명시. 즉 sync는 **인터페이스만 존재, 계약은 미체결**.
- 🔴2(휘발 URL)는 🔴1 미구현의 *하위 증상*(audio를 제대로 고정 안 함). F5(삭제/편집 맹점)는 같은 불완전 프로토콜이 **미래에** 드러내는 증상.
- FLOW 측도 외부 음악을 "상상하지 않음"(수신 측/서버 playlist 부재) — 양측 다 단독 MVP 진화의 결과.

**RC-B. "비대칭 보안 성숙도"** → 🟡8, F3, F4, F7 (음악 측 집중)
- FLOW는 시크릿 마스킹·엄격 CORS·PIPL 모델·암호화키로 성숙. 음악은 CORS `credentials=True`·api_key 평문·redis 무인증·심의 스텁으로 **한 단계 뒤**.
- 공존 전엔 "음악은 별도 서비스"였으나, **같은 호스트/같은 docker network/같은 프록시/같은 `.env` 디렉터리**에 두면 약한 보안이 강한 시스템의 **공유 리스크 표면**이 된다(§5.3).

**RC-C. "단일 인스턴스 가정"** → F1, F2, (🟠3의 본질)
- FLOW는 이미 SQLite+workers=2로 단일 박스 한계 도달. 음악 레이트리밋도 in-memory.
- ~~🟠3(포트 충돌)은 **가시적 증상**일 뿐, 진짜는 8G 박스에 FLOW ~6.5G 점유 → 음악(backend+front+postgres+redis ≈ 1.5~2G)을 추가할 **용량 여유 부족**(§5.3). 즉 포트보다 용량이 먼저 터짐.~~ ⚠ **폐기(§20.2 정정 2)**: "8G 박스"는 근거 없는 추정. 홍콩 대용량 호스트(T-2)에서는 **용량이 병목이 아니다**. 병목은 보안(§5.3 블라스트)·가용성(SPOF)·데이터 평면(PII 한국, §21)이다.

**RC-D. "계약 성숙도 부재"** → F6, (🔴1의 취약성)
- 버저닝 없는 API로 Phase 3 수신 측을 붙이면, 향후 음악 백엔드 변경이 FLOW 하드코딩 연동을 깨뜨림. 🔴1 해결은 버저닝과 함께 가야 안정적.

### 5.2 갭 의존 그래프 (무방향 — 무엇이 무엇을 만듦)

```
            RC-A(통합 미구현)
           /    |    \      \
      🔴A(인증) 🔴1(수신) 🔴2(URL)  F5(생명주기)
         |        |           |
         +----┐   |           |
              ▼   ▼           ▼
        [Request B broker] [Phase3 receiver] [Phase0 S3]
              │   │                  │
              └──▶ 시나리오 종단 달성 ◀── F6(버저닝, 수신 측 안정성)

            RC-B(비대칭 보안)                RC-C(단일인스턴스)
         🟡8  F3  F4  F7  ──공유호스트──▶  F1  F2  (🟠3:용량)
              │                                  │
              └────── 블라스트 레이디어스 ────────┘
                     (음악 붕괴가 FLOW에 전이)

   🟠4(CORS), 🟠5(Caddy편입), 🟡7(명칭) = 공존 품질(시나리오와 무관, 병렬 해결)
```

### 5.3 블라스트 레이디어스 — 비대칭 성숙도가 공유 리스크로 증폭되는 경로

공존 전엔 음악의 약한 보안이 "음악만의 문제"였다. 공존 후:
1. **네트워크 전이**: 음악 redis 무인증(F4)이 FLOW `gospel-network`에 편입 → 침해자가 redis 통해 음악 측 횡적 이동 후 호스트 자원 탐색. → redis `requirepass`+네트워크 분리 필수.
2. **로그 오염**: 음악은 시크릿 마스킹 없음(F3 맥락) → 공유 로그 집계 시 음악 `admin_token`/S3 key가 평문 노출, FLOW 성숙 마스킹도 같이 오염. → 로그 스트림 분리 또는 음악에도 마스킹 적용.
3. **저장소 결합**: FLOW `./data` 볼륨(로컬 디스크)과 음악 S3(🔴2 해결 시)가 혼재 → 크로스 백업/복원 정합성 필요(§8).
4. **~~용량 전이~~ → 자원 경쟁 전이(§20.2 정정 2)**: RC-C — ~~8G 박스 포화~~. 실제 위험은 **음악 컨테이너에 리소스 상한이 없다는 것**(음악 compose에 앱 서비스 자체가 없음 = X2). 홍콩 대용량 호스트라도 상한 없는 음악이 FLOW를 OOMKill 할 수 있다(`docker-compose.prod.yml:39` 주석: FLOW 상한은 OOMKilled 방지 목적). → **음악에도 동일한 mem_limit/cpus 상한 정의**가 해법(호스트 분리 아님).

### 5.4 2차 파생 갭 (설계를 시작하면 드러나는 숨은 요구 — 추측 아님, 아키텍처 필연)

- **G-S3-CORS**: 🔴2 해결로 `audio_cdn_url`이 안정 S3 URL이 되면, FLOW `<audio>`/모바일이 cross-origin 로드 → **S3 bucket CORS에 flow 도메인 허용** 필요. (음악 S3 설정의 하위 항목)
- **G-Client-Merge**: Phase 3가 FLOW 서버 playlist를 만들면, 모바일 `gospel_pl`(state.js:26)은 로컬+외부 트랙 병합 로직 + 오프라인 다운로드(`gospel_hd`, state.js:25) 확장 필요 → **모바일 앱 리팩터**.
- **G-Consent**: 🔴A 브로커가 음악 사용자↔FLOW 구독자 연결 시, FLOW 기존 PIPL 동의 모델(`models/compliance.py:64-80` `ConsentRecord` + `models/orm.py:197-198, 241`) 재사용한 **교차시스템 동의+감사** 필요. F7(법무)와 직결.
  > ⚠ **인용 정정(2026-08-31)**: 본 항은 원래 `compose:306-308` 로 인용했으나 **해당 위치는 실재하지 않는다.** `docker-compose.prod.yml` 은 153줄이며 모든 compose 파일에 `consent`/`PIPL` 문자열이 0건이다. 실제 동의 모델은 **코드**에 있다(`models/compliance.py:64-80`). → §26.1 SC-7
- **G-Orch-Merge**: 🟠5 — 음악 컨테이너를 FLOW compose/caddy `gospel-network`에 편입 + healthcheck 추가(현재 음악은 healthcheck 없음).
- **G-Backup**: FLOW SQLite+`./data` + 음악 Postgres+S3의 **크로스 백업 정합성** 설계.

### 5.5 사용자 시나리오 크리티컬 패스 (필요충분조건)

시나리오 종단 달성에는 아래가 **모두** 필요(하나라도 빠지면 단절):
1. 🔴A 인증 조정(broker/FLOW_INTERNAL_TOKEN) — **선행 블록커**
2. 🔴2 안정 audio URL(S3) — 수신 측이 가리킬 URL 필요
3. 🔴1 FLOW 수신 측 endpoint — Pull 수용
4. F5 동기화 생명주기(갱신/삭제) — 수신 측과 함께 설계
5. F6 버저닝 — 수신 측 안정성

→ Phase 0(🔴2) + Phase 3(🔴1+F5) + Request B broker(🔴A) = **시나리오의 필요충분조건**.
나머지(🟠3/4/5, 🟡7/8, F1/2/3/4/7)는 "공존 품질(보안/확장/운영)"으로 분류 — 시나리오 달성과는 **별개**이나 공존 자체의 품질.

---

## 6. 아키텍처 제안 (핵심 3개 계약)

### 6.1 인증 브로커 (🔴A 해결)
- 음악에 내부 `POST /api/auth/flow-exchange`(공유 `FLOW_INTERNAL_TOKEN` 보호) 신설: FLOW `subscriber_id`를 받아 **단기·범위제한 JWT** + `user_link` 매핑 발급.
- FLOW 모바일은 토큰 메모리만 보관(state.js:34) → 음악 JWT를 메모리에만 유지, localStorage 미저장(XSS 방지 일관).
- 브로커 없이 음악 `admin_token`/JWT를 FLOW Streamlit 위젯에 넣지 말 것(🟡8 시크릿 경계 침범).

### 6.2 FLOW 수신 측 (Phase 3, 🔴1 해결)
- 신규 `GET /api/external-music`(FLOW HMAC 보호) — 브로커 토큰으로 음악 `GET /api/sync/flow` Pull 후, FLOW 서버 playlist(신규 테이블 또는 `/admin/media` `source` 필드)에 기록.
- 모바일은 `gospel_pl` + 서버 playlist 병합(G-Client-Merge).

### 6.3 동기화 계약 (RC-A/D 해결)
- `audio_cdn_url` 안정화(🔴2) + `SyncRecord`에 `updated_at`(있음, models.py:60) 활용한 **증분 Pull**.
- 삭제 전파: 음악 곡 삭제 시 `SyncRecord`를 `revoked`로 마킹(`if not s: continue` 대신 상태 반환, F5 수정) → FLOW가 제거.
- 재편집: `audio_url` 변경 시 `_ensure_cdn_url` 재소싱 트리거(sync_to_flow early-return 제거, F5 수정).
- 모든 교차 API는 `/api/v1` 버전화(F6).

---

## 7. 계층화 로드맵 (갭 매핑 + 의존성)

| Phase | 목표 | 대상 갭 | 의존 |
|---|---|---|---|
| **0** | 음악 저장소/시크릿 기반 | 🔴2(S3), F4(redis pass), F3(encrypt) | RC-B/C |
| **1** | 인프라 분리/라우팅 | 🟠3(포트 8001), 🟠5(Caddy편입+healthcheck), 용량 재산정 | RC-C |
| **2** | 교차 호출/명칭 | 🟠4(CORS 양방향), 🟡7(프리픽스), 🟡8(토큰 로그) | RC-B |
| **3** | **시나리오 종단** | 🔴1(수신측), F5(생명주기), F6(버저닝) + G-S3-CORS/G-Client-Merge | 🔴A(브로커 선행) |
| **4** | 신뢰/법무 | Request B broker, PIPL 동의(G-Consent), F7(자동 심의) | RC-A/B |
| **5** | 확장/성숙 | F1(redis 레이트리밋), F2(FLOW Postgres 이전), G-Orch-Merge/G-Backup | RC-C |

> Phase 3는 🔴A 브로커(Phase 4 또는 병렬)에 선행 종속. 즉 **시나리오는 최소 Phase 0+3+브로커**가 함께 가야 달성.

---

## 8. 운영·보안·컴플라이언스 연관

- **관측성**: FLOW 로그 마스킹 성숙 vs 음악 미마스킹 → 공유 집계 시 오염(§5.3-2). 음악에도 동일 마스킹 적용 또는 스트림 분리.
- **백업/DR**: FLOW SQLite+`./data` + 음악 Postgres+S3 크로스 정합(G-Backup). 복원 시 두 시스템 일관 필수.
- **오케스트레이션**: 음악 컨테이너를 FLOW `gospel-network`+healthcheck로 병합(G-Orch-Merge).
- **컴플라이언스**: 음악 UGC 음악은 중국 심의 대상 → F7 자동 심의 + FLOW PIPL 동의 재사용(G-Consent). 🔴A 브로커는 교차 PII 연계이므로 동의 필수.
- **용량**: ⚠ 종전 "8G 박스 잔여 ~1.5G → 포화"는 **근거 없는 추정으로 폐기**(§20.2 정정 2). → 대체 결론: 음악 컨테이너 **리소스 상한 정의 필수**(X2), FLOW SQLite+workers=2 단일 박스 한계(F2)는 여전히 유효, 호스트 스펙 미확정(T-5)으로 정량 결론 보류.

---

## 9. 리스크 레지스터 (우선순위)

| 우선 | 리스크 | 영향 | 완화 |
|---|---|---|---|
| P0 | 🔴A/🔴1/🔴2 미해결 → 시나리오 불가 | 비즈니스 목표 미달 | Phase 0+3+브로커 |
| P0 | RC-C 용량 포화 → OOM 연쇄 | 양측 다운 | 용량 재산정/분리 |
| P1 | F4 redis 무인증 → 횡적 이동 | 공유 호스트 침해 | requirepass+망분리 |
| P1 | F3 api_key 평문 → 유출 | 음악 엔진 키 탈취 | KMS/env 암호화 |
| P1 | F5 삭제/편집 무전파 → 데이터 부정합 | 사용자 혼선 | SyncRecord 상태머신 |
| P2 | F7 심의 스텁 → 불법 콘텐츠 노출 | 중국 컴플라이언스 위반 | 자동 ASR 심의 |
| P2 | F1 레이트리밋 무력 → 남용 | 쿼터 우회/비용폭탄 | redis 공유 |
| P2 | 🟡8 토큰 URL 로그 → 유출 | 관리자 토큰 탈취 | 로그 필터/cookie |

---

## 10. 미해결 결정사항 (사용자 확정 필요)

1. **수신 측 구축 범위**: 서버측 playlist 테이블 신규 vs 기존 `/admin/media` `source` 필드 재사용?
2. **음악 `STORAGE_*` S3/COS 확보 가능** 여부(🔴2 선행조건)?
3. **호스트**: ~~동일 8G 박스 수용 가능?~~ → **확정(T-2): 양 서비스 모두 홍콩 대용량 1대 공존.** 잔여 질문은 "호스트 분리"가 아니라 **음악 컨테이너 상한 설정**과 **홍콩 내부 서비스 분할 시점**(§22.1)이다.
4. **도메인 전략**: 서브도메인(`admin.flow`/`admin.music`, 권장) vs 경로 prefix?
5. **총 관리 포털**(링크만) 여부?
6. **FLOW 관리 UI 내 음악 동기화 위젯** 포함 여부(🟡8 경계 주의)?
7. **음악 `admin_token`**: MVP 단일 공유 허용 vs 관리자별 JWT 하드닝?
8. **PIPL 동의**: 교차시스템 연계 동의 흐름 누가 주관(법무)?

---

## 부록: 자가정정 이력 (기존 기획 오판)
- ~~"GET /api/sync/flow 인증/스코프 미확인(노출 위험)"~~ → 실제 `get_current_user`+`filter_by(user_id=)`(flow.py:87-88)로 **정상**. 노출 리스크 없음. (🔴A 불일치만 잔존)
- ~~"음악 migrations 없음"~~ → `backend/migrations/`(alembic, 6버전) 존재, CI `alembic upgrade head`+`check`. (Redis는 선언만·未접속은 맞음)

---

## 11. 심층 메커니즘·위협·설계 (Deep Dive)

> 본 장은 기획의 "왜/무엇"을 넘어 **요청 단위 추적·구체 위협·비용·동시성·구체 설계 diff**까지 내려간다. 전부 `file:line` 실증.

### 11.1 사용자 시나리오 단계별 HTTP 트레이스 + 정확한 실패점

| # | 단계 | 요청 | 현재 결과 | 실패 근인(갭) |
|---|---|---|---|---|
| 1 | 음악 생성 | `POST /api/songs/generate` | 200 + 백그라운드 `_process_song` | 정상 |
| 2 | "FLOW에 추가" 클릭 | `POST /api/songs/{id}/sync-flow` | 200 + `SyncRecord(synced)` 로컬 기록 | 🔴1: FLOW 호출 0건. 🔴A: 이 endpoint는 음악 JWT 필요(`get_current_user`)→FLOW 앱은 HMAC이라 호출 자체 불가 |
| 3 | FLOW 앱이 곡 가져오기 | `GET /api/sync/flow`(음악 JWT) | 200 + 본인 SyncRecord 목록 | 🔴A: FLOW 모바일은 JWT 비보유(토큰 메모리만, `state.js:34`); 🔴1: FLOW 수신 측 부재 |
| 4 | FLOW 재생 | 모바일 `gospel_pl`(localStorage) | 로컬 트랙만 | 🔴1: 서버 playlist 엔티티 없음(`state.js:26`) |
| 5 | 오디오 재생 | `<audio src=audio_cdn_url>` | 미설정 시 Suno 휘발 URL | 🔴2: `flow.py:27-29` |

→ **시나리오는 Step 2에서 🔴A로, Step 3~4에서 🔴1로 연쇄 단절**. 설정만으로는 Step 2조차 통과 못 함(인증 모델 불일치).

### 11.2 SSRF 심층 (신규 발견 — 🟡→실제 High)

- `flow.py:32` `httpx.AsyncClient().get(song.audio_url)` 는 **서버 측에서 임의 URL fetch**. `song.audio_url`은 Suno 응답으로 채워짐(`songs.py:146`), 또는 후처리로 재작성(`_process_post:413-414`, `_apply_postprocess:153-154`).
- 검증/허용목록 **없음**. 공존 시 블라스트 레이디어스: 음악 백엔드가 같은 호스트에서 FLOW 내부(`backend:8000`, `qdrant:6333`, `postgres`, `redis`, 메타데이터 `169.254.169.254`)를 fetch 가능.
- FLOW는 이미 `security/ssrf.py`(예: `ssrf.py:29` `10.0.0.0/8` 블록) 보유 → **음악으로 재사용**. 음악에는 SSRF 가드 0건.
- 추가 유입점: `upload_audio`(`songs.py:343`)가 사용자 `audio_url`을 그대로 `provider.upload` 에 전달.
- **완화**: `audio_url` 스킴+호스트 허용목록(Suno 도메인 + 구성 S3) + 사설 IP 차단 + FLOW `ssrf.py` 유틸 재사용. 동기화는 신뢰 출처(S3/CDN)만 fetch하도록 고정.

### 11.3 동시성 / 멱등 분석 (신규)

- `sync_to_flow`(flow.py:57)는 `select_for_update`/락 없음. 동일 곡에 동시 2회 POST 시: 두 요청 모두 `rec and rec.status=="synced"`(None) 통과 → 둘 다 `_ensure_cdn_url` 호출 → **S3 이중 PUT**(키 동일→덮어쓰기, 결과적 멱등이나 트래픽 낭비) + `_mark_synced` get-or-create(중복 row 없음).
- 영향: 결과 정합성은 유지되나, 고동시성 시 **S3 egress 이중 과금**(§11.5 비용과 연결). 권고: `select_for_update` 또는 `(user_id, song_id)` unique 제약 + UPSERT.

### 11.4 동기화 생명주기 구체 실증 (F5 재확인 + 정확 라인)

- **삭제 맹점**: `delete_song`(songs.py:355-364)은 Song만 삭제, `SyncRecord` 미변경 → `GET /api/sync/flow`의 `if not s: continue`(flow.py:91-93)가 orphan를 **침묵 드롭**. FLOW는 삭제를 결코 인지 못함.
- **편집 맹점**: `crop/speed/whole_song`(`_process_post:413-414`)가 sync 이후 `audio_url` 재작성해도, `sync_to_flow:70-72`(이미 synced면 early return) + `_ensure_cdn_url:25-26`(`audio_cdn_url` 있으면 early return) → **FLOW는 stale URL 유지**.
- **수정 설계**: `SyncRecord.status` 상태머신(`pending|synced|revoked`) + `delete_song`가 연관 `SyncRecord→revoked` 마킹 + `sync_to_flow`가 `audio_url` 변경 시 강제 재소싱. FLOW 수신 측은 `revoked` 수신 시 로컬/서버 playlist에서 제거.

### 11.5 비용 귀속 / 비즈니스 모델 (신규 — 미해결)

- `cost_cny`는 생성 시 음악 사용자 할당(`songs.py:80`, `models.py:46`). FLOW 사용자는 무료 재생.
- 진짜 미해결 비용 = **S3 egress**: `audio_cdn_url`이 음악 S3(🔴2 해결 시) → FLOW 사용자 재생마다 **음악 플랫폼 S3 트래픽 과금**. 대량 재생 시 음악 플랫폼 부담 가중.
- 결정 필요(**G-CostAttribution**): ① 음악 플랫폼이 egress 부담 허용? ② FLOW가 자체 CDN/캐시로 흡수? ③ 동기화 시점에 1회 복사해 FLOW 저장소(`data/music`)로 이관(음악 egress 최소화, 단 저장소 정책 변경)?

### 11.6 공개 탐색 노출 (F7 구체 실증 — 컴플라이언스 High)

- `enable_moderation=False`(기본) + `enable_audio_moderation=False`(기본) → `_process_song`이 `result.status=="completed" and moderation_status=="pending"`이면 **자동 `approved`**(songs.py:167-168).
- `explore_songs`(songs.py:281-296)는 `is_public and moderation_status=="approved" and completed` 필터로 **익명 접근 가능**.
- 결론: **기본 설정에서 모든 생성 UGC 음악이 심의(인간/자동) 없이 익명 공개**. 중국 컴플라이언스 중대 노출. → F7 자동 심의는 "있으면 좋은 것"이 아니라 **공존 필수 선행**.

### 11.7 구체 설계 diff

**A. Caddyfile 확장 (🟠5) — FLOW Caddyfile 뒤에 블록 추가**
```
# 음악 플랫폼 (FLOW Caddy 단일 진입 확장)
admin.music.example.com {
  tls internal  # 또는 Let's Encrypt
  reverse_proxy music-frontend:3000 { header_up Host {host} header_up X-Forwarded-Proto {scheme} }
}
api.music.example.com {
  tls internal
  reverse_proxy music-backend:8001 {
    header_up Host {host}
    header_up X-Real-IP {remote_host}
    header_up X-Forwarded-Proto {scheme}
  }
}
music.example.com {
  tls internal
  reverse_proxy music-frontend:3000
}
```
(backend를 `:8001`로 리바인드해 FLOW `:8000`과 충돌 회피; music-backend/music-frontend는 FLOW `gospel-network`에 편입)

**B. 통합 compose 스니펫 (🟠5/G-Orch-Merge)**
```
  music-backend:
    build: { context: ../ai-music-platform/backend, dockerfile: Dockerfile }
    env_file: [../ai-music-platform/.env.production]
    environment: [PORT=8001, CORS_ORIGINS=https://admin.music.example.com, ADMIN_TOKEN=***]
    networks: [gospel-network]
    healthcheck: { test: ["CMD","curl","-f","http://localhost:8001/health"], ... }
  music-frontend:
    build: { context: ../ai-music-platform/frontend }
    environment: [NEXT_PUBLIC_API_BASE=https://api.music.example.com]
    networks: [gospel-network]
    healthcheck: { test: ["CMD","wget","-q","-O","-","http://localhost:3000"], ... }
  music-postgres / music-redis: 기존 compose 에서 이동 + redis requirepass 추가(F4)
```

**C. 브로커 프로토콜 상세 (🔴A 해결, §6.1 확장)**
```
POST /api/auth/flow-exchange
  Header: X-Flow-Internal-Token: <FLOW_INTERNAL_TOKEN>   # 공유시크릿, 상수시간비교
  Body:   { flow_subscriber_id: int, signed_nonce: str }  # FLOW가 AUTH_SECRET으로 서명
응답:   { music_jwt: str, scope: "sync:read", exp: 3600,
          user_link_id: int }   # song_user_id ↔ flow_subscriber_id 매핑
```
- FLOW 모바일은 `music_jwt`를 **메모리만** 보관(`state.js:34` 원칙 준수, localStorage 금지).
- 음악 `GET /api/sync/flow` 호출 시 `Bearer <music_jwt>`.
- 해제: `user_link` 삭제 → 발급된 jwt 즉시 무효(블랙리스트 또는 jti 테이블).

**D. FLOW 수신 측 스키마 (🔴1 해결, §6.2 확장)**
```python
class ExternalMusic(Base):
    __tablename__ = "external_music"
    id = Column(Integer, primary_key=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), index=True)
    source_song_id = Column(Integer)  # 음악 Song.id
    audio_cdn_url = Column(Text)
    title = Column(String(255))
    cover_url = Column(Text)
    lyric = Column(Text)
    synced_at = Column(DateTime)
    status = Column(String(16), default="active")  # active|revoked (F5)
```
- 마이그레이션: `backend/migrations/`(FLOW는 `migrations/` 네이밍, alembic) 신규 버전.
- 모바일: `gospel_pl`(state.js:26) + `external_music` 병합 노출(G-Client-Merge); 오프라인(`gospel_hd`, state.js:25)은 외부 트랙은 캐시 복사본으로 처리.

### 11.8 용량 수치 (RC-C 구체화) — ⚠ 제3부 §20.2 정정 2로 전면 수정

> **정정(2026-08-30 3차 라운드)**: 아래 "8G/4cpu Lighthouse"는 **코드에 존재하지 않는 추정치**였다. 재검증 결과 폐기하고, 실측 가능한 값만 남긴다.

**실측값 (컨테이너 상한 — 호스트 크기가 아님)**

| 서비스 | 상한 | 근거 |
|---|---|---|
| qdrant | 2G / 1cpu | `docker-compose.prod.yml:40-42`(mem_limit 2g, cpus 1.0) |
| backend | 3G / 2cpu | `:73-75` |
| ui | 1.5G / 1cpu | `:110-112` |
| caddy | 256M / 0.5cpu | `:138-140` |
| **FLOW 합계** | **≈ 6.75G / 4.5cpu** | 위 합산 |

**추정/미확정 (결론 근거로 사용 금지)**

- 음악 추가 필요량(backend~1G + frontend~512M + postgres~1G + redis~256M ≈ 2.8G): **근거 없는 추정 → 보류**. 음악측에는 컨테이너 리소스 상한 정의 자체가 없다(`docker-compose.yml`은 postgres/redis만, 앱 서비스 없음 = X2) → 실측 불가.
- 호스트 여유: **홍콩 대용량 서버 스펙 미확정(T-5) → 계산 불가.**

**수정된 결론**

1. ❌ 폐기: "잔여 1.5G < 필요 2.8G → 동일 박스 수용 불가 / 호스트 분리 필요".
2 ✅ 현행 결론: 양 서비스는 **홍콩 대용량 1대 공존(T-2)** 이며, 위 상한은 **서비스별 cgroup 상한(과다 점유 방지용)** 이지 호스트 한계가 아니다. 따라서 **공존의 제약은 용량이 아니라 보안(블라스트 레이디어스 §5.3)·가용성(SPOF §12.6)·데이터 평면(PII 한국 §21)이다.**
3. 남는 실제 작업: (a) 음악 컨테이너에도 **동일한 mem_limit/cpus 상한 정의**(X2 해결 시) — 상한이 없으면 음악이 홍콩 박스 자원을 무제한 점유해 FLOW를 OOMKill 시킬 수 있다(`docker-compose.prod.yml:39` 주석이 FLOW는 "OOMKilled 방지" 목적임을 명시). (b) FLOW의 SQLite+workers=2 단일 박스 한계(F2)는 **여전히 유효**(§5.3-4). (c) 음악 실측 후 본 절 수치 복원.

### 11.9 장애 모드 매트릭스 (운영 연관)

| 장애 | 영향 | 검출 | 완화 |
|---|---|---|---|
| 음악 플랫폼 다운 | FLOW 동기화 불가(수신 측 Pull 실패) | healthcheck | FLOW는 로컬 보유 곡은 재생 유지, 복구 시 재동기화 |
| S3 URL 만료/삭제 | FLOW 곡 재생 404 | audio_cdn_url 프로브 | G-CostAttribution ③ 복사 이관 권고 |
| 사용자 FLOW 계정 삭제 | media_asset 고아행 (§17 정정: ExternalMusic 폐기) | FK 해제 후 정합 배치(Y1) | 삭제 원장 기반 팬아웃(Y5, §21.4) |
| 연동 해제(unlink) | music_jwt 잔존 | 브로커 revoke | jti 블랙리스트 ⚠ **미구축** — FLOW 백엔드 전역 `jti` 구현 0건(2026-08-31 grep). 완화책은 **설계 제안**이며 구현 전제. → §26.1 SC-10 |
| sync 이후 곡 rejected(심의) | FLOW에 부적절 곡 잔존 | F5 상태머신 | music이 rejected 시 SyncRecord→revoked |
| 네트워크 분할(두 호스트) | Pull 타임아웃 | 경계 타임아웃 | 지수 백오프 재시도 |

---

## 12. 미래지향 분석 (Future-Oriented, 1~3년 축)

> ⚠️ 표기: **[현재]** = 검증된 코드 사실. **[전망]** = 미래 리스크/권고(추측 아님, 아키텍처 필연/규제 드리프트 기반).
> 현재 근거: 음악 `MusicProvider` 추상화(`config.py:29` "비즈니스 코드는 MusicProvider 인터페이스에만 의존"), `pricing_tiers`/`cost_cny`(models.py:46, admin.py:264), Song 모델에 AIGC 라벨/워터마크/출처 필드 **없음**(models.py:40-60), FLOW 타깃=중국 본토+글로벌 한인 디아스포라(메모리), FLOW 홍콩 호스팅(메모리).

### 12.1 성장 단계별 무엇이 깨지는가 (시간축)

| 시점 | 규모 | 깨지는 것 | 근인(현재 갭) |
|---|---|---|---|
| ~6개월 | 현재(동일 박스) | 용량 포화, 인증 블로커 | RC-C(§11.8), 🔴A |
| ~1년 | 10x 사용자 | SQLite 쓰기잠금(F2), 레이트리밋 무력(F1), S3 egress 폭증(§11.5) | F2/F1/§11.5 |
| ~2년 | 중국+디아스포라 본격 | 데이터 레지던시/국경 간 이전(PIPL), AIGC 라벨링 규제, 다지역 지연 | §12.2, §12.3 |
| ~3년 | 제품군 통합 또는 분리 | "두 앱" 경계 모호 → 식별 연합/완전 분리 중 하나로 수렴 필요 | §12.3 |

### 12.2 지리 / 컴플라이언스 진화 [전망+현재]

> ⚠ **제3부 정정(§20.2 정정 4 / §21)**: 본 절의 "PII가 홍콩 DB에 있다"는 전제는 **사용자 확정(T-3: 개인정보는 한국)** 으로 바뀌었다. 아래 내용은 **역사적 분석**으로만 읽고, 최신 설계는 **§21(분할 데이터 평면)** 을 따른다. 특히 "교차 PII 연계"는 한국 금고 내부로 국한되며, 홍콩에는 **가명 데이터만** 남는다(§21.3).

- **데이터 레지던시(PIPL)**: [현재] FLOW는 중국 본토 사용자 + 홍콩 호스팅(메모리). [전망] 음악 플랫폼이 같은 중국 타깃이면, 🔴A 브로커가 맺는 **음악 사용자↔FLOW 구독자 교차 PII 연계**는 국경 간 이전 대상. 음악 데이터가 HK/해외에 있으면 중국 본토 사용자 데이터가 국외 이전됨 → 별도 동의+보안 평가(PIPL 제38조) 필요. → 공존 설계는 **데이터 소재지 명시 + 교차 이전 차단/동의**를 브로커에 내장해야 함(G-Consent 확장).
- **AIGC 라벨링/워터마크 규제 드리프트**: [현재] Song 모델에 라벨/워터마크/출처 필드 없음(models.py:40-60), F7 심의는 스텁. [전망] 중국 AIGC 규정은 AI 생성물 명시적 표시+워터마크를 의무화 추세. 동기화 계약(§6.3)은 **`audio_cdn_url`만 넘기지 말고 `label`/`watermark`/프로바이더/모델버전(song.model_version 있음, models.py:43)을 함께 전달**해야 FLOW가 재생 UI에 "AI 생성 음악" 표시 가능. 이 필드가 없으면 향후 규제 대응 시 FLOW 전체가 미표시로 적발.
- **심의 책임 소재**: [전망] 음악이 FLOW로 곡을 밀어내면, 콘텐츠 책임은 생성자(음악)인가 유통자(FLOW)인가. §11.6 노출(기본 익명 공개)은 규제 강화 시 양측 공동 리스크. → F7 자동 심의는 공존 전제조건(§5.5).

### 12.3 아키텍처 진화 경로 — "공존은 정지간접(Stopgap)"

- **핵심 원칙**: 지금의 동일 박스 공존은 **비용 최소화를 위한 임시 구조**이지 목표 아키텍처 아님. 명시적 **exit/extract 경로**를 설계에 넣어야 함.
- **계단(단계별 추출)**:
  1. Now: 동일 호스트, Caddy 공유(🟠5), 별도 프로세스.
  2. Phase X: 호스트 분리(§11.8 용량 포화 트리거) → 음악 전용 호스트, Caddy만 남김(또는 각자 Caddy).
  3. Phase Y: 음악 백엔드를 **독립 서비스**로 추출(이미 `MusicProvider` 추상화로 생성 엔진 교체는 설계됨, config.py:29) → FLOW는 단일 콘슈머.
  4. Phase Z: **식별 연합(Identity Federation)** — 🔴A 브로커가 진화해 OIDC/SSO로, "두 앱"이 "하나의 스위트"로 수렴(또는 완전 분리 매각).
- [현재] FLOW는 HMAC `AUTH_SECRET`+자체 `Subscriber`, 음악은 JWT. [전망] 미래 식별 표준은 OIDC. 브로커(§11.7-C)를 **맞춤 JWT가 아닌 OIDC OP 경량 구현**으로 설계하면 Phase Z로의 이행 비용이 줄어듦.

### 12.4 계약 진화 — 버저닝을 넘어

- [현재] 양측 비버저닝(F6). [전망] 동기화 계약은 **컨슈머 주도 계약(Consumer-Driven Contract)** + 스키마 레지스트리로 진화 권고. FLOW(콘슈머)가 기대 스키마를 선언, 음악(프로듀서) 변경 시 CI에서 계약 깨짐 탐지. → F6 단순 버저닝(`/api/v1`)은 시작일 뿐, 장기는 계약 테스트 인프라.
- [현재] 음악 공급자 추상화 존재(config.py:29, `resolve_engine`). [전망] Suno→다른 생성 모델 교체 시 `engine_name`(models.py:43)·`cost_cny` 귀속은 이미 설계됨. FLOW 수신 측은 `engine_name`을 메타로 보관해 공급자 교체 투명성 확보(§11.7-D `ExternalMusic`에 `source_engine` 필드 추가 권고).

### 12.5 FinOps / 관측성 진화

- [현재] `cost_cny`(models.py:46)·`pricing_tiers`(admin.py:264)·`resolve_cost_cny`(songs.py:80)로 비용 귀속 기반 있음. [전망] §11.5 S3 egress가 스케일에서 지배적 비용이 되면, **다중테넌트 비용 추적 + CDN 오프로드** 필요. FLOW 사용자 재생은 음악 S3가 아닌 **공용 CDN/캐시**로 흡수(§11.5 안 ②) → egress를 일회 동기화 트래픽으로 고정. 관측성은 양측 로그 마스킹 통합(§5.3-2) 후 **비용/재생 메트릭 대시보드** 필수.

### 12.6 가용성 / DR 진화

- [현재] ~~단일 Lighthouse 박스~~ → **홍콩 대용량 단일 박스(T-2), SPOF.** ⚠ "Lighthouse"는 근거 없는 추정이었다(§20.2 정정 2). SPOF 결론 자체는 유효하며, §22.1에서 **한국 PII 금고 SPOF(FU9)** 가 추가로 식별되었다. [전망] 10x~100x에서 단일 박스는 장애 블래스터. → 다중 AZ, 크로스 리전 백업(FLOW SQLite+`./data` ↔ 음악 Postgres+S3 정합, G-Backup). 동기화는 **비동기 이벤트**(Pull 폴링이 아닌 webhook/큐)로 진화해 네트워크 분할(§11.9) 시 내구성 확보.

### 12.7 기술 부채 이자 (지연 비용 정량)

- 미해결 갭은 **복리**로 누적. 예:
  - 🔴A 브로커를 지금 안 만들면 → FLOW 모바일이 음악 JWT를 localStorage에라도 박제하려는 유혹(🟡8 위반) → 추후 시크릿 유출 사고 시 전면 재설계.
  - F2(SQLite)를 10x까지 방치하면 → 쓰기잠금 장애로 프로덕션 다운 → Postgres 이전은 다운타임 동반 대역수술.
  - F6 미버저닝 → 1년 후 음악 백엔드 리팩터 시 FLOW 하드코딩 연동 동시 깨짐(§5.1-RC-D).
- **권고**: Phase 0+3+브로커(§5.5 필요충분조건)를 **제품 출시 전** 선행. 늦을수록 이자 가중.

### 12.8 결정 프레임워크 — "공존하되 명시적 exit 경로"

```
지금(Stopgap): 동일 호스트 + Caddy 공유 + 별도 프로세스 + 별도 시크릿
   └─ 트리거: 용량 포화(§11.8) OR 음악 트래픽 급증
        → 호스트 분리(Phase X)
             └─ 트리거: 음악 독립 배포 필요 OR 조직 분리
                  → 음악 백엔드 독립 서비스 추출(Phase Y, MusicProvider 추상화 활용)
                       └─ 트리거: 제품군 통합 의사결정
                            → 식별 연합 OIDC(Phase Z) OR 완전 분리
```
- 모든 단계에서 **시크릿은 절대 공유**(§3), **CORS는 화이트리스트**(🟠4), **교차 PII는 동의+레지던시**(§12.2) 유지.

### 12.9 미래 리스크 레지스터 (신규)

| ID | 미래 리스크 | 시점 | 완화(현재 연결) |
|---|---|---|---|
| FU1 | 중국 본토 사용자 데이터 국외 이전(PIPL) | ~2년 | 브로커에 레지던시+동의 내장(§12.2) |
| FU2 | AIGC 라벨/워터마크 의무화 미대응 적발 | ~1~2년 | Song에 label/watermark 필드+동기화 계약 전달(§12.2) |
| FU3 | SQLite 쓰기잠금 프로덕션 다운 | ~1년 | F2 Postgres 이전(다운타임 리허설) |
| FU4 | S3 egress 폭증 비용 폭탄 | ~1년 | §11.5 ② CDN 오프로드 + FinOps |
| FU5 | 단일 박스 SPOF 장애 | ~1년 | §12.6 다중 AZ/크로스 리전 |
| FU6 | 음악 백엔드 리팩터 시 FLOW 연동 동시 깨짐 | ~1~2년 | F6 버저닝 + 계약 테스트(§12.4) |
| FU7 | 식별 표준(OIDC) 전환 시 브로커 폐기 | ~3년 | §11.7-C를 OIDC 경량 설계(§12.3) |
| FU8 | 규제 강화 시 익명 공개(§11.6) 양측 공동 적발 | ~1년 | F7 자동 심의 선행(§5.5) |

---
---

# 제2부 · 획기적 재설계 (2026-08-30 심층 재검증 라운드)

> 본 제2부는 "더 획기적인 해결방안 / 기존 DB에 서비스 구분 / 남은 연관성"을 위해 **양측 DB 계층·마이그레이션·미디어 파이프라인을 추가 직독**한 결과다.
> 결론부터: **제1부 §6.2/§11.7-D의 신규 `ExternalMusic` 테이블 제안은 재발명이었고 철회한다**(§17).
> FLOW에는 이미 "오디오 수신 → 예약 발행 → 푸시 알림 → 카탈로그 → Range 스트리밍"의 **완결된 미디어 커널**이 존재한다.

---

## 13. 획기적 해결방안 #1 — 미디어 커널 재사용 (Plan Z / Media-Kernel Reuse)

### 13.1 결정적 신규 발견 — FLOW는 이미 종단 오디오 파이프라인을 갖고 있다

이번 라운드에서 처음 실증한 사실이며, 기획 전체의 난이도를 바꾼다.

| 이미 존재하는 능력 | 근거(`file:line`) |
|---|---|
| 관리자 오디오 **업로드 엔드포인트**(multipart) | `backend/app/api/media.py:61-62` `@admin_router.post("")` `admin_upload_media` |
| 업로드 폼 필드: file/title/category/artist/description/scripture_refs/order_index/active/**publish_at**/**notify_mode** | `media.py:63-72` |
| 업로드 **인증 게이트** | `media.py:80` `_check_admin(authorization)` → `app/api/auth.py:216-241` (Bearer 또는 X-API-Key, 상수시간 대조) |
| 파일 **바이트를 로컬 디스크에 소유** | `app/services/media_service.py:21` `MEDIA_DIR = root/data/media`, `:148` `storage_path=str(target)` |
| 확장자 화이트리스트(.mp3/.wav/.m4a/.ogg/.aac/.flac) | `media_service.py:24` `_ALLOWED_EXTS` |
| **예약 발행** 스케줄러(도래 시 `active=True` 플립) | `app/services/media_publisher.py:33`, 필터 `:39-40` |
| **푸시 알림 브로드캐스트** + 중복 발송 방지 + 실패 시 재시도 | `media_publisher.py:54`, 필터 `:65-67`, `notified_at` 기록 `:84-91` |
| 스케줄러 **앱 수명주기 등록** | `app/main.py:199-201`(start), `:234-236`(stop), `:207-209`(`ensure_media_schema`) |
| 모바일 **공개 카탈로그** + 경량 버전 API(변경 감지) | `media.py:157-158` `GET /mobile/media`, `:172-173` `/version`, 서비스 `media_service.py:195` `list_public` / `:301` `media_version` |
| **Range 스트리밍** | `media.py:178-179` `GET /mobile/media/{asset_id}/stream` → `media_service.py:226` `get_file` |
| 라우터 등록 완료 | `main.py:364-365` `include_router(media.admin_router / media.public_router)` |
| 테이블 스키마(제목/카테고리/아티스트/길이/정렬/활성/예약/알림) | `app/models/orm.py:597` `media_asset` + 동 클래스 `publish_at`/`notify_at`/`notified_at` |

→ 즉 **"FLOW 앱 음악단에 곡을 추가"라는 사용자 시나리오의 수신 측은 이미 100% 구현되어 있다.** 없던 것은 "음악 플랫폼이 그 문을 두드리는 행위" 뿐이었다.

### 13.2 Plan Z 정의 (한 문장)

> 음악 플랫폼은 곡 생성이 완료되고 **심의를 통과한 시점**에, 오디오 **바이트 자체**를 FLOW의 기존 `POST /admin/media`로 multipart 푸시하고, 반환된 `asset_id`를 `sync_records.flow_song_id`에 저장한다.

- 신규 FLOW 엔드포인트: **0개**
- 신규 FLOW 테이블: **0개** (컬럼 2개만 추가, §13.5)
- 브로커/JWT 교환: **MVP에서 불필요**

### 13.3 Plan Z가 해소하는 기존 갭 (대조표)

| 기존 갭(제1부) | 기존 해법(제1부) | Plan Z | 근거 |
|---|---|---|---|
| 🔴1 FLOW 수신 측 부재 | 신규 `ExternalMusic` 테이블+신규 수신 API 개발 | **소멸** — 기존 `/admin/media` 재사용 | `media.py:61-62` |
| 🔴2 오디오 URL 휘발(Suno 만료) | S3 확보 + CDN 재업로드 파이프라인 | **구조적 소멸** — FLOW가 바이트를 소유하므로 만료 개념 자체가 없음 | `media_service.py:148` |
| 🔴A 인증 모델 불일치(JWT 가정 vs HMAC) | 인증 브로커(`/api/auth/flow-exchange`) 신설 | **MVP 우회** — 서버-서버 관리자 토큰 1개로 성립 | `media.py:80`, `auth.py:216-241` |
| 🟠5 S3/오브젝트 스토리지 확보 결정 | 필수 선결 | **보류 가능** — 같은 호스트라 바이트 전송이 localhost, egress 0 | `docker-compose.prod.yml` 단일 박스 |
| F5 동기화 생명주기(삭제 전파) | 신규 설계 | 부분 획득 — FLOW측 삭제는 `media_service.py:255` `delete_media` 재사용 | 동일 |
| 알림/푸시 미설계 | 신규 설계 필요 | **무료 상속** — `notify_mode=immediate/batch/none` | `media.py:72`, `media_publisher.py:54` |
| 예약 발행(콘텐츠 운영) | 미고려 | **무료 상속** | `media_publisher.py:33` |

**핵심**: Plan Z는 "새로 만들 것을 줄이는 설계"가 아니라 **"이미 검증된 운영 경로를 재사용해 리스크를 줄이는 설계"** 다. 예약 발행·푸시·중복방지·재시도는 이미 운영 중인 코드다.

### 13.4 Plan Z 종단 트레이스 (8단계 · 실패점 명시)

```
① 사용자: 음악 웹에서 생성 요청           → POST /api/songs (JWT HS256, jwt_secret)
② 음악: 공급자 폴링 → status=completed     → songs.audio_url (Suno 휘발 URL)
③ 음악: 심의 게이트                        → moderation_status == 'approved' 확인 (models.py:38)
   ⚠ 실패점 A: enable_moderation=False 기본(songs.py:167-168) → 무심의 통과
④ 음악: 오디오 바이트 확보                 → audio_url GET (⚠ 실패점 B: SSRF 미검증, routers/flow.py:32)
⑤ 음악: FLOW 푸시                          → POST http://backend:8000/admin/media
        헤더 Authorization: Bearer <FLOW_ADMIN_API_KEY>
        폼   file, title, category=ai_music, artist, notify_mode, publish_at
   ⚠ 실패점 C: ADMIN_API_KEY=='change-me' 면 503 (auth.py:229-233)
   ⚠ 실패점 D: category 화이트리스트 worship|sermon 뿐 → 400 (media_service.py:95-96)
⑥ FLOW: 바이트 저장 + 레코드 생성          → data/media/{category}/, media_asset (media_service.py:74·148)
⑦ FLOW: 예약/알림 스케줄러                 → active 플립(publisher:33) + Web Push(publisher:54)
⑧ 모바일: 카탈로그 + 스트리밍              → GET /mobile/media, /{id}/stream (media.py:157·178)
   ⚠ 실패점 E: 이 두 경로는 인증이 없음 → 유입 즉시 전 인터넷 공개
```

기존 제1부 트레이스(§11.1)는 ⑤~⑧이 "미구현"이었다. Plan Z는 ⑤의 호출 1건과 ③④의 게이트만 남긴다.

### 13.5 Plan Z 필수 변경 (최소 diff · 3건)

**(1) 카테고리 확장** — 현재 `worship|sermon` 만 허용하므로 AI 음악이 찬양으로 오분류되거나 400을 맞는다.

```python
# backend/app/services/media_service.py:95-96 (현재)
if category not in ("worship", "sermon"):
    raise ValueError("category 는 'worship' 또는 'sermon' 이어야 합니다.")
# → 변경
if category not in ("worship", "sermon", "ai_music"):
    raise ValueError("category 는 'worship' | 'sermon' | 'ai_music' 이어야 합니다.")
```
- 연동 확인: `media_service.py:195` `list_public(category=...)`는 화이트리스트 검사가 없어 신규 카테고리를 그대로 서빙한다(추가 변경 불필요).
- 모바일 UI에는 탭/필터 추가가 필요(클라이언트 작업, §15 X7).

**(2) 멱등키 컬럼 2개 추가** — 재푸시·재시도 시 중복 곡을 막는다(§11.3 동시성 연결).

```python
# backend/app/models/orm.py  MediaAsset(=media_asset, :597) 내부
source_service: Mapped[str] = mapped_column(String(20), default="flow", index=True)
# 'flow' (관리자 업로드) | 'music' (ai-music-platform 유입)
external_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
# 'music:song:{song_id}' — 재푸시 멱등키
```
- **이것이 사용자가 제안한 "서비스 항목으로 구분"의 정확한 착지점**이다(§14.5 참조).
- Alembic 리비전 1개로 추가(FLOW `migrations/env.py:80-84`가 `render_as_batch=True`이므로 SQLite에서도 안전).

**(3) 심의·노출 게이트** — 유입된 AI 음악은 유입 즉시 무인증 공개된다.

```
푸시 조건: songs.moderation_status == 'approved' (models.py:38)
FLOW 수신: active=False + publish_at 지정으로 "보류 후 발행"을 기본값으로 (media.py:69·71)
```

### 13.6 Plan Z의 한계 (정직한 경계)

| 한계 | 설명 | 대응 |
|---|---|---|
| 개인 소유권 없음 | `media_asset`은 **전역 카탈로그**(사용자별 소유 컬럼 없음, `orm.py:597`) → "내가 만든 곡만 내 앱에" 시나리오 불가 | 개인화가 요구되면 그때 브로커(§6.1)+소유자 컬럼 도입 |
| 관리자 신뢰 경계 | 음악 서비스가 FLOW 관리자 권한 토큰을 보유 → 침해 시 FLOW 카탈로그 전체 조작 가능 | 전용 스코프 토큰 또는 `/admin/media` 한정 프록시(§15 X5) |
| 대용량 스트리밍 | `FileResponse` 로컬 디스크 서빙(`media.py:185`) → 트래픽 급증 시 앱 서버 대역폭 소모 | §11.8 용량 임계 도달 시 CDN 오프로드 |
| 양방향 아님 | FLOW → 음악 방향 상태 회신 경로 없음 | `flow_song_id` 저장으로 추적(§13.2), 회신은 Phase 3 |

---

## 14. 획기적 해결방안 #2 — 사용자 제안 정식 평가: 공유 커널 DB

> 사용자 제안: *"기존 데이터베이스에 두 서비스 항목을 추가하여 구분하는 방법도 생각해봤어?"*
> 정식 명칭을 부여한다: **Shared-Kernel Database (공유 커널 DB)**. 아래는 실증 기반 정면 평가다.

### 14.1 실증 팩트 — 충돌 스캔 결과 (놀랍게도 유리하다)

| 검증 항목 | 결과 | 근거 |
|---|---|---|
| **테이블명 충돌** | **0건** — FLOW는 단수 snake, 음악은 복수형 | FLOW: `subscriber`(orm.py:184), `document`(:89), `media_asset`(:597), `content_link`(:560), `payment`(:276), `audit_log`(:392), `workspace`(compliance.py:90), `workspace_membership`(:101), `consent_record`(:48), `wallet_reservation`(:72), `ccp_rate_card`(ccp.py:88) ↔ 음악: `users`(models.py:8), `songs`(:17), `sync_records`(:51), `engine_configs`(:69), `routing_rules`, `pricing_tiers` |
| FLOW의 **Postgres 전환 난이도** | **코드 변경 0** — 환경변수 1줄 | `app/db.py:41-49` `_resolve_db_url()`(DATABASE_URL 우선), `:52`, `app/config.py:259` `database_url: str \| None = None` |
| 비-SQLite 풀 설정 준비 여부 | **이미 분기 구현** | `db.py:66-79` `_engine_kwargs(is_sqlite)` — sqlite면 `check_same_thread`, 아니면 순수 QueuePool |
| SQLite 전용 최적화의 조건부 적용 | WAL/busy_timeout PRAGMA가 sqlite 조건문 내부 | `db.py:98-118` |
| 음악측 DB URL | 환경변수 기반(기본 sqlite) | `backend/app/config.py:9` `database_url = "sqlite:///./app.db"`, `app/db.py` `create_engine(settings.database_url)` |
| 음악측 운영 Postgres 존재 | postgres:16 컨테이너 실재 | `docker-compose.yml:4-14` |
| FLOW의 **서비스/테넌트 구분 축 기존 보유** | 있음 → 사용자 아이디어와 정확히 같은 사상 | `compliance.py:94` `workspace_id`(WorkspaceScopedMixin), `:108` `class Workspace`, `:119` `class WorkspaceMembership`, 마이그레이션 `4d7d02caeed2_true_baseline_all_tables.py:486,497`(`workspace`·`workspace_membership` 테이블 생성 — 구 `m1_compliance_security.py` 가 단일 베이스라인으로 통합됨) |

→ 즉 **"기존 DB에 두 서비스를 담는다"는 발상은 기술적으로 성립**한다. 이름 충돌이 없고, FLOW는 Postgres로 갈 준비가 되어 있고, 이미 테넌트 구분 패턴을 갖고 있다.

### 14.2 그러나 — 치명 함정 2건 (이번 라운드 신규 발견, 둘 다 실증)

**(T1) Alembic 버전 테이블 충돌 — 단일 스키마 공유 시 즉시 파손**

- FLOW `migrations/env.py:57-63`(offline) / `:80-84`(online): `context.configure(...)`에 **`version_table` 인자 없음** → 기본값 `alembic_version`.
- 음악 `migrations/env.py:39-44` / `:58-60`: 동일하게 **`version_table` 없음** → 기본값 `alembic_version`.
- 결과: 같은 DB·같은 스키마에 두 프로젝트가 마이그레이션하면 **하나의 `alembic_version` 행을 서로 덮어쓴다.** FLOW가 `alembic upgrade head`를 실행하면 그 안에 든 음악 리비전 ID를 **자기 스크립트 트리에서 찾지 못해 실패**(또는 반대로 음악이 FLOW head를 미지 리비전으로 인식)한다.

**(T2) autogenerate 상호 DROP — 데이터 파괴 시나리오**

- 양측 `env.py`에 **`include_object` / `include_name` 필터가 전무**(grep 결과 0건)하고, `target_metadata = Base.metadata`(FLOW `env.py:44`, 음악 `env.py:34`)로 **자기 모델만** 등록된다.
- 따라서 공유 스키마에서 FLOW가 `alembic revision --autogenerate`를 실행하면, Alembic은 DB에 있는 `songs`·`users`·`sync_records`를 "메타데이터에 없는 잉여 테이블"로 판정해 **`op.drop_table('songs')` 를 생성**한다. 역방향도 동일하게 FLOW의 30여 개 테이블에 대해 발생한다.
- 이는 리뷰를 통과하면 **운영 데이터 전삭제**로 직결된다. 공유 커널의 최대 리스크는 성능도 결합도도 아니고 **이 자동 생성 DROP**이다.

보조 리스크: 음악 `app/db.py init_db()`가 `Base.metadata.create_all(bind=engine)`를 개발 폴백으로 호출한다 — 자기 metadata 한정이라 파괴적이지는 않으나, 공유 DB에서 **마이그레이션을 우회한 테이블 생성**이 일어나 Alembic 상태와 실제 스키마가 어긋난다(drift).

### 14.3 세 가지 변형 — 정면 비교

| 축 | **A. 단일 DB·단일 스키마 + `service` 컬럼** (사용자 제안 직역) | **B. 단일 Postgres 인스턴스 + 스키마 분리** ✅권장 | **C. 물리 분리 유지**(현행) |
|---|---|---|---|
| 테이블 배치 | 한 스키마에 양측 전부 | `flow.*` / `music.*` 스키마 2개 | DB 2개(SQLite + Postgres) |
| T1 alembic_version | **정면 충돌** | `version_table_schema`로 자연 분리 | 무관 |
| T2 autogenerate DROP | **치명적 발생** | 스키마 격리로 미발생(+`include_schemas` 미설정이 오히려 안전) | 무관 |
| 크로스 서비스 조인 | 가능(최대 장점) | 가능(스키마 수식자 필요: `music.songs`) | 불가 |
| 백업/복구 | 1회 스냅샷으로 양측 정합 | 1회 스냅샷으로 양측 정합 | 2회, 정합 보장 없음 |
| 인프라 컨테이너 | 1개 | 1개 | 2개(현재는 FLOW가 파일 DB라 1개) |
| 결합도 | 최대(스키마 변경이 상호 침범) | 중간(경계 명확) | 최소 |
| 권한 분리 | 어려움(동일 스키마) | **가능** — 롤별 `GRANT`로 상호 접근 차단 | 완전 분리 |
| 미래 서비스 분리(§12.3 exit) | 매우 어려움 | 스키마 단위 덤프로 용이 | 이미 분리 |
| FLOW 부수 이익 | — | **F2(SQLite→Postgres) 동시 달성**, 🔴 쓰기잠금·SPOF 해소(FU3) | 없음 |
| 판정 | **비권장** | **권장** | 단기 안전, 중기 부채 |

### 14.4 권장 변형 B — 실행 계획 (구체 diff 수준)

**단계 1. 단일 Postgres 인스턴스에 스키마·롤 준비**
```sql
CREATE SCHEMA flow  AUTHORIZATION flow_app;
CREATE SCHEMA music AUTHORIZATION music_app;
REVOKE ALL ON SCHEMA music FROM flow_app;    -- 상호 침범 차단(블라스트 레이디어스 축소)
REVOKE ALL ON SCHEMA flow  FROM music_app;
```

**단계 2. 접속 URL에 search_path 고정 (코드 변경 없음 — 환경변수만)**
```
# FLOW  (app/db.py:41-49 가 그대로 사용)
DATABASE_URL=postgresql+psycopg://flow_app:***@postgres:5432/app?options=-csearch_path%3Dflow
# 음악 (app/config.py:9 오버라이드)
DATABASE_URL=postgresql+psycopg://music_app:***@postgres:5432/app?options=-csearch_path%3Dmusic
```

**단계 3. Alembic 격리 (T1 해결) — 양측 `env.py` 2줄씩**
```python
# FLOW backend/migrations/env.py:80-84 online 블록
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,
    version_table="alembic_version",
    version_table_schema="flow",  # 추가
)
# 음악 backend/migrations/env.py:58-60 online 블록
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    version_table="alembic_version",
    version_table_schema="music",  # 추가
)
```
- offline 블록(FLOW `:57-63`, 음악 `:39-44`)에도 동일 인자를 넣어야 `--sql` 모드가 어긋나지 않는다.

**단계 4. autogenerate 안전벨트 (T2 해결) — 방어적 이중화**
```python
def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and reflected and compare_to is None:
        return False  # DB에만 있고 내 metadata에 없는 테이블 → 절대 DROP 생성 금지
    return True
```
- 스키마 격리만으로도 대부분 차단되지만, 이 필터는 **사람의 실수(잘못된 search_path)까지 막는 마지막 방어선**이다. 현재 양측 모두 부재.

**단계 5. FLOW 데이터 이전 (F2와 통합 수행)**
- 현재 FLOW 운영 데이터는 SQLite 파일(`docker-compose.prod.yml:69` `./gospel.db`).
- 이전 시 주의: `db.py:98-118`의 WAL/PRAGMA 최적화는 Postgres에서 무효 → 대체 튜닝 필요(연결 풀은 `db.py:66-79`가 이미 처리).
- `render_as_batch=True`(FLOW `env.py:62,83`)는 Postgres에서 불필요하지만 무해(호환 유지).

**단계 6. 선결 조건 (반드시 §15 X1 먼저)** — 공유 DB는 노출된 포트 1개가 곧 양측 전체 유출이다.

### 14.5 사용자 아이디어를 살리는 정답 — "DB 통합"이 아니라 "출처 태깅"

사용자 제안의 본질은 *"두 서비스를 하나의 데이터 평면에서 구분해 다루자"* 이다. 이 본질은 **DB를 합치지 않고도 즉시 달성**할 수 있으며, 그것이 §13.5-(2)이다.

| 적용 층위 | 형태 | T1/T2 리스크 | 시나리오 달성 | 판정 |
|---|---|---|---|---|
| ① 물리 DB 통합 + `service` 컬럼 | 변형 A | **높음** | 즉시 아님 | 비권장 |
| ② 인스턴스 공유 + 스키마 구분 | 변형 B | 낮음(수정 후) | 즉시 아님 | 중기 권장 |
| ③ **레코드 수준 출처 태깅** | `media_asset.source_service` + `external_ref` | **없음** | **즉시 달성** | **지금 채택** |

③의 근거와 이점:
- FLOW는 이미 동일 사상의 구분축(`workspace_id`, `compliance.py:75`)을 운영 중 → 새로운 개념 도입이 아니라 **기존 패턴의 확장**.
- `external_ref`가 멱등키가 되어 §11.3의 중복 생성 문제를 동시에 해결.
- 곡의 출처(`'music'`)가 데이터에 남아 **삭제 전파(F5)·비용 귀속(§11.5)·AIGC 라벨(FU2)** 을 나중에 붙일 앵커가 된다.
- 즉 사용자 제안은 **폐기가 아니라 "층위를 바꿔 채택"** 된다. 이것이 이번 라운드의 가장 실용적인 결론이다.

### 14.6 사용자 식별 통합 — 공유 커널이라도 자동으로는 안 된다

| 사실 | 근거 | 함의 |
|---|---|---|
| 음악 `users.id`는 **Integer** PK | `models.py:10` | FLOW PK와 타입 불일치 |
| FLOW `subscriber`는 문자열 PK 계열, workspace도 `String(40)` | `orm.py:184`, `compliance.py:75` | 직접 FK 불가 |
| 음악 인증=JWT HS256(`jwt_secret`, `config.py:11`) / FLOW=HMAC(`AUTH_SECRET`) | 제1부 §2 | 토큰 상호 검증 불가(🔴A 그대로) |

→ 결론: **DB를 합쳐도 식별은 합쳐지지 않는다.** 권장은 `users`에 `subscriber_id String(40) NULL` 매핑 컬럼(또는 별도 매핑 테이블) 1개를 두고, **크로스 서비스 FK는 만들지 않는 것**(서비스 분리 exit 경로 보존, §12.3).

---

## 15. 신규 갭 카탈로그 (X 시리즈 · 전부 이번 라운드 실증)

| ID | 갭 | 심각도 | 근거 | 조치 |
|---|---|---|---|---|
| **X1** | 음악 compose가 **Postgres 5432·Redis 6379를 호스트에 공개** + 약한 비밀번호 `music123` + Redis 무인증 | 🔴 **최상** | `docker-compose.yml:11-12`, `:19-20`, `:9` | `127.0.0.1:` 바인딩 또는 ports 제거(내부 네트워크만), 비밀번호 교체, Redis `requirepass`. **공유 커널(§14.4)의 선결 조건** |
| **X2** | 음악 compose에 **앱 컨테이너 자체가 없음**(전체 24줄, postgres/redis만) | 🔴 | `docker-compose.yml:3-21` | 백엔드/프론트 서비스 정의 추가(§11.7-B 통합 compose) |
| **X3** | Alembic `alembic_version` 충돌 위험 | 🔴(공유 시) | 양측 `env.py`에 `version_table` 부재 | §14.4 단계 3 |
| **X4** | autogenerate가 상대 테이블 **DROP 생성** | 🔴(공유 시) | 양측 `include_object` 부재, `target_metadata` 자기 한정(FLOW `env.py:44` / 음악 `:34`) | §14.4 단계 4 |
| **X5** | 관리 라우트 **활성화 전제조건 미문서화** — 음악 `admin_token` 기본 `""`(빈값이면 admin 라우트 비활성), FLOW `admin_api_key=='change-me'`면 **503** | 🟠 | 음악 `config.py:59`, FLOW `auth.py:229-233` | 배포 체크리스트에 4종 시크릿 필수값 명시(제1부 §3) |
| **X6** | `/mobile/media` 카탈로그·스트림 **무인증 공개** | 🟠(유입 시 🔴) | `media.py:157-158`, `:178-179` | Plan Z에서 심의 통과분만 + `active=False` 기본(§13.5-3) |
| **X7** | `category` 화이트리스트가 `worship\|sermon` 뿐 | 🟠 | `media_service.py:95-96` | `ai_music` 추가 + 모바일 탭 |
| **X8** | `sync_records.flow_song_id` **예약만 되고 미사용** | 🟡 | `models.py:61` (주석 "Push 模式（独立 FLOW 后端）预留") | Plan Z에서 `asset_id` 저장으로 활성화 → 계약 완성 |
| **X9** | 음악 `init_db()`의 `create_all` 개발 폴백이 마이그레이션과 이중 경로 | 🟡 | `app/db.py` `init_db()` | 운영에서 비활성(환경 분기) |

---

## 16. 연관성 2차 분석 — "더 이상의 연관성은 없는가?"에 대한 답

### 16.1 신규 루트커즈 **RC-E: 재발명(Reinvention)**

제1부는 RC-A~D(계약 미정의/성숙도 비대칭/단일 박스/생명주기 부재)를 제시했다. 이번 라운드에서 **다섯 번째, 그리고 가장 비용이 큰 근본 원인**이 드러났다.

> **양측이 서로가 이미 가진 능력을 모른 채 각자 설계했다.**

증거 3점:
1. 음악 `routers/flow.py:4` 주석이 "FLOW APP 동일 JWT 재사용"을 **사실이 아닌 전제**로 기술 → 실제 FLOW는 HMAC(🔴A).
2. 음악 `models.py:61` `flow_song_id`를 "독립 FLOW 백엔드 Push용 예약"으로 만들어 두었으나, **FLOW엔 이미 수신 엔드포인트가 존재**(`media.py:61-62`).
3. 제1부(본 문서 §6.2/§11.7-D) 자체가 `ExternalMusic` 신규 테이블을 제안 → `media_asset`(`orm.py:597`)과 **필드가 사실상 중복**.

→ RC-E의 처방은 코드가 아니라 프로세스다: **"양측 능력 인벤토리(capability inventory)를 통합 설계 전 필수 산출물로 둔다."**

### 16.2 keystone 이동 — 의존 그래프가 바뀐다

| | 제1부(브로커 전제) | 제2부(Plan Z) |
|---|---|---|
| keystone | 🔴A 인증 모델 통일 | **관리자 토큰 신뢰 경계 + 심의 게이트** |
| 크리티컬 패스 | 인증 → 스토리지 → 수신 API → 계약 → 클라이언트 (5단계) | **심의 → 푸시 → 카테고리/멱등 (3단계)** |
| 최초 가치 도달 | 스토리지/브로커 개발 후 | **엔드포인트 1개 호출 후** |

즉 **의존성의 병목이 "인증 설계"에서 "노출·심의 정책"으로 이동**한다. 리스크의 성격도 기술적(통합 실패)에서 규제·평판(무심의 공개)으로 바뀐다 → §12.2/FU2/FU8과 직결.

### 16.3 신규 교차 연쇄 (4건 · 단독 갭으로는 안 보이는 것들)

**연쇄 1 — X1 × 공유 커널 × 블라스트 레이디어스 (순서 의존의 발견)**
```
docker-compose.yml:11-12 (5432 호스트 공개) + :9 (music123)
   └─ 현재: 음악 DB만 유출
        └─ §14 변형 B 채택 후: 같은 인스턴스 = FLOW 구독자/결제/PII 동시 유출
             ⇒ 결론: "공유 커널은 X1 해결 이전에 절대 채택 불가"
```
이것은 두 갭의 단순 합이 아니라 **아키텍처 결정의 선후 관계를 강제하는 제약**이다(제1부에 없던 연관).

**연쇄 2 — 무심의 × 무인증 공개 × AIGC 라벨 (3자 규제 결합)**
```
songs.py:167-168 (enable_moderation=False 기본 → 자동 approved)
   × media.py:157-158·178-179 (FLOW 카탈로그/스트림 무인증)
   × FU2 (AIGC 라벨 의무화 전망)
   ⇒ AI 생성물이 심의 없이 FLOW 브랜드 도메인에서 전 인터넷에 라벨 없이 배포
   ⇒ 적발 시 책임 주체는 "음악 서비스"가 아니라 도메인 소유자(FLOW)
```

**연쇄 3 — 바이트 소유권 × 비용 × 컴플라이언스 (한 결정이 세 문제를 동시에 움직임)**
```
Plan Z: FLOW가 바이트 소유(media_service.py:148)
  ├─(+) 🔴2 만료 소멸, S3 결정 보류(🟠5), egress 0
  ├─(−) FLOW 디스크 증가 → §11.8 용량 임계 가속 → 호스트 분리 트리거 앞당김
  └─(−) FLOW가 콘텐츠 보관 주체 → PIPL/저작권 책임이 FLOW로 이동(§12.2)
```
→ 즉 Plan Z는 무료가 아니라 **"개발 리스크를 운영·법적 책임으로 환전"** 하는 거래다. 이 트레이드오프는 명시적으로 승인되어야 한다(§19 신규 결정 D9).

**연쇄 4 — 공유 커널 × 비용 귀속 × 지갑 원장 (숨은 시너지)**
```
음악: songs.cost_cny(생성 원가) / engine_configs.api_key(models.py:76)
FLOW: wallet_reservation(compliance.py:72) hold→capture/release 원장
  ⇒ 변형 B(공유 인스턴스)면 스키마 수식 조인으로 "곡 원가 ↔ FLOW 크레딧 차감" 정합 검증 가능
  ⇒ 제1부 §11.5 "비용 귀속 미해결"의 유일한 저비용 해법 경로
```
→ 공유 커널의 진짜 가치는 성능이나 인프라 절감이 아니라 **회계 정합성**이다. 이는 제1부에서 발견하지 못한 연관이다.

### 16.4 갱신된 의존 그래프 (요약)

```
X1(포트 노출) ──선결──▶ 변형 B(공유 커널) ──▶ §11.5 비용 귀속 해결 / F2 Postgres 달성
                                      └──▶ FU3(SQLite 잠금) 해소
X5(시크릿 미설정) ──선결──▶ Plan Z(푸시) ──▶ 🔴1·🔴2 소멸 ──▶ 사용자 시나리오 달성
                                └── X7(카테고리) · §13.5-2(멱등) 동반 필수
심의 게이트(F7) ──선결──▶ X6(무인증 공개) 안전화 ──▶ FU2/FU8 규제 리스크 차단
RC-E(재발명) ──메타──▶ 위 모든 항목의 설계 낭비 원인
```

---

## 17. 자가정정 #3 — 제1부 설계 제안 일부 철회

| 항목 | 제1부 기술 | 정정 | 근거 |
|---|---|---|---|
| 신규 `ExternalMusic` 테이블(§11.7-D) | "FLOW에 외부 음악 테이블 신설" | **철회.** 기존 `media_asset` 재사용 + 컬럼 2개 추가 | `orm.py:597` 스키마가 이미 title/artist/duration/order/active/publish/notify 보유 |
| 신규 수신 엔드포인트(§6.2) | "FLOW에 수신 API 개발" | **철회.** `POST /admin/media` 재사용 | `media.py:61-62`, `:80` |
| 인증 브로커가 MVP 선결(§6.1) | "🔴A가 keystone" | **완화.** 서버-서버 관리자 토큰으로 MVP 성립, 브로커는 개인화 단계에서 필요 | `auth.py:216-241` |
| S3 확보가 🔴2의 필수 해법(🟠5) | "오브젝트 스토리지 필수" | **완화.** 공존 환경에서 FLOW 로컬 디스크 소유로 대체 가능 | `media_service.py:21,148` |
| 푸시/예약 발행 신규 설계 | 미고려/신규 | **불필요.** 기존 스케줄러 상속 | `media_publisher.py:33,54`, `main.py:199-201` |

> 자가정정 누적: #1 `GET /api/sync/flow` 인증 정상 · #2 음악 alembic 존재 · **#3 미디어 커널 재사용(본 절)**.

---

## 18. 업그레이드된 로드맵 + 결정 매트릭스

### 18.1 로드맵 (Plan Z 우선 · 기존 Phase 재배열)

| Phase | 내용 | 해소 갭 | 선결 |
|---|---|---|---|
| **P0 안전(즉시)** | X1 포트 비공개+비밀번호 교체, X5 시크릿 4종 설정, 음악 심의 강제(F7) | X1·X5·F7 | 없음 |
| **P1 시나리오 종단(1~2일)** | `ai_music` 카테고리(X7) + `source_service`/`external_ref` 마이그레이션(§13.5-2) + 음악측 푸시 워커 + `flow_song_id` 기록(X8) | 🔴1·🔴2·X8 | P0 |
| **P2 공존 품질** | 통합 compose(X2), Caddy 서브도메인(§11.7-A), CORS 화이트리스트(🟠4), 모바일 탭 | X2·🟠3·🟠4 | P1 |
| **P3 데이터 전략** | 변형 B 공유 커널: Postgres 단일 인스턴스+스키마 분리, `version_table_schema`(X3), `include_object`(X4), FLOW F2 이전 | X3·X4·F2·FU3·§11.5 | P0(X1 필수) |
| **P4 개인화** | 인증 브로커(§6.1) + 소유자 컬럼 → "내 곡" 시나리오 | 🔴A | P1 |
| **P5 미래** | 관측성/DR/OIDC/CDN 오프로드 | §12.5~12.7 | P3 |

**최소 성공 경로(MVP)**: P0 → P1 만으로 사용자 시나리오가 종단 달성된다. 제1부 추정(브로커+스토리지+수신 API)의 개발 범위가 **1/3 이하로 축소**된다.

### 18.2 결정 매트릭스 (조건부 권고)

| 상황 | 권고 |
|---|---|
| 지금 당장 시나리오만 되게 하고 싶다 | **Plan Z(P0+P1)**. DB는 건드리지 않는다 |
| 곧 "내가 만든 곡만" 개인화가 필요하다 | Plan Z 후 P4 브로커. `media_asset`에 소유자 컬럼 추가 |
| 운영 부담(백업·인프라·회계 정합)이 문제다 | **변형 B**(P3). 단 X1 선결 |
| 두 서비스를 향후 분리할 가능성이 높다 | 변형 C 유지 + 출처 태깅(③)만 채택 |
| 절대 하지 말 것 | 변형 A(단일 스키마 + service 컬럼) — T1/T2가 데이터 파괴로 직결 |

---

## 19. 갱신된 미해결 결정사항 (기존 8건 + 신규 5건)

| ID | 결정 필요 | 선택지 | 영향 |
|---|---|---|---|
| D9 | **Plan Z의 책임 환전 승인** — FLOW가 AI 콘텐츠 바이트 보관 주체가 되는 것 | 승인 / S3 경유 유지 | 저작권·PIPL 책임 귀속(§16.3 연쇄 3) |
| D10 | 음악 서비스에 부여할 **FLOW 권한 범위** | 전체 admin 토큰 / `/admin/media` 전용 스코프 토큰 / 프록시 | 침해 시 블라스트(§13.6) |
| D11 | **DB 전략** 확정 | A / **B** / C | P3 전체(§14.3) |
| D12 | AI 음악의 **기본 노출 정책** | 즉시 공개 / `active=False` 후 승인 발행 | X6·FU8 |
| D13 | 모바일 앱 **탭 구조** — 찬양/설교와 별도 "AI 음악" 탭 여부 | 별도 탭 / 찬양 내 필터 | 클라이언트 작업량, 사용자 인지 |

> 기존 D1~D8(수신 범위/S3/호스트 분리/도메인/총관리 포털/FLOW 위젯/admin_token 하드닝/PIPL 동의 주관)은 유효하며, D1·D2는 §17 정정으로 **범위가 축소**되었다.

---
---

# 제3부 · 토폴로지 확정 + 환각 이중감사 + 개인정보 분리 (2026-08-30 3차 라운드)

> 사용자 지시: *"한국서버는 작은거고, 홍콩 서버는 큰거야"* / *"양 서비스 모두 홍콩 대용량 1대"* / *"개인정보만 한국서버에 놓을거야"*
> 본 제3부는 (1) 기획 전체를 **무턱대고 쓰지 않았는지** 전 주장을 재검증하고, (2) 확정된 토폴로지로 기억을 고치고, (3) PII 한국 분리에 따른 **분할 데이터 평면**을 설계한다.

---

## 20. 환각 이중감사 (Hallucination Double-Check)

### 20.1 방법

기존 기획의 **모든 정량·위치 주장**을 대상으로 ①코드 근거 재확인 ②수치 재계산 ③근거 없으면 `보류` 강등 의 3단계를 적용했다. "그럴듯해서 쓴 문장"을 찾는 것이 목적이다.

### 20.2 감사 결과 — 정정 4건

| # | 기존 기획의 주장 | 판정 | 실제 근거 |
|---|---|---|---|
| **정정 1** | 음악 `migrations/`에 버전 **6개** | ❌ **오류** | 실제 **5개**: `6e64ba2229a2_initial_schema`, `c2d3e4f5a6b7`, `a1b2c3d4e5f6`, `b3f7a1c2d4e5`, `659f128c2856` (디렉터리 실측) |
| **정정 2** | "동일 **8G** 박스 포화(잔여 1.5G) → 호스트 분리" (§11.8) | ❌ **근거 없는 수치** | 8G는 **코드에 존재하지 않는 추정**이었다. 코드에 있는 것은 FLOW **컨테이너 상한의 합** `2g+3g+1.5g+256m ≈ 6.75G / 4.5cpu`(`docker-compose.prod.yml:40-42, 73-75, 110-112, 138-140`)이며, 이는 **호스트 크기가 아니라 서비스별 cgroup 상한**이다. 호스트=홍콩 대용량(사용자 확정)이므로 **"포화" 결론은 폐기**한다 |
| **정정 3** | 음악 backend는 "**홍콩 경량 서버**에 배포" (음악 문서 인용) | ⚠ **현재 운영과 불일치** | `FLOW_APP_API.md:4`/`README.md:10`은 "홍콩 경량(轻量) 서버"로 적지만, 사용자는 **홍콩 대용량 1대 공존**으로 확정했다. → 문서 구술은 **이전 상태 또는 계획 문구**로 취급하고, 설계 기준은 사용자 확정(T-2)을 따른다. 음악 저장 리전 `ap-hongkong`(`backend/.env.example:12`)은 **유효** |
| **정정 4** | PII가 홍콩 DB(`gospel.db`)에 저장되는 것을 전제로 레지던시를 논의 (§12.2) | ❌ **방침 변경** | 사용자 확정: **PII는 한국**. FLOW는 현재 PII를 홍콩 SQLite(`docker-compose.prod.yml:69` `./gospel.db`)에 보관 중 → **이전 필요** (§21) |

### 20.3 재검증 통과 (근거 재확인 완료 · 변경 없음)

| 주장 | 근거 |
|---|---|
| FLOW 관리 UI=Streamlit, 8502 미구동 | `docker-compose.prod.yml` 서비스 목록, Caddyfile |
| 관리 게이트=HMAC `APP_PASSWORD`+`kg_admin_gate`, `?a=` | `admin/lib/auth.py:41-99` |
| 모바일 토큰=메모리 전용(localStorage 미저장) | `mobile/utils/state.js:34` |
| 시크릿 로그 마스킹 | `backend/app/logging_setup.py:27-40` |
| CORS `allow_credentials=False` | `backend/app/main.py:292-304` |
| SSRF 가드(10.0.0.0/8 등) | `backend/app/security/ssrf.py:29` |
| Workspace 멀티테넌트 | `models/compliance.py:75, 90, 101` |
| 음악 인증=JWT HS256 / 관리=`X-Admin-Token` | `config.py:11, 59` / `routers/auth.py:75` |
| 음악 sync 3종 엔드포인트 + 인증 정상(`get_current_user`+`filter_by(user_id=)`) | `routers/flow.py:57, 86, 108` |
| `audio_cdn_url` 미설정 시 Suno 원본 링크로 **降级** | `routers/flow.py:25-29` |
| `EngineConfig.api_key` 평문(주석으로 KMS 권고) | `models.py:66, 76` |
| 레이트리밋 in-memory, **Redis는 선언만**(앱 코드 접속 0건) | `app/config.py:10` 만 존재, `docker-compose.yml:16-20`에 컨테이너만 |
| `enable_moderation=False` 기본 | `routers/songs.py:167-168` |
| FLOW 미디어=로컬 디스크 `data/media` | `services/media_service.py:21, 148` |
| FLOW Postgres 전환=환경변수 1줄, 코드 변경 0 | `app/db.py:41-49`, `config.py:259`, `db.py:66-79` |
| 테이블명 충돌 0건 | FLOW 단수 / 음악 복수 (§14.1) |
| Alembic `version_table`·`include_object` 양측 부재 | 양측 `migrations/env.py` grep 0건 |

### 20.4 의도적 보류 (환각 방지 — 근거 없으면 쓰지 않음)

| 항목 | 왜 보류인가 | 필요한 입력 |
|---|---|---|
| 홍콩 대용량 서버의 RAM/CPU/디스크 | 코드·문서 어디에도 없음. 추정 시 §11.8과 동일한 오류 반복 | 실측치 |
| 한국 소용량 서버의 RAM/CPU/디스크 | 위와 동일. **PII 금고가 이 박스에 들어가므로 용량이 설계 결정에 직결** | 실측치 |
| 한국↔홍콩 RTT(ms) | 미측정. 설계는 **구간 추정치로만** 표기(§21.6) | ping/traceroute 실측 |
| "8G"·"잔여 1.5G" 등 모든 기존 정량 | 정정 2로 폐기 | 재측정 후 복원 |

> 원칙: **추정 수치는 결론의 근거로 쓰지 않는다.** 필요한 입력(§20.4)이 오면 §11.8·§21.6을 그 자리에서 수치화한다.

### 20.5 쓰지 않은 것들 (과잉 설계 경계)

- 홍콩 서버 스펙을 임의로 가정한 용량 계산
- 한국 서버를 "읽기 전용 replica/캐시 노드"로 규정한 상세 설계 (스펙 미확정)
- 중국 본토 CDN/가속 노드 배치 (법적·사업적 결정, 코드 근거 없음)

---

## 21. 분할 데이터 평면 — "개인정보는 한국, 나머지는 홍콩"

### 21.1 PII 실측 인벤토리 (추정 아님, 전부 `file:line`)

**FLOW (홍콩 SQLite `gospel.db`에 현재 저장 중 → 이전 대상)**

| 데이터 | 필드 | 근거 | 성격 |
|---|---|---|---|
| 직접 식별 | `email`, `email_verify_token` | `models/orm.py:1056`, `:251` | PII 1급 |
| 표시 식별 | `display_name` | `orm.py:189` | PII |
| 준식별 | `age_group`, `gender` | `orm.py:201-202` | PII |
| **민감(종교)** | `journey_stage`, `faith_stage`, `salvation_status`, `salvation_confidence`, `religion`, `denomination`, `is_darakbang_member`, `darakbang_chapter` | `orm.py:191-192, 219-222, 263-266, 232` | **민감정보 최고등급** |
| 민감(내용) | `current_struggle`, `emotional_state`, `last_emotion` | `orm.py:203,200`, `emotional_state` **`:200`**(정정), `last_emotion` **`:216`**(정정) | 민감(정신·건강·가족사 혼입) |
| 동의·이력 | `consent`, `consent_data`, `consent_kakao`, `first_seen_at`, `last_active_at` | `orm.py:197-198, 241, 245-248` | PII + 증빙 |
| 결제 | `payment` 테이블(`subscriber_id` 연결) | `orm.py:276` 주석 | PII |
| 동의 증빙 | `consent_record.user_agent` | `models/compliance.py:59` | PII 준식별 |
| **동의 시점 IP** | `consent_record.ip` | `models/compliance.py:58` | **PII 준식별 — §25.1 보완에서도 누락, 2026-08-31 추가**(§26.1 SC-11) |
| **대화 원문** | `interaction.question`(사용자 원문)·`answer` | `orm.py:336-337` | **PII 고위험**(실명·질병·가족사 자유 입력) |
| 벡터/색인 | `qdrant` collection | `docker-compose.prod.yml` qdrant 서비스 | 파생 PII(원문 임베딩) |

**음악 플랫폼 (홍콩 Postgres → 이전 대상)**

| 데이터 | 필드 | 근거 |
|---|---|---|
| 직접 식별 | `users.email` | `app/models.py:9` |
| 인증 | `users.hashed_password` | `models.py:10` |
| 사용자 생성물 | `songs.prompt`, `songs.lyric`(개인정보 혼입 가능) | `models.py:20, 21` |
| 시크릿 | `engine_configs.api_key`(평문) | `models.py:76` |

### 21.2 핵심 판단 — "테이블 이전"이 아니라 "가명화 경계 재설계"

단순히 `subscriber`를 한국으로 옮기면 두 가지가 즉시 깨진다.

1. **정합성**: `interaction.subscriber_id`가 `subscriber.subscriber_id`를 **FK**로 참조한다(`orm.py:333-335`). 한국/홍콩이 물리 분리되면 DB 레벨 FK는 성립하지 않는다.
2. **성능**: 대화 1턴마다 개인화 상태(`journey_stage`·`salvation_status`·토큰 쿼터)를 읽어야 하는데, 이것이 국경 간 왕복이 되면 **매 요청 지연**이 발생한다.

→ 따라서 경계는 **"테이블"이 아니라 "식별 가능성"** 으로 긋는다.

### 21.3 계층 설계 (2계층 분할)

```
[한국 · 소용량 = PII 금고 (Vault)]          [홍콩 · 대용량 = 가명 콘텐츠/연산]
  subscriber_identity                          subscriber_profile (가명)
   - subscriber_id (가명키, 공유)              - subscriber_id (가명키)
   - email, email_verify_token                 - journey_stage, faith_stage
   - display_name, age_group, gender           - salvation_status/confidence
   - religion, denomination, darakbang_*       - emotional_state, preferred_tone
   - current_struggle (또는 홍콩엔 해시만)      - 토큰 쿼터/구독 티어 (캐시)
   - consent_record, payment                   - interaction (PII 스크러빙 후)
  music_users_identity                         - document/index_snapshot/qdrant
   - email, hashed_password                    - media_asset (오디오 바이트)
  데이터 주체 권리 API + 삭제 원장              - songs/sync_records/engine_configs
```

**게이트웨이 계약 (신규 컴포넌트 1개)**
- `POST /pii/resolve` — 가명키 ↔ 식별자 매핑, **한국만**.
- `POST /pii/scrub` — 홍콩 저장 전 원문에서 PII 제거/치환.
- `DELETE /pii/subjects/{id}` — 열람·삭제·이전 요청의 **단일 진입점** → 홍콩(Qdrant·S3·interaction)으로 삭제 팬아웃.
- 홍콩은 **`subscriber_id`(가명키)만** 들고 다닌다. 직접 식별자는 홍콩에 **저장하지 않는다**.

### 21.4 파생 변경 (분할의 필연적 결과 — 추측 아님)

| # | 변경 | 근거/내용 |
|---|---|---|
| **Y1** | `interaction.subscriber_id` FK **해제** → 애플리케이션 정합 + 야간 정합 배치 | `orm.py:333-335` FK 실측 |
| **Y2** | **PII 스크러버 신규**(원문 저장 전) | `orm.py:336` `question` 자유 입력 실측 |
| **Y3** | Qdrant payload에서 **직접 식별자 제거** + 삭제 시 컬렉션 payload 필터 삭제 | 벡터는 원문 파생물 |
| **Y4** | 홍콩측 **가명 프로필 로컬 캐시**(TTL·read-through) | 매 요청 국경 왕복 방지(§21.6) |
| **Y5** | **삭제 전파 원장**(`deletion_ledger`) 신규 | 주체 삭제 요청의 3자 전파(Suno·S3·Qdrant) 증빙 |
| **Y6** | 음악 `users` 분할 — 식별은 한국, 프로필/곡은 홍콩 | `models.py:9-10` 실측 |
| **Y7** | 로그· observability 파이프라인에 **PII 유출 검사** | `logging_setup.py:27-40` 마스킹은 기존이나 분할 후 경계 확대 |
| **Y8** | 백업 2계층 분리 보관 + **복원 시 교차 오염 금지**(한국 백업에 홍콩 데이터 혼입 역방향도 차단) | 분할 평면의 필연 |

### 21.5 이 설계가 오히려 강화하는 것 (미래지향)

1. **감시 회피 요건(메모리)과 정합**: 홍콩 서버가 압수·열람되어도 남는 것은 **가명 콘텐츠 + 가명키**뿐이다. 식별자는 한국에 있어 **"누가 어떤 질문을 했는가"가 단일 관할에서 완성되지 않는다.** 이것은 부수효과가 아니라 §12.3의 surveillance 전제를 **구조적으로 만족**시키는 유일한 배치다.
2. **개인정보보호법·PIPL 대응**: 처리 거점(홍콩)과 식별 정보 거점(한국) 분리 → 주체 권리(열람·정정·삭제·이전)를 **한국 단일 API**로 봉합 가능(Y5).
3. **음악 플랫폼의 동일 구조 재사용**: 음악도 동일한 PII 금고를 쓰면(Y6) 양 서비스의 컴플라이언스 경로가 **1개**로 수렴한다.

⚠ 단, 과대평가 금지: **PIPL의 본토 내 저장 의무가 적용되는 경우, 홍콩은 "국내"가 아니다.** 분할은 위험을 **줄이는** 것이지 면책이 아니다(§20.5·§22.3).

### 21.6 지연 예산 (구간 추정 — 실측 전 확정 금지)

| 경로 | 예상 | 근거 |
|---|---|---|
| 한국↔홍콩 RTT | **보류**(미측정, 통상 수십 ms급으로만 인지) | §20.4 |
| 홍콩 내부(Plan Z 푸시) | **거의 0** — 동일 호스트 Docker 네트워크 | T-2 확정 |
| PII 의존 요청 | 로그인·프로필·결제·삭제 등 **저빈도 경로에만** 국경 왕복 발생 | 설계로 빈도 제한(Y4) |
| 대화 요청(핫패스) | **국경 왕복 0** — 가명 프로필 캐시(Y4) + 토큰에 서명된 프로필 포함 | 동일 |

> 설계 원칙: **국경 왕복을 핫패스에서 제거하는 것**이 이 평면의 성능 요건이다. 캐시 미스 시 폴백 동작(차단 vs 제한 기능)은 스펙 확정 후 결정(D14).

---

## 22. 미래지향 재점검 (토폴로지 확정 후 갱신)

### 22.1 역할 재정의 (기존 §12.3 교체)

| 노드 | 역할 | 신뢰/역할 성격 | 근거 |
|---|---|---|---|
| **홍콩 대용량 1대** | 콘텐츠·연산·캐시·미디어(양 서비스 공존) | 고연산 / **저신뢰(감시 관할)** | T-2 |
| **한국 소용량** | **PII 금고** + 주체 권리 + 결제/동의 증빙 | 저연산 / **고신뢰(법적 거점)** | T-3 |
| 중국 본토 | 사용자(데이터 정체 없음) | — | `PROMPT.md:13` |

→ **exit 경로 재정의**: §12.8의 "호스트 분리"는 **"홍콩 내부 서비스 분할(음악 독립)"** 로 교체되고, **"PII 금고 이중화(한국 HA)"** 가 새로운 1순위 진화 축이 된다.

### 22.2 신규 미래 리스크 (FU9~FU12)

| ID | 리스크 | 시점 | 완화 |
|---|---|---|---|
| FU9 | 한국 PII 금고 **단일 장애점**(소용량) → 로그인·결제 전면 중단 | 즉시~1년 | Y4 캐시 + 한국 HA(§22.1) |
| FU10 | 홍콩 서버 열람 시 **가명 재식별**(준식별 조합) | 상시 | 준식별(age/gender/지역) 홍콩 미저장 + 스크러빙 Y2 |
| FU11 | 삭제 요청의 **3자 전파 누락**(Suno·S3·Qdrant) | 1~2년 | Y5 삭제 원장 + 정기 감사 |
| FU12 | 분할 평면 운영 복잡도(정합·지연·백업 2벌) | 상시 | Y1 정합 배치 + Y8 백업 분리 |

### 22.3 컴플라이언스 — 정직한 경계

- **개인정보보호법(한)**: PII 한국 저장은 **유리**.
- **PIPL(중)**: 본토 사용자 PII가 홍콩으로 가는 구조가 남는다면(콘텐츠·가명 로그) **여전히 국외 이전 논점**이며, 홍콩을 본토로 보지 않는 한 분할로 **완전 해소되지 않는다**. 다만 **홍콩에 두는 것을 가명 데이터로 제한**하면 이전 대상 데이터의 범위가 좁아진다(§21.5).
- 결론: 분할은 **"노출 최소화(data minimisation)"** 전략이며, 법적 면책이 아니다. 필요 시 별도 법률 검토(D15).

---

## 23. 로드맵 갱신 (PII 금고 단계 삽입)

| Phase | 내용 | 신규/변경 |
|---|---|---|
| **P0** | X1 포트 비공개·비밀번호 교체, X5 시크릿 4종, F7 심의 강제 | 기존 |
| **P0.5 (신규·선결)** | **PII 금고 설계·이전** — 인벤토리 확정(§21.1), 가명키 체계, 게이트웨이 3종 API, Y1~Y8 | **사용자 시나리오의 새로운 선결 조건** |
| **P1** | `ai_music` 카테고리 + `source_service`/`external_ref` + 음악 푸시 워커 | 기존 |
| **P2** | 통합 compose·Caddy 서브도메인·CORS·모바일 탭 | 기존 |
| **P3** | 변형 B 공유 커널(스키마 분리) — **단, PII 테이블은 한국 금고로 제외** | **범위 축소·수정** |
| **P4** | 인증 브로커 + 개인화 | 기존 |
| **P5** | 관측성·DR·한국 금고 HA | 수정 |

> **§14 변형 B의 정정**: PII 테이블(`subscriber` 식별부, `payment`, `consent_record`, 음악 `users` 식별부)은 **홍콩 공유 커널에 넣지 않는다.** 공유 커널의 대상은 **홍콩에 남는 비식별 데이터**로 한정된다(§21.3).

---

## 24. 누적 정정 이력 + 갱신된 미결정

### 24.1 자가정정 #4~#5 (본 라운드)

| # | 정정 | 근거 |
|---|---|---|
| #4 | 음악 Alembic 버전 **6 → 5개** | 디렉터리 실측 |
| #5 | "8G 단일 박스 포화 → 호스트 분리" **폐기** (8G는 코드에 없는 추정, 컨테이너 상한 합 6.75G와 혼동) | `docker-compose.prod.yml:40-140` |
| (방침) | 음악 "홍콩 경량 서버"는 **이전 상태 문구**로 취급, 설계 기준은 홍콩 대용량 1대 공존 | 사용자 확정 vs `FLOW_APP_API.md:4` |
| (방침) | PII 위치 전제 변경(홍콩 → 한국) → §12.2·§14·§23 전면 갱신 | 사용자 확정 |

### 24.2 신규 미결정 (D14~D17)

| ID | 결정 | 선택지 |
|---|---|---|
| D14 | PII 캐시 미스 시 동작 | 차단(fail-closed) / 제한 기능(fail-open, 가명 기본 프로필) |
| D15 | PIPL 본토 내 저장 의무 적용 여부 법률 검토 | 자체 판단 / 외부 자문 |
| D16 | 홍콩에 남길 **준식별** 범위(age/gender/지역) | 전부 한국 / 일부 홍콩 허용(재식별 위험 감수) |
| D17 | 한국 서버 스펙·HA 예산 | 단일 / 이중화(§22.1 1순위) |

---

## 25. PII 표면 보완 감사 + 출국 경로 (4차 라운드)

> 본 절은 **§21.1 인벤토리가 불완전했다는 자백**에서 시작한다.
> 핵심 교훈: **PII 경계는 "어디에 저장하는가"가 아니라 "어디로 빠져나가는가(egress)"다.**
> 제3부의 분할 설계는 **저장소**만 다뤘고, **출구**를 놓쳤다.

### 25.1 §21.1 누락분 — 실측 10건 추가 (전부 `file:line`)

| 데이터 | 필드 | 근거 | 성격 |
|---|---|---|---|
| **결정적 발화 원문** | `salvation_journey.trigger_quote` | `orm.py:1116` (JourneyVault 필드) | **최고 민감** — 구원 결단 순간의 사용자 원문 + `journey_id` FK(`:1113-1115`) |
| 구원 여정 시계열 | `salvation_journey.from_status/to_status/trigger_signal` | `orm.py:446-448` | 민감(종교 이력), subscriber FK(`:443-445`) |
| **사용자 연락처** | `support_ticket.contact` | `orm.py:1134` | **직접 식별자**(이메일/전화) |
| 문의 본문 | `support_ticket.content` | `orm.py:499` | 자유 입력 PII |
| **클라이언트 IP** | `app_error_log.client_ip` | `orm.py:481` | 개인정보(준식별) — **자동 기록·무인 심사** |
| 스택트레이스 | `app_error_log.traceback` | `orm.py:478` | 요청 파라미터 PII 혼입 가능 |
| 사용자 메모 | `verse_annotation.note` | `orm.py:380` | 자유 입력 + subscriber FK(`orm.py:373-375`) |
| 기기 식별자 | `activity_event.device_id` | `orm.py:876` | 영속 식별자(준식별) |
| 이벤트 페이로드 | `activity_event.payload` | `orm.py:881` | JSON 임의 데이터 |
| 운영자 식별 | `audit_log.who` | `orm.py:395` | 관리자 식별자(이메일 형태 가능) |

→ §21.1의 "3개 테이블"은 **전체의 일부**였다. 실제 PII 표면은 **최소 8개 테이블**에 분산돼 있다. 다만 이 누락이 **분할 방향을 바꾸지는 않는다**(모두 동일하게 "식별부=한국 / 원문=스크러빙" 규칙 적용). 다만 **인벤토리 완전성은 컴플라이언스 증빙의 전제**이므로 보완한다.

### 25.2 출국 경로 ① — Langfuse 원격측정 (🔴 신규·최고 심각)

| 사실 | 근거 |
|---|---|
| **스위치 기본값 ON** | `config.py:178` `langfuse_enabled: bool = True` |
| 목적지 = **EU 클라우드** | `config.py:181` `langfuse_host: str = "https://cloud.langfuse.com"` |
| 전송 내용 = **요청 전문 + 구독자 ID** | `chat.py:193` `lf.trace(name="chat", input=req.model_dump(), user_id=sub_id)` |
| 파이프라인 내 원문 재전송 | `chat_pipeline.py:154` `input=query` / `:458` `input=query` / `:538` `input=query` |
| 활성 조건 | `connections.py:282-285` — `enabled=True` **이고** public·secret key 가 모두 있어야 전송 |
| 운영 가시성 | `admin.py:807-811` 상태 노출, `main.py:46` 기동 로그 |

**판정**: 기본값이 **ON** 이므로 운영자가 키만 채우면 **사용자 질문 원문(실명·질병·가족사) + `subscriber_id` 가 EU 로 즉시 유출**된다. 이는 T-3(PII 한국 전용)과 **정면 충돌**하며, **§21의 DB 분할로는 전혀 막히지 않는다.** 스크러버를 DB **쓰기 지점**에만 두었기 때문이다(§25.4).

### 25.3 출국 경로 ② — LLM 프로바이더 (불가피한 제3자 처리)

- 지원 7종: `config.py:23-24` `gemini|openai|claude|ollama|deepseek|tencent|nvidia`
- 텐센트 국제 엔드포인트: `config.py:44` `https://tokenhub-intl.tencentcloudmaas.com/v1`
- **사용자 프롬프트는 추론을 위해 프로바이더로 나갈 수밖에 없다** — 기능의 전제조건.
- → PII 금고(§21.3)로 **덮을 수 없는 유일한 예외 경로**. 은폐하지 말고 **처리위탁(DPA) + 프롬프트 최소화**로 관리해야 한다.

### 25.4 §21 설계 정정 — 스크러버 위치가 틀렸다

| 항목 | 제3부 기술 | 정정 |
|---|---|---|
| Y2 PII 스크러버 | "홍콩 **저장 전** 원문에서 PII 제거" | **불충분.** 스크러빙 지점은 **저장 전이 아니라 모든 출구 전**이어야 한다. Langfuse trace(`chat.py:193`)와 LLM 호출은 **DB 저장보다 앞서거나 병렬로** 발생한다 |
| PII 경계 정의 | "DB 테이블 단위" | **"egress 경로 단위"** — 저장소를 아무리 분할해도 출구가 열려 있으면 무의미 |
| 자가정정 | — | **#6: PII 인벤토리 불완전 + 출국 경로 누락**(§25.1·§25.2) |

### 25.5 신규 파생 변경 (Y9~Y12)

| ID | 변경 | 근거 |
|---|---|---|
| **Y9** | **원격측정 스크러빙** — `lf.trace(input=...)`·`trace.event(input=...)` 호출 **직전**에 PII 제거 | `chat.py:193`, `chat_pipeline.py:154/458/538` |
| **Y10** | **Langfuse 기본값 OFF 전환**(`True`→`False`) + 명시적 opt-in + 전송 전 데이터 소재지 확인 | `config.py:178` |
| **Y11** | **LLM 프로바이더 처리위탁(DPA) + 프롬프트 최소화** — 예외 경로 문서화, 원문 전문 대신 마스킹/요약 전달 검토 | `config.py:23-24, 44` |
| **Y12** | **에러로그 IP·트레이스백 마스킹** 또는 보존기간 단축 | `orm.py:478, 481` |

### 25.6 신규 미결정 (D18~D19)

| ID | 결정 | 선택지 |
|---|---|---|
| D18 | Langfuse 유지 여부 | 자체 호스팅(홍콩 또는 한국) / 완전 OFF / EU 클라우드 + Y9 스크러빙 |
| D19 | LLM 프롬프트 전문 전송 허용 | 전문 전송(DPA) / 마스킹 후 전송(응답 품질 저하 감수) |

### 25.7 출국 경로 ③ — 스케줄러의 **저장 원문 재전송** (🔴 신규, 2026-08-31)

> 발견 경로: L0 정적 게이트(`tests/test_colocation_l0_guards.py`)를 작성하던 중
> **PII 인벤토리 스캐너가 `QATrendSnapshot.canonical_text` 를 의심 필드로 지목**했고,
> 역추적 끝에 §25.2·§25.3 어디에도 없는 **세 번째 출구**를 확인했다.

**경로 (전부 실측):**

| 단계 | 근거 |
|---|---|
| ① 60초 주기 스케줄러 (사용자 요청 **없음**) | `trending_scheduler.py:29` `(now - last_aggregation).total_seconds() >= 60` |
| ② 자동 통찰 생성 호출 | `trending_scheduler.py:57-59` `_generate_insights_async(limit=5)` |
| ③ 원문 로드 | `insight_engine.py:196` `question = snap.canonical_text` / `:347` / `:489` |
| ④ **무가공 변형** 로드 | `insight_engine.py:197-198` `variants = (snap.raw_variants or [])[:5]` → `v.get("text")` 를 프롬프트에 삽입 |
| ⑤ 외부 LLM 전송 | `insight_engine.py:117-122` `chat_with_fallback(...)` |
| ⑥ 기동 시 자동 시작 | `main.py:191` `start_scheduler as _start_trending` |

**경로 ②(대화)와의 결정적 차이:**

| | 경로 ② 대화 | **경로 ③ 스케줄러** |
|---|---|---|
| 발화 시점 | 사용자가 **방금 입력** | **이미 저장된** 과거 발화 |
| 트리거 | **그 사용자의 요청** | **시스템 타이머**(60초) |
| 동의 앵커 | 요청 단위 동의·목적 제한 적용 가능 | **없음** — 요청이 없으므로 요청 단위 통제를 걸 곳이 없다 |
| 최소 수집 | 프롬프트 최소화(Y11)로 완화 가능 | 원문을 **의도적으로 다시 꺼내** 보내므로 최소화 원칙과 정면 충돌 |
| 재전송 횟수 | 1회 | **누적 반복 가능** |

**원문이 실제로 들어있다는 증명:**

`QATrendSnapshot.canonical_text` 는 `normalize_question(Interaction.question 또는 .answer)` 의 결과다.
- 소스: `trending_service.py:158`(question 분기), `:181`(answer 분기)
- 저장: `trending_service.py:218` `canonical = normalize_question(text)` → `:256` `canonical_text=canonical`
- `normalize_question`(`trending_service.py:26-45`) 은 **NFC 정규화·공백 축약·물음표 중복 제거·표기 변형 통일** 만 한다.
  → **의미 내용은 그대로 보존** 된다. 즉 이 컬럼은 "집계값"이 아니라 **사용자 발화 원문**이다.

> ⚠️ 이 설계는 **의도적으로 "자주 묻는 질문"을 뽑기 위한 것**이므로(min_count=2 집계),
> 원문이 모이는 것은 결함이 아니라 기능이다. 문제는 **그 원문을 다시 LLM 에 보내는 것**이며,
> 그 사실이 §25 에 기록돼 있지 않았다는 점이다.

**판정**: T-3(PII 한국 전용) 관점에서 경로 ②보다 **다루기 어렵다**. 경로 ②는 "불가피한 예외"로
DPA 로 관리할 수 있지만, 경로 ③은 **사용자 요청 없이 저장 원문을 재전송**하므로
최소 수집·목적 제한 원칙의 근거를 제시하기 어렵다.

### 25.8 §21.1 인벤토리 3차 보완 (SC-12, 2026-08-31)

스캐너를 **3축**(이름 / 크기 / 구조)으로 강화하자 이전 두 판이 놓친 항목이 드러났다.
**교훈: "필드명이 무해하면 안전하다"는 가정 자체가 환각의 원천이었다.**

| 데이터 | 필드 | 근거 | 잡아낸 축 |
|---|---|---|---|
| **트렌딩 원문** | `qa_trend_snapshot.canonical_text` | `trending_service.py:218,256` (원문 = `Interaction.question`/`.answer`) | **크기(Text)** — 이름은 무해 |
| **무가공 변형 원문** | `qa_trend_snapshot.raw_variants` | `insight_engine.py:197-198` 프롬프트 삽입 | **구조(JSON)** |
| **결제 응답 전문** | `payment.raw` | `api/payment.py:135` `rec.raw = p` ← `portone_service.py:122` `return r.json()` **무필터** | **구조(JSON)** — 이름·크기 모두 무해 |
| 관리자/시스템 자유입력 | `audit_log.note` | `api/subscriber.py:142` `{"reason": ...}`, `api/auth.py:789` `{"device_id": dev}` | 이름+구조 |
| 운영자 메모 | `support_ticket.admin_note` | `api/support.py:163-164` | 이름 |
| 오류 메시지 | `app_error_log.error_message` | `middleware/error_monitor.py:65,118` — `scrub_secrets`(`logging_setup.py:78-95`)는 **시크릿만** 제거, 사용자 내용은 비제거 | 이름 |
| 초안 기기 식별자 | `document_draft.device_id` | `api/drafts.py:50,57` | 이름 |
| 초안 본문 | `document_draft.draft_body` | `api/drafts.py:45-46,55` | 크기 |
| 편집 이력(원문 전/후) | `qa_admin_edit_log.old_value/new_value/diff_summary` | `api/trending.py:318-319`, `:488-489` | 크기 |
| 편집 초안 | `qa_edit_draft.draft_content` | 편집 대상이 `canonical_text` | 크기 |
| 관심 주제 프로파일 | `subscriber.topic_interests` | `orm.py:240` | 구조 |
| 여정 신호 | `interaction.journey_signal` | `orm.py:342` | 이름 |
| 초안 메타 | `document_draft.draft_meta` | `orm.py:544` | 구조 |
| 잡 오류 전문 | `background_job.error_json` | `orm.py:528` | 구조 |
| **LLM 산출물 5종** | `qa_trend_snapshot.root_cause_analysis/ai_diagnosis/reflection_prompt/short_sermon_text/best_answer_why` | 원문을 인용·재서술하므로 **파생 PII 로 동급 취급**(`insight_engine.py:196,347,489`) | 크기 |

**`payment.raw` 에 대한 정확한 서술(과장 금지)**: FLOW 가 PortOne 에 **보내는** 값은
`customer: {id: sub_id}`(`api/payment.py:198`)로 이미 가명화돼 있다. 문제는 **응답**쪽이다 —
`get_payment`(`portone_service.py:117-122`)가 `r.json()` 을 **여과 없이 반환**하고,
이를 `payment.raw` 에 그대로 저장한다(`api/payment.py:135`). 즉 **제3자 페이로드의 필드 단위
통제권이 없다.** 특정 필드(구매자명·이메일 등)가 들어있다고 단정하지는 않으나,
들어올 수 있는 통로가 열려 있고 검증되지 않았다는 사실 자체가 분류 대상이다.

### 25.9 신규 파생 변경 (Y13~Y14) / 미결정 (D20)

| ID | 변경 | 근거 |
|---|---|---|
| **Y13** | **스케줄러 통찰 생성의 opt-in 전환** — 기본 OFF, 또는 `canonical_text`/`raw_variants` 대신 **집계 메타데이터만** 프롬프트에 사용 | `trending_scheduler.py:57-59`, `insight_engine.py:196-198` |
| **Y14** | **`payment.raw` 필드 화이트리스트 저장** — PortOne 응답에서 필요한 키만 추려 저장 | `api/payment.py:135` |

| ID | 결정 | 선택지 |
|---|---|---|
| **D20** | 스케줄러 통찰 생성 유지 여부 | 유지(원문 전송 DPA) / 원문 제외 집계만 / 수동 트리거 전환 / 완전 중단 |

### 25.10 신규 결함 D21 — FLOW 마이그레이션 체인은 빈 DB 에서 재현되지 않는다

**실측(D5 구현 중 발견, 설계서·계획서 어디에도 없던 항목).**

| 항목 | 실측값 |
|---|---|
| FLOW ORM 테이블 | **32개** |
| 마이그레이션이 `op.create_table` 하는 테이블 | **11개** (`ads`, `ccp_rate_card`, `consent_record`, `document_drafts`, `eval_question`, `eval_run`, `salvation_journey`, `support_ticket`, `wallet_reservation`, `workspace`, `workspace_membership`) |
| **어떤 마이그레이션도 만들지 않는 테이블** | **21개** — `activity_event`, `app_error_log`, `app_setting`, `audit_log`, `background_job`, `category`, `content_link`, `document`, `document_version`, `index_snapshot`, `interaction`, `media_asset`, `payment`, `prompt_template`, `qa_admin_edit_log`, `qa_edit_draft`, `qa_trend_snapshot`, `question_quota`, `source_artifact`, `subscriber`, `verse_annotation` |

빈 DB 에 `alembic upgrade head` 를 실행하면 첫 단계에서 죽는다:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: document_version
[SQL: ALTER TABLE document_version ADD COLUMN target_audience JSON]
```

**직접 원인 — `c2fbb2654cf3_baseline_all_tables.py` 는 'baseline' 이 아니다.**
`op.create_table` 은 `salvation_journey` **1개** 뿐이고, 나머지는 전부
`batch_alter_table` **8개** 다. 즉 **이미 `create_all` 로 만들어진 운영 DB 에 대해
autogenerate 된 증분** 이다(이름만 baseline).

**이것은 우연이 아니라 의도된 설계다** — `backend/app/main.py:318-321` 이 스스로 밝힌다:

> 신규 테이블 생성은 init_db() 의 create_all 로 보장된다 (위 init_db() 호출).

**공유 커널 관점의 파급(중요)**: 공유 Postgres 를 새로 프로비저닝할 때
**반드시 FLOW 앱을 한 번 기동(create_all)해야** 스키마가 완성된다.
`alembic upgrade head` 만으로는 절대 완성되지 않는다.
→ §14 의 "변형 B(단일 Postgres + 스키마 분리)" 채택 시 **프로비저닝 순서가
배포 스크립트의 필수 불변식** 이 된다.

| ID | 결정 | 선택지 |
|---|---|---|
| **D21** | 빈 DB 재현성 확보 여부 | ① 현행 유지(create_all 의존 문서화 + 프로비저닝 순서 강제) / ② 진짜 baseline 재생성 / ③ 마이그레이션 폐기·create_all 단일화 |

### 25.11 신규 결함 D22 — FLOW alembic 은 cwd 에 의존한다

| 구분 | `script_location` | cwd 의존 |
|---|---|---|
| **FLOW** `alembic.ini:6` | `backend/migrations` (**상대경로**) | **있음** — 저장소 루트에서만 동작 |
| **음악** `alembic.ini:8` | `%(here)s/migrations` (**절대경로**) | 없음 |

실측: 음악 backend 로 chdir 한 상태에서 FLOW 설정을 만들자

```
alembic.util.exc.CommandError: Path doesn't exist:
'C:\Desktop\korean-gospel-ai\ai-music-platform\backend\backend\migrations'
```

한 서버에서 두 서비스를 함께 운영하면 **배포 스크립트의 cwd 가 섞이기 쉽다**.
음악은 cwd 무관하지만 FLOW 는 깨진다. 우회책은 `script_location` 을 절대경로로
덮어쓰는 것(D5 하네스가 실제로 사용).

| ID | 결정 | 선택지 |
|---|---|---|
| **D22** | FLOW `script_location` 절대경로 전환 | `%(here)s/migrations` 로 변경 / 배포 스크립트 cwd 고정으로 유지 |

### 25.12 기존 기록 2건 정정

| 항목 | 정정 전 | 정정 후 (실측) |
|---|---|---|
| **X9 (create_all 폴백)** | §14: **음악** `app/db.py init_db()` 만 기록 | **양측 모두 무조건 호출.** 음악 `main.py:11-14`(lifespan) + **FLOW `main.py:310`**. 환경 분기(운영 비활성)는 **양측 모두 없다**. → X9 의 범위가 2배 |
| **T5.8 (원가-크레딧 조인)** | 공유 인스턴스 전제 하 정합 검증 가능으로 상정 | **조인이 정의되지 않는다.** 음악 `models.py:46` `cost_cny`(**CNY**) ↔ FLOW `compliance.py:76-78` `amount_krw`/`currency`(기본 **KRW**). **양측 저장소에 CNY↔KRW 환율 소스 0건.** `ccp_rate_card.currency` 는 `server_default="KRW"` 단일 통화(`models/ccp.py:102-104`)이고, `source_note` 의 "환율 기준일"(`models/ccp.py:77`)은 **사람이 적어 넣으라는 서술** 일 뿐 코드가 아니다 |

> **T5.8 의 교훈(중요)**: `ccp.py:77` 에 "환율" 이라는 낱말이 **실제로 등장** 한다.
> 원문 그대로 grep 하면 "환율 소스가 있다" 는 **거짓 양성** 이 나온다.
> 이 문서를 작성하는 과정에서 실제로 한 번 속았다.
> → **문자열 검색은 주석·독스트링을 제거한 실행 코드에 대해서만** 수행한다.

---

### 25.13 신규 결함 D23 — Plan Z 의 push 경로는 **존재하지 않는다** (2026-08-31)

§13 의 Plan Z 는 "음악 플랫폼이 심의 통과 곡을 FLOW `POST /admin/media` 로 push 하고
`asset_id` 를 `sync_records.flow_song_id` 에 저장한다" 를 전제로 한다.
**D1/D2 mocktest 구현 중 전수 실측한 결과, 이 경로는 한 줄도 구현되어 있지 않다.**

| 검색 | 대상 | 결과 |
|---|---|---|
| `/admin/media` | 음악 저장소 전체 `.py` | **0건** — 호출 자체가 없다 |
| `flow_song_id` | 음악 저장소 전체 `.py` | **2건** — `models.py:61`(컬럼 정의) + `migrations/versions/6e64ba2229a2_initial_schema.py:67` 뿐 |
| `flow_song_id` **대입/갱신** | AST 속성 참조 전수 | **0건** |

음악 `routers/flow.py:1-8` 의 독스트링이 스스로 밝힌다:

```
MVP 采用 Pull：本接口负责「准备」——校验归属/完成态、把音频回源香港 CDN、
写入 SyncRecord(synced)；APK 端用同一 JWT 调 GET /api/sync/flow 拉取自己的歌单。
以后若 FLOW APP 有独立后端并要实时推送，可在 _mark_synced 后加 Push 调用。
```

그리고 `models.py:61` 의 컬럼 주석은 **`# Push 模式（独立 FLOW 后端）预留`** — **예약** 이다.

**수신 측(FLOW) 계약은 실재한다** (`api/media.py:61-74`):

| 필드 | 기본값 |
|---|---|
| `file` (UploadFile, 필수) | — |
| `title` (필수) / `category` | `"worship"` |
| `artist` / `description` / `scripture_refs` | `None` |
| `order_index` / `active` / `publish_at` / `notify_mode` | `0` / `True` / `None` / `"immediate"` |
| `authorization` (Header) | `None` → `_check_admin(authorization)` (`api/media.py:80`) |

**규격 접합부는 호환된다** (T1.1a 통과):
FLOW `asset_id` = `String(40)`(`orm.py:600`), 실제값은 `str(uuid.uuid4())` = **36자**(`orm.py:39-40`).
음악 `flow_song_id` = `String(255)`(`models.py:61`). → 36 ≤ 255.

**결론**: 접합 규격은 맞으나 **연결 코드가 없다.** D23 은 결함이 아니라
**"계획이 아직 계획일 뿐임"을 코드로 확인한 것**이며, Plan Z 는 착수 전이다.

---

### 25.14 신규 결함 D24 — 음악 `sync_to_flow` 는 심의 상태를 **보지 않는다**

```python
# 음악 backend/app/routers/flow.py:64-68
song = db.get(Song, song_id)
if not song or song.user_id != user.id:
    raise HTTPException(status_code=404, detail="歌曲不存在")
if song.status != "completed":
    raise HTTPException(status_code=400, detail="歌曲尚未生成完成")
# ← moderation_status 검사 없음
```

**왜 위험한가** — 심의 결과는 `status` 와 **무관한 별도 필드**다:

| 경로 | 코드 | 결과 |
|---|---|---|
| 음성 기심사 탈락 | `routers/songs.py:159-164` | `moderation_status = "rejected"` — **`status` 는 completed 유지** |
| 관리자 수동 reject | `routers/admin.py:117-131` | `moderation_status` + `moderation_note` **만** 변경, `song.status` **미접촉** |
| 생성 완료 시 자동 승인 | `routers/songs.py:167-168` | `status == "completed" and pending` → **`"approved"` 자동 전환** |

→ **rejected 곡도 `status == "completed"` 이므로 `sync_to_flow` 를 그대로 통과한다.**

**대조**: `routers/songs.py:294` 는 발견 페이지(explore)를 `moderation_status == "approved"` 로
필터한다. **동일한 필터가 동기화 경로에만 없다.**

**Plan Z 함의**: push 경로(D23)가 구현될 때 심의 필터를 함께 넣지 않으면,
**관리자가 명시적으로 거부한 곡이 FLOW 무인증 카탈로그(§25.15 참조)에 실린다.**

---

### 25.15 신규 결함 D25 — **미발행 오디오가 무인증으로 스트리밍된다** (🔴 최우선)

Plan Z 와 무관하게 **현재 이미 성립하는 노출**이다.

```python
# backend/app/api/media.py:178-185
@public_router.get("/{asset_id}/stream")
def stream_media(asset_id: str):  # ← 인증 파라미터 자체가 없다
    result = media_service.get_file(asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="미디어 파일이 없습니다.")
    path, mime = result
    return FileResponse(str(path), media_type=mime, filename=path.name)
```

```python
# backend/app/services/media_service.py:226-234
def get_file(asset_id: str) -> tuple[Path, str] | None:
    asset = get_asset(asset_id)  # ← asset_id 단일 조회
    ...
    if not path.exists():
        return None
    return path, asset.mime_type  # ← active 미검사
```

**대조**: `list_public`(`media_service.py:195-206`) 은 `MediaAsset.active.is_(True)` 를 건다
(`:201-202`). 즉 **목록은 가리고, 파일은 연다.**

**실측 (2026-08-31, `TestClient`)**:

| 단계 | 결과 |
|---|---|
| `publish_at = +3일` 로 업로드 | `200`, `active=False`, `publish_at=2026-09-02…` |
| `GET /mobile/media` (공개 카탈로그) | 예약곡 **미노출** ✅ |
| `GET /mobile/media/{asset_id}/stream` (**헤더 없음**) | **200 OK, 2055 bytes 전문 수신** 🔴 |

→ **예약 발행 전(엠바고) 오디오가 `asset_id` 만 알면 누구나 내려받을 수 있다.**

**D26 과의 결합 리스크**: 아래 §25.16 대로 스케줄러가 기본 구성에서 죽어 있으므로,
운영자가 "예약 발행" 했다고 믿은 오디오는 **카탈로그에 영구 미노출**(운영자는 발행됐다고 오해)인 채
**이미 무인증 다운로드 가능** 한 상태로 남는다 — **"안 보이지만 누구나 가져갈 수 있는"** 기본 구성.

---

### 25.16 신규 결함 D26 — `media_publisher` 는 apscheduler 미설치 시 **조용히 죽는다**

**의존 선언 상태 (2026-08-31 실측)**:

| 파일 | 상태 |
|---|---|
| `requirements.txt:53-56` | `# ----- Push notifications (선택 — APK 푸시 알림 활성화 시 설치) -----` 아래 **`# apscheduler==3.10.4`** (주석) |
| `requirements-313-local.txt:55-58` | 동일 (주석) |
| `requirements-deploy.txt` | **없음** |
| `Dockerfile` / `Dockerfile.ui` / `docker-compose.prod.yml` / `docker-compose.local.yml` | **없음** |
| 실제 venv(`venv`, `venv313`) | **`ModuleNotFoundError`** |

**사용처**: `media_publisher.py:20-21`, `push_scheduler.py` (**2개 파일**).

**왜 "푸시만 안 된다"가 아닌가** — `media_publisher` 는 두 가지 일을 한다:

| 함수 | 역할 | 코드 |
|---|---|---|
| `_publish_due` | **예약 발행 활성화 플립** (`publish_at` 도래 → `active=True`) | `media_publisher.py:32-50` |
| `_notify_due` | 푸시 브로드캐스트 + `notified_at` 기록 | `media_publisher.py:53-103` |

→ apscheduler 가 없으면 **예약 발행(`publish_at`) 자체가 영구 미동작**이다.
(추가 검증: `_publish_due` 의 호출자는 `tick()` **하나뿐**이며, 모듈 레벨 직접 호출도 없다 —
`tests/test_colocation_d1_d2_plan_z.py::test_publish_due_is_only_invoked_by_scheduler` 로 고정.)

**왜 조용한가** — `main.py:198-203`:

```python
try:
    from .services.media_publisher import start_scheduler as _start_media_pub

    _start_media_pub()
except Exception as _mpub_err:
    log.warning("[STARTUP] 미디어 발행 스케줄러 시작 실패 (무시): %s", _mpub_err)
```

`except Exception` 이 `ImportError` 마저 삼키고 **경고 로그만 남긴 뒤 부팅은 성공**한다.
→ 헬스체크에도 안 잡힌다(`api/admin.py` `/admin/health` 는 스케줄러를 보지 않는다).

**결정 필요 (D26)**:
① apscheduler 를 **필수 의존으로 승격**(`requirements-deploy.txt` + Dockerfile 반영)할지,
② 예약 발행을 **스케줄러 없이도 동작하는 경로**(예: 카탈로그 조회 시 지연 평가)로 옮길지.
② 를 택하면 D25 의 "엠바고 우회" 노출 면이 함께 줄어든다.

---

### 25.17 신규 결함 D27 — 관리자 속도 제한이 **공존 토폴로지에서 자기 차단** 을 유발한다

**전제(사용자 확정)**: FLOW + 음악 플랫폼은 **홍콩 대용량 1대에 공존** 한다.
→ 음악 플랫폼이 FLOW 로 보내는 트래픽의 송신 IP 는 **단일 IP** 다.

| 항목 | 실측 |
|---|---|
| 제한 대상 경로 | `middleware/rate_limit.py:20` `_RATE_PREFIXES = ("/chat","/auth","/rag","/admin","/documents","/support")` — **`POST /admin/media` 포함** |
| 분당 한도 | `config.py:172` `rate_limit_per_minute = 60` → `rate_limit.py:15` `MAX_IP_RPM = 60` |
| 시간 한도 | `rate_limit.py:16` `MAX_IP_RPH = 600` |
| 초과 시 | `rate_limit.py:185-186` **`_blocked_ips[ip] = now + 15분`** (IP 단위) |
| 상태 저장소 | `rate_limit.py:22-23` `_buckets` / `_blocked_ips` — **모듈 전역 dict** |
| Redis 미설정 시 | `rate_limit.py:105-114` 인메모리 폴백(단일 프로세스 한정) |

**Plan Z 함의**: 카탈로그 60곡을 일괄 push 하면 **분당 예산을 소진하고 15분 차단** 된다.
`api/media.py:61` 은 **단건 업로드** 만 지원하므로 곡 1곡 = 요청 1회다.
**재시도 로직이 있으면 차단이 오히려 연장된다.**

**테스트 스위트에서도 실제로 발생했다** (SC-23, 아래 #26 참조):
`TestClient` 의 IP 는 항상 `"testclient"` 단일 값이므로, D1/D2 mocktest(200+ 회 `/admin/media`)
가 이후 **모든** `/admin*` · `/support*` 테스트를 429 로 죽였다.

---

### 25.18 신규 결함 D28 — 홍콩 검색 캐시 키가 **한국 금고 유래 속성** 을 품는다 (2026-08-31)

**전제(§21.3)**: 분할 후 홍콩에는 **가명키(`subscriber_id`) + 콘텐츠** 만 남는다.
직접 식별자는 한국 금고로 이전된다.

**실측 체인** (전부 `file:line`, AST + 소스 대조로 확인):

| 단계 | 위치 | 내용 |
|---|---|---|
| ① 프로필 조립 | `services/subscriber_service.py:85,90,91,96` | 프로필 dict 를 **`Subscriber` 행에서 직접** 생성 — `salvation_status`(85) · `assume_saved`(90) · `darakbang_role`(91) · `darakbang_verified`(96) |
| ② 검색 엔진 전달 | `services/rag_engine.py:171-178` | `await self._search_engine.search(..., profile=profile)` |
| ③ 재전달 | `services/search_v4.py:358,368` | `profile: dict \| None = None`(358) → `profile=profile`(368) |
| ④ 캐시 키 구성 | `services/retriever.py:216` | ~~`cache_profile = (salvation_status, darakbang_role, is_darakbang_member, darakbang_verified, assume_saved)`~~ **(정정 2026-09-01) 실제 코드 = `cache_profile = (profile.get("subscriber_id"),)`** — 가명키(`subscriber_id`) **1개만**. 5속성 버전은 현재 코드에 없음(D28 코드 해결, `retriever.py:205-215` `[SEC] D28 / §25.18 / D16` 주석 참조) |
| ⑤ 키에 포함 | `services/retriever.py:217-226` | `cache_key = (query, top_k, rerank_top_n, dw, sw, **cache_profile**, dense_query, sparse_query)` |
| ⑥ 저장소 | `services/retriever.py:175` | `self._cache: OrderedDict[tuple, tuple[list[RetrievedItem], float]]` |
| ⑦ 소유자 수명 | `services/retriever.py:49` | `_retriever_cache` **모듈 싱글턴** → **프로세스 수명** |
| ⑧ TTL | `services/retriever.py:155` | `_CACHE_TTL_SEC = 1800` (**30분**) |

> **⚠ 정정 (2026-09-01, 23차 · 무환각 재실측): D28 은 코드에서 이미 해결되었다.**
> 실측 재확인 — `retriever.py:216` 의 실제 코드는 `cache_profile = (profile.get("subscriber_id"),)`
> (**가명키 `subscriber_id` 1개만**)이며, 5개 금고 유래 PII 속성(`salvation_status`·`darakbang_role`·
> `is_darakbang_member`·`darakbang_verified`·`assume_saved`)은 **포함되지 않는다**. 이는
> `retriever.py:205-215` 의 `[SEC] D28 / §25.18 / D16` 주석이 명시적으로 금지한 결과다.
> → 본 절의 핵심 주장("§21.3 위반 — 캐시 키에 PII 5속성 30분 상주")은 **현재 코드에는 해당하지 않는다**.
> **D28 상태: 코드 해결됨**(§26.3 §1744 도 "해결됨"으로 정정).
> 잔여 설계 과제(별도 추적): ①→③ 단계에서 `Subscriber` 행 유래 프로필 dict(PII 5속성 보유)가
> 검색 함수 **인자**로는 여전히 흐른다. 핫패스 "토큰에 서명된 프로필"(설계서:1066) 원칙은 미구현이며,
> 이는 캐시 키 위반(D28 본체)과는 별개의 항목이다. 증거: `tests/test_colocation_d3_pii_split.py`
> `test_t3_2e`/`test_t3_2f`/`test_t3_10a` docstring 이 모두 "**D28 해결됨**" 이고,
> `test_t3_10a`(`:950`) 는 `profile_attrs == ("subscriber_id",)` 를 단언.

**왜 결함인가 (원문 — 2026-08-31 작성 당시의 전제; 현재는 코드 해결로 소급 적용됨)**: `Subscriber` 는 §21.3 에서 **한국 금고 귀속** 이다. 따라서 위 5개 속성은
**금고 유래** 다. 그것이 홍콩 프로세스의 **메모리 캐시 키** 로 30분간 상주하면:

1. **§21.3 원칙 위반** — 홍콩에 "가명키 + 콘텐츠"만 남는다는 규정이 **식별자 컬럼을
   옮기는 것만으로는 달성되지 않는다.** 속성이 **캐시 키·로그·메트릭** 으로 스며들면
   분할의 의미가 사라진다.
2. **FU10(§22.2) 표면 확대** — `salvation_status`(구원 상태)는 **종교적 민감 속성** 이다.
   `age_group`·`gender` 같은 일반 준식별자와 성격이 다르며, 재식별 기준선(D16)을
   정할 때 **반드시 포함** 해야 한다.
3. **설계 의도와 반대** — 설계서:1066 은 핫패스에서 **"토큰에 서명된 프로필"** 을 쓰도록
   규정한다. 즉 프로필은 **요청마다 토큰으로 오고 서버에 남지 않아야** 하는데,
   현재는 **서버 캐시 키에 남는** 정반대 구조다.

**부수 실측 — 캐시 미스는 현재 `fail-open` 이다**: `retriever.py:224-236`
히트면 `return`(:234), 미스면 그대로 빠져나와 Stage 1 Coarse Recall(:240~) 을 수행한다.
**차단 경로가 없다.** 이는 D14(캐시 미스 동작) 결정의 근거 자료다
(단, Y4 캐시의 미스 동작과 동일시하면 안 된다 — Y4 는 미구축).

**테스트**: `tests/test_colocation_d3_pii_split.py`
`test_t3_2e_hk_cache_key_carries_vault_derived_attributes`(현행 고정, passed) +
`test_t3_2f_hk_cache_key_carries_pseudonym_only`(**목표 게이트**, xfail strict).

> **본 결함의 성격 — "분할하면 해결될 줄 알았던 것이 분할해도 남는다."**
> D23~D27 이 "계획의 전제가 코드에 없었다" 류라면, D28 은 그 반대다.
> **코드에 이미 너무 많이 있어서** 분할 설계가 놓친 경우다.
> → §21.3 을 "테이블 이전" 이 아니라 **"가명화 경계의 재설계"** 로 읽어야 한다.

---

# 제5부 · 인용 전수 검증 + 자가정정 #7~#11 (2026-08-31)

> 수행 계기: §5.4 의 `compose:306-308` 인용이 **실재하지 않는 것**으로 밝혀졌다.
> "한 건이면 더 있을 수 있다"는 원칙 아래, 본 문서의 **모든 `file:line` 인용을 전수 검증**했다.

## 26.1 검증 방법과 결과

**방법**: `docs/_verify_citations.py`(→ `scripts/verify_citations.py`) 로 본 문서의 모든 `file:line` 인용을 추출해
① 파일 실재 확인, ② 행번호 범위 초과 여부, ③ 양측 동명 파일(AMBIG) 판별을 자동 수행.
추가로 **행번호 존재만으로는 불충분**하므로 주요 항목은 **내용까지 대조**하는 의미 검증을 병행했다.

| 단계 | 총 인용 | 통과 | 환각 |
|---|---|---|---|
| 1차 검증(원문) | 157 | 156 | **1** |
| 정정 후 재검증 | 168 | 167 | **0** (폐기 인용 1건은 취소선 처리로 검증 제외) |

### 검증 결과 — 환각 1건, 문서 **2곳**에 중복 사용

| 문서 위치 | 인용 | 실제 | 판정 |
|---|---|---|---|
| §5.4 G-Consent | `compose:306-308` | `docker-compose.prod.yml` 은 **153줄**, 전 compose 파일 `consent`/`PIPL` **0건** | **존재하지 않는 인용** → `compliance.py:64-80` 로 정정 완료 |
| §2.1 컴플라이언스 | ~~`docker-compose.prod.yml:306-308`~~ | 동일 — 같은 환각 인용이 **별도 표에도 재사용**됨 | → `models/compliance.py:64, 84, 94,108,119` 로 정정 완료 |

> 같은 환각 인용 하나가 서로 다른 두 표에 실려 있었다. 1건을 잡은 뒤에도 **문자열 전체 검색으로 재사용처를 확인**했기 때문에 두 번째를 찾을 수 있었다.

### 의미 검증 통과 (내용까지 대조)

| 인용 | 대조 결과 |
|---|---|
| 음악 `config.py:59` | 실제 `admin_token: str = ""` ✅ |
| 음악 `main.py:20-26` | 실제 CORSMiddleware 등록부(20행 `add_middleware`, 22행 `allow_origins`) ✅ |
| 음악 frontend `api.ts:2` | 실제 `API_BASE = ... "http://localhost:8000"` ✅ |
| FLOW `main.py:3` | docstring `uvicorn ... --port 8000` ✅ — 🟠3 포트 충돌 사실 유지 |
| 양측 `migrations/env.py` | **FLOW(92줄)·음악(69줄) 모두 `version_table`·`include_object` 부재** → **X3·X4 함정 그대로 성립** ✅ |

> ⚠ **검증 중 스스로 오판한 건**: `main.py:3` 을 음악 측으로 오독해 "환각"으로 의심했으나, 실제로는 **FLOW 측 docstring** 이었고 인용은 정확했다. 음악의 8000 포트는 코드 상수가 아니라 `README.md:53`·`PROMPT.md:88` 의 **실행 명령**에 있다. 동명 파일은 반드시 문맥으로 귀속을 판정해야 한다.

## 26.2 자가정정 #7~#26

| # | 항목 | 정정 내용 |
|---|---|---|
| **#7** | **G-Consent 인용 환각** | `compose:306-308` → **`compliance.py:64-80` + `orm.py:197-198, 241`**. (§5.4 본문 정정 완료) |
| **#8** | **F1 양측 동일 취급** | 음악 = 모듈 전역 `defaultdict`(`ratelimit.py:13`) 진짜 비공유. **FLOW = Redis 우선 + 인메모리 폴백**(`middleware/rate_limit.py:100-102` / `:116-134`). (§4 F1 행 정정 완료) |
| **#9** | **F3 범위 과대** | 저장은 평문이 맞으나 **GET 응답은 이미 마스킹 구현**(`schemas.py:150` `api_key_set: bool`, `admin.py:45`). 갭은 "at-rest 암호화 부재"로 한정. (§4 F3 행 정정 완료) |
| **#10** | **jti 블랙리스트** | §11.9 완화란의 기재는 **설계 제안**이며(환각 아님), **구현은 0건**(전역 grep). → 완화란에 미구축 명시 완료 |
| **#11** | **PII 인벤토리** | ① **`consent_record.ip`(`compliance.py:58`) 누락** 추가(§21.1 반영). ② 행번호 2건 정정: `emotional_state` 195→**192**, `last_emotion` 207→**208** |
| **#12** | **PII 탐지 방법 결함** | 필드명 키워드 판정을 폐기. 겉보기 무해한 이름의 원문 파생 컬럼 3건 누락: `canonical_text`(`trending_service.py:218,256`), `Payment.raw`(`api/payment.py:135`), `raw_variants`(`insight_engine.py:197-198`). → **3축(이름/크기/구조) 탐지**로 교체. (§25.8 신설) |
| **#13** | **trace 입력 지점 과소 집계** | 계획서 "3개소" → **AST 전수 스캔 13개 지점**. `chat.py` 7곳 + `chat_pipeline.py` 6곳. (계획서 §4 D4 정정 완료) |
| **#14** | **spy 주입법 무효** | `connections.py:459` `langfuse = staticmethod(langfuse)` 가 정의 시점에 바인딩 → **모듈 속성 패치는 무효**. 기존 `tests/test_tracing.py:176-178` 은 **환경 우연**으로 통과 중이었고, **Langfuse 분기를 검증한 테스트는 없었다**. (계획서 §4 D4 정정 완료) |
| **#15** | **출국 경로 ③ 발견** | §25 가 ①Langfuse ②LLM 만 다뤘으나 **③스케줄러 원문 재전송** 존재: `trending_scheduler.py:29`(60초) → `:57-59` → `insight_engine.py:196,347,489` → `:117-122`. **사용자 요청 없이 저장 원문을 재전송** → 요청 단위 동의 앵커가 없다. (§25.7~25.9 신설, Y13/Y14/D20 추가) |

> **#12~#15 는 성격이 다르다.** #7~#11 이 "문서의 사실 확인 오류"라면,
> #12~#15 는 **테스트를 실제로 만들면서 비로소 드러난 것**들이다.
> 특히 #12 는 **"안전해 보이는 이름 = 안전하다"는 가정 자체가 환각의 원천** 이었음을,
> #15 는 **아무리 정적 분석을 해도 실행 경로를 따라가지 않으면 출구를 놓친다** 는 것을 보여준다.
> → 정적 감사(1~4차 라운드)의 **방법론적 한계**로 기록한다.

**#16~#21 — D5(공유 커널) 구현 중 발견 (2026-08-31 5차).**

| # | 항목 | 정정 내용 |
|---|---|---|
| **#16** | **D21 — 빈 DB 재현 불가 (신규 결함)** | FLOW ORM **32개** 중 마이그레이션이 만드는 것은 **11개** 뿐. `c2fbb2654cf3_baseline_all_tables.py` 는 create_table 1개 + `batch_alter_table` **8개** 로 **가짜 baseline**(기존 DB 대상 증분). 빈 DB `upgrade head` 는 `no such table: document_version` 으로 죽는다. **의도된 설계** 임을 `main.py:318-321` 이 자기 고백한다. → **공유 Postgres 신규 프로비저닝 시 FLOW 앱 1회 기동이 필수 불변식**. (§25.10 신설, D21 추가) |
| **#17** | **X9 범위 과소** | §14 는 X9 를 **음악만** 으로 기록했으나 **FLOW 도 동일**: `main.py:310` 에서 환경 분기 없이 `init_db()` 호출. 음악은 `main.py:11-14`(lifespan). → X9 대상이 2배. (§25.12 정정, T5.6 게이트 수정) |
| **#18** | **T5.8 조인 정의 불가** | `cost_cny`(CNY) ↔ `amount_krw`(KRW) 에 **환율 소스 0건**. `ccp_rate_card.currency` 는 `server_default="KRW"` 단일 통화. (§25.12 정정, T5.8 을 "조인 가능"이 아닌 "환율 근거 부재" 게이트로 변경) |
| **#19** | **D22 — FLOW alembic cwd 의존 (신규 결함)** | FLOW `alembic.ini:6` `script_location = backend/migrations`(상대) vs 음악 `alembic.ini:8` `%(here)s/migrations`(절대). 다른 cwd 에서 FLOW alembic 은 `.../backend/backend/migrations` 를 찾아 죽는다. (§25.11 신설, D22 추가) |
| **#20** | **`sys.modules["app"]` 오염 — 측정 대상 자체가 바뀌는 사고** | `tests/test_salvation_regression.py:29` 가 `backend` 를 `sys.path` 에 넣고 `:36` 에서 `from app.db import ...` 를 수행 → 전체 회귀 시 `app` = **FLOW 의 backend/app**. 음악 `env.py` 의 `from app.db import Base` 가 **캐시된 FLOW Base** 를 반환해, autogenerate 가 **음악 테이블 6개를 drop** 하는 **현실에 없는 결과** 를 냈다. 단독 실행에선 우연히 맞았으므로 **더 위험** 했다. → 픽스처에서 `app` 패키지를 격리·복원하고, **하네스 무결성 테스트 3종** 을 신설 |
| **#21** | **`Base.metadata` 는 전역 가변 상태** | `tests/test_compliance_security.py:55` 의 `class _TempEnc(Base)`(`tmp_enc_at_rest`)가 메타데이터에 영구 합류 → 단독 실행 32개 vs 전체 회귀 **33개**. → 소유 모듈(`backend.app.models`) 기준 필터링 + `create_all` 대상 한정으로 실행 순서 독립성 확보 |

> **#20·#21 의 교훈(#12 와 같은 계열)**: 테스트가 초록불이라는 것은
> **"옳은 대상을 측정했다"는 뜻이 아니다.** 모듈 캐시·전역 메타데이터처럼
> 실행 순서에 따라 내용이 바뀌는 대상을 측정할 때는
> **"내가 지금 무엇을 측정하고 있는가"를 검증하는 테스트** 를 반드시 함께 둬야 한다.
> D5 의 `TestHarnessIntegrity` 3종이 그 장치다.

**#22~#26 — D1/D2(Plan Z) 구현 중 발견 (2026-08-31 6차).**

| # | 항목 | 정정 내용 |
|---|---|---|
| **#22** | **D23 — Plan Z 의 push 경로는 없다 (신규)** | 음악 저장소 전체에 `/admin/media` 호출 **0건**, `flow_song_id` 는 `models.py:61` 의 **예약 필드**(주석: `Push 模式（独立 FLOW 后端）预留`) + 마이그레이션 1건뿐. `routers/flow.py:1-8` 이 **Pull 모델** 임을 자기 고백. 수신 계약(`api/media.py:61-74`)과 규격(`asset_id` 36자 ≤ `flow_song_id` 255자)은 실재하나 **연결 코드가 없다**. (§25.13 신설, T1.1b 게이트) |
| **#23** | **D24 — 음악 `sync_to_flow` 심의 미검사 (신규)** | `routers/flow.py:64-68` 은 소유권(404) + `status != "completed"`(400) 만 본다. `moderation_status` **미검사**. 그런데 `admin.py:117-131` 의 수동 reject 는 **`song.status` 를 건드리지 않으므로** rejected 곡도 `status == "completed"` → **그대로 동기화**. `songs.py:294` explore 가 approved 만 노출하는 것과 대조. (§25.14 신설, T1.9c 게이트) |
| **#24** | **D25 — 미발행 오디오 무인증 스트리밍 (신규, 🔴 최우선)** | `api/media.py:178-185` `stream_media(asset_id)` 에 **인증 파라미터 자체가 없고**, `media_service.py:226-234` `get_file` 은 `active` 를 필터하지 않는다(`list_public` 의 `:201-202` 와 대조). 실측: `publish_at=+3일` 업로드 → 카탈로그 미노출이나 **`GET /mobile/media/{id}/stream` 무헤더 → 200, 전문 수신**. **Plan Z 와 무관한 현행 노출.** (§25.15 신설, T1.9b 게이트) |
| **#25** | **D26 — media_publisher 는 apscheduler 없이 조용히 죽는다 (신규)** | `apscheduler==3.10.4` 가 `requirements.txt:55`·`requirements-313-local.txt:57` 에서 **주석 처리**(선택 의존), 배포 매니페스트 어디에도 없음. 그런데 `media_publisher` 는 푸시만 하는 게 아니라 `_publish_due`(`:32-50`)로 **예약 발행 활성화** 를 담당 → **예약 발행이 영구 미동작**. `main.py:198-203` 의 `except Exception` 이 `ImportError` 마저 삼켜 **경고 로그만 남고 부팅은 성공**. `_publish_due` 호출자는 `tick()` 하나뿐(테스트로 고정). (§25.16 신설, D26 결정 대기) |
| **#26** | **D27 — 관리자 속도 제한 자기 차단 (신규)** | `rate_limit.py:20` 이 `/admin` 을 제한 → `POST /admin/media` 도 대상. `MAX_IP_RPM=60`(`:15`, `config.py:172`), 초과 시 **IP 단위 15분 차단**(`:185-186`). **양 서비스 홍콩 1대 공존 → 송신 IP 단일** 이므로 카탈로그 60곡 일괄 push 는 **자기 차단**. (§25.17 신설) |

> **#22~#26 의 공통 성격 (#12~#15·#20·#21 계열의 연장)**:
> 이 다섯 건은 **모두 "계획을 코드로 검증하려다 발견된" 것** 이다.
> 특히 #22 는 **계획서가 전제로 삼은 경로가 한 줄도 없었다** 는 뜻이고,
> #24 는 **"목록은 가리고 파일은 연다"** 는 가장 단순한 형태의 노출이며,
> #25 는 **선택 의존 하나가 핵심 기능(예약 발행)을 조용히 무효화** 한 사례다.
> → 정적 감사만으로는 못 잡고, **실행 가능한 계약 검증을 만들어야만 드러나는** 부류다.

**#27 — D3(PII 분할) 구현 중 발견 (2026-08-31 7차).**

| # | 항목 | 정정 내용 |
|---|---|---|
| **#27** | **D28 — 홍콩 검색 캐시 키가 한국 금고 유래 속성을 품는다 (신규)** | §21.3 은 분할 후 홍콩에 **가명키(`subscriber_id`) + 콘텐츠** 만 남는다고 규정한다. 그러나 실측: `subscriber_service.py:85,90,91,96` 이 프로필 dict 를 `Subscriber` **행에서 직접** 조립(`salvation_status`·`assume_saved`·`darakbang_role`·`darakbang_verified`) → `rag_engine.py:171-178` → `search_v4.py:358,368` → `retriever.py:216` 이 5속성으로 `cache_profile` 을 구성 → `:217-226` `cache_key` 튜플에 **요소로 포함**. 저장소는 `self._cache`(`:175`, `OrderedDict`)이고 소유자는 **모듈 싱글턴** `_retriever_cache`(`:49`) → **프로세스 수명**, TTL **1800초**(`:155`). 즉 **분할 후에도 홍콩 프로세스 메모리에 구독자 종교상태 조합이 30분간 상주** 한다. §21.3 위반이며 **FU10(§22.2 준식별 재식별) 의 표면을 넓힌다.** §21.6 설계서:1066 은 핫패스에서 **"토큰에 서명된 프로필"** 을 쓰도록 규정하므로, 프로필이 **서버측 캐시 키에 남는 것 자체가 설계 의도의 반대** 다. (T3.2e 현행 고정 + **T3.2f xfail(strict) 게이트**) <br><br>**정정 (2026-09-01, 23차 · 무환각 재실측)**: `retriever.py:216` 은 현재 `cache_profile = (profile.get("subscriber_id"),)`(가명키 1개) 이며 5속성은 **미포함**(`:205-215` `[SEC] D28 / §25.18 / D16` 주석). 따라서 "캐시 키에 PII 5속성 30분 상주" 주장은 **현재 코드에 해당 없음** → **#27/D28 코드 해결됨**. 본문은 2026-08-31 작성 당시 전제 기록으로 유지. |

> **#27 의 성격 — "분할하면 해결될 줄 알았던 것이 분할해도 남는다."**
> #22~#26 이 "계획의 전제가 코드에 없었다" 류라면, #27 은 그 반대다.
> **코드에 이미 너무 많이 있어서** 분할 설계가 놓친 경우다.
> 가명키만 남긴다는 원칙은 **식별자 컬럼** 을 옮기는 것으로 달성되지만,
> 프로필 **속성이 캐시 키·로그·메트릭 스며든 형태로 남으면** 원칙은 무의미해진다.
> → §21.3 을 "테이블 분할" 로만 읽지 말고 **"가명화 경계의 재설계"** 로 읽어야 한다
> (작업 메모리의 확정 사항과 동일한 결론).

### 26.2.1 하네스 결함 (SC-22 · SC-23 · SC-24 · SC-26 · SC-27)

SC-18~SC-21 에 이어 D1/D2·D3 구현 중 **테스트 하네스 자체의 결함** 5건을 더 찾았다.
(SC 번호는 **하네스 결함 전용** 이다 — 제품 결함은 §26.2 의 #22~#27 을 쓴다.)

| # | 결함 | 실측 | 대응 |
|---|---|---|---|
| **SC-22** | **`sessionmaker()` 를 `with` 로 쓰면 commit 이 아니라 close(롤백)** | `tests/conftest.py:45-57` 의 `iso_db` 는 **sessionmaker** 를 주지만 `get_session`(`db.py:136-147`) 처럼 commit 해 주지 않는다. 직접 삽입 시 `with session_factory() as s:` 만 쓰면 **행이 저장되지 않는다**. 그 결과 "대상이 0건" 이 되어 `_notify_due()["count"] == 0` 이 **거짓 통과** 했다(T1.5d) | 삽입 헬퍼 `_insert_media_asset` 에서 `s.commit()` 명시 + **삽입 후 재조회로 존재를 먼저 단언**. 모든 호출부에 **사전 조건 검증** 추가 |
| **SC-23** | **전역 속도 제한이 테스트 파일 간 오염을 일으킨다** | `rate_limit.py:22-23` 의 `_buckets`/`_blocked_ips` 는 **모듈 전역**. `TestClient` IP 는 항상 `"testclient"`. D1/D2(200+ 회 `/admin/media`) 가 이후 `test_content_admin_api.py`(9건)·`test_support_api.py`(6건)·`test_subscriber_pagination.py`(3건) 을 **모두 429 로 죽였다**. 본 모듈 제외 동일 조건 실행은 전부 통과 | autouse 픽스처 `_rate_limit_isolated`: `rate_limit_enabled=False` + 전역 버킷 저장/초기화/복원. teardown 에서 **차단을 남기지 않음** 을 자체 테스트로 고정 |

| **SC-24** | **`MUSIC_PLATFORM_REPO` 미설정 — 측정이 아예 안 일어나는데도 초록불** | 네 공존 테스트 파일이 음악 저장소 경로를 이 환경변수로 받는다(`tests/test_colocation_l0_guards.py:72`, `tests/test_colocation_d5_shared_kernel.py:189`, `tests/test_colocation_d1_d2_plan_z.py:200`, `tests/test_colocation_d3_pii_split.py:106`). **미설정 시 전체 회귀 `exit=0` 이면서 838 passed / 33 skipped / 16 xfailed**. 설정 시 853 / 4 / 30. 차이 **29건 = xfail 게이트 14 + 일반 15**. **총 테스트 수는 887 로 양쪽이 같아서, 총수만 보면 차이를 알 수 없다** | 회귀 명령에 환경변수 고정 + `--junit-xml` 로 **skip 사유를 반드시 확인**. CI env 에 누락되면 **검증 없이 통과** 한다 |
| **SC-26** | **문자열 검색은 거짓 양성만이 아니라 **위험 은폐** 도 만든다** | D3 의 T3.10a 를 `"profile" in text and "cache" in text` 로 작성 → 3개 파일 히트. 처음엔 "또 거짓 양성(SC-18b)" 으로 보고 **측정법만 고치려 했으나**, `retriever.py` 는 **정말로 프로필 속성을 캐시 키에 넣고 있었다.** 잘못된 방법이 우연히 **맞는 방향** 을 가리켰고, 그 결과를 의심하지 않았다면 **실존 결함(D28/#27) 을 영구히 놓쳤을** 것이다. 반대편 T3.8b 는 진짜 거짓 양성이었다(`models/orm.py:103` `cascade="all, delete-orphan"` = ORM 캐스케이드, `services/extraction_service.py:273` `orphan_josa_re` = 한국어 조사 정규식) | 낱말 검색 폐기 → **정의 심볼 단위**(`_def_names`, 스캔 실측 157 파일·0 히트) 및 **AST 구조 단위**(`_retriever_cache_shape`·`_retrieve_miss_path`) 로 전면 교체. 심볼 스캐너 자체를 테스트로 고정 |
| **SC-27** | **음악 플랫폼은 SQLAlchemy 1.x 스타일 — 2.0 전용 파서는 결과가 빈다** | ORM 파서 `_orm_columns()` 가 FLOW 의 2.0 선언(`Mapped[T] = mapped_column(...)`)만 인식. 음악 `models.py:7-13` 은 1.x(`email = Column(String(255), ...)`) → 파싱 결과 `{}` → `assert "User" in cols` 실패. **이를 "음악에 모델이 없다"로 읽으면 거짓 결론** 이며, 음악측 PII 검증 전체가 무력화된다 | `_decl_field()` 신설로 `AnnAssign + mapped_column` **와** `Assign + Column` 을 모두 인식(혼합 선언 포함). 하네스 자기검증 2종(`test_orm_parser_reads_sqlalchemy_1x_style`·`test_orm_parser_reads_both_styles_in_one_class`)으로 고정 |

> **SC-24 의 성격 — SC-20 의 변주다.** SC-20 이 "측정 대상이 바뀐" 사고라면,
> SC-24 는 **"측정이 아예 일어나지 않았는데도 초록불"** 인 사고다.
> → **exit code 는 "실패 없음"만 말하고 "검증했음"은 말하지 않는다.**
> 따라서 **skip/xfail 건수 자체를 회귀 지표로 추적** 해야 한다
> (예: 본 스위트는 `MUSIC_PLATFORM_REPO` 설정 시 **skip 4 / xfail 30** 이 기대값).
> 이 값이 흔들리면 "통과" 여부와 무관하게 **게이트가 무력화된 것** 이다.
> D3 추가 후 **미설정 시에도 총 테스트 수는 887 로 동일** 하므로,
> 총수가 아니라 **skip/xfail 의 분리 집계** 만이 이를 드러낸다.

> **SC-26 의 교훈 — 거짓 양성보다 위험한 것은 "우연히 맞은 틀린 방법"이다.**
> SC-18b·SC-20 은 **틀린 결과** 를 냈기에 즉시 드러났다. SC-26 은 틀린 방법이
> **그럴듯한 결과** 를 냈고, 그 결과를 신뢰했다면 **실존 결함을 놓쳤을** 것이다.
> → **부정 단언의 측정법을 바꿨을 때 결론이 유지되는지 반드시 재검증** 한다.
> 유지되면 결론이 강화되고(T3.8b), 뒤집히면 결함을 찾은 것이다(D28). **둘 다 값이 있다.**

> **SC-22 의 교훈이 특히 중요하다.** 실패가 아니라 **통과** 로 드러났기 때문이다.
> "기대한 대상이 아무것도 없어서" 조건을 만족한 것이며,
> 이는 **거짓 음성보다 발견하기 어렵다.**
> → **부정 단언("아무 일도 일어나지 않았다")을 할 때는
>   반드시 "대상이 존재한다"를 먼저 단언** 해야 한다.

## 26.3 남은 미완료 (사용자 결정·입력 대기)

| 구분 | 항목 | 상태 |
|---|---|---|
| 결정 필요 | **D1~D19** 전 항목 | 사용자 미결. 이 중 **D14·D16·D18·D19** 는 테스트 단언값을 직접 좌우(최우선) |
| 결정 필요 | **D21** 빈 DB 재현성 / **D22** FLOW `script_location` 절대경로 | 2026-08-31 D5 구현 중 신규 발견(§25.10·§25.11). **D21 은 공유 Postgres 프로비저닝 절차를 직접 좌우** 하므로 변형 B 채택 전 결론 필요 |
| 결정 필요 | **D23** Plan Z push 미구축 (착수 여부) | §25.13. 계획서 §13 의 전제 경로가 **한 줄도 없다**. 수신 계약·규격은 실재하므로 (a) push 구현 (b) Pull 모델 유지 중 택일 필요 |
| 결정 필요 | **D24** `sync_to_flow` 심의 필터 부재 | §25.14. **현행 결함**(관리자 거부 곡이 그대로 동기화). D23 착수 시 **반드시 함께** 해결 |
| 결정 필요 | **D25** 미발행 오디오 무인증 스트리밍 | §25.15. 🔴 **현행 노출·최우선**. `stream_media` 에 `active` 필터 + 인증 방식을 정해야 함(공개 카탈로그 성격상 인증보다 **서명 URL / 만료 토큰** 이 현실적) |
| 결정 필요 | **D26** apscheduler 필수화 vs 예약 발행 경로 이전 | §25.16. 현재 기본 구성에서는 **예약 발행이 영구 미동작** |
| 결정 필요 | **D27** 관리자 속도 제한(60 RPM) 자기 차단 | §25.17. 공존 1대 → 단일 송신 IP. 일괄 push 시 **15분 차단**. 대량 동기화용 별도 경로/예외 화이트리스트 필요 |
| **해결됨(코드)** | **D28** 홍콩 검색 캐시 키의 금고 유래 속성 | §26.2 **#27**. 2026-09-01 재실측 — `retriever.py:216` 실제 코드 = `cache_profile = (profile.get("subscriber_id"),)`(가명키 1개), 5속성 미포함(`:205-215` `[SEC]` 주석). **§21.3 캐시 키 위반은 해소됨.** 잔여 설계 과제(별도): ①→③에서 `Subscriber` 유래 프로필 dict(PII 5속성)가 검색 함수 **인자**로 흐르며, 핫패스 "토큰에 서명된 프로필"(설계서:1066) 원칙은 미구현 — 이는 D28 본체(캐시 키)와는 별개 항목. **D16(홍콩 잔존 준식별 범위) 과 함께 추적 유지** — 장기 잔여(프로필 dict 인자 흐름·"토큰에 서명된 프로필" 미구현)의 plan-first 방안은 별도 문서 docs/PLAN_PII_HOTPATH_RESIDUAL_2026-09-01.md 에서 추적(Phase 1~3 완료 시 비로소 근본 해결). **2026-09-01 23차-4 코드층 구현완료** — `profile_token.py:42/:100/:122` 신규·`db.py:212/:229` 이중세션·`orm.py:1065 SubscriberSensitive`·`subscriber_service.py:299/:322` 토큰 도우미·`tests/test_profile_token.py` 9 passed·`verify_citations` 261 OK/37-0. D-A(한국 DB 물리+`Subscriber` 민감컬럼 물리제거) 만 잔류. |
| 입력 대기 | 한국/홍콩 **서버 스펙** 및 **RTT** | 미확정 → 정량 결론 전면 보류(§20.4 방침 유지) |
| 확인 필요 | 음악 플랫폼 경로 **2개 존재** | `korean-gospel-ai/ai-music-platform`(**활성** — `MOCK_TEST.md` 66 passed/3 skipped 일치) / `b86db9b7-.../ai-music-platform`(사본 추정) |

## 26.4 검증 도구

`scripts/verify_citations.py` — 본 문서(및 타 문서)의 `file:line` 인용을 전수 검증하는 재실행 가능한 스크립트.
설계서 수정 시마다 재실행해 **신규 인용의 환각을 사전 차단**한다.

```
python scripts/verify_citations.py
```

| 회차 | 일시 | 인용 수 | OK | 환각(NOTFOUND) | 비고 |
|---|---|---|---|---|---|
| 1~5 | 2026-08-31 | 210 | 209 | **1** (`compose:306-308`) | → SC-7, 설계서 §5.4 정정 |
| **6** | 2026-08-31 | **243** | **243** | **0** | D1/D2(§25.13~25.17, §26.2 #22~#26, §26.2.1 SC-22~SC-24) 신규 인용 33건 전수 통과 |
| **7** | 2026-08-31 | **252** | **252** | **0** | D3(§26.2 #27, §26.2.1 SC-26·SC-27, §26.3 D28) 신규 인용 9건 전수 통과 |

> **7차 보충 — 검증기의 사각을 직접 메웠다.**
> `verify_citations.py` 는 ① **설계서만** 훑고(계획서는 대상이 아님) ② **백틱으로
> 감싼 `파일명:행` 만** 인식한다. 따라서 7차에서 추가한 인용 중
> `` `:175` `` 처럼 **파일명 없는 축약 표기** 와 **계획서 §13 의 인용** 은
> 검증 대상이 아니었다. → 별도 스크립트로 **행 번호 + 그 행의 내용** 을
> 함께 대조해 **36건 전수 일치(불일치 0)** 를 확인했다
> (`retriever.py` 49·155·175·207·214·234·236·240, `subscriber_service.py` 45·50·51·56,
> `rag_engine.py` 171·178, `search_v4.py` 358·368, `orm.py` 103·183·188·189·193·194·195·213·334·336·449·500,
> `extraction_service.py` 273, `error_monitor.py` 65·66·69, `compliance.py` 45·61,
> `logging_setup.py` 75, 음악 `models.py` 7·11·12·58).
> → **검증기가 "문제 없음"이라고 한 것과 "내가 인용을 다 확인했다"는 별개다.**
> 검증 범위(어떤 문서·어떤 표기)를 먼저 밝히고, **범위 밖 인용은 따로 검증** 한다.

> **6차에서 새로 잡은 것은 "틀린 인용"이 아니라 "귀속 오판"이었다.**
> 검증기는 동명 파일이 양측에 있으면 "행 범위가 맞는 첫 후보"를 채택하는데,
> 양쪽 파일이 모두 충분히 길면 **행 번호만으로는 구별이 안 된다**.
> 실제로 설계서 1625행 `admin.py:117-131`(음악 `reject_song`)과
> 1352행 `main.py:11-14`(음악 `lifespan`)가 **FLOW 로 오귀속** 되어 있었다.
> → `AMBIG_RESOLUTION` 에 **내용 대조 근거를 적어** 확정(11→13건).
> **"행 번호가 맞다"와 "옳은 파일이다"는 별개다** — SC-20 계열의 교훈이 인용 검증에서도 반복됐다.

