#!/usr/bin/env node
/* ═══════════════════════════════════════════════════════════════════════════
   build-music-config — FLOW CCM 音乐(ai-music-platform) 안드로이드 타겟 설정 주입

   PLAN_MUSIC_APK §5 단계 0-4: "Capacitor 음악 타겟 추가(webDir → 음악 정적 산출물)".

   ── 핵심: §2-A 쿠키 동일 사이트(same-site) 토폴로지 ────────────────────────
   auth 쿠키가 HttpOnly + SameSite=lax 이다. APK WebView origin 이
   `https://localhost` 이고 API 가 `https://api.<도메인>` 이면 **교차 사이트** 가 되어
   Lax 쿠키가 실린다 → 로그인 성공 후 모든 인증 API 가 401 이 된다(실기기에서만 재현).

   → 여기서는 Capacitor `server.hostname = music.<도메인>` + `androidScheme = "https"`
     로 두고, API 를 같은 등록가능도메인(eTLD+1) 의 서브도메인 `api.<도메인>` 으로 둔다.
     두 호스트가 같은 eTLD+1 을 공유 → **동일 사이트** → Lax 가 자연스럽게 성립한다.
     (검증: backend/tests/test_apk_cookie_samesite.py 의 A~D 토폴로지 케이스)
   → 정적 자산은 여전히 APK 에 로컬 번들링되므로 **오프라인 구동 가능** 하다.
     `server.url` 이 아니라 `server.hostname` 이므로 원격에서 가져오지 않는다.

   ⚠️ Capacitor 6.2.1 의 `cap sync` 는 **--config 옵션이 없다** (--deployment/--inline 뿐).
      따라서 별도 설정 파일을 둘 수 없고, FLOW 기존 파이프라인과 동일하게
      **capacitor.config.json 을 이 스크립트가 직접 기록** 하는 방식을 쓴다.
      → FLOW 본체 앱으로 돌리려면 반드시 `node scripts/build-config.js android ...`
        를 다시 실행해 appId/webDir 을 되돌려야 한다(아래 경고 출력).

   사용법:
     node scripts/build-music-config.js --domain=flow.example.com
     node scripts/build-music-config.js --domain=flow.example.com --channel=googleplay
     node scripts/build-music-config.js --domain=flow.example.com --dry-run

   이후:
     npx cap sync android && npm run build:apk-release
   ═══════════════════════════════════════════════════════════════════════════ */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const V = require('./variants');

const APP = 'music';
const APP_NAME = 'FLOW Music';
/** 음악 정적 산출물(= frontend/scripts/build-apk.mjs 의 출력). POSIX 구분자 필수. */
const WEB_DIR = 'ai-music-platform/frontend/out';

function die(msg) {
  console.error('[build-music-config] ❌ ' + msg);
  process.exit(1);
}

function arg(name) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : null;
}

const dryRun = process.argv.includes('--dry-run');
const domain = arg('domain') || process.env.MUSIC_DOMAIN || '';
const channel = arg('channel') || 'direct';
const marketRaw = arg('market') || 'hk';

if (!domain) {
  die('--domain=<부모도메인> 이 필요합니다.\n' +
    '    WebView origin = music.<domain>, API = api.<domain> 으로 배치됩니다.\n' +
    '    ⚠️ 두 호스트가 **같은 eTLD+1** 을 공유해야 SameSite=lax 쿠키가 실립니다.');
}
// 'music.' / 'api.' 접두어를 이미 붙여 넣는 실수 방지(그러면 music.music.x 가 됨).
if (/^(music|api)\./i.test(domain)) {
  die(`--domain 에는 'music.'/'api.' 접두어 없이 **부모 도메인** 만 넣으세요(입력: ${domain}).\n` +
    '    예: --domain=flow.example.com  →  music.flow.example.com + api.flow.example.com');
}
if (/^(127\.0\.0\.1|localhost)(:|$)/i.test(domain)) {
  die(`--domain 에 루프백(${domain})은 쓸 수 없습니다(실기기에서 쿠키/연결 실패).`);
}

let market;
try {
  market = V.normalizeMarket(marketRaw, 'android');
} catch (e) {
  die(e.message);
}
V.validateChannel(channel, 'android');

let variant, appId;
try {
  variant = V.resolveVariant(channel, market, APP);
  appId = V.resolveAppId(channel, market, null, APP);
} catch (e) {
  die(e.message);
}

// ── 버전: version.json 의 음악 전용 버전선(music / music.variants.<key>) ──────
const versionKey = V.resolveVersionKey(variant, APP);
const vPath = path.join(ROOT, 'version.json');
let code = 1, name = '1.0.0';
if (fs.existsSync(vPath)) {
  const v = JSON.parse(fs.readFileSync(vPath, 'utf8'));
  for (const src of V.versionLookup(v, null, variant, APP)) {
    const blk = src || {};
    if (blk.versionCode != null || blk.versionName != null) {
      code = Number(blk.versionCode != null ? blk.versionCode : 1) || 1;
      name = String(blk.versionName != null ? blk.versionName : '1.0.0');
      break;
    }
  }
}

const webHost = `music.${domain}`;
const apiHost = `api.${domain}`;
const argApi = arg('api');
const apiBase = argApi || `https://${apiHost}`;

// API 가 동일 사이트인지 방어적으로 확인(다른 eTLD+1 이면 쿠키가 실리지 않는다).
function registrable(host) {
  const parts = host.split('.').filter(Boolean);
  return parts.slice(-2).join('.'); // ⚠️ 단순 2레벨 근사(co.uk 등 다중 접미사는 별도 표 필요)
}
if (registrable(webHost) !== registrable(apiHost)) {
  die(`WebView(${webHost}) 와 API(${apiHost}) 가 같은 등록가능도메인이 아닙니다.\n` +
    '    SameSite=lax 쿠키가 실리지 않아 **로그인 후 401** 이 발생합니다(실기기 전용 재현).');
}

const cfg = {
  appId,
  appName: APP_NAME,
  webDir: WEB_DIR,
  bundledWebRuntime: false,
  server: {
    // 로컬 자산을 이 가상 호스트로 서빙(원격 아님 → 오프라인 가능).
    // androidScheme=https 여야 프로덕션과 동일한 https 컨텍스트가 되고,
    // API 도 https 서브도메인이면 **동일 사이트** 가 성립한다.
    hostname: webHost,
    androidScheme: 'https',
    url: null,
  },
  android: {
    // 프로덕션은 전 구간 https → mixed content 불필요. (기본 허용은 공격면 확대)
    allowMixedContent: false,
    backgroundColor: '#0C0A08',
    captureInput: true,
    webContentsDebuggingEnabled: false,
  },
  plugins: {
    SplashScreen: {
      launchShowDuration: 1500,
      backgroundColor: '#0C0A08',
      androidScaleType: 'CENTER_CROP',
      showSpinner: false,
    },
    StatusBar: { style: 'LIGHT', backgroundColor: '#0C0A08' },
    Keyboard: { resize: 'body', style: 'DARK', resizeOnFullScreen: true },
  },
};

console.log('[build-music-config] target=music variant=' + variant + ' channel=' + channel + '/market=' + market);
console.log('  appId        : ' + appId);
console.log('  appName      : ' + APP_NAME);
console.log('  webDir       : ' + WEB_DIR);
console.log('  WebView host : https://' + webHost + '   (로컬 자산, 오프라인 가능)');
console.log('  API          : ' + apiBase + '   (same-site: ' + registrable(webHost) + ')');
console.log('  version      : versionCode=' + code + ' versionName="' + name + '"  (versionKey=' + versionKey + ')');

if (dryRun) {
  console.log('\n[build-music-config] --dry-run 이므로 어떤 파일도 쓰지 않았습니다.');
  process.exit(0);
}

// ── 1) capacitor.config.json 기록 ───────────────────────────────────────────
const cfgPath = path.join(ROOT, 'capacitor.config.json');
fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
console.log('[build-music-config] ✅ capacitor.config.json 기록 완료');

// ── 2) build.gradle: applicationId + version (cap sync 는 appId 를 안 건드린다) ──
// 실측: Capacitor CLI 는 `cap add` 시점에만 appId → build.gradle 반영(tasks/add.js:115).
//       `cap sync` 는 무관 → 스크립트가 직접 써야 "설정만 음악, APK 는 FLOW 패키지" 사고를 막는다.
const gradlePath = path.join(ROOT, 'android', 'app', 'build.gradle');
if (fs.existsSync(gradlePath)) {
  let g = fs.readFileSync(gradlePath, 'utf8');
  const had = {
    id: /applicationId\s+"[^"]*"/.test(g),
    code: /versionCode\s+\d+/.test(g),
    name: /versionName\s+"[^"]*"/.test(g),
  };
  g = g.replace(/applicationId\s+"[^"]*"/, `applicationId "${appId}"`);
  g = g.replace(/versionCode\s+\d+/, `versionCode ${code}`);
  g = g.replace(/versionName\s+"[^"]*"/, `versionName "${name}"`);
  // ⚠️ fail-closed: 빌드 스크립트는 "설정을 주입했다" 는 **증명** 을 산출물로 남겨야 한다.
  //    예전에는 build.gradle 이 없으면 경고만 하고, 있어도 패턴이 안 맞으면 경고만 하고
  //    그래도 exit 0 으로 끝났다 → CI/자동화가 "음악 패키지 설정 완료" 로 오인한다.
  //    실제로는 applicationId/versionCode 가 주입되지 않아 **음악 APK 가 본체 패키지로 빌드**
  //    될 수 있다(설정만 음악, APK 는 FLOW). 배포 바이너리를 만드는 경로이므로 실패해야 한다.
  const missing = Object.entries(had).filter(([, ok]) => !ok).map(([k]) => k);
  if (missing.length) {
    die('build.gradle 에 ' + missing.join('/') + ' 패턴을 찾지 못했습니다(치환 실패).\n' +
      '    이대로 진행하면 설정만 음악이고 APK 는 FLOW 패키지/버전으로 나옵니다(가짜 분리).\n' +
      '    android/app/build.gradle 의 defaultConfig 에 applicationId / versionCode / versionName 이 있는지 확인하세요.');
  }
  fs.writeFileSync(gradlePath, g, 'utf8');
  console.log('[build-music-config] ✅ build.gradle applicationId/version 기록');
} else {
  die('android/app/build.gradle 이 없습니다 — 네이티브 프로젝트가 없으면 음악 패키지로 빌드할 수 없습니다.\n' +
    '    macOS/Xcode 없이도 android/ 는 필요합니다(`npx cap add android`).\n' +
    '    ⚠️ 이 스크립트를 그대로 건너뛰면 capacitor.config.json 만 음악용이 되고 APK 는 FLOW 로 빌드됩니다.');
}

// ── 3) strings.xml package_name 동기화 ──────────────────────────────────────
// ⚠️ 이 단계는 **아무 것도 보고하지 않을 수 있었다** (파일이 없거나 키가 없으면 그냥 통과).
//    strings.xml 의 package_name 은 런타임이 자기 패키지를 식별하는 값이라, 조용히 어긋나면
//    "설정은 음악인데 strings.xml 은 FLOW" 인 반쪽 상태가 로그에 흔적 없이 남는다.
//    → 파일 누락과 키 누락을 각각 명시적으로 실패시킨다.
const stringsPath = path.join(ROOT, 'android', 'app', 'src', 'main', 'res', 'values', 'strings.xml');
if (!fs.existsSync(stringsPath)) {
  die('android/app/src/main/res/values/strings.xml 이 없습니다.\n' +
    '    package_name/custom_url_scheme 을 음악 패키지(' + appId + ')로 맞추지 못하면 런타임이 자기 패키지를 잘못 인식합니다.');
}
let stringsSrc = fs.readFileSync(stringsPath, 'utf8');
let stringsChanged = false;
const stringsKeys = ['package_name', 'custom_url_scheme'];
const stringsMissing = [];
for (const k of stringsKeys) {
  const re = new RegExp(`(<string name="${k}">)[^<]*(</string>)`);
  if (!re.test(stringsSrc)) { stringsMissing.push(k); continue; }
  const next = stringsSrc.replace(re, `$1${appId}$2`);
  if (next !== stringsSrc) { stringsSrc = next; stringsChanged = true; }
}
if (stringsMissing.length) {
  die('strings.xml 에 ' + stringsMissing.join('/') + ' 키가 없습니다(치환 실패).\n' +
    '    런타임이 자기 패키지를 잘못 인식할 수 있습니다(수동 확인 필요).');
}
if (stringsChanged) {
  fs.writeFileSync(stringsPath, stringsSrc, 'utf8');
  console.log('[build-music-config] ✅ strings.xml package_name/custom_url_scheme → ' + appId);
} else {
  console.log('[build-music-config] ℹ️  strings.xml package_name/custom_url_scheme 는 이미 ' + appId + ' (변경 없음)');
}

console.log('\n[build-music-config] 다음 단계:');
console.log('  npx cap sync android');
console.log('  npm run build:apk-release');
console.log('\n⚠️  FLOW 본체 앱으로 되돌리려면 반드시 실행:');
console.log('    node scripts/build-config.js android --market=hk');
console.log('    (이 스크립트가 capacitor.config.json/build.gradle/strings.xml 을 음악용으로 덮어썼기 때문)');
