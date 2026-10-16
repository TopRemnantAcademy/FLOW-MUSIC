/**
 * 보안 헤더 **단일 진실 공급원(single source of truth)**.
 *
 * 왜 분리했나:
 *   - Web(next dev/start) 는 next.config.js 의 async headers() 로 실제 HTTP 헤더를 내려준다.
 *   - APK 는 Capacitor WebView 가 로컬 정적 자산을 서빙하므로 **Caddy/Nginx 가 없다**.
 *     게다가 Next `output: 'export'` 는 headers() 를 **무시** 한다(정적 파일에 헤더가 없음).
 *     → APK 에서 헤더를 살리는 유일한 방법은 `<meta http-equiv>` 를 HTML 에 주입하는 것.
 *   - 두 경로가 서로 다른 곳에 정책을 적으면 반드시 드리프트가 생긴다(한쪽만 강화되는 사고).
 *     → 따라서 정책 문자열을 이 파일 하나에만 둔다.
 *
 * ⚠️ <meta http-equiv> 로는 전달할 수 없는 지시문(CSP 레벨3):
 *     frame-ancestors / sandbox / report-uri / report-to
 *     → meta 로 내릴 때는 제외된다. (APK 는 네이티브 WebView 라 frame 조차 없어 실효Risk 없음.
 *       Web 은 headers() 로 나가므로 frame-ancestors 'none' 이 정상 적용된다.)
 */
"use strict";

/** 拼接 origin 列表（去重、保序、丢弃空值）。`*` 与 `https:` 这类**通配**一律只允许显式传入。 */
function origins(...lists) {
  const out = [];
  for (const list of lists) {
    for (const raw of list || []) {
      const o = String(raw || "").trim().replace(/\/+$/, "");
      if (o && !out.includes(o)) out.push(o);
    }
  }
  return out;
}

/**
 * 미디어(오디오/이미지) 호스트 화이트리스트 — **SSoT**.
 *
 * 왜 여기(헤더 모듈)에 두나:
 *   `img-src`/`media-src` 는 "이 프로젝트가 실제로 쓰는 미디어 출처"의 함수다.
 *   백엔드 CORS(`app/config.py:cors_origins`)와 **의미가 다른 별개 목록**이므로
 *   같은 파일에 섞으면 둘 중 하나가 반드시 어긋난다(교집합 = API 원본뿐).
 *
 * 포함 근거(전부 repo 안에서 실측 가능, 지어낸 값 없음):
 *   · apiBase           — `SongOut.audio_url` 은 GAP-019 미적용 시 상대경로,
 *                         적용 시 API 와 같은 호스트의 `/api/songs/{id}/download` 등.
 *   · STORAGE_PUBLIC_BASE — 자체 CDN(음악/커버). 없으면 같은 출처·API 출처로 커버된다.
 *   · cdn.mureka.ai     — **주 엔진** Mureka 가 내려주는 audio_url/cover_url 호스트
 *                         (PRO_MUSIC_PLATFORM_GAP_PLAN.md:325 에 도메인 명시).
 *                         storage 미설정 배포(=현행 prod 기본)의 실제 재생 경로다.
 *
 * ⚠ 여기에 도메인을 추가할 때: 실제로 그 호스트의 미디어를 **렌더**하는 코드가 있어야 한다.
 *   "혹시 몰라서" 넣는 항목은 그 순간 `https:` 통짜 허용과 다를 바 없어진다.
 */
function mediaHosts(apiBase, cdnBase) {
  return origins([apiBase], [cdnBase], ["https://cdn.mureka.ai"]);
}

/**
 * connect-src 출처 목록 정규화: 공백/빈 값 제거 + 중복 제거(순서 보존).
 * 빈 문자열을 그대로 붙이면 `connect-src 'self' ` 처럼 지시문이 비어 보이고,
 * 중복은 정책 문자열만 길어지고 이득이 없다.
 */
function dedupeOrigins(origins) {
  const seen = new Set();
  const out = [];
  for (const o of origins || []) {
    const v = String(o || "").trim();
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

/**
 * CSP 정책 문자열 생성.
 * @param {string} apiBase -런타임 API 원본(예: https://api.example.com). connect-src/media-src/img-src 에 허용.
 * @param {string[]} [extraConnectOrigins] -추가 connect-src 출처(예: 문의용 FLOW 主站, 埋点 엔드포인트).
 *   프론트가 **빌드 시점에 굽는** 교차 출처 fetch 대상. 여기 안 넣으면 Web 에서
 *   CSP 가 요청을 **조용히 차단** 한다(콘솔만 에러, 기능은 죽은 채로 보임).
 * @param {string} [cdnBase] -자체 CDN/오브젝트 스토리지 공개 베이스(`STORAGE_PUBLIC_BASE`).
 *   음악·커버가 API 와 **다른 호스트**로 나갈 때만 필요하다.
 */
function contentSecurityPolicy(apiBase, extraConnectOrigins = [], cdnBase) {
  // 'unsafe-eval' 은 Next dev(HMR/React Refresh) 에만 필요하고 **프로덕션 산출물에는 불필요**.
  // → 개발에서만 붙여 프로덕션 CSP 를 실제로 강화한다(항상 넣으면 CSP 무력화).
  const scriptSrc = ["'self'", "'unsafe-inline'"];
  if (process.env.NODE_ENV !== "production") scriptSrc.push("'unsafe-eval'");

  const api = origins([apiBase])[0] || "";
  const media = mediaHosts(apiBase, cdnBase);

  return [
    "default-src 'self'",
    // Next.js 인라인 부트스트랩 스크립트 → 'unsafe-inline' 불가피(nonce 화는 별도 과제).
    "script-src " + scriptSrc.join(" "),
    "style-src 'self' 'unsafe-inline'",
    // [SEC] 2026-10-16: 종전 `img-src 'self' data: blob: https:` 의 **통짜 `https:`** 를 제거.
    //   `https:` 하나만 남아 있어도 "임의의 https 호스트에서 이미지/오디오를 받아도 된다"는
    //   뜻이라, connect-src 로 좁혀 둔 화이트리스트가 실질적으로 무력했다(이미지/오디오
    //   경유 유출·추적 픽셀·외부 스트리밍 모두 통과). data:/blob: 은 유지한다 —
    //   후자는 `URL.createObjectURL`(미리듣기·다운로드), 전자는 인라인 아이콘/파비콘에 실사용.
    "img-src " + ["'self'", "data:", "blob:", ...media].join(" "),
    "font-src 'self' data:",
    "connect-src 'self' " + [api, ...dedupeOrigins(extraConnectOrigins)].filter(Boolean).join(" "),
    "media-src " + ["'self'", "blob:", ...media].join(" "),
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** next.config.js headers() 용 — 실제 HTTP 응답 헤더. */
function securityHeaders(apiBase, extraConnectOrigins = [], cdnBase) {
  return [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "geolocation=(), microphone=(), camera=()" },
    { key: "Content-Security-Policy", value: contentSecurityPolicy(apiBase, extraConnectOrigins, cdnBase) },
    // HSTS: HTTPS 배포에서만 의미가 있어承载层(Caddy) 가 내리는 것을 권장.
    // 여기서는 중복 전송을 피하기 위해 넣지 않는다(로컬 http 개발 시 브라우저가 기억해버림).
  ];
}

// <meta http-equiv> 로 옮길 수 없는 CSP 지시문(명세상 무시됨).
const META_UNSUPPORTED = ["frame-ancestors", "sandbox", "report-uri", "report-to"];

/**
 * APK 정적 HTML 에 주입할 <meta http-equiv> 목록(headers() 의 등가물).
 * @returns {Array<{httpEquiv: string, content: string}>}
 */
function metaEquivalent(apiBase, extraConnectOrigins = [], cdnBase) {
  const out = [];
  for (const h of securityHeaders(apiBase, extraConnectOrigins, cdnBase)) {
    if (h.key === "Content-Security-Policy") {
      // 지원되지 않는 지시문을 제거한 CSP 만 meta 로 내린다(남겨도 무시되지만,
      // "적용되는 줄 알았는데 무시됨" 오해를 없애기 위해 명시적으로 걷어낸다).
      const filtered = h.value
        .split(";")
        .map((s) => s.trim())
        .filter((s) => s && !META_UNSUPPORTED.some((d) => s.startsWith(d + " ")))
        .join("; ");
      out.push({ httpEquiv: "Content-Security-Policy", content: filtered });
      continue;
    }
    // X-Frame-Options / X-Content-Type-Options 등은 <meta http-equiv> 로 **동작하지 않는다**
    // (브라우저는 이 두 헤더를 meta 로 인정하지 않음). CSP frame-ancestors 가 대체한다.
    // → 혼선 방지를 위해 넣지 않고, 호출부가 CSP 에 의존하도록 한다.
  }
  return out;
}

module.exports = {
  contentSecurityPolicy,
  securityHeaders,
  metaEquivalent,
  mediaHosts,
  dedupeOrigins,
  META_UNSUPPORTED,
};
