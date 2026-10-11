/* ═══════════════════════════════════════════════════════════════════════════
   variants — 배포 variant(변종) 정의의 **단일 진실원**

   왜 별도 파일인가:
     이전에는 패키지명 매핑이 `build-config.js`(치환용) 와 `collect-build.js`
     (역매핑용) 두 곳에 중복 정의되어 있었다. 채널을 하나 추가할 때 두 곳을
     같이 고치지 않으면 "빌드는 되는데 산출물 이름이 default 로 찍히는" 식의
     조용한 불일치가 생긴다. → 한 곳에서만 정의하고 양쪽이 require 한다.

   variant = (channel, market) 조합 = 하나의 배포 단위.
     각 variant 는 반드시 ① 고유 applicationId ② 독립 versionCode 증가선 을 가진다.

   규범 문서: docs/ANDROID_VARIANT_VERSIONING.md
   ═══════════════════════════════════════════════════════════════════════════ */

/** 허용 market. 'hk' 는 global 과 동일 취급(홍콩 배포 = 글로벌 백엔드). */
const MARKETS = ['hk', 'cn', 'global'];
/** 허용 channel (android 기준). appstore 는 iOS 전용이라 android 에선 거부. */
const CHANNELS_ANDROID = ['direct', 'googleplay'];
const CHANNELS_IOS = ['appstore', 'direct'];

/**
 * variant 정의.
 *   applicationId : 빌드 산출물의 실제 패키지명(build.gradle 에 기록된다)
 *   versionKey    : version.json 에서 이 variant 의 버전선을 찾는 키
 *                   'default' 는 version.json 루트(versionCode/versionName)를 쓴다(하위호환)
 *   match         : 이 variant 로 판정되는 (channel, market) 조합
 *   label         : 로그/문서용 설명
 */
const VARIANTS = {
  default: {
    applicationId: 'com.gospelai.app',
    versionKey: null, // = version.json 루트 버전선
    match: { channel: 'direct', market: ['hk', 'global'] },
    label: '기본(자체 사이트 직링크 · 글로벌)',
  },
  cn: {
    applicationId: 'com.gospelai.app.cn',
    versionKey: 'variants.cn',
    match: { channel: 'direct', market: ['cn'] },
    label: '중국 본토 직분포(Google SDK 0)',
  },
  play: {
    applicationId: 'com.gospelai.app.play',
    versionKey: 'variants.play',
    match: { channel: 'googleplay', market: ['hk', 'global', 'cn'] },
    label: 'Google Play(제3자 결제 0)',
  },
};

/* ── FLOW CCM 音乐(ai-music-platform) 패키지군 ──────────────────────────────
   별도 앱이므로 **반드시 별도 패키지명** 을 가져야 한다.
   같은 패키지명으로 서로 다른 앱을 배포하면 Android 가 동일 앱으로 취급해
   조용히 덮어쓰거나 INSTALL_FAILED_VERSION_DOWNGRADE 로 막힌다.
   versionKey 는 version.json 내 음악 전용 버전선 경로(점 표기):
     기본   → version.json.music
     cn     → version.json.music.variants.cn
     play   → version.json.music.variants.play
   (music 블록이 없으면 version.json 루트로 폴백 — 하위호환)
   ------------------------------------------------------------------------ */
const MUSIC_VARIANTS = {
  default: {
    applicationId: 'com.gospelai.music',
    versionKey: 'music',
    match: { channel: 'direct', market: ['hk', 'global'] },
    label: 'FLOW 音乐 기본(자체 배포)',
  },
  cn: {
    applicationId: 'com.gospelai.music.cn',
    versionKey: 'music.variants.cn',
    match: { channel: 'direct', market: ['cn'] },
    label: 'FLOW 音乐 중국 직분포',
  },
  play: {
    applicationId: 'com.gospelai.music.play',
    versionKey: 'music.variants.play',
    match: { channel: 'googleplay', market: ['hk', 'global', 'cn'] },
    label: 'FLOW 音乐 Google Play(IAP 필수)',
  },
};

const DEFAULT_APP_ID = VARIANTS.default.applicationId;
const DEFAULT_MUSIC_APP_ID = MUSIC_VARIANTS.default.applicationId;

/** app 키('flow' | 'music') → variant 표. */
function variantTable(app) {
  return app === 'music' ? MUSIC_VARIANTS : VARIANTS;
}

/** market 정규화(hk → global) + 허용값 검증. 잘못된 값은 예외(조용한 오배포 방지). */
function normalizeMarket(market, target) {
  const m = market || 'hk';
  if (MARKETS.indexOf(m) === -1) {
    throw new Error('--market 은 ' + MARKETS.join('|') + ' 만 가능합니다(입력: ' + m + ').');
  }
  return m === 'hk' ? 'global' : m;
}

function allowedChannels(target) {
  return target === 'ios' ? CHANNELS_IOS : CHANNELS_ANDROID;
}

function validateChannel(channel, target) {
  const allowed = allowedChannels(target);
  if (allowed.indexOf(channel) === -1) {
    throw new Error('--channel 은 ' + target + ' 타겟에서 ' + allowed.join(' 또는 ') +
      ' 만 가능합니다(입력: ' + channel + ').');
  }
}

/**
 * (channel, market) → variant 키. 매칭되는 것이 없으면 예외.
 * 이전에는 매칭 실패 시 조용히 default 로 떨어졌는데, 그러면
 * "중국판을 만들려다 기본 패키지로 배포" 같은 사고가 로그에 남지 않는다.
 * @param {string} app 'flow'(기본) | 'music'
 */
function resolveVariant(channel, market, app) {
  const table = variantTable(app);
  for (const key of Object.keys(table)) {
    const v = table[key];
    if (v.match.channel !== channel) continue;
    if (v.match.market.indexOf(market) === -1) continue;
    return key;
  }
  const known = Object.keys(table)
    .map(k => k + '(' + table[k].match.channel + '/' + table[k].match.market.join(',') + ')')
    .join(', ');
  throw new Error('[' + (app || 'flow') + '] channel=' + channel + ' + market=' + market +
    ' 조합에 해당하는 variant 가 없습니다. 정의: ' + known);
}

/** variant 키 → applicationId. 화이트라벨 테넌트가 지정되면 테넌트 값이 최우선. */
function resolveAppId(channel, market, tenant, app) {
  if (tenant && tenant.android && tenant.android.applicationId) return tenant.android.applicationId;
  return variantTable(app)[resolveVariant(channel, market, app)].applicationId;
}

/** variant 키 → version.json 내 버전선 경로(점 표기). 'default' 는 앱 루트 버전선. */
function resolveVersionKey(variantKey, app) {
  return variantTable(app)[variantKey].versionKey;
}

/**
 * 점 표기 경로의 **접두사 체인** 을 긴 것부터 만들어 반환(항상 루트로 폴백).
 *   'music'                → [v.music, v]
 *   'music.variants.cn'    → [v.music.variants.cn, v.music.variants, v.music, v]
 * 존재하지 않는 노드는 건너뛴다.
 */
function versionChain(v, dottedKey) {
  const chain = [];
  const parts = String(dottedKey || '').split('.').filter(Boolean);
  for (let i = parts.length; i >= 1; i--) {
    let node = v;
    for (const p of parts.slice(0, i)) {
      node = node && typeof node === 'object' ? node[p] : undefined;
    }
    if (node && typeof node === 'object') chain.push(node);
  }
  chain.push(v); // 항상 루트 폴백(하위호환)
  return chain;
}

/** applicationId → variant 키 (산출물에서 역산. 미등록 패키지면 null). */
function variantByAppId(appId) {
  for (const table of [VARIANTS, MUSIC_VARIANTS]) {
    for (const key of Object.keys(table)) {
      if (table[key].applicationId === appId) return key;
    }
  }
  return null;
}

/** applicationId → 앱 구분('flow' | 'music'). 미등록이면 null. */
function appByAppId(appId) {
  for (const [app, table] of [['flow', VARIANTS], ['music', MUSIC_VARIANTS]]) {
    for (const key of Object.keys(table)) {
      if (table[key].applicationId === appId) return app;
    }
  }
  return null;
}

/**
 * version.json 에서 variant 버전선을 찾는 경로 배열(우선순위 순).
 *   테넌트: tenants.<id>.variants.<key> → tenants.<id>.android → tenants.<id>
 *   기본  : variants.<key> → 루트
 * 'default' 는 루트 버전선을 쓰므로 variants 항목이 없다.
 */
function versionLookup(v, tenantId, variantKey, app) {
  if (tenantId && v.tenants && v.tenants[tenantId]) {
    const t = v.tenants[tenantId];
    return [t.variants && t.variants[variantKey], t.android, t];
  }
  // 음악 앱: versionKey 가 점 표기 경로('music', 'music.variants.cn') → 접두사 체인.
  if (app === 'music') {
    return versionChain(v, resolveVersionKey(variantKey, app) || 'music');
  }
  if (variantKey && variantKey !== 'default' && v.variants && v.variants[variantKey]) {
    return [v.variants[variantKey], v];
  }
  return [v];
}

module.exports = {
  MARKETS,
  CHANNELS_ANDROID,
  CHANNELS_IOS,
  VARIANTS,
  MUSIC_VARIANTS,
  DEFAULT_APP_ID,
  DEFAULT_MUSIC_APP_ID,
  variantTable,
  normalizeMarket,
  allowedChannels,
  validateChannel,
  resolveVariant,
  resolveAppId,
  resolveVersionKey,
  versionChain,
  variantByAppId,
  appByAppId,
  versionLookup,
};
