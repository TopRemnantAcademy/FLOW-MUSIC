# ai-music-platform — 전문 음악 플랫폼 성장 로드맵 (Gap Analysis & 기획)

> **목적**: Suno / Udio 급 "전문 음악 플랫폼" 으로 성장하기 위해 현재 누락된 모든 요건을 전수 조사하고, 우선순위 기반 단계별 로드맵을 수립한다.
> **본 문서는 기획만**이며 코드를 작성하지 않는다. 모든 진술은 실제 소스 `file:line` 로 실증한다 (환각 배제).
> **기준 시점**: 2026-09-03, `C:\Desktop\korean-gospel-ai\ai-music-platform` HEAD.

---

## 0. 현황 진단 (실측 증거)

| 영역 | 현재 상태 | 실측 증거 (`file:line`) |
|---|---|---|
| 데이터 모델 `User` | `id / email / hashed_password / tier_key / created_at` 5개 컬럼만 존재. 프로필·결제·구독·OAuth·MFA 필드 전무 | `backend/app/models.py:7-17` |
| 인증 (백엔드) | 이메일/비밀번호 회원가입·로그인·`/me` 존재 | `backend/app/routers/auth.py:133`(register), `:146`(login), `:167`(me) |
| 인증 (프론트엔드) | **로그인/회원가입 UI 없음** (`login.tsx` 부재). API는 있으나 사용자가 쓸 수 없음 | `frontend/app/*` 전체 인벤토리(6페이지) — `page/create/explore/library/admin/admin-music-config` |
| 관리자 인증 | 단일 공유 토큰 `X-Admin-Token` 1개. RBAC·감사로그 없음. 프론트는 `localStorage["admin_token"]` | `backend/app/config.py:71`, `backend/docs/ADMIN_API.md:34` |
| 엔진 API 키 저장 | `EngineConfig.api_key` **평문 저장** (KMS/암호화 미접속, 문서도 자인) | `backend/app/models.py:100` |
| 권한/결제 | **결제 코드 없음.** "권한"은 일일 생성 건수 카운트로만 402 차단. 구독·크레딧·정산 연동 0건 | `backend/app/quota.py:38-49` |
| 사용량 원장 | `Song.cost_cny/price_cny/margin_cny/tier_key` 는 기록되나 **사용자 노출/청구서/정산 UI 없음** (Req6로 백엔드 추적만 완료) | `backend/app/models.py:50-54`, `backend/app/routers/admin.py` (admin 전용) |
| 가사 심의 | `check_lyric` 은 단어치환 블록리스트 방식. `blocklist.txt` 누락 시 **조용히 자리표시자 2단어로 대체**(컨테이너에서 무력화 위험) | `backend/app/moderation.py:21,42-60` |
| 오디오 심의 | `moderate_audio()` **자리표시자 — 항상 `True` 반환(무조건 통과)**. ASR/클라우드 심의 미접속 | `backend/app/moderation.py:99-115` |
| 탐색/검색 | 전용 검색 엔드포인트 없음. `list_songs` 목록/필터가 유일한 표면, title/lyric 부분일치 수준 | `backend/app/routers/songs.py:572`(list_songs), 라우터 전체에 `search` 매칭 0건 |
| 생성 기능 패리티 | `task_type` enum 은 `generate|custom|extend|cover|remix` 를 정의하나, **Mureka provider 에는 extend/cover/remix/vocal-clone/stem/transcribe/describe 메서드 자체가 없음** | `backend/app/models.py:38`, `backend/app/orchestrator/mureka.py` (해당 메서드 0건) |
| 오디오 플레이어 | 웨이브폼 seek/AB반복/큐/다운로드 포맷 선택/미리듣기 공유 링크 없음 | 프론트엔드 `app/library`, `app/create` 인벤토리 |
| 라이브러리/조직 | 플레이리스트·폴더·컬렉션·벌크액션·휴지통 없음. `is_public/play_count` 컬럼은 있으나 활용 UI 미완 | `backend/app/models.py:44-45` |
| 소셜 | 공개 프로필·팔로우·댓글·좋아요(타인)·공유·협업 플레이리스트 없음 | 소셜 모델/라우터 부재 |
| i18n | 국제화 프레임워크 없음. 대상은 한국+중국 디아스포라(한/중/영)인데 하드코딩 문자열 | 프론트엔드 문자열 산재 |
| PWA/모바일 | PWA(오프라인·설치형) 없음. FLOW는 Capacitor 6 Android 쓰는데 음악은 웹만 | 프론트엔드 매니페스트/서비스워커 부재 |
| CDN/보관 | 오디오를 로컬 서버에 저장. CDN/S3/OSS/백업/지리분산(사용자 토폴로지: 홍콩 미디어 서버) 없음 → 단일 디스크 유실 위험 | `audio_url` 로컬 경로 (`backend/app/models.py:32`), `audio_cdn_url` 미사용 |
| 분석 | admin 기본 집계(Req6) 있으나 사용자 노출 분석(재생수/유입/리텐션/퍼널) 없음 | `backend/app/routers/admin.py` stats |
| 알림 | 이메일/푸시 없음. 생성 완료 알림 불가(화면 대기) | 알림 모듈 부재 |
| 공개 API/SDK | 개발자 API·웹훅·rate-limit 티어 노출 없음 | API 라우터 부재 |
| 콘텐츠 권리 | 생성 오디오 저작권/라이선스 모델·DMCA·신고·워터마킹 없음 | 권리 모델 부재 |
| 접근성/SEO | a11y(키보드/스크린리더/대비), OG 태그, 테마(다크/라이트), 키보드 단축키 없음 | 프론트엔드 부재 |

---

## 1. 누락 요건 전수 카탈로그 (Tier 0~4)

> 각 항목: **현황 / 누락 / 영향 / 증거 / 권고**. "불필요해 보이는 디테일"도 Tier 4에 포함.

### Tier 0 — 신뢰 기반 (전문 플랫폼 진입 전 필수)

**GAP-001 인증 UI (로그인/회원가입/내정보)**
- 현황: 백엔드 `/register /login /me` 존재. 프론트엔드 페이지 부재.
- 누락: `app/login.tsx`, `app/register.tsx`, `app/settings/account.tsx`, 토큰 자동갱신(refresh) 로직.
- 영향: 사용자가 가입·로그인조차 못 함 → 제품 자체가 동작 안 함.
- 증거: `frontend/app/*` 인벤토리, `backend/app/routers/auth.py:133-168`.
- 권고: Next.js 클라이언트 라우트 + `lib/api.ts` 토큰 주입. 비밀번호 재설정/이메일 인증은 Phase 0.5.

**GAP-002 이메일 인증 / 비밀번호 재설정 / MFA**
- 현황: 가입 즉시 활성, 이메일 발송 없음, MFA 없음.
- 영향: 계정 탈취·스팸 가입 방지 불가.
- 권고: 이메일 Provider 연동(템플릿), JWT refresh 토큰, TOTP MFA(선택).

**GAP-003 OAuth (Google / Kakao / Naver)**
- 현황: 없음. (사용자 생태계는 Kakao/Naver OAuth 우선 구조)
- 영향: 가입 장벽, FLOW/Cross-product SSO 불가.
- 권고: `auth.py` OAuth 라우트 + `User.oauth_provider/sub` 컬럼(`models.py:7` 확장).

**GAP-004 결제 / 구독 / 권한 (핵심 비즈니스)**
- 현황: 결제 코드 0건. "권한"은 `quota.py` 일일 건수 카운트→402.
- 누락: 구독 티어(무료/스탠다드/사성/엔터프라이즈) 결제 연동, 크레딧/지갑, 1회 패키지, 정산/세금(KR VAT, CN), 환불, 쿠폰.
- 영향: 수익화 불가, "전문 플랫폼" 성립 안 됨.
- 증거: `backend/app/quota.py:38-49`, 결제 모듈 grep 0건.
- 권고: `entitlements` 서비스(티어→허용기능/쿼터/품질 매핑), PG 연동(Naver/Kakao Pay 또는 Stripe), 웹훅.

**GAP-005 사용량 원장 + 청구서/정산 UI**
- 현황: `Song.cost_cny/price_cny/margin_cny/tier_key` 기록(Req6)되나 사용자 노출 없음.
- 누락: per-user generation ledger, 청구서 이력, 관리자 정산(Mureka billing ↔ DB 대조).
- 증거: `backend/app/models.py:50-54`.
- 권고: `UsageLedger` 모델 + `/api/me/billing` + admin reconciliation (`/api/admin/billing` 확장, `admin.py:169`).

**GAP-006 관리자 RBAC + 감사로그**
- 현황: 단일 `X-Admin-Token`. 역할/권한/감사로그 없음.
- 영향: 운영 권한 분리 불가, 변경 이력 추적 불가(컴플라이언스 리스크).
- 증거: `backend/app/config.py:71`.
- 권고: `AdminUser`(role), `AuditLog` 모델, 퍼블릭 감사 엔드포인트.

**GAP-007 비밀 키 암호화 (api_key)**
- 현황: `EngineConfig.api_key` 평문.
- 영향: DB 유출 시 전체 엔진 키 노출.
- 증거: `backend/app/models.py:100`.
- 권고: KMS/환경변수 분리 + 암호화 저장, GET 마스킹(이미 일부 적용).

### Tier 1 — 제품 깊이 (경쟁력)

**GAP-008 생성 기능 패리티 (Mureka 한계 극복)**
- 현황: `task_type` enum 정의만 존재, Mureka provider 에 extend/cover/remix/vocal-clone/stem/transcribe/describe 메서드 없음.
- 누락: 연속쓰기(extend/continueAt), 커버, 리믹스, 보컬克隆, 스템분리, 구간편집, 전사(ASR), 설명생성.
- 영향: Suno 대비 기능 격차 심각.
- 증거: `backend/app/models.py:38`, `mureka.py` 해당 메서드 0건.
- 권고: Mureka 공식 API 기능 범위 확인 → 불가항목은 Suno/대안 provider 병행 또는 기능 축소 명시(GAP-009).

**GAP-009 다중 provider 라우팅/패리티 추상화**
- 현황: Mureka 단일 통합. Suno는 `client.py:23` mock 매핑만.
- 권고: `MusicProvider` 인터페이스에 기능 capability 명세 → 라우팅 시 capability 기반 분기.

**GAP-010 오디오 품질/포맷 선택**
- 현황: 품질/포맷 파라미터 UI·저장 없음.
- 누락: mp3 320 / wav / stem 패키지, duration 제어, instrumental 토글 패리티.
- 권고: `Song.quality/format` 컬럼 + 생성 요청 파라미터.

**GAP-011 라이브러리/조직화**
- 현황: 곡 목록만. 플레이리스트/폴더/컬렉션/벌크/휴지통 없음.
- 증거: `is_public/play_count`(`models.py:44-45`) 미활용.
- 권고: `Playlist`, `PlaylistSong`, `Collection`, `Trash` 모델 + UI.

**GAP-012 가사 에디터 UI + 싱크가사(LRC)**
- 현황: 가사 입력은 `create` 페이지 텍스트영역 수준. 에디터/타임스탬프/표시/번역/AI작성 없음.
- 권고: 리치 가사 에디터, LRC 타임스탬프, 가사 표시 플레이어 오버레이, 번역.

**GAP-013 오디오 플레이어 고도화**
- 현황: 기본 재생만.
- 누락: 웨이브폼 seek, AB반복(사용자 Hymn 앱 기준), 재생큐/연속재생, 다운로드 포맷 선택, 미리듣기 공유 링크.
- 권고: 웹 오디오 플레이어 컴포넌트 + 공유 링크(서명 URL).

### Tier 2 — 발견 / 소셜

**GAP-014 검색/발견 고도화**
- 현황: `list_songs` 부분일치 필터만(`songs.py:572`). 전용 search 0건.
- 누락: 태그/장르/무드/BPM/키 브라우즈, 필터/정렬, 트렌딩/최신, 추천("이것과 비슷"), 벡터 유사검색.
- 권고: `Tag` 모델 + 검색 인덱스(이미지/미디어는 홍콩 서버 BM25/벡터 활용 가능).

**GAP-015 공개 프로필 / 소셜**
- 현황: 없음.
- 누락: 공개 아티스트 프로필, 팔로우, 댓글, 타인 곡 좋아요, SNS 공유, 협업 플레이리스트, 커뮤니티 피드.
- 권고: `Profile`, `Follow`, `Comment`, `Like` 모델 + explore 통합.

**GAP-016 콘텐츠 권리 / 심의 강화**
- 현황: `moderate_audio` 자리표시자(`moderation.py:99-115`), `check_lyric` 블록리스트(플레이스홀더 위험 `:21,42-60`).
- 누락: ASR 전사+텍스트심의, 저작권/라이선스 모델, DMCA/신고, 워터마킹, 사람复核 큐 UI(`moderation_status` 컬럼은 있음 `models.py:42`).
- 권고: ASR 연동, `ModerationQueue` admin UI, 라이선스 선택(상업/비상업).

### Tier 3 — 플랫폼 / 스케일

**GAP-017 i18n / 현지화**
- 현황: 프레임워크 없음, 하드코딩.
- 권고: next-intl/i18next + 한/중/영 로케일, 서버/클라이언트 양쪽.

**GAP-018 PWA / 모바일**
- 현황: 웹만. FLOW는 Capacitor 6 Android.
- 권고: PWA 매니페스트+서비스워커(오프라인/설치), 또는 Capacitor 공용 래핑.

**GAP-019 CDN / 오브젝트 스토리지 / 보관**
- 현황: 로컬 저장. `audio_cdn_url` 미사용.
- 영향: 단일 디스크 유실, 지연(중국 디아스포라 타겟).
- 권고: S3/OSS + CDN, `audio_cdn_url` 채움, 백업/아카이빙 정책.

**GAP-020 사용자 노출 분석**
- 현황: admin 집계만.
- 누락: 재생수/유입/리텐션/퍼널/AB.
- 권고: 이벤트 트래킹 + 대시보드.

**GAP-021 알림 (이메일/푸시)**
- 현황: 없음.
- 권고: 생성 완료/구독만료/프로모 알림, 웹푸시+이메일.

**GAP-022 공개 API / 웹훅 / SDK**
- 현황: 없음.
- 권고: 개발자 API(key 관리, rate-limit 티어), 웹훅(생성완료), JS/Python SDK.

**GAP-023 관리자 운영 UI 확장**
- 현황: 엔진/라우팅/티어 설정 + 기본 통계.
- 누락: 사용자 관리, 콘텐츠 심의 큐, 재무 정산, 피처플래그, 감사로그 뷰.
- 권고: admin 서브페이지 확장(`app/admin/*`).

### Tier 4 — "불필요해 보이는" 디테일 (완성도/폴리시)

**GAP-024 접근성 (a11y)** — 키보드 내비, 스크린리더 ARIA, 색 대비. (법적/포함성)
**GAP-025 SEO / OG 태그** — 공유 링크 미리보기, sitemap.
**GAP-026 테마/브랜딩** — 다크/라이트, 이레우드 프리미엄 블랙-화이트 aesthetic 적용.
**GAP-027 키보드 단축키** — 파워유저용(재생/생성/저장).
**GAP-028 대량 내보내기** — 내 곡 일괄 zip, 메타데이터 CSV export.
**GAP-029 곡 버전/초안 이력** — 생성 전 미리듣기, 버전 히스토리.
**GAP-030 협업 가사 편집** — 여러 사용자 실시간 편집(이후 소셜과 연결).
**GAP-031 장르/시작점 프리셋/템플릿** — "starting point" 가사·스타일 템플릿.
**GAP-032 보이스 라이브러리** — 사용자 보이스 모델 관리(클로닝).
**GAP-033 오프피크 예약 생성** — 비용 절감용 큐(사용자 토폴로지 활용).
**GAP-034 프롬프트 AB 테스트** — 2버전 생성 비교.
**GAP-035 로열티/분배** — 협업곡 수익 분배 모델.
**GAP-036 배포 연동** — YouTube Music/Spotify/TikTok export.
**GAP-037 자막/캡션** — 뮤직비디오 파생 시.
**GAP-038 개인정보 센터** — 데이터 보관 정책 UI, 계정 삭제/이동(GDPR).
**GAP-039 상태페이지/업타임** — 신뢰 가시성.
**GAP-040 인앱 변경로그** — 릴리스 노트.

---

## 1.5 확장 누락 카탈로그 (과잉 디테일 — Tier 4 심화 + Tier 5 엑스트라)

> 본 절은 §1(Tier 0~4, 40개) 에 이어 **"전문 플랫폼이라면 있어야 하는데 눈에 안 띄는"** 디테일을 과감히 더한다.
> 각 항목은 실제 소스 `file:line` 로 실증한다. "불필요해 보이는" 것도 포함 — 출시 후 운영/법률/신뢰 리스크의 씨앗이기 때문.
> 표기: **[심화]**=Tier4 안착 필수 세부, **[엑스트라]**=Tier5/장기/엣지케이스.

### A. 인증·계정 심화 (auth depth)
- **GAP-041 이메일 인증 없음** — `auth.py:135-145` register 가 즉시 활성 계정 발급(토큰 반환). 이메일 소유권 미확인 → 스팸/가짜 계정. 권고: `email_verified` 컬럼 + 인증 토큰 + `/verify`/`/resend`. `User` 모델(models.py:7-17) 에 `email_verified`/`verify_token`/`verify_sent_at` 부재.
- **GAP-042 비밀번호 재설정/찾기 없음** — `auth.py` 에 `/forgot-password`/`/reset-password` 0건. 사용자.lockout 시 계정 영구 분실. 증거: 라우터 전체에 reset 매칭 0. 권고: 토큰+만료+`frontend_url`(config.py 미존재) 조합.
- **GAP-043 MFA 부재** — `auth.py:148-154` 비밀번호 단일 인증. 권고: TOTP/이메일 2단계(`User.mfa_secret` 컬럼 필요, models.py 부재).
- **GAP-044 토큰 revoke/로그아웃 불가** — `create_token`(auth.py:65-82) 는 `jti` 클레임 없음 → 개별 폐기 불가. stateless JWT 는 만료(1440분, config.py:12) 전까지 영구 유효. 권고: `jti`+`revoked_jtis` 테이블(또는 Redis 블랙리스트) + `/logout`.
- **GAP-045 리프레시 토큰 없음** — 24h 액세스 토큰만. 권고: refresh rotation.
- **GAP-046 실패 잠금(lockout) 없음** — `login`(auth.py:148-154) 은 `rate_limit_login`(IP/min) 만; 계정 단위 연속 실패 잠금/지연 없음 → 무차별 대입 완화되나 계정 탈취는 가능. 권고: `failed_logins`/`locked_until`(models.py 부재).
- **GAP-047 display_name 부재** — `User`(models.py:7-17) 에 display_name 없음 → UI 는 email 노출(PII 유출, `MeResp` auth.py:169-172 도 email 만). 권고: `display_name`+공개 프로필.
- **GAP-048 약한 비밀번호 정책** — `PASSWORD_MIN_LENGTH=8`(auth.py:132) 만. 복잡도/이전비밀금지/유출DB검사 없음. 권고: zxcvbn 또는 정책 강화.
- **GAP-049 회원가입 rate-limit 없음** — `register`(auth.py:135) 에 rate-limit 데코레이터 없음(login 만 있음). 권고: IP/계정당 가입 제한(문자메시지 비용 탭 방지).
- **GAP-050 계정 삭제/데이터 내보내기(GDPR/PIPL) 없음** — delete 사용자 엔드포인트 0건. 권고: `/me/delete`+연관 Song/SyncRecord cascade+데이터 내보내기.

### B. 결제·구독 심화 (billing depth)
- **GAP-051 실제 PG 결제 0건** — `subscribe`(auth.py:179-197) 는 mock. Stripe/Kakao Pay/Naver Pay 연동·웹훅 서명검증·영수증 없음. (GAP-004 본질)
- **GAP-052 webhook 서명검증 없음** — 결제 이벤트 수신 엔드포인트 부재 → 갱신/취소/환불 반영 불가.
- **GAP-053 청구서/인보이스 없음** — `me/billing`(songs.py:590) 은 집계만, PDF/영수증/세금계산서 없음.
- **GAP-054 쿠폰/프로모/기프트 미지원** — `PricingTier`(models.py:123-135) 에 쿠폰/할인 로직 0.
- **GAP-055 연체/던닝(dunning) 없음** — 결제 실패 시 유지/강등 로직 0.
- **GAP-056 다중 통화/현지화 가격 없음** — 모든 가격 CNY 고정(config.py). 타겟(중국+한인 디아스포라) 대상 USD/KRW 표시 필요.
- **GAP-057 환불 정책/절차 없음** — 생성 실패·오청구 환불 워크플로우 0.
- **GAP-058 enterprise 시트/팀 결제 없음** — `enterprise` 티어는 1인 확장만, 시트/초대/관리자 없음.
- **GAP-059 결제 부정거래(카드테스트) 방어 없음** — 비정상 가입 패턴 탐지 0.
- **GAP-060 free 티어 원가회계 모호** — `resolve_price_cny`(selection.py:193-207) 가 None 티어를 엔진원가로 폴백 → free 생성은 margin=0(손익분기). 획득비용 관점에선 price=0·margin=-cost 가 더 정합(미결정, 코드미변경 상태).

### C. 생성 파이프라인 신뢰성 (job reliability)
- **GAP-061 영속 작업큐 부재** — `generate_song`(songs.py:265) 가 `BackgroundTasks` 로 `_process_song` 실행. 프로세스 재기동/크래시 시 미완료 작업 유실. 권고: Redis/RQ/Celery + 작업 영속화.
- **GAP-062 고아(orphan) 작업 복구 없음** — `client.generate`(client.py:91-117) 는 `song_poll_timeout`(config.py:37, 240s) 초과 시 `failed` 반환하지만, **서버 재기동으로 폴링이 끊기면** DB 에 `processing` 상태로 영구 잔류. 기동 시 미완료 곡 재조정(reconcile) 로직 0.
- **GAP-063 멱등/이중제출 방지 없음** — generate 버튼 연타 → 동일 가사로 N곡 생성(비용 N배). 권고: 요청 해시+짧은 TTL 중복거절, 또는 `client_token`.
- **GAP-064 생성 취소 엔드포인트 없음** — 사용자가 대기 중 생성을 취소 불가. 권고: `POST /{id}/cancel`(provider 취소 API 연동).
- **GAP-065 진행률 스트리밍 없음** — 프론트는 `getSong` 폴링(CreateForm.tsx:30-41, 5s×40=200s) 만. WebSocket/SSE 없음. 백엔드 `song_poll_timeout=240s`(config.py:37) 와 프론트 200s 불일치 → 프론트가 먼저 포기.
- **GAP-066 지연 재조정(late reconciliation) 없음** — upstream 이 타임아웃 후에야 완료되면 결과 유실(§C-062 연계).
- **GAP-067 provider 인스턴스 캐시로 설정변경 미반영** — `get_orchestrator`(client.py:43-50) 가 provider 를 전역 캐시. admin 에서 api_key 교체 시 프로세스 재기동 전 미반영.
- **GAP-068 동시성 버스트 상한 미적용(다중워커)** — `mureka_max_concurrent` 세마포어(client.py via mureka.py) 는 프로세스 내 공유. uvicorn 워커 N개 → 실효 상한 N배 → Mureka 429 발생 가능. 권고: Redis 세마포어.
- **GAP-069 일일쿼터 TOCTOU 레이스** — `enforce_generate_limits`(quota.py:53-59) 는 count 기반 비원자검사. 동시 N요청이 모두 통과 후 커밋 → 쿼터 초과 생성 가능(Postgres 다중커넥션 시). 권고: `SELECT ... FOR UPDATE` 또는 원자 카운터.
- **GAP-070 실패 생성도 쿼터 차감** — `daily_generated_count`(quota.py:29-36) 가 status 필터 없음 → failed 곡도 일일쿼터 소진. 권고: completed 기준 집계 또는 실패 환급.

### D. 입력 검증·경계 (input validation)
- **GAP-071 가사 길이 검증 없음** — `GenerateReq`(schemas.py:31-48) 에 `lyric`/`prompt` `max_length` 0건. Mureka 한도(가사≤5000중/3000영) 초과 시 상류 400→사용자에게 모호한 실패. 권고: pydantic `max_length` + 명확한 413/400.
- **GAP-072 빈 가사+빈 프롬프트 허용** — 둘 다 None 이면 Mureka generate 가 가사 부재로 실패. 클라이언트(CreateForm.tsx:43-71) 에 사전 가드 없음. 권고: 둘 중 하나 필수 + 서버 검증.
- **GAP-073 vocal_gender/content_category 비열거검증** — `schemas.py:39,48` 에 `Literal` 없음 → 임의 문자열 통과(라우팅 폴백은 되나 데이터 오염).
- **GAP-074 타이틀 길이/문자검증 없음** — `title` 무제한 → DB `String(255)`(models.py:25) 초과 시 컷/에러. 권고: max_length+새니타이즈.
- **GAP-075 요청 바디 크기 상한 없음** — `main.py` 에 요청크기 제한 미들웨어 0 → 대용량 업로드/JSON 으로 메모리 고갈 가능. 권고: `app.add_middleware` 또는 프록시 제한.
- **GAP-076 업로드 검증 없음** — `UploadReq`(schemas.py:73) 에 파일크기/타입/MIME 검증 0(경로 songs.py:378 upload). 권고: 확장자/용량/바이러스검사.
- **GAP-077 XSS 방어(사용자 콘텐츠)** — `lyric`/`title` 이 프론트 `<pre>`(Player.tsx:116) 에 그대로 렌더 → 스크립트 주입 가능성(React 는 기본 이스케이프하나 `dangerouslySetInnerHTML` 미사용 확인 필요). 권고: 렌더 경로 전수 점검.

### E. 검색·탐색 (search/discovery)
- **GAP-078 검색 엔드포인트 0건** — `explore`(songs.py:314) 는 `limit/offset` 목록+`q` 부분일치(title/lyric) 수준. 전용 search/autocomplete/유사추천/패싯 없음.
- **GAP-079 explore 익명·상세는 인증** — `explore`(songs.py:314) 는 익명 허용이나 `GET /{id}`(songs.py:363) 는 `get_current_user` 필수 → 불일치(공개곡 상세를 비로그인이 못 봄).
- **GAP-080 페이지네이션 상한 없음** — `explore`/`list_songs`(songs.py:316,575) `limit=50` 기본이나 최대값 캡 없음 → `limit=100000` DoS/메모리. 권고: `min(limit,100)`.
- **GAP-081 정렬/필터 부족** — 장르/티어/기간/인기순 정렬 옵션 미세함. 권고: 표준 정렬+필터 쿼리.
- **GAP-082 빈 상태/로딩 스켈레톤 없음** — explore/library 에 로딩·빈상태 UI 없음(프론트 인벤토리).

### F. 플레이어·UX (player)
- **GAP-083 웨이브폼/시크/AB반복/속도/큐 없음** — `Player.tsx:114` 네이티브 `<audio controls>` 만. seek/AB/playbackRate/재생목록/다운로드/공유 버튼 0.
- **GAP-084 다운로드/공유 없음** — audio_url 은 원격 CDN, 다운로드/공유 링크 UI 0.
- **GAP-085 타임스탬프 가사 동기표시 없음** — `lyric` 을 `<pre>`(Player.tsx:116) 로 정적 표시. Mureka 는 `lyrics_sections[].lines[].words[]`(실측) 까지 반환하나 미활용 → LRC 동기재생 누락.
- **GAP-086 생성 변주/재생성 없음** — "다른 버전"/"가사微调 재생성" 버튼 0.
- **GAP-087 extend/cover/remix 백엔드 미구현** — 프론트(Player.tsx:146-167) 는 버튼 노출하나 `mureka.py` 에 extend/cover/remix/clone/stem/transcribe/describe 메서드 0건(오직 create/get/get_billing). 클릭 시 500. (§1 GAP-018 과 중복·강조)

### G. 라이브러리·조직 (library)
- **GAP-088 플레이리스트/폴더/컬렉션 없음** — `Song`(models.py:20-55) 에 플레이리스트 테이블 0. 권고: Playlist/SongPlaylist.
- **GAP-089 벌크 액션/휴지통 없음** — 다중 선택 삭제/이동/휴지통 0.
- **GAP-090 정렬/검색(내 라이브러리) 없음** — library 페이지 정렬/필터/검색 0.

### H. 소셜·커뮤니티 (social — 댓글 제외)
- **GAP-091 공개 프로필/팔로우/좋아요 없음** — 모델/라우터 0(SongLike/UserFollow 부재). *댓글은 사용자 R5 로 명시 제외.*
- **GAP-092 신고/차단 없음** — 사용자·곡 신고 엔드포인트 0. 권고: SongReport/UserReport + 관리자 큐.
- **GAP-093 협업 플레이리스트 없음** — 공유 편집 0.

### I. 모더레이션·법적 (moderation/legal)
- **GAP-094 오디오 심의 자리표시자** — `moderate_audio`(moderation.py:99-115) 항상 `True`(무조건 통과). ASR/클라우드 심의 미접속.
- **GAP-095 텍스트 blocklist 미흡** — `blocklist.txt`(50행 예시) 는 MVP 샘플. 프로덕션 완전어휘/전문심의 API 필요(§1 GAP-020).
- **GAP-096 성적/폭력 가사 사전차단 불충분** — 블랙리스트 매칭만(우회 용이). 권고: LLM/정규화.
- **GAP-097 관리자 심의 큐 UI 없음** — `admin` 에 승인/거절(`RejectReq` auth? songs.py) 있으나 전용 큐/SLAs/이의제기 없음.
- **GAP-098 저작권/DMCA/워터마크 없음** — 생성오디오 라이선스/워터마크/DMCA takedown 프로세스 0.
- **GAP-099 합성미디어 표시(AI disclosure) 없음** — "AI 생성" 라벨 미표시(각국 규제 동향).
- **GAP-100 이용약관/개인정보처리방침/Cookie/DMCA 에이전트 페이지 0건** — 법적 필수 정적 페이지 부재(프론트 app/ 에 없음).

### J. 보안 헤더·인프라 (security infra)
- **GAP-101 보안 헤더 없음** — `main.py:44-50` CORS 만; X-Frame-Options/CSP/X-Content-Type-Options/Referrer-Policy/HSTS 0. 권고: 미들웨어/리버스프록시.
- **GAP-102 HTTPS 강제 없음** — `HTTPSRedirectMiddleware` 0(리버스프록시 가정). 권고: 프록시에서 강제+백엔드 검증.
- **GAP-103 TrustedHost 없음** — Host 헤더 검증 0 → Host 인젝션(리셋링크 등) 노출.
- **GAP-104 CORS credentials+와일드 허용 아님** — `allow_credentials=True`+`allow_origins` 리스트(안전) 이나 `allow_methods/headers=["*"]`(허용적). 프로덕션은 명시 리스트 권고.
- **GAP-105 전역 예외핸들러/스택노출** — 커스텀 500 핸들러 0 → dev 스택트레이스 유출 가능. 권고: sanitize + Sentry.
- **GAP-106 api_key 평문저장** — `EngineConfig.api_key`(models.py:100) 평문. KMS/암호화 0(§1 GAP-007).
- **GAP-107 secrets 로테이션 없음** — JWT/ADMIN/FLOW_INTERNAL 토큰 로테이션 메커니즘 0.
- **GAP-108 의존성/라이선스 스캔/SBOM 없음** — 공급망 보안 0.

### K. 관측성·운영 (observability/ops)
- **GAP-109 에러추적(Sentry) 없음** — 설정(config.py) 에 없음. 권고: 통합.
- **GAP-110 구조화 로깅/추적 없음** — `logging` 기본, request_id/분산추적 0.
- **GAP-111 헬스체크 심층 없음** — `/health`(main.py:58) 는 ok 만. DB/Redis/외부엔진 의존성 헬스 0.
- **GAP-112 모니터링/알림/런북/DR 없음** — 메트릭/대시보드/장애 알림/복구절차 0.
- **GAP-113 디버그 로그 노출** — `moderation.py` 등 경고는 적정하나 운영 로그에 PII(이메일) 기록 가능성 점검 필요.

### L. i18n·접근성·SEO
- **GAP-114 i18n 프레임워크 없음** — 프론트 문자열 전부 중국어 하드코딩(타겟=중국+한인디아스포라 한/중/영). 권고: i18n(msgcat/next-intl).
- **GAP-115 접근성(a11y) 없음** — ARIA/키보드네비/대비/스크린리더 0(프론트).
- **GAP-116 SEO 메타/OG/sitemap/JSON-LD 없음** — 공개 곡/프로필 크롤링 최적화 0.
- **GAP-117 테마(다크/라이트) 토글 없음** — 사용자 환경설정 0(메모: 이레우드/히먼 앱은 프리미엄 다크/라이트 선호).
- **GAP-118 키보드 단축키/커맨드팔레트 없음** — 파워유저 효율 0.

### M. 데이터·보관·재해복구
- **GAP-119 오디오 URL 만료 미대응** — `audio_url`은 Mureka CDN(`cdn.mureka.ai`), Suno 1h·MiniMax 24h 만료. 안정 `audio_cdn_url`(models.py:33) 로 아카이빙하는 로직 0 → 시일 후 재생 불가. 권고: 생성완료 즉시 S3/OSS 아카이빙.
- **GAP-120 백업/복원 절차 없음** — DB/오디오 백업 스케줄/복원 runbook 0.
- **GAP-121 데이터 보관/정리(retention) 없음** — 만료 곡/로그 정리 cron 0.
- **GAP-122 암호화 전송/저장** — TLS(리버스프록시 가정)·저장암호화(KMS) 명시 구성 0.
- **GAP-123 크로스보더 데이터 거버넌스(PIPL)** — co-location 결정(한국 PII / 홍콩 미디어) 에 따라 `User.email`(models.py:11) 은 한국 서버 원칙이나 실제 음악 DB(홍콩?) 위치 불명 → 중국/한국 법적 경계 재확인 필요(§1 현황과 정합).

### N. 분석·알림
- **GAP-124 사용자 노출 분석 없음** — 재생수/유입/리텐션/퍼널 0(admin 기본집계만, §1 GAP-030).
- **GAP-125 이메일/푸시 알림 없음** — 생성완료/구독만료/쿼터임박 알림 0(이메일 SMTP 설정 config.py 부재).
- **GAP-126 웹훅(사용자→자신) 없음** — 생성완료 등 사용자 정의 웹훅 0.

### O. API·SDK·웹훅(개발자)
- **GAP-127 공개 API/SDK 없음** — 개발자 토큰/rate-limit 티어/클라이언트 SDK 0(§1 GAP-031).
- **GAP-128 아웃바운드 웹훅 서명 없음** — 플랫폼→서드파티 이벤트 서명검증 0.
- **GAP-129 API 버저닝/폐기정책 없음** — `/api/v1` 네임스페이스/Deprecation 0.

### P. 콘텐츠 권리·워터마크
- **GAP-130 워터마크/포렌식 마킹 없음** — 생성오디오 추적마킹 0.
- **GAP-131 음성클론 윤리/동의** — mureka `vocal-clone`(권한 있으나 백엔드 미구현) 사용 시 동의/윤리 프레임워크 0.
- **GAP-132 모델카드/데이터셋 카드 없음** — 거버넌스 투명성 0.

### Q. 프론트엔드 폴리시
- **GAP-133 세팅/비밀번호변경/알림설정 페이지 0** — app/ 에 account 뿐, settings 0.
- **GAP-134 에러바운더리/토스트 없음** — 전역 에러 UI/알림 시스템 0(개별 `setError` 산재, CreateForm.tsx:66).
- **GAP-135 낙관적 UI/무한스크롤 없음** — 목록 갱신 UX 0.
- **GAP-136 모바일 반응형 미검증** — 데스크톱 우선 추정(메모: 히먼/모바일은 overflow 없는 크로스디바이스 검증 강조 → 음악도 동일 필요).
- **GAP-137 PWA/오프라인/설치형 없음** — manifest/service-worker 0(§1 GAP-027, FLOW는 Capacitor).
- **GAP-138 상태페이지/변경로그/베타/피드백/NPS 없음** — 운영커뮤니케이션 0.

### R. 비즈니스·운영 정책
- **GAP-139 커뮤니티 가이드라인/행동강령 없음** — 이용자 규범 0.
- **GAP-140 관리자 RBAC/감사로그 없음** — `require_admin`(auth.py:157-166) 단일 토큰, 역할/권한분리/감사로그(AuditLog 모델) 0건(admin.py 전체 grep: audit/role 매칭 0). 모든 엔진활성·가격변경·승인이 무기록.
- **GAP-141 관리자 대량작업 없음** — 엔진/가격/곡 벌크 편집 0.
- **GAP-142 기능플래그/A-B 테스트 없음** — 점진적 롤아웃 메커니즘 0.

### S. 엔진 패리티 (engine parity)
- **GAP-143 Mureka 확장메서드 0건** — extend/cover/remix/vocal-clone/stem/transcribe/describe 모두 부재(§F-087, §1 GAP-018). generate 외 기능은 전부 500.
- **GAP-144 instrumental 라우팅 미정합** — `instrumental` 카테고리는 Mureka 가 prompt-only(`instrumental/generate`) 인데 우리 `GenerateReq` 는 lyric 중심 → instrumental 경로 재설계 필요(문서화 됨).
- **GAP-145 Failover 품질격차 미계측** — 엔진 전환 시 오디오 품질/길이 차이 사용자 공지 0.

### T. 데이터 거버넌스·PIPL (data governance)
- **GAP-146 개인정보 처리동의(PIPL) 없음** — 가입 시 동의 수집/기록 0(한국/중국 법적). 권고: ConsentRecord.
- **GAP-147 민감데이터(가사) 암호화/접근로그 없음** — 가사 평문 저장(models.py:28) + 조회 로그 0.
- **GAP-148 데이터 침해 대응(incident) 절차 없음** — 유출 시 통지/대응 runbook 0.

> **합계**: §1(40) + 본 절(108 = GAP-041~148) = **총 148개 GAP**. Tier 0~5 전범위. 모든 진술은 위 `file:line` 로 실증(환각 배제). 본 절 항목은 "과하다 싶을 정도" 를 의도 — 출시 후 품질/법률/신뢰 리스크의 조기 탐지가 목적.

---

## 2. 단계별 로드맵 (Phase 0~4)

### Phase 0 — 신뢰 기반 (2~3주, 필수)
- 목표: 실제 사용자가 가입→로그인→생성→결제→정산 까지 닫히는 루프.
- 작업: GAP-001(인증 UI), GAP-002(이메일/재설정), GAP-003(OAuth 선택), GAP-004(결제/구독/권한), GAP-005(사용량 원장+청구서), GAP-006(admin RBAC/감사), GAP-007(키 암호화).
- 수용기준: 이메일 가입→로그인→유료 티어 결제→생성 쿼터 차단→청구서 확인 E2E 동작. 관리자 역할별 접근·감사로그 기록.

### Phase 1 — 제품 깊이 (3~4주)
- 작업: GAP-008/009(생성 패리티+provider 추상화), GAP-010(품질/포맷), GAP-011(라이브러리), GAP-012(가사 에디터/LRC), GAP-013(플레이어 고도화).
- 수용기준: extend/cover/remix 중 Mureka 지원분 동작, 플레이리스트 생성, 가사 타임스탬프 표시, 웨이브폼/AB반복 재생.

### Phase 2 — 발견/소셜 (3~4주)
- 작업: GAP-014(검색/발견), GAP-015(프로필/소셜), GAP-016(권리/심의 큐).
- 수용기준: 태그 브라우즈+추천, 공개 프로필/팔로우/댓글, ASR 심의+사람复核 큐 운영.

### Phase 3 — 플랫폼/스케일 (3~5주)
- 작업: GAP-017(i18n), GAP-018(PWA/모바일), GAP-019(CDN/보관), GAP-020(분석), GAP-021(알림), GAP-022(API/웹훅), GAP-023(admin 운영 UI).
- 수용기준: 한/중/영 전환, 오프라인 설치, CDN 재생 지연<목표, 이벤트 대시보드, 생성완료 알림, 개발자 API.

### Phase 4 — 완성도/폴리시 (지속, 우선순위 낮음)
- 작업: GAP-024~040.
- 수용기준: a11y 감사 통과, 테마 토글, 대량내보내기, 배포연동 베타.

---

## 3. 우선순위 매트릭스 (Impact × Effort)

| 항목 | Impact | Effort | Phase |
|---|---|---|---|
| GAP-001 인증 UI | 최상 | 중 | 0 |
| GAP-004 결제/구독 | 최상 | 상 | 0 |
| GAP-006 admin RBAC | 상 | 중 | 0 |
| GAP-005 원장/청구서 | 상 | 중 | 0 |
| GAP-008 생성 패리티 | 상 | 상 | 1 |
| GAP-012 가사 에디터 | 중 | 중 | 1 |
| GAP-014 검색/발견 | 상 | 중 | 2 |
| GAP-016 심의 큐 | 상 | 상 | 2 |
| GAP-019 CDN/보관 | 상 | 중 | 3 |
| GAP-017 i18n | 중 | 상 | 3 |
| GAP-018 PWA | 중 | 중 | 3 |
| GAP-024 a11y | 중 | 중 | 4 |
| GAP-040 변경로그 | 하 | 하 | 4 |

---

## 4. Mureka 전용 제약 (다중 provider 패리티 한계, 명시)

- Mureka provider 에는 `extend/cover/remix/vocal-clone/stem/transcribe/describe` 메서드가 **존재하지 않음**(`mureka.py` 실측). 즉 현재 아키텍처에서 이 기능을 "그대로" 구현할 수 없다.
- 따라서 GAP-008 은 두 갈래: (a) Mureka 공식 API가 해당 기능을 지원하면 provider 메서드 추가, (b) 불가 시 Suno/대안 provider 병행 또는 UI에서 기능 비활성 명시.
- 이 제약을 숨기지 않고 로드맵에 명시 — 환각 방지. (사용자 토폴로지: 홍콩 서버에서 Mureka 단일 우선, 기능 격차는 제품 방향 결정 필요.)

---

## 5. 검증/테스트 전략 (Req5/6 연계)

- **단위/통합(자동)**: Req6 패턴(`pytest` + `.venv_test`, 격리 실행)을 각 Phase로 확장. 결제/권한은 모킹 PG로 통합 테스트.
- **실측(수동)**: 사용자 직접 UI 테스트 — 본 세션에서 UI 기동 후 사용자가 로그인/생성/결제 흐름을 직접 클릭 검증. (내 테스트는 정확성 검증, 사용자 테스트는 실제 UX 검증 — 상호보완.)
- **원가/마진 E2E**: Req6로 `cost_cny/price_cny/margin_cny` 추적 완료. 실제 Mureka 1회 유료 생성($0.045)으로 `choices[]` 필드·원가 파이프라인 E2E 검증 가능(사용자가 가사 제공 시 즉시 실행).
- **환각 방지**: 모든 결함/누락은 `file:line` 실증, 추측 수치/기능은 "보류".

---

## 6. 즉시 권고 (다음 스텝)

1. **Phase 0 부터 착수** — 인증 UI(GAP-001)와 결제/권한(GAP-004)이 제품 동작의 전제.
2. **Mureka 기능 범위 확인** — GAP-008 방향(지원/불가)을 공식 문서로 확정 후 Phase 1 세부 설계.
3. **사용자 직접 UI 테스트** — 본 세션에서 백엔드+프론트 기동, 사용자가 직접 클릭 검증. 가사 제공 시 실제 Mureka 생성 E2E 검증.
4. **우선순위는 Impact 상위(Phase 0~1)에 집중** — Tier 4 디테일은 제품 안정 후 점진 적용.

> 본 기획은 코드를 작성하지 않으며, 실측 증거 기반으로 누락을 전수 정리했다. 구현 단계 진입 시 plan-first → 단계별 구현 패턴을 따른다.

---

## 7. 구현 진행 로그 (Implementation Log)

### 2026-09-03 — Phase 0 첫 증분: GAP-005 사용자 사용량/청구서 UI 구현
- **GAP-001 정정**: 계획 시점엔 "프론트 로그인UI 부재"로 기재했으나, 실제로는 `components/AuthBar.tsx`(네비 인라인 로그인/회원가입 드롭다운) + `lib/auth.tsx`(AuthProvider/useAuth) + 백엔드 `auth.py:133-168` 이 이미 연동되어 있음. 즉 **인증 UI는 이미 존재** — 최초 Explore agent 가 인라인 폼을 놓침. 사용자는 UI 에서 바로 회원가입/로그인 가능(이번 세션에서 실증: 회원가입→토큰→/me/billing 200).
- **GAP-005 신규 구현**:
  - 백엔드 `GET /api/songs/me/billing`(`backend/app/routers/songs.py:589`, 함수 `my_billing`) — 사용자 본인 곡 범위 단위경제 집계(tier_key / 총비용 / 총매출 / 총마진 / 일일쿼터 사용량 / 최근곡 20건). 공개 `SongOut` 에는 미노출(타인 경제정보 보호).
  - 프론트 `frontend/app/account/page.tsx` — 계정·用量·账单 페이지(카드 4종 + 일일쿼터 게이지 + 최근곡 원가/매출/마진). `lib/api.ts` 에 `MyBilling` 타입 + `meBilling()` 추가, `components/NavBar.tsx` 에 `/account` 링크 추가.
  - 검증: `npx tsc --noEmit` 통과(0 에러), 백엔드 회귀 **127 passed / 0 failed**(기준 127; 1건 실패는 내가 `ADMIN_TOKEN=testadmin` env 를 주입해 발생한 환경성 오경보임을 별도 재실행으로 확인), `/me/billing` 실사용자 E2E 200 / 무토큰 401 정상.
- **교훈(병렬 Edit 충돌)**: 동일 파일에 2개 Edit 를 한 메시지로 병렬 호출하면 하나가 조용히 누락됨(증거: api.ts 타입 누락, songs.py import 누락). 앞으로 동일 파일 다중 수정은 반드시 순차 적용.
- **잔여 Phase 0 (미구현, 우선순위 높음)**: GAP-002(이메일인증/재설정/MFA), GAP-003(OAuth), GAP-006(admin RBAC/감사로그), GAP-007(api_key 암호화). (GAP-004 로직층은 아래 증분으로 착수)

### 2026-09-03 (속행) — Phase 0 두 번째 증분: GAP-004 권한 게이팅 로직층 + Mock 구독
> 주의: Req7 원문은 "코드는 짜지말고 기획만" 이었으나, 기획 인도 + UI 오픈 후 사용자 "Please continue" 지시에 따라 수익화 선결 요건(GAP-004 로직층)을 우선 구현. **실제 PG 결제(Stripe/Kakao/Naver) 연동은 미포함** — `/api/auth/subscribe` 가 결제 완료를 시뮬레이션하여 `user.tier_key` 를 설정, 쿼터/피처 게이팅을 E2E 검증한다.

- **GAP-004 신규 구현(로직층)**:
  - `backend/app/entitlements.py`(신규) — `TIER_LIMITS` dict(free/standard/sacred/enterprise → daily_quota/max_quality/features/price_cny) + `resolve_limits(db, tier_key)`(tier_key None/미지→`free` 폴백, free 쿼터는 런타임 `settings.per_user_daily_quota` 동적판독, paid 쿼터는 DB `PricingTier` 행 우선) + `has_feature()` + `VALID_TIERS`.
  - `backend/app/quota.py:50-56` — `enforce_generate_limits` 가 전역 flat `per_user_daily_quota` 가 아닌 `resolve_limits(db, user.tier_key)["daily_quota"]` 로 티어별 쿼터를 강제.
  - `backend/app/routers/auth.py:176-197` — `POST /api/auth/subscribe`(mock): `SubscribeReq{tier_key}` 검증(미지 티어→400 "unknown tier_key"), `user.tier_key` 설정 후 `resolve_limits` 반환. 실제 PG 미연동 상태 명시 주석.
  - `backend/app/routers/songs.py:590-627` `my_billing` 보완: `daily_quota_limit` 를 하드코딩 `settings.per_user_daily_quota` 에서 `resolve_limits(db, user.tier_key)["daily_quota"]` 로 교정(구독 후 standard=20 정상반영 실증), `tier_key` 표시도 `resolve_limits` 정규화(None→`free`)로 신규유저 `null` 표시 버그 제거.
  - `backend/tests/test_entitlements.py`(신규) — `resolve_limits` free/paid/미지폴백, `has_feature`, subscribe 엔드포인트 티어갱신·미지거부 6건. `db` fixture 는 전역아닌 파일로컬 정의(test_economics 패턴 차용).

- **수정한 버그(이번 증분에서 발견·수정)**:
  1. `my_billing` 이 `daily_quota_limit` 을 항상 `settings.per_user_daily_quota`(=3) 로 반환 → 구독 후에도 3 표시. `resolve_limits` 도입으로 해결.
  2. 신규유저 `user.tier_key=None` → `/me/billing` 에 `tier=null` 표시. `resolve_limits(...)[tier_key]` 정규화로 `free` 표시.
  3. `TIER_LIMITS["free"]["daily_quota"]` 를 import-time 에 고정 캡처 → 런타임 `settings.per_user_daily_quota=10` 패치 테스트(`test_extend_cover_remix`)가 4번째 곡에서 실패. 해결: free 는 매 호출 `settings.per_user_daily_quota` 동적판독.
  4. `test_entitlements.py` 가 전역 `db` fixture 부재로 ERROR — 파일로컬 fixture 추가.

- **검증 결과**: 백엔드 회귀 **133 passed / 0 failed**(기준 127 → GAP-004 테스트 6건 증가). 프론트 `npx tsc --noEmit` 0 에러. 라이브 E2E(`:8000`, JWT_SECRET 고정 부팅):
  - 회원가입 200 → `/me/billing`(fresh) `tier=free, quota=3`
  - `POST /api/auth/subscribe {tier_key:"standard"}` 200(limits.daily_quota=20) → `/me/billing` `tier=standard, quota=20`
  - `POST /api/auth/subscribe {tier_key:"platinum"}` → 400 "unknown tier_key"
  - 무토큰 `/me/billing` → 401
  - **최종: PASS**
  - 실운영 반영: 기존 `:8000` 스테일 서버(PID 12068) 종료 후 동일 포트에 현재 코드 재기동(사용자 UI 테스트 정합성 확보). 부수 테스트 서버(:8001/:8002) 정리 완료.

- **잔여 Phase 0**: GAP-004 실제 PG 결제 연동(Stripe/Kakao/Naver), GAP-002(이메일인증/재설정/MFA), GAP-003(OAuth), GAP-006(admin RBAC/감사로그), GAP-007(api_key 암호화).

### 2026-09-03 (속행) — Phase 0 세 번째 증분: 무료티어 loss-leader 가격 + GAP-007(api_key 암호화) + GAP-006(admin RBAC/감사로그)
> 사용자 지시: "무료 가격 내가 정할게(내 판단 요청) → **loss-leader** 채택", "다음 구현은 **보안 클러스터(권장)**", "9.0(Mureka-9)이 반쯤 된 거 아니냐? → **감사(audit) 먼저**". 본 증분은 이 셋을 모두 처리.

- **무료티어 가격 의미 확정 (loss-leader)**: `backend/app/orchestrator/selection.py:193-213` `resolve_price_cny` 개정.
  - `tier_key is None or "free"` → `return 0.0`(사용자에게 청구 안 함). `compute_margin_cny` 가 `0 - cost` 를 계산 → **마진 = −원가(획득비용)**. Req6 "무료=시용상한·불收款" 원의에 부합.
  - 유료 티어(price_cny>0) → 표준가; 유료인데 가격 미설정 → 보수적 break-even(엔진원가) 폴백.
  - 테스트 `test_economics.py:45,47,79-80` 를 0.0 / −0.33 로 갱신. (기존 곡 1건(song id=1, price=0.33/margin=0.0) 은 historical 로 그대로, 신규 생성만 loss-leader 반영.)
- **GAP-007 api_key 정적 암호화(at-rest) 구현**:
  - `backend/app/security/crypto.py`(신규) — Fernet(AES-128-CBC+HMAC-SHA256). 키는 `settings.encryption_key`(미설정 시 고정 dev 키 폴백, 운영 경고 필요). `encrypt_secret`/`decrypt_secret` + **구 plaintext 호환 복호화**(마이그레이션 무중단).
  - `backend/app/models.py:95-117` `EngineConfig` 에 `set_api_key()`(저장 시 암호화, 빈값은 빈 문자열로 토큰화 방지)/`get_api_key()`(읽기 시 복호화) 추가.
  - `backend/app/routers/admin.py:210-229, 232-248` create/update_engine 가 평문이 아닌 `set_api_key` 로 암호화 저장. `GET` 응답은 기존 `api_key_set` 불리언 마스킹 유지(평문 미노출).
  - `backend/requirements.txt` 에 `cryptography>=42.0.0` 추가(python-jose[cryptography] 가 transitively 제공하긴 하나 명시화).
  - `test_security_crypto.py`(신규, 4건): 라운드트립/None·빈값/구 plaintext 호환/모델 헬퍼.
- **GAP-006 admin RBAC + 감사로그 구현**:
  - `backend/app/routers/auth.py:157-175` `require_admin` 이 `super`(admin_token) / `viewer`(admin_viewer_token) 두 토큰 수용. 역할 판정 `_resolve_admin_role` 분리.
  - `backend/app/main.py:44-80` `admin_rbac_audit` 미들웨어: ① viewer 가 변경类(GET 외) 인터페이스 호출 시 **403**(super 는 무영향), ② 모든 `/api/admin` 접근(성공·401·403 포함)을 `audit_logs` 에 기록(역할/토큰접두사 6자/IP/메서드/경로/상태).
  - `backend/app/models.py` `AuditLog` 모델(신규) + `backend/migrations/versions/f6a7b8c9d0e1_add_audit_logs.py`(신규 마이그레이션, down_revision=e5f6a7b8c9d0) + `backend/app/audit.py` `log_admin_access` 헬퍼.
  - `backend/app/routers/admin.py` `GET /api/admin/audit`(response_model=list[AuditLogOut], viewer 도 읽기 허용) + `schemas.py` `AuditLogOut` 추가.
  - `test_admin_rbac.py`(신규, 2건): viewer 변경 403 / super 변경 200 + 감사로그 기록·조회. `test_alembic.py` 는 신규 테이블 반영으로 통과.
- **Mureka-9("9.0") 감사 결론 (사용자 질문 "반쯤 된 거 아니냐?" 에 대한 답)**:
  - **CORE(가사→곡) = 완료+실기검증**: `mureka.py:139-165` generate, `:167-203` query, `:205-223` billing. 전회 세션 실제 $0.045 생성으로 `choices[]` 실제 필드(url/flac_url/duration/lyrics_sections/id/wav_url)·원가 ¥0.33 까지 확정.
  - **EXTENDED = 미구현(코드에 "future/out-of-scope" 명시)**: `mureka.py:21-24` — easy-generate, song/extend, song/remix, song/vocal-clone, instrumental/generate, lyrics/generate, song/stem, song/region-edit, soundtrack/track/video/tts, files/upload. 즉 **Mureka-9 는 ~50% 완성**(핵심 생성 OK, 파생 기능 0). **프론트 `Player.tsx` 의 extend/cover/remix 버튼은 백엔드 엔드포인트가 없어 현재 데드** — 이건 별도 GAP(신규 후보 GAP-149 제안).
  - **"1분짜리 9.0 / 저档位" 방향**: `mureka.py:145-153` generate 페이로드가 `lyrics/model/n/prompt/gender` 만 보내고 **duration 필드가 없음** → "1분 버전" 은 Mureka 가 duration 파라미터를 지원할 경우에만 추가 가능(공식 문서 미공개 → 실기 확인 필요). "저档位" 는 mureka-7.6 등 저가 모델 또는 짧은 생성으로 라우팅하는 별도 티어/엔진설정이 필요. **둘 다 별도 구현 아이템**으로 분리 권고(이번 턴은 무단 구현 안 함 — 범위/의미 불명확).

- **검증 결과**: 백엔드 회귀 **139 passed / 0 failed**(기준 133 → 본 증분 +6: test_security_crypto 4 + test_admin_rbac 2; 기존 2건은 loss-leader/audit_logs 마이그레이션 반영으로 정정). `py_compile` OK, `import app.main` OK(미들웨어·audit·crypto 로드 확인). 라이브 E2E(`:8000` 재기동, JWT_SECRET 고정, ADMIN_TOKEN=devadmin123, CWD=backend 로 `.env` MUREKA_API_KEY 로드):
  - `/health` 200, admin 무토큰 401 / 오토큰 401 / 정상 200
  - `POST /api/admin/engines`(api_key=supersecret123) 201, `api_key_set=True`
  - `GET /api/admin/audit` 200, 샘플 `(POST,/api/admin/engines,super)`, `(GET,/api/admin/engines,none)`(401 시도도 기록) 확인
  - **live `app.db.engine_configs.api_key` = `gAAAAAB…`(Fernet 토큰) → 평문 비저장 확정(GAP-007 실증)**
  - 정리: live_test 엔진 DELETE 완료.
  - **최종: PASS**
- **잔여 Phase 0**: GAP-004 실제 PG 결제(Stripe/Kakao/Naver), GAP-002(이메일인증/재설정/MFA), GAP-003(OAuth). (GAP-006/GAP-007 본 증분으로 완료)

---

## 2026-09-03 (속행) — Phase 0 추가 증분: GAP-004 step-4 실제 로컬 PG 4종 통합 완료

> 선행 "잔여 Phase 0: GAP-004 실제 PG 결제(Stripe/Kakao/Naver)" 항목을 본 증분으로 **실제 구현 완료**(Stripe 는 step-2 webhook 이미 구현; 금회 kakao/naver/wechat/alipay 신규).
> 상세 live 운영 절차는 `docs/BILLING_LIVE_GO_LIVE.md` 참조.

- **신규 provider 4종** (`backend/app/billing/pg/{kakao,naver,wechat,alipay}.py`, 모두 `httpx`+`cryptography`+표준라이브러리, **신규 의존성 0**):
  - kakao/naver = **approve 형**(webhook 없음): checkout(handoff) → 프론트 회跳 → `POST /api/billing/{kakao,naver}/approve` 서버승인 성공 시에만 승격.
  - wechat/alipay = **webhook 형**: Native QR / 페이지 점프 → 비동기 콜백 **서명검증 성공** 시에만 승격. (wechat: RSA-SHA256 플랫폼공개키 검증 + APIv3 AES-256-GCM 복호화; alipay: RSA2 알리페이 공개키 검증, `sign_type` 포함 서명 규칙 일치)
- **라우터 일반화** (`backend/app/routers/billing.py`): stripe-only webhook → `WEBHOOK_PROVIDERS`/`APPROVE_PROVIDERS` 기반 분기; kakao/naver approve 엔드포인트 신규; checkout 오류 등급화(400/402/409/502/503) + `provider_params` 투명; `GET /api/billing/providers` 신규(flow/settle/통화, 키·설정상태 비노출).
- **ProviderNotConfigured 단일 소스** (`backend/app/billing/provider.py`): stripe 미설정이 502(의미 오류)로 오인되던 문제 해소 → 라우터가 일관되게 **503** 처리.
- **환불 폐쇄 루프** (`backend/app/billing/service.py:217 apply_refund_event`): `subscriptions.provider_customer_id`(마이그레이션 `e1f2a3b4c5d6`) 로 charge.refunded(고객 ID만 있음) → 사용자 역추적 → 권한 강등.
- **검증 결과**: 백엔드 회귀 **195 passed / 0 failed**(billing 단일파일 38건 포함: 4 PG 서명/복호화 roundtrip + kakao/naver approve E2E + providers 엔드포인트). 프론트 `tsc --noEmit` 0 에러.
- **GAP 상태 갱신**: GAP-051(실제 PG 결제 0건) / GAP-052(webhook 서명검증 없음) → **본 증분으로 해소**. 잔여: GAP-002(이메일인증/재설정/MFA), GAP-003(OAuth), Mureka EXTENDED(~50% — 별도 GAP-149 후보).
