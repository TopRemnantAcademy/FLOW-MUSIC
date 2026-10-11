# 찬송 + 광장 병합 방안 — 「음악」 단일 탭 설계·구현 보고

## ⚠️ SUPERSEDED（部分）— 已被 ISSUE #72 决策取代（2026-10-11）

> **本文的「并入 FLOW App 作单一『音乐』Tab」结论已被推翻。**
> 用户于 2026-10-11 在 ISSUE #72 拍板 **决策 3 = A 独立 App**（`com.flowccm.app`）。
> FLOW App 侧的「音乐」Tab 降级为**跳转入口**——那是 FLOW 仓库的改动，
> 另开 issue 跟踪，**不在本仓库实施**。
>
> - 受影响项：本文「하단 탭 6 → 5，찬송+광장 合并为『음악』」的**产品最终形态**判定。
> - 本文记录的**实现与真机验证事实仍然有效**（历史轨迹），仅「最终归属」被改写。
> - **原文保留**（不删）。
>
> 取代指针：ISSUE #72。

- 작성일: 2026-09-21
- 범위: FLOW APP (Capacitor 6 / `mobile/`) 하단 탭 구조 개편
- 상태: **구현 완료 + web-e2e 통과 + 真机 검증 완료**（되돌리기 3줄）
- 산출물: `builds/flow-ai-default-v1.0.0-r22.4-musicui.apk`（真机 설치·검증）

---

## 1. 결론 (TL;DR)

> **찬송(라이브러리)과 광장(커뮤니티)은 "곡을 고른다 → Player 로 재생한다" 는 목적이 완전히 동일하므로,
> 하단 탭 2개를 1개(`음악`)로 합치고 상단 세그먼트 `찬송 | 광장` 으로 전환하는 방식이 최적이다.**

- 하단 탭 **6 → 5** (`bible / music / today / word / mypage`) — 탭바가 여유를 되찾고 라벨이 잘리지 않음
- 두 라이브러리가 **동일한 전역 Player(미니바 + 전면 패널)를 이미 공유**하고 있었으므로 재생 의미론 변경이 **0**
- 라우트 별칭(`hymns` / `plaza`)을 남겨 **기존 딥링크·내부 호출·테스트가 깨지지 않음** (하위호환)
- 되돌리기 = `TABS` 1줄 + `SCREEN_RENDERERS` 2줄 복구 (아래 §5)

---

## 2. 왜 병합이 자연스러운가 (근거)

| 항목 | 근거 |
|---|---|
| 목적 동일 | 두 화면 모두 `article.track` / 카드 선택 → `Player.play(track, queue, idx)` → `openSheet()` |
| Player 공유 | `mobile/player.js` 는 전역 싱글턴. 찬송 `playOrToggle` 과 광장 `plaza-play` 가 **같은 `Player` API** 호출 |
| 결합도 | 탭 id 리터럴 참조가 `mobile/components/index.js`(TABS 2행) + `mobile/app.js`(SCREEN_RENDERERS 2행) **단 3곳**뿐 (android/backend/scripts 전수 grep 결과 0건) |
| 화면 독립성 | `renderHymns()` / `renderPlaza()` 가 각자 `#screen-container` 를 `empty()` 후 자체 렌더 → **본문 컨테이너만 주입하면 그대로 재사용 가능** |
| 재생 상태 | `_plazaUnSub` 로 Player 이벤트를 구독 → 세그먼트 전환 시에도 재생 상태 일관 |

---

## 3. 대안 비교

| 안 | 내용 | 장점 | 단점 | 판정 |
|---|---|---|---|---|
| **A. 완전 병합 (채택)** | 탭 1개 `음악` + 상단 세그먼트 `찬송\|광장` | 탭바 6→5, 목적 일치, 재생 의미론 무변경, 되돌리기 쉬움 | 세그먼트 상태가 앱 재시작 시 초기화(기본 찬송) | ✅ **채택** |
| B. 2탭 유지 + 상호 진입 버튼 | 탭은 그대로, 각 화면 끝에 "광장 가기" CTA | 변경 최소 | 사용자가 원한 "하나로 묶기" 미충족, 탭바 과밀(6개) 유지 | ✗ |
| C. 광장을 찬송 하위 섹션으로 흡수 | 찬송 목록 안에 광장 섹션 삽입 | 탭 1개 | 광장의 독립 필터(인기/최신/평점)·페이지네이션·작성자 카드가 라이브러리 목록과 성격 충돌 → 정보구조 혼탁 | ✗ |

> 권고: **A**. 단, 광장이 향후 "내 작품/업로드" 기능을 얻으면 `마이` 탭이 아니라 `음악 > 광장` 안에서 처리하는 것이 일관적이다.

---

## 4. 구현 변경 목록 (file:line)

### 4.1 탭·라우팅

| 파일 | 라인 | 변경 |
|---|---|---|
| `mobile/components/index.js` | 25–33 | `TABS`: `hymns`+`plaza` 2행 → `{ id:'music', icon:'music', labelKey:'tab.music' }` 1행 |
| `mobile/app.js` | 19–21 | `SCREEN_RENDERERS`: `music: renderMusic` 추가, `hymns`/`plaza` 는 **세그먼트 별칭**으로 유지 |
| `mobile/app.js` | 40–42 | `activeTabId` 정규화 — `hymns`/`plaza` 라우트로 진입해도 `음악` 탭이 active 표시 |

### 4.2 화면

| 파일 | 라인 | 내용 |
|---|---|---|
| `mobile/screens/index.js` | 762 | `let _musicSeg = 'hymns'` (기본 세그먼트 = 찬송) |
| `mobile/screens/index.js` | 765 | `_segRoot()` — 내부 재렌더(fav 토글 등)가 세그먼트 바를 지우지 않도록 본문(`#music-body`) 우선 반환 |
| `mobile/screens/index.js` | 767–781 | `_buildMusicSegBar()` — `찬송 \| 광장` 세그먼트 바 |
| `mobile/screens/index.js` | 784–795 | `renderMusic(seg)` — 세그먼트 바 + `#music-body` 생성 후 기존 렌더러에 위임, `plaza` 세그먼트일 때 `#screen-container` 에 `plaza-screen` 클래스 부여 |
| `mobile/screens/index.js` | 797 / 2924 | `renderHymns(root)` / `renderPlaza(root)` — 컨테이너 주입 가능하게 시그니처 확장(미주입 시 기존과 동일 동작) |
| `mobile/screens/index.js` | 15 / 156 / 285 / 413 | i18n 4개 언어에 `tab.music` + `music.seg.hymns` + `music.seg.plaza` 추가 |

### 4.3 스타일

| 파일 | 라인 | 내용 |
|---|---|---|
| `mobile/styles.css` | 1476–1487 | `.music-seg-bar`(sticky, 상단 고정) / `.music-seg` / `.music-seg.active`(금색) / `.music-body` |

### 4.4 테스트 동기화 (필수)

| 파일 | 라인 | 내용 |
|---|---|---|
| `mobile-e2e/tests/01-smoke.spec.mjs` | 22 | `EXPECTED_TABS` 6 → 5 (`music` 로 교체) |
| `mobile-e2e/tests/02-playback.spec.mjs` | 17 | 탭 셀렉터 `data-tab="hymns"` → `data-tab="music"` |
| `mobile-e2e/tests/02-playback.spec.mjs` | 31–37 | 재생 아이콘 판정을 **이모지 textContent → SVG 도형**(pause=`rect` 2 / play=`path` 1)으로 변경 |

---

## 5. 되돌리기 (Rollback)

정확히 3곳만 복구하면 병합 이전 상태로 돌아간다.

1. `mobile/components/index.js` 30행 → `{ id:'hymns', … }` + `{ id:'plaza', … }` 2행으로 복원
2. `mobile/app.js` 19–21행 → `hymns: renderHymns`, `plaza: renderPlaza` 로 복원
3. `mobile-e2e/tests/01-smoke.spec.mjs:22` → 6개 배열로 복원

`renderMusic` / `_buildMusicSegBar` / CSS 는 잔존해도 무해하다(미호출). `renderHymns(root)` / `renderPlaza(root)` 의 `root` 인자는 미주입 시 기존 경로(`#screen-container`)로 동작하므로 그대로 둬도 된다.

---

## 6. 검증 (실측)

### 6.1 web-e2e (Playwright, 360×772)

```
Running 3 tests using 1 worker
  ok 1 [chromium] › tests\01-smoke.spec.mjs    S1: app shell renders with tab bar and no uncaught JS errors (3.1s)
  ok 2 [chromium] › tests\02-playback.spec.mjs S2: selecting a hymn track shows mini player and toggles play/pause (4.5s)
  ok 3 [chromium] › tests\03-offline-sw.spec.mjs S3: service worker registers and app shell loads offline (3.9s)
  3 passed (53.7s)
```

### 6.2 실기기 (SM-N986N / R3CN80KGTJD, 720×1544 @dpr2)

| 확인 | 결과 |
|---|---|
| 탭바 | **5개** `성경 / 음악 / 오늘 / 말씀 / 설정` 렌더, `음악` = 음표 아이콘 |
| `음악` 진입 | 상단 세그먼트 `찬송(활성·금색) \| 광장` 정상 |
| `광장` 전환 | hero / 정렬칩(인기·최신·평점) / `▶ 미리듣기` 정상, 세그먼트 상태 유지 |
| 재생 (실음원) | `dumpsys media_session` → `state=PlaybackState {state=PLAYING(3), position=2797, buffered=60029}` |
| 크래시 | `logcat` FATAL/AndroidRuntime 0건, `pidof com.gospelai.app` 생존 |
| 미니바 지속 | 세그먼트 전환·탭 이동 후에도 미니바 유지 |

> 실음원 재생은 `adb reverse tcp:8000 tcp:8000` + 로컬 백엔드(ExoPlayer 스트리밍)로 확인했다(§7-① 참조).

---

## 7. 남은 결정 사항 (사용자 입력 필요)

### ① 프로덕션 API base 미설정 (배포 블로커)
- `mobile/capacitor.config.json` 의 `server` 키가 없고(의도된 상태), `.env.mobile` 이 **존재하지 않음**
- `scripts/build-config.js:110-113` → `--api` / `.env.mobile` / `ANDROID_API_BASE` 미지정 시 `http://127.0.0.1:8000` 으로 굳는다
- 현재 r22.4 APK 는 **`--allow-localhost` 로 빌드된 진단용**이다(루프백 cleartext 허용). 실배포용은 공개 HTTPS 도메인을 `--api=https://…` 로 주입해야 한다.

### ② 기본 세그먼트
- 현재 **찬송** 고정. "마지막 본 세그먼트 기억"이 필요하면 `AppState` 영속화 1곳 추가로 가능.

### ③ `광장` 탭 아이콘
- 병합으로 `plaza` 글러브 아이콘은 탭바에서 사라지고 `음악`(음표)으로 통일됨. 세그먼트에는 텍스트만 사용 중(의도).

---

## 8. 부록 — 이번 라운드에 함께 수정된 재생 UI 결함 (실측 기반)

병합 작업 중 `음악` 탭에서 발견되어 같이 고친 항목. **모두 360×772 / 360×660 두 뷰포트 실측으로 회귀 확인.**

| 결함 | 원인 (file:line) | 수정 |
|---|---|---|
| 재생 버튼이 컬러 이모지로 렌더 | `▶ ⏸ ⏮ 🔀` 등 텍스트 글리프 | `mobile/player.js:827` `_PICONS` + `pIcon()` 인라인 SVG(`currentColor`) 로 전면 교체. `_mkActionIco`(:848) 로 액션시트까지 적용 |
| 전면 패널 하단 툴행(A–B/EQ/수면) 잘림 | 콘텐츠 663px vs `92dvh≈635px` (기기 실제 뷰포트는 시스템바 제외 ~690px) | `mobile/styles.css:573` 패널을 `display:flex;flex-direction:column` + `:609` 가사 영역만 `flex:1 1 auto` → **overflow 0** (772/660 양쪽) |
| 헤더 ✕ 와 큐 버튼 겹침 | DOM 은 `.player-toprow` 플렉스인데 `.player-close` 가 구버전 `position:absolute` | `mobile/styles.css:582` `position:static` + 40px 정렬 → **overlap 면적 0** |
| 광장 hero 제목이 `FLOW 광 / 장` 으로 분절 | CJK 기본 `word-break` 로 글자 단위 줄바꿈 | `mobile/styles.css:1507` `word-break:keep-all` + hero `flex-wrap` → **titleLines=1** |
| 「전체 찬송」 헤더가 `전…` 로 잘림 | 제목 77 + 액션 270 = 355px > 가용 332px, `flex-wrap:nowrap` | `mobile/styles.css:509` `flex-wrap:wrap` → 제목 **332px 만폭, 잘림 0** |
| 무음 상태에서 "가짜 재생" | 로컬 `hymns.json` 에 `stream_url` 0건 → 합성 패드(peak gain 0.05) 폴백 | `mobile/player.js` `state.noSource` + `_notifyNoSource()` 토스트(4초 스로틀), `player.noSource` i18n 4개 언어 |
