/* ═══════════════════════════════════════════════════════════════════════════
   build-config — 웹(PWA) / 안드로이드(APK) / iOS(IPA) 빌드 시 API 주소 + 시장/화이트라벨 테넌트 설정 주입

   ⚠️ 중요: 이 스크립트는 소스(mobile/index.html)를 절대 수정하지 않는다.
      빌드 결과는 항상 mobile/dist/<tenant?>/<target>/ 로 출력된다.
        web     → mobile/dist/[<tenant>/]web/      (PWA 정적 서빙용)
        android → mobile/dist/[<tenant>/]android/  (Capacitor webDir)
        ios     → mobile/dist/[<tenant>/]ios/      (Capacitor webDir, 네이티브는 macOS/Xcode)
      → 서버(8000) · PWA(4174) · APK/IPA 빌드가同一个 소스를 공유하더라도
        빌드 산출물이 소스를 덮어쓰지 않아 서로 밟지 않는다(混淆 根本消除).

   화이트라벨(기업별 위탁 빌드):
      tenants/<id>/config.json 에 테넌트별 applicationId/bundleId/API/브랜딩을 둔다.
      --tenant=<id> 로 빌드하면 mobile/dist/<id>/<target>/ 로 격리 출력 + appId 분리.
      테넌트 미지정 시 기존 경로(mobile/dist/<target>/) 유지(하위호환).

   index.html 에는 플레이스홀더가 다섯 개 있다:
     __GOSPEL_API_BASE__        → 백엔드 API 주소 (FLOW 主后端)
     __GOSPEL_GOOGLE_SDK__       → Google Identity Services SDK <script> 태그 (중국은 비움)
     __GOSPEL_PORTONE_SDK__      → PortOne V2 결제 SDK <script> 태그
     __GOSPEL_PAYMENT_CHANNEL__  → 런타임 window.GOSPEL_PAYMENT_CHANNEL ('direct'|'googleplay')
     __GOSPEL_DONATE_URL__       → 런타임 window.GOSPEL_DONATE_URL (후원 페이지 외부 링크, 미설정='')
     __MUSIC_API_BASE__          → FLOW CCM 音乐广场 백엔드 (ai-music-platform, FLOW 主后端과 공유 DB 의 별도 서비스)

   사용법:
     node scripts/build-config.js web              --api=https://api.domain.com
     node scripts/build-config.js android --tenant=<id> --api=https://api.<brand>.com
     node scripts/build-config.js android --tenant=<id> --channel=googleplay
     node scripts/build-config.js ios     --tenant=<id>   # macOS/Xcode 에서 cap add ios 필요

   배포 variant = (channel, market) 조합 = 하나의 배포 단위. 패키지명과 버전선이 각각 독립이다.
     기본(direct + hk/global) → com.gospelai.app       version.json 루트 버전선
     market=cn                → com.gospelai.app.cn    version.json → variants.cn
     channel=googleplay       → com.gospelai.app.play  version.json → variants.play
   자세한 규범: docs/ANDROID_VARIANT_VERSIONING.md

   --market=cn   : 중국 직분용. Google SDK 주입 안 함. 단 PortOne 은 주입(위챗/알리페이
                   해외 채널이 PortOne 에 내장 — 별도 제3자 SDK 아님). API는 CN_API_BASE.
   --market=global: 해외 배포용. Google GIS SDK + PortOne 모두 주입.
   --channel=googleplay: Google Play 상점용. PortOne SDK 완전 제거(제3자 결제 코드 0),
                   런타임 채널='googleplay' → openPayment 가 GP Billing placeholder 로 폴백.
                   (GP 정책: 제3자 결제 금지 → 물리적 격리 필수)
  --channel=appstore: App Store 상점용. PortOne SDK 완전 제거(제3자 결제 코드 0),
                   런타임 채널='appstore' → openPayment 가 StoreKit placeholder 로 폴백.
                   (Apple 가이드라인 3.1.1: 디지털 상품은 StoreKit(IAP) 전용. 제3자 결제 SDK 는
                    IPA 정적 스캔으로 적발 → 리젝·계정 종료 위험 → 물리적 격리 필수)
                   ⚠️ iOS 타겟의 **기본값**이다(안전 기본값). --channel=direct 는 TestFlight/엔터프라이즈 전용.

   환경변수 파일(.env.mobile)로도 주입 가능:
     WEB_API_BASE=...   ANDROID_API_BASE=...
     CN_API_BASE=...    GLOBAL_API_BASE=...
     MUSIC_API_BASE=... (FLOW CCM 音乐广场 백엔드 주소, GOSPEL_API_BASE 와 독립)

  ⚠️ 안드로이드/ios 타겟은 API base 가 localhost/127.0.0.1 이면 기본적으로 빌드 실패(fail-fast)합니다.
     공개 HTTPS 도메인을 --api 또는 .env.mobile 로 주입하세요. 로컬 에뮬레이터 테스트용은 --allow-localhost.
     (릴리스 APK 에 localhost 가 박히는 사고 재발 방지)
   ═══════════════════════════════════════════════════════════════════════════ */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC_DIR = path.join(ROOT, 'mobile');          // 소스(절대 수정 금지)
const INDEX = path.join(SRC_DIR, 'index.html');     // 소스 템플릿
const DIST_ROOT = path.join(SRC_DIR, 'dist');       // 빌드 산출물 루트

const PH_API = '__GOSPEL_API_BASE__';
const PH_GOOGLE = '__GOSPEL_GOOGLE_SDK__';
const PH_PORTONE = '__GOSPEL_PORTONE_SDK__';
const PH_CHANNEL = '__GOSPEL_PAYMENT_CHANNEL__';
const PH_DONATE = '__GOSPEL_DONATE_URL__';
const PH_MUSIC_API = '__MUSIC_API_BASE__';   // FLOW CCM 音乐广场后端 (ai-music-platform)

const GOOGLE_SDK_TAG = '<script src="https://accounts.google.com/gsi/client" async defer onerror="window.__gisFailed=true"></script>';
const PORTONE_SDK_TAG = '<script src="https://cdn.portone.io/v2/browser-sdk.js"></script>';

// dist 에 복사하지 않을 항목(소스 전용/불필요)
//  - dist / node_modules / .git: 빌드 산출물·의존성·버전관리
//  - portal.html: 개발용 포털(하드코딩된 127.0.0.1:8501/8502/8000/docs/4174), 릴리스에 절대 포함 금지
//  - __mocks__: 테스트 스텁
//  - *.md / *.log / *.test.* : 문서·로그·테스트 파일 → release 누수 방지
const COPY_EXCLUDE_NAMES = new Set(['dist', 'node_modules', '.git', 'portal.html', '__mocks__']);
function isCopyExcluded(name) {
  if (COPY_EXCLUDE_NAMES.has(name)) return true;
  if (name.endsWith('.md')) return true;
  if (name.endsWith('.log')) return true;
  // [2026-10-02] 빌드 산출물(.apk/.aab)은 웹 자산에 절대 포함하지 않는다.
  //   루트 releases/ 를 출력으로 복사할 때 APK 가 그대로 들어가, 앱이 자기 자신을 품어
  //   용량이 12MB → 24MB 로 두 배 부풀었다. 운영은 백엔드 update_url(호스팅 APK) 경로를
  //   쓰므로 오프라인 폴백용 자기복제는 불필요 → 웹 자산에서는 제외한다.
  if (name.endsWith('.apk') || name.endsWith('.aab')) return true;
  if (name.includes('.test.')) return true; // *.test.cjs 등
  return false;
}

function loadEnvMobile() {
  const envPath = path.join(ROOT, '.env.mobile');
  const out = {};
  if (fs.existsSync(envPath)) {
    for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
      const m = line.match(/^\s*([\w.]+)\s*=\s*(.*)\s*$/);
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  }
  return out;
}

function arg(flagName, argv) {
  const f = argv.find(a => a.startsWith(flagName + '='));
  return f ? f.slice(flagName.length + 1) : null;
}

function getApiBase(target, market, argv, env, tenant) {
  const fromFlag = arg('--api', argv);
  if (fromFlag) return fromFlag;
  // 화이트라벨 테넌트: tenants/<id>/config.json 의 api[market] 가 최우선(env 보다 위).
  if (tenant && tenant.api && tenant.api[market]) return tenant.api[market];
  if (market === 'cn') return env.CN_API_BASE || 'http://127.0.0.1:5000';   // 중국 채널 = payment-gateway(:5000)
  if (market === 'global') return env.GLOBAL_API_BASE || 'http://127.0.0.1:8000'; // 글로벌/다이렉트 = 메인 백엔드(:8000)
  // market 미지정: 타겟 기본
  if (target === 'android' && env.ANDROID_API_BASE) return env.ANDROID_API_BASE;
  if (target === 'web' && env.WEB_API_BASE) return env.WEB_API_BASE;
  return 'http://127.0.0.1:8000';
}

// 소스 템플릿(index.html)에서 플레이스홀더 4개 존재 확인.
// 소스는 git 에 의해 보존되므로 백업 불필요 — 손실 시 git checkout 으로 복구.
function readTemplate() {
  if (!fs.existsSync(INDEX)) {
    console.error('[build-config] mobile/index.html 소스 템플릿이 없습니다. git checkout mobile/index.html 후 재시도하세요.');
    process.exit(1);
  }
  const html = fs.readFileSync(INDEX, 'utf8');
  const ok = [PH_API, PH_GOOGLE, PH_PORTONE, PH_CHANNEL, PH_DONATE, PH_MUSIC_API].every(p => html.includes(p));
  if (!ok) {
    console.error('[build-config] mobile/index.html 에 플레이스홀더(' + PH_API + '/' + PH_GOOGLE + '/' + PH_PORTONE + '/' + PH_CHANNEL + '/' + PH_MUSIC_API +
      ')가 없습니다. 소스 템플릿이 손상되었으니 git checkout mobile/index.html 후 재시도하세요.');
    process.exit(1);
  }
  return html;
}

// 소스 디렉터리 전체를 출력 디렉터리로 복사(재귀, exclude 적용).
function copyTree(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (isCopyExcluded(entry.name)) continue;
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyTree(s, d);
    } else if (entry.isFile()) {
      fs.copyFileSync(s, d);
    }
  }
}

// 단일 파일을 조건부 복사(원본 없으면 무시).
function copyIfExists(src, dest) {
  if (!fs.existsSync(src)) return;
  fs.copyFileSync(src, dest);
  console.log('[build-config] 복사: ' + path.relative(ROOT, src) + ' → ' + path.relative(ROOT, dest));
}

// 디렉터리를 조건부 복사(원본 없으면 무시). releases/ 는 gitignore 되어 있으므로 출력에도 미포함.
function copyDirIfExists(src, dest) {
  if (!fs.existsSync(src)) return;
  copyTree(src, dest);
  console.log('[build-config] 복사: ' + path.relative(ROOT, src) + ' → ' + path.relative(ROOT, dest));
}

// ── 화이트라벨 테넌트 설정 로드 ──
// tenants/<id>/config.json 를 읽는다. 존재하지 않으면 실패(온보딩 체크리스트 참고).
function loadTenant(id) {
  // ⚠️ 보안: id 는 CLI(--tenant) 에서 온다. path.join 으로 조립 후 readFileSync 하므로
  // '..'/'/'/'\\' 가 들어오면 임의 경로 노출(파일 인클루전)이 가능했음(Aikido High).
  // 엄격한 slug allowlist 로 경로 탐색을 원천 차단한다.
  if (!/^[A-Za-z0-9_-]+$/.test(id)) {
    console.error('[build-config] tenants id 는 [A-Za-z0-9_-] 만 허용됩니다 (경로 탐색 차단): ' + JSON.stringify(id));
    process.exit(1);
  }
  const p = path.join(ROOT, 'tenants', id, 'config.json');
  if (!fs.existsSync(p)) {
    console.error('[build-config] tenants/' + id + '/config.json 이 없습니다. tenants/README.md 온보딩 체크리스트를 참고해 생성하세요.');
    process.exit(1);
  }
  const cfg = JSON.parse(fs.readFileSync(p, 'utf8'));
  cfg.id = id;
  return cfg;
}

/* ── 배포 variant(변종) ────────────────────────────────────────────────────
   하나의 '배포 단위' = (channel, market) 조합. 각 variant 는 반드시
     ① 고유한 applicationId(패키지명)  ② 독립된 versionCode 증가선
   을 가져야 한다. 같은 패키지명으로 서로 다른 바이너리를 배포하면
   사용자가 A 를 쓰다가 B 를 설치할 때 Android 가 동일 앱으로 취급해
   (versionCode 가 높으면) 조용히 덮어쓰거나 (낮으면) INSTALL_FAILED_VERSION_DOWNGRADE
   로 막힌다 — 중국판(Google SDK 0)과 글로벌판(Google SDK 포함)이 여기에 해당.

   정의는 scripts/variants.js 한 곳에만 있다(여기서 재정의하지 않는다 — 중복 방지).

   ⚠️ 실측(2026-09-09): 이전에는 channel=googleplay 만 'com.gospelai.app.play' 로
   appId 를 바꿨지만 **빌드 산출물에는 반영되지 않았다**. 이유는
   Capacitor CLI 가 appId → build.gradle(applicationId/namespace) 반영을
   `cap add android` 시점에만 수행하기 때문
   (node_modules/@capacitor/cli/dist/tasks/add.js:115 → android/common.js:86,88).
   `cap sync`(android/update.js) 는 appId 를 전혀 건드리지 않는다.
   → 따라서 build.gradle 은 **이 스크립트가 직접** 기록해야 한다.
   ------------------------------------------------------------------------ */
const V = require('./variants');
const resolveVariant = V.resolveVariant;
const resolveAndroidAppId = V.resolveAppId;

/* 현재 커밋의 짧은 SHA. git 저장소가 아니거나 git 이 없으면 null(추정 금지). */
function gitSha() {
  try {
    return require('child_process')
      .execSync('git rev-parse --short HEAD', { cwd: ROOT, stdio: ['ignore', 'pipe', 'ignore'] })
      .toString().trim() || null;
  } catch (_) { return null; }
}

// version.json(루트, 빌드 정설) 에서 (테넌트/variant 오버라이드 우선) 플랫폼별 버전을 추출.
// platform='android' → versionCode/versionName
// platform='ios'     → buildNumber/version (iOS 는 정수 빌드번호+문자열 버전 분리)
// variant            → android 전용 독립 버전선('cn'|'play'). null 이면 루트 버전선(하위호환).
function resolveVersion(tenant, platform, variant) {
  const isIos = platform === 'ios';
  const codeKey = isIos ? 'buildNumber' : 'versionCode';
  const nameKey = isIos ? 'version' : 'versionName';
  const fallback = { code: 1, name: '1.0.0' };
  const verPath = path.join(ROOT, 'version.json');
  if (!fs.existsSync(verPath)) return fallback;
  try {
    const v = JSON.parse(fs.readFileSync(verPath, 'utf8'));
    // ① 화이트라벨 테넌트: 자기 버전 블록을 쓴다(variant 무시 — 테넌트는 그 자체로 별개 앱).
    if (tenant && v.tenants && v.tenants[tenant.id]) {
      const t = v.tenants[tenant.id];
      const tb = t[platform] || t.android || {};
      return {
        code: Number(tb[codeKey] != null ? tb[codeKey] : t[codeKey]) || 1,
        name: String(tb[nameKey] != null ? tb[nameKey] : (t[nameKey] != null ? t[nameKey] : '1.0.0')),
      };
    }
    // ② variant(중국/Play): variants.js 의 versionLookup 이 우선순위를 결정한다.
    //    tenants.<id>.variants.<key> → tenants.<id>.android → 루트 (항상 폴백 있어 하위호환).
    const chain = V.versionLookup(v, tenant ? tenant.id : null, variant).filter(Boolean);
    for (const src of chain) {
      const blk = (src && src[platform]) || (src && src.android) || src || {};
      if (blk[codeKey] != null || blk[nameKey] != null) {
        return {
          code: Number(blk[codeKey] != null ? blk[codeKey] : src[codeKey]) || 1,
          name: String(blk[nameKey] != null ? blk[nameKey] : (src[nameKey] != null ? src[nameKey] : '1.0.0')),
        };
      }
    }
  } catch (_) { /* 손상 시 기본값 */ }
  return fallback;
}

// 안드로이드 타겟 빌드 시 version.json(versionCode/versionName) + variant applicationId 를
// android/app/build.gradle 로 동기화(수동 불일치 방지).
// version.json 은 저장소 루트에 위치(빌드 스크립트는 이를 정설로 읽음).
//
// ⚠️ applicationId 를 여기서 직접 쓰는 이유(실측): `cap sync` 는 appId 를 build.gradle 에
//    반영하지 않는다(Capacitor CLI 는 `cap add` 시에만 반영 — tasks/add.js:115).
//    따라서 스크립트가 직접 기록하지 않으면 "설정만 Play 패키지, APK 는 여전히 기본 패키지"
//    라는 거짓 분리가 발생한다(2026-09-09 실측으로 확인).
// ⚠️ namespace 는 절대 변경하지 않는다. namespace 는 R/BuildConfig 생성 패키지이며
//    src/main/java/com/gospelai/app/** 소스의 `package` 선언과 일치해야 한다.
//    applicationId 만 바꾸는 것은 AGP 가 공식 지원(applicationIdSuffix 와 동일 원리).
function applyVersionToGradle(tenant, target, variant, appId) {
  const gradlePath = path.join(ROOT, 'android', 'app', 'build.gradle');
  if (!fs.existsSync(gradlePath)) return;
  const { code, name } = resolveVersion(tenant, 'android', variant);
  let g = fs.readFileSync(gradlePath, 'utf8');
  // 정규식 매칭 실패를 조용히 넘기지 않는다(거짓 성공 로그 방지).
  const hadCode = /versionCode\s+\d+/.test(g);
  const hadName = /versionName\s+"[^"]*"/.test(g);
  const hadAppId = appId ? /applicationId\s+"[^"]*"/.test(g) : true;
  g = g.replace(/versionCode\s+\d+/, 'versionCode ' + code);
  g = g.replace(/versionName\s+"[^"]*"/, 'versionName "' + name + '"');
  if (appId) g = g.replace(/applicationId\s+"[^"]*"/, 'applicationId "' + appId + '"');
  fs.writeFileSync(gradlePath, g, 'utf8');
  const from = '← version.json' + (variant ? '/variant:' + variant : '') + (tenant ? '/tenant:' + tenant.id : '');
  console.log('[build-config] android/app/build.gradle versionCode=' + code + ' versionName="' + name + '"'
    + (appId ? ' applicationId="' + appId + '"' : '') + ' (' + from + ')');
  if (!hadCode) console.warn('[build-config] ⚠️  build.gradle 에 versionCode 패턴이 없어 치환 실패. 수동 확인 필요.');
  if (!hadName) console.warn('[build-config] ⚠️  build.gradle 에 versionName 패턴이 없어 치환 실패. 수동 확인 필요.');
  if (!hadAppId) console.warn('[build-config] ⚠️  build.gradle 에 applicationId 패턴이 없어 패키지명 치환 실패. 수동 확인 필요.');
}

// 패키지명 문자열 리소스 동기화. Capacitor 의 `cap add` 는 strings.xml 의
// com.getcapacitor.myapp → appId 로 치환하지만(android/common.js:93) sync 는 하지 않으므로
// 여기서 직접 기록한다. 키 기반 치환이라 여러 variant 를 왕복 빌드해도 멱등하다.
function applyAndroidStrings(appId) {
  if (!appId) return;
  const p = path.join(ROOT, 'android', 'app', 'src', 'main', 'res', 'values', 'strings.xml');
  if (!fs.existsSync(p)) return;
  let s = fs.readFileSync(p, 'utf8');
  const keys = ['package_name', 'custom_url_scheme'];
  let changed = false;
  for (const k of keys) {
    const re = new RegExp('(<string name="' + k + '">)[^<]*(</string>)');
    if (!re.test(s)) continue;
    const next = s.replace(re, '$1' + appId + '$2');
    if (next !== s) { s = next; changed = true; }
  }
  if (!changed) return;
  fs.writeFileSync(p, s, 'utf8');
  console.log('[build-config] android/app/src/main/res/values/strings.xml package_name·custom_url_scheme → ' + appId);
}

// 런타임 강제업데이트 체크용 dist/version.json 을 해석된 버전으로 재생성.
// (mobile/version.json 이 그대로 복사되지만, 테넌트 오버라이드/ios 버전을 반영하기 위해 덮어씀)
// iOS 타겟은 buildNumber/version 키를 추가로 기록(런타임 게이트가 iOS 용으로 이 키를 읽음).
// 하위호환을 위해 versionCode/versionName 도 항상 함께 기록.
function emitRuntimeVersion(outDir, tenant, target, variant, appId) {
  const { code, name } = resolveVersion(tenant, target, variant);
  const isIos = target === 'ios';
  const out = isIos
    ? { version: name, buildNumber: code, versionName: name, versionCode: code }
    : { versionName: name, versionCode: code };
  // 배포 variant 식별자: 강제업데이트/통계가 "어느 APK 인지" 구분할 수 있게 한다.
  // (mobile/services/index.js 는 versionCode/versionName 만 읽으므로 키 추가는 무해)
  if (variant) out.variant = variant;
  if (appId) out.applicationId = appId;
  // 빌드 추적(provenance): 산출물만 보고도 어느 커밋에서 나왔는지 역추적 가능하게 한다.
  // git 정보가 없으면(아카이브 빌드等) null — 추정값을 채우지 않는다.
  out.gitSha = gitSha();
  fs.writeFileSync(path.join(outDir, 'version.json'),
    JSON.stringify(out, null, 2) + '\n', 'utf8');
  const summary = isIos
    ? 'buildNumber=' + code + ' version="' + name + '"'
    : 'versionName="' + name + '" versionCode=' + code;
  console.log('[build-config] dist/version.json → ' + summary + (tenant ? ' (tenant:' + tenant.id + ')' : '') + ' (platform=' + target + ')');
}

// iOS 타겟: Capacitor 설정의 appId(bundle id) · webDir 를 테넌트/기본값으로 세팅.
// 실제 iOS 네이티브 프로젝트(ios/) 는 macOS/Xcode 에서 `npx cap add ios` 로 생성해야 하며,
// 여기(Windows)에서는 config 레이어만 준비하고 Info.plist 패치는 프로젝트 존재 시에만 시도.
function applyIosConfig(tenant, target) {
  const cfgPath = path.join(ROOT, 'capacitor.config.json');
  if (fs.existsSync(cfgPath)) {
    const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    const bid = (tenant && tenant.ios && tenant.ios.bundleId) ? tenant.ios.bundleId : (cfg.appId || 'com.gospelai.app');
    let changed = false;
    if (cfg.appId !== bid) { cfg.appId = bid; changed = true; }
    // 웹 자산은 mobile/dist/[<tenant>/]<target>(=ios) 를 사용(webDir 격리).
    // ⚠️ 이전 버그 2건(둘 다 iOS 산출물 오염):
    //    ① ios 타겟인데 webDir 을 'android' 로 하드코딩 → 안드로이드 자산이 iOS 네이티브에 번들링.
    //    ② webDir 을 tenant 가 있을 때만 갱신 → 테넌트 없이 빌드하는 FLOW 자체 App Store 앱은
    //       기본값 'mobile/dist/android' 가 그대로 남아 동일하게 오번들링.
    //    → tenant 유무와 무관하게 **항상** target(ios) 기준으로 갱신한다.
    const webDir = tenant ? 'mobile/dist/' + tenant.id + '/' + target : 'mobile/dist/' + target;
    if (cfg.webDir !== webDir) { cfg.webDir = webDir; changed = true; }
    // 화이트라벨 앱 표시명: cap sync 가 이 값을 iOS Info.plist 의 CFBundleDisplayName 으로 반영
    // (실측: node_modules/@capacitor/cli/dist/ios/common.js:97).
    // ⚠️ 이전 버그: appName 을 아예 설정하지 않아 모든 테넌트 앱이 "FLOW AI" 로 빌드됨.
    if (tenant) {
      const appName = (tenant.branding && tenant.branding.appName) || tenant.displayName || null;
      if (appName && cfg.appName !== appName) { cfg.appName = appName; changed = true; }
    }
    if (changed) {
      fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
      console.log('[build-config] (ios) capacitor.config.json appId → ' + bid + ' webDir=' + cfg.webDir + (tenant ? ' appName=' + cfg.appName : ''));
    }
  }
  const plistPath = path.join(ROOT, 'ios', 'App', 'App', 'Info.plist');
  if (!fs.existsSync(plistPath)) {
    console.log('[build-config] (ios) ios/ 네이티브 프로젝트 없음 — `npx cap add ios` 는 macOS/Xcode 에서 실행 필요. config 레이어만 준비됨.');
    return;
  }
  const { code, name } = resolveVersion(tenant, 'ios');
  let p = fs.readFileSync(plistPath, 'utf8');
  // ⚠️ 무음실패 방지: 정규식이 매칭되지 않으면 replace 는 아무 것도 바꾸지 않고
  //    스토어에 이전/잘못된 버전이 그대로 업로드된다(Xcode 템플릿이 $(CURRENT_PROJECT_VERSION)
  //    변수형을 쓰면 <string>...</string> 패턴이 아님). 매칭 여부를 검증해 실패 시 경고.
  const reVer = /<key>CFBundleVersion<\/key>\s*<string>[^<]*<\/string>/;
  const reShort = /<key>CFBundleShortVersionString<\/key>\s*<string>[^<]*<\/string>/;
  const hadVer = reVer.test(p);
  const hadShort = reShort.test(p);
  p = p.replace(reVer, '<key>CFBundleVersion</key>\n\t<string>' + code + '</string>');
  p = p.replace(reShort, '<key>CFBundleShortVersionString</key>\n\t<string>' + name + '</string>');

  // ── App Store 수출규정(암호화) 선언 ──
  // Apple 공식(Complying with Encryption Export Regulations): 이 키가 없으면 App Store Connect 가
  // **업로드마다** 수출규정 질문을 하며, 답변 전까지 빌드를 심사에 제출할 수 없다.
  // FLOW 는 표준 HTTPS(TLS) 만 사용 → OS 내장 암호화만 해당 = 비면제 암호화 "미사용" → false 가 정답.
  // (false 로 두면 App Store Connect 의 반복 질문이 사라져 제출이 간소화된다.)
  const reITS = /<key>ITSAppUsesNonExemptEncryption<\/key>\s*<(?:true|false)\/>/;
  let itsResult;
  if (reITS.test(p)) {
    p = p.replace(reITS, '<key>ITSAppUsesNonExemptEncryption</key>\n\t<false/>');
    itsResult = 'updated';
  } else {
    const reClose = /<\/dict>\s*<\/plist>/;
    if (reClose.test(p)) {
      p = p.replace(reClose, '\t<key>ITSAppUsesNonExemptEncryption</key>\n\t<false/>\n</dict>\n</plist>');
      itsResult = 'inserted';
    } else {
      itsResult = 'failed';
    }
  }
  fs.writeFileSync(plistPath, p, 'utf8');
  if (!hadVer || !hadShort) {
    const missing = [!hadVer ? 'CFBundleVersion' : null, !hadShort ? 'CFBundleShortVersionString' : null].filter(Boolean).join(', ');
    console.warn('[build-config] (ios) ⚠️ Info.plist 버전 패치 실패 — ' + missing + ' 키를 <string>형으로 찾지 못했습니다.\n' +
      '    Info.plist 를 열어 해당 키가 <string>리터럴인지 확인하세요(변수형 $(...) 이면 수동 반영 필요).');
  } else {
    console.log('[build-config] (ios) Info.plist CFBundleVersion=' + code + ' CFBundleShortVersionString="' + name + '"');
  }
  if (itsResult === 'failed') {
    console.warn('[build-config] (ios) ⚠️ ITSAppUsesNonExemptEncryption 추가 실패 — </dict></plist> 종료 태그를 찾지 못했습니다.\n' +
      '    Xcode > Target > Info 에서 "App Uses Non-Exempt Encryption = NO" 를 수동 추가하세요(미설정 시 업로드마다 수출규정 질문).');
  } else {
    console.log('[build-config] (ios) Info.plist ITSAppUsesNonExemptEncryption=false (' + itsResult + ') — 표준 HTTPS 전용, 수출규정 면제 선언');
  }
}

// 안드로이드 타겟 빌드 시 variant/테넌트에 따라 capacitor.config.json 의 appId · webDir 를 분리.
//   기본(direct/hk·global) → com.gospelai.app      (자체 공식사이트 직링크, PortOne 결제 포함)
//   market=cn              → com.gospelai.app.cn   (중국 직분용, Google SDK 0)
//   channel=googleplay     → com.gospelai.app.play (GP 상점 전용, 제3자 결제 0 — GP 정책 준수 물리적 격리)
//   테넌트 지정 → tenants/<id>/config.json 의 android.applicationId (최우선, 위 격리와 동일 원리)
// 각 variant 는 별도 패키지 = 별도 versionCode 증가선(서로 덮어쓰기/다운그레이드 거부 방지).
// webDir 은 타겟/테넌트별 모바일 자산 디렉터리로 항상 갱신(기존 출력 덮어쓰기 안전).
function applyAndroidAppId(channel, market, tenant, target, allowMixedContent) {
  const cfgPath = path.join(ROOT, 'capacitor.config.json');
  if (!fs.existsSync(cfgPath)) return;
  const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
  const appId = resolveAndroidAppId(channel, market, tenant);
  let changed = false;
  if (cfg.appId !== appId) { cfg.appId = appId; changed = true; }
  // webDir 격리: 타겟/테넌트별 모바일 자산 디렉터리 지정(POSIX 구분자 — Capacitor config 호환).
  const webDir = tenant ? 'mobile/dist/' + tenant.id + '/' + target : 'mobile/dist/' + target;
  if (cfg.webDir !== webDir) { cfg.webDir = webDir; changed = true; }
  // 화이트라벨 앱 표시명: cap sync 가 이 값을 Android strings.xml 의 app_name 으로 반영
  // (실측: node_modules/@capacitor/cli/dist/android/common.js:94 — stringsContent.replace(/My App/g, appName)).
  // ⚠️ 이전 버그: appName 미설정 → 모든 테넌트 앱이 "FLOW AI" 로 빌드.
  if (tenant) {
    const appName = (tenant.branding && tenant.branding.appName) || tenant.displayName || null;
    if (appName && cfg.appName !== appName) { cfg.appName = appName; changed = true; }
  }
  // ── allowMixedContent(2026-09-30, UPDATE_PLAN §16 U7) ──────────────────────
  //   Capacitor WebView 컨텍스트는 https://localhost 라서 **평문 HTTP 리소스는 기본 차단**된다.
  //   그런데 이 값이 상수 true 로 굳어 있으면(= 2026-09-30 이전 상태) 프로덕션 HTTPS API 조차
  //   "혼합 콘텐츠 허용" 상태로 출시되어 중간자 공격 면적이 불필요하게 넓어진다.
  //   게다가 res/xml/network_security_config.xml 주석은 이미 "allowMixedContent=false 와 일관"
  //   이라고 적어 놓고 실제값은 true 였다(문서 ↔ 코드 드리프트).
  //   → **실제 주입된 API 스킴으로 결정**: apiBase/musicApiBase 중 하나라도 http:// 이면
  //     루프백 개발 빌드(= --allow-localhost)용으로만 true, 전부 https 면 false(프로덕션 기본).
  const desiredMixed = allowMixedContent === true;
  if (!cfg.android) cfg.android = {};
  if (cfg.android.allowMixedContent !== desiredMixed) {
    cfg.android.allowMixedContent = desiredMixed;
    changed = true;
  }
  if (changed) {
    fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
    console.log('[build-config] capacitor.config.json appId → ' + appId + (tenant ? ' (tenant=' + tenant.id + ')' : ' (channel=' + channel + '/market=' + market + ')') + ' appName=' + cfg.appName + ' webDir=' + webDir + ' allowMixedContent=' + desiredMixed);
  }
}

function main() {
  const target = process.argv[2];
  if (target !== 'web' && target !== 'android' && target !== 'ios') {
    console.error('Usage: node scripts/build-config.js <web|android|ios> [--tenant=<id>] [--api=URL] [--market=cn|global] [--channel=direct|googleplay|appstore]');
    process.exit(1);
  }
  const rest = process.argv.slice(3);
  const tenantId = arg('--tenant', rest);
  const tenant = tenantId ? loadTenant(tenantId) : null;
  // hk = 香港部署（中国大陆用户可达，无需 Google 框架）:
  //   后端用香港域名，但 GIS SDK 仍注入（海外/香港 Google 框架可用）。
  //   若只想服务大陆且彻底零 Google，用 --market=cn（不注入 GIS）。
  // ⚠️ 허용되지 않은 market 은 조용히 global 로 떨어뜨리지 않고 실패시킨다
  //    (오타로 엉뚱한 시장 설정이 배포되는 것을 막기 위해).
  let market;
  try {
    market = V.normalizeMarket(arg('--market', rest) || 'hk', target);
  } catch (e) {
    console.error('[build-config] ❌ ' + e.message);
    process.exit(1);
  }
  // 결제 채널: direct     = PortOne 단일 SDK 로 한국카드+중국 위챗/알리페이 커버(자체배포/웹/APK)
  //            googleplay = GP 상점용, 제3자 결제 0 (런타임에서 GP Billing placeholder 로 폴백)
  //            appstore   = App Store 용, 제3자 결제 0 (런타임에서 StoreKit placeholder 로 폴백)
  //
  // ⚠️ iOS 기본값을 'appstore' 로 둔다(안전 기본값). 근거 — Apple 가이드라인 3.1.1:
  //    App 내 디지털 상품(구독/크레딧)은 반드시 StoreKit(IAP) 로만 판매해야 하며, 제3자 결제 SDK 는
  //    IPA 압축 해제 후 정적 스캔(클래스명·라이브러리·리소스 지문)으로 적발된다 → 리젝 + 계정 종료 위험.
  //    따라서 App Store 제출 빌드에는 PortOne 코드를 물리적으로 넣지 않는다(GP 격리와 동일 원리).
  const iosTarget = target === 'ios';
  let channel = arg('--channel', rest) || (iosTarget ? 'appstore' : 'direct');
  const allowed = iosTarget ? ['appstore', 'direct'] : ['direct', 'googleplay'];
  if (allowed.indexOf(channel) === -1) {
    console.error('[build-config] --channel 은 ' + target + ' 타겟에서 ' + allowed.join(' 또는 ') + ' 만 가능합니다.');
    process.exit(1);
  }
  if (iosTarget && channel === 'direct') {
    console.warn(
      '[build-config] ⚠️  WARNING: iOS 타겟에 --channel=direct 를 지정했습니다.\n' +
      '    Apple 가이드라인 3.1.1 — 디지털 상품은 StoreKit(IAP) 로만 판매해야 하며, 제3자 결제 SDK(PortOne)가\n' +
      '    IPA 에 포함되면 정적 스캔으로 적발됩니다(리젝·계정 종료 위험). TestFlight/엔터프라이즈 전용으로만 쓰세요.\n' +
      '    App Store 제출용은 --channel=appstore(기본값) 를 사용하세요.'
    );
  }

  // ── 배포 variant 결정: (channel, market) → 패키지명 + 독립 버전선 ──
  // 매칭 실패는 예외(조용한 default 폴백 금지 — "중국판을 만들려다 기본 패키지 배포" 방지).
  let variant = null;
  let androidAppId = null;
  if (target === 'android') {
    try {
      variant = resolveVariant(channel, market);
      androidAppId = resolveAndroidAppId(channel, market, tenant);
    } catch (e) {
      console.error('[build-config] ❌ ' + e.message);
      process.exit(1);
    }
  }
  if (!iosTarget && channel === 'googleplay' && market === 'cn') {
    console.warn(
      '[build-config] ⚠️  WARNING: --channel=googleplay 와 --market=cn 의 조합입니다.\n' +
      '    Google Play 는 중국 본토에서 서비스되지 않아 이 조합은 실배포 대상이 아닙니다.\n' +
      '    중국 직분용은 --market=cn(기본 채널 direct), Play 는 --channel=googleplay 를 각각 단독으로 쓰세요.'
    );
  }
  const env = loadEnvMobile();
  const apiBase = getApiBase(target, market, rest, env, tenant);

  // ── FLOW CCM 音乐广场后端地址 (FLOW 主后端과 공유 DB 의 별도 서비스) ──
  //   --music-api=<url> > env.MUSIC_API_BASE > 기본값(http://127.0.0.1:8001, 본 작업공간 음악플랫폼 포트)
  const musicApiBase = arg('--music-api', rest) || env.MUSIC_API_BASE || 'http://127.0.0.1:8001';

  // ── 안드로이드/ios(APK/IPA) 가드: localhost/loopback API 는 실기기에서 동작 불가 ──
  // (127.0.0.1 은 기기 자신을 가리킴 + WebView 는 https 컨텍스트라 http mixed-content 도 차단)
  // 배포용 바이너리를 만들 때는 반드시 공개 HTTPS 도메인을 주입해야 한다.
  //  기본값은 실패(fail-fast) → localhost 가 그대로 릴리스되는 사고(commit 6eb7c8a 이후 APK) 재발 방지.
  //  로컬 에뮬레이터 테스트용은 명시적 --allow-localhost 로만 허용한다.
  if ((target === 'android' || target === 'ios') && /^https?:\/\/(127\.0\.0\.1|localhost)([:/]|$)/i.test(apiBase)) {
    if (rest.includes('--allow-localhost')) {
      console.warn(
        '[build-config] ⚠️  WARNING: --allow-localhost 사용 → localhost API(' + apiBase + ')로 빌드합니다.\n' +
        '    에뮬레이터 로컬 백엔드 테스트 전용. 실기기 릴리스용이 아닙니다.'
      );
    } else {
      console.error(
        '[build-config] ❌ ERROR: ' + target + ' 타겟 API base가 localhost(' + apiBase + ')입니다.\n' +
        '    실기기 바이너리는 백엔드에 연결할 수 없습니다. 다음 중 하나로 지정하세요:\n' +
        '      --api=https://<공개HTTPS도메인>\n' +
        '      .env.mobile 의 ANDROID_API_BASE / GLOBAL_API_BASE / CN_API_BASE\n' +
        '    (로컬 에뮬레이터 테스트는 --allow-localhost 를 명시 — 기본값은 실패합니다)'
      );
      process.exit(1);
    }
  }

  // ── CCM 音乐后端 주소에도 동일한 fail-fast(2026-09-27 실측) ──
  //   기본값이 http://127.0.0.1:8001 인데 --music-api / MUSIC_API_BASE 를 주입하는
  //   빌드 스크립트가 **하나도 없었다**(`package.json` 전수 확인). 그대로 조립하면
  //   실기기에서 음악 탭 「광장」이 loopback 으로 향해 **조용히** 실패한다
  //   (주 backend 는 :520 에서 같은 사고를 막고 있었는데 CCM 쪽은 가드가 없었다).
  if ((target === 'android' || target === 'ios') && /^https?:\/\/(127\.0\.0\.1|localhost)([:/]|$)/i.test(musicApiBase)) {
    if (rest.includes('--allow-localhost')) {
      console.warn(
        '[build-config] ⚠️  WARNING: --allow-localhost 사용 → localhost music API(' + musicApiBase + ')로 빌드합니다.\n' +
        '    에뮬레이터 로컬 음악백엔드 테스트 전용. 실기기 릴리스용이 아닙니다.'
      );
    } else {
      console.error(
        '[build-config] ❌ ERROR: ' + target + ' 타겟 music API base가 localhost(' + musicApiBase + ')입니다.\n' +
        '    실기기에서 127.0.0.1 은 기기 자신을 가리켜 음악 탭(광장)이 영구 실패합니다. 지정하세요:\n' +
        '      --music-api=https://<공개HTTPS도메인>\n' +
        '      .env.mobile 의 MUSIC_API_BASE\n' +
        '      (로컬 에뮬레이터 테스트는 --allow-localhost 를 명시 — 기본값은 실패합니다)'
      );
      process.exit(1);
    }
  }

  // ── 소스를 출력 디렉터리로 복사(기존 출력은 매번 깨끗하게 덮어씀) ──
  // 화이트라벨: tenants/<id> 별로 mobile/dist/<tenant>/<target>/ 로 격리 출력.
  // 테넌트 미지정 시 기존 경로(mobile/dist/<target>/) 유지(하위호환).
  const outDir = tenant ? path.join(DIST_ROOT, tenant.id, target) : path.join(DIST_ROOT, target);
  try {
    fs.rmSync(outDir, { recursive: true, force: true });
  } catch (_) {
    // Windows 에서 PWA 서버(http.server)가 dist/web 을 점유 중이면 EBUSY 발생.
    // 삭제 실패해도 아래 copyTree 가 파일을 덮어쓰므로 계속 진행(구동 중 핫리로드 안전).
    console.warn('[build-config] 기존 ' + path.relative(ROOT, outDir) + ' 삭제 실패(점유 중?). 덮어쓰기로 진행합니다.');
  }
  copyTree(SRC_DIR, outDir);

  // ── 화이트라벨 테넌트 자산(로고/스플래시/아이콘) 복사 + 런타임 tenant.json 노출 ──
  if (tenant) {
    if (tenant.assets) {
      const assetSrc = path.join(ROOT, 'tenants', tenant.id, tenant.assets);
      if (fs.existsSync(assetSrc)) copyTree(assetSrc, path.join(outDir, 'assets', 'tenant'));
    }
    const publicCfg = {
      id: tenant.id,
      displayName: tenant.displayName || tenant.id,
      branding: tenant.branding || {},
      paymentChannel: tenant.paymentChannel || 'direct'
    };
    fs.writeFileSync(path.join(outDir, 'tenant.json'), JSON.stringify(publicCfg, null, 2) + '\n', 'utf8');
    console.log('[build-config] 테넌트 자산 + dist/tenant.json 노출: ' + tenant.id);
  }
  // 런타임 강제업데이트 체크용 dist/version.json 을 해석된 버전으로 재생성(테넌트/variant/ios 반영).
  emitRuntimeVersion(outDir, tenant, target, variant, androidAppId);

  // ── 강제 업데이트 오버레이 링크 보정(숨은 버그 수정) ──
  // download.html 은 저장소 루트에 있음. 빌드 출력(mobile/dist/<tenant?>/<target>/)에는 없으므로,
  // PWA(4174 서빙)·APK(네이티브 자산) 모두에서 `./download.html` → 404 가 됨.
  // 강제 업데이트 시 사용자가 APK 를 받을 수 없게 되므로 출력 디렉터리에 함께 복사한다.
  // download.html 내부 링크는 `./releases/flow-ai.apk` 이므로 releases/ 도 같이 복사(web 은 로컬 테스트용,
  // android 는 오프라인 자체 포함 폴백 — 운영 시 백엔드 update_url 로 호스팅 URL 지정 권장).
  // ⚠️ iOS(App Store) 는 APK 를 배포하지 않는다 → download.html(APK 다운로드 페이지)·releases/(APK 폴더) 제외.
  //    App Store 앱의 업데이트는 App Store 가 담당하며, 앱 내 강제업데이트는 App Store 링크로 이동한다
  //    (mobile/app.js 의 _renderForceUpdateOverlay: ios → updateUrl/App Store). APK 자산은 불필요한 IPA 오염.
  if (target !== 'ios') {
    copyIfExists(path.join(ROOT, 'download.html'), path.join(outDir, 'download.html'));
    copyDirIfExists(path.join(ROOT, 'releases'), path.join(outDir, 'releases'));
  }

  // 안드로이드/ios 빌드: variant·테넌트 applicationId/bundleId 분리 + version.json 연동
  // (스토어 패키지 격리 필수 + build.gradle/strings.xml 에 실제로 기록해야 산출물에 반영됨).
  if (target === 'android') {
    // 평문 HTTP API(루프백 개발 빌드)일 때만 혼합 콘텐츠 허용 — 프로덕션은 HTTPS 강제(U7).
    // 위 fail-fast 덕분에 android 타겟에서 http:// 가 살아남는 경우는 --allow-localhost 명시 때뿐이다.
    const allowMixedContent = /^http:\/\//i.test(apiBase) || /^http:\/\//i.test(musicApiBase);
    applyAndroidAppId(channel, market, tenant, target, allowMixedContent);
    applyAndroidStrings(androidAppId);
    applyVersionToGradle(tenant, target, variant, androidAppId);
  } else if (target === 'ios') {
    applyIosConfig(tenant, target);
  }

  let html = readTemplate();

  // ── API base 치환 ──
  html = html.replaceAll(PH_API, apiBase);

  // ── 音乐广场后端地址 치환 ──
  html = html.replaceAll(PH_MUSIC_API, musicApiBase);

  // ── Google SDK 치환 (중국 시장은 주입 안 함) ──
  const googleTag = market === 'cn' ? '' : GOOGLE_SDK_TAG;
  html = html.replaceAll(PH_GOOGLE, googleTag);

  // ── PortOne SDK 치환 ──
  //   direct 채널: 모든 market 에 주입(cn 도 주입 — 위챗/알리페이 해외채널이 PortOne 에 내장,
  //               별도 제3자 SDK 가 아님. GP 정책 위반 아님).
  //   googleplay 채널: 완전 제거(제3자 결제 코드 0 — GP 정책 준수 물리적 격리).
  //   appstore   채널: 완전 제거(제3자 결제 코드 0 — Apple 가이드라인 3.1.1 물리적 격리).
  const portoneTag = (channel === 'googleplay' || channel === 'appstore') ? '' : PORTONE_SDK_TAG;
  html = html.replaceAll(PH_PORTONE, portoneTag);

  // ── 결제 채널 치환 (런타임 window.GOSPEL_PAYMENT_CHANNEL) ──
  html = html.replaceAll(PH_CHANNEL, channel);

  // ── 후援 링크 치환 (2026-10-04) ──
  //   .env.mobile 의 GOSPEL_DONATE_URL 또는 --donate-url. 미설정 시 빈 문자열 →
  //   앱의 「후원하기」는 死なない 링크 대신 '준비 중' 토스트만 보여준다.
  const donateUrl = arg('--donate-url', rest) || env.GOSPEL_DONATE_URL || '';
  html = html.replaceAll(PH_DONATE, donateUrl);

  // 출력 디렉터리의 index.html 에만 기록 — 소스는 그대로 보존.
  fs.writeFileSync(path.join(outDir, 'index.html'), html, 'utf8');
  console.log('[build-config] ' + target + ' / market=' + market + ' / channel=' + channel +
    (variant ? ' / variant=' + variant : '') +
    (tenant ? ' / tenant=' + tenant.id : '') +
    ' → out=' + path.relative(ROOT, outDir) +
    (androidAppId ? ', package=' + androidAppId : '') +
    ', API=' + apiBase +
    ', GoogleSDK=' + (market === 'cn' ? 'OFF(중국)' : 'ON') +
    // ⚠️ 실제 치환 결과(portoneTag)로 보고한다. 이전엔 channel 만 보고 'ON' 을 출력해
    //    "로그는 ON 인데 산출물엔 없음" 이라는 거짓 로그가 발생(검증 신뢰 붕괴).
    ', PortOneSDK=' + (portoneTag ? 'ON' : 'OFF(' + (channel === 'googleplay' ? 'GP' : channel === 'appstore' ? 'AppStore-3.1.1' : channel) + ')') +
    ', PaymentChannel=' + channel);
}

main();
