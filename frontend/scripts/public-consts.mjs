/**
 * 빌드 시점에 **클라이언트 번들에 인라인되는** 공개 상수(NEXT_PUBLIC_*)의
 * 단일 해석/검증 지점.
 *
 * 왜 필요한가(2026-10-15, P1-3):
 *   `next.config.js` 가 아니라 **소스**에 fallback 이 박혀 있는 상수들이 있다:
 *     · lib/api.ts:API_BASE           → http://localhost:8000 (Web)
 *     · lib/api.ts:GOSPEL_API_BASE    → https://anistar.studio
 *     · lib/eventReporter.ts:FLOW_EVENTS_BASE → http://localhost:8000 (Web)
 *   APK 경로(scripts/build-apk.mjs)만 GOSPEL 을 주입했고 **Web 배포 경로는 아무도
 *   주입하지 않았다** → 운영 도메인에서 ①"문의하기"가 엉뚱한 기본값으로 가고
 *   ②埋点이 localhost 로 향해 **조용히 실패**(fetch 예외는 삼켜짐)했다.
 *
 *   게다가 정적 익스포트는 값이 **빌드 시점에 굽히므로** 런타임 .env 로 못 고친다.
 *   → "빌드할 때 없으면 실패"(fail-fast)가 유일하게 안전한 기본값이다.
 *
 * 설계:
 *   · 여기서는 **해석 + 검증**만 한다(부작용 없음) → Web/APK/CI 가 공유.
 *   · `REQUIRE=1`(기본) 이면 값이 없거나 loopback/비 HTTPS 면 throw.
 *     로컬 개발 검증용으로만 REQUIRE=0 으로 완화한다(운영 기본값은 엄격).
 */
import { fileURLToPath } from "node:url";

/** 엔드포인트별 스펙. 없으면 die. */
export const PUBLIC_CONSTS = [
  {
    env: "NEXT_PUBLIC_API_BASE",
    // Web 개발용 fallback 은 소스(lib/api.ts)에 남아 있지만, **운영 빌드는 명시 필수**.
    webDefault: "",
    apkDefault: "",
    mustHttps: true,
    what: "음악 플랫폼 백엔드 API",
  },
  {
    env: "NEXT_PUBLIC_GOSPEL_API_BASE",
    // 문의(support_tickets)는 FLOW 主站에 쌓인다(lib/api.ts:1262). 主站 주소는
    // 음악 도메인과 다르므로 MUSIC_DOMAIN 으로 유도할 수 없다 → 명시 필수.
    webDefault: "",
    apkDefault: "https://anistar.studio",
    mustHttps: true,
    what: "FLOW 主站(문의 접수)",
  },
  {
    env: "NEXT_PUBLIC_FLOW_EVENTS_BASE",
    // 埋点 배치 엔드포인트(/mobile/events/batch). FLOW 主站과 동일 백엔드.
    webDefault: "",
    apkDefault: "https://anistar.studio",
    mustHttps: true,
    what: "FLOW 埋点 수집",
  },
];

function isLoopback(v) {
  try {
    return /^(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])(:|$)/i.test(new URL(v).hostname);
  } catch {
    return false;
  }
}

/**
 * @param {Record<string,string|undefined>} env
 * @param {{target?: "web"|"apk", require?: boolean}} [opts]
 * @returns {Record<string,string>} env 에 그대로 합쳐 쓸 확정값
 */
export function resolvePublicConsts(env = {}, opts = {}) {
  const target = opts.target === "apk" ? "apk" : "web";
  const require = opts.require !== false; // 기본 엄격
  const out = {};
  const problems = [];

  for (const c of PUBLIC_CONSTS) {
    let val = String(env[c.env] ?? "").trim();
    if (!val) val = String((target === "apk" ? c.apkDefault : c.webDefault) || "").trim();
    if (!val) {
      if (require) problems.push(`${c.env} 미설정 (${c.what})`);
      continue;
    }
    // 값 형태 검증: 정적 익스포트는 이 문자열을 **그대로 fetch URL 접두어**로 쓴다.
    if (require) {
      if (/\s/.test(val)) problems.push(`${c.env} 에 공백: ${JSON.stringify(val)}`);
      let u;
      try { u = new URL(val); } catch { problems.push(`${c.env} 이 URL 이 아님: ${JSON.stringify(val)}`); }
      if (u) {
        if (c.mustHttps && u.protocol !== "https:") problems.push(`${c.env} 이 HTTPS 아님: ${val}`);
        if (isLoopback(val)) problems.push(`${c.env} 이 loopback(${val}) — 배포 산출물에 굽히면 접속 불가`);
      }
    }
    out[c.env] = val;
  }

  if (problems.length) {
    const msg = `[public-consts] ❌ 빌드 시점 공개 상수 검증 실패(${target}, ${problems.length}건):\n` +
      problems.map((p) => "    · " + p).join("\n") +
      `\n  → 정적 익스포트는 빌드 시점에 값이 굽힌다. 런타임 .env 로 고칠 수 없다.` +
      `\n  → 위 값을 빌드 env 로 주입하세요(Web: deploy/hk/bootstrap.sh, APK: --api / .env).`;
    throw new Error(msg);
  }
  return out;
}

/** connect-src 에 허용할 **교차 출처** 목록(API base 제외). */
export function connectOriginsFrom(consts) {
  return [consts.NEXT_PUBLIC_GOSPEL_API_BASE, consts.NEXT_PUBLIC_FLOW_EVENTS_BASE].filter(Boolean);
}

// ── CLI: bootstrap.sh / CI 가 `node scripts/public-consts.mjs check` 로 fail-fast ──
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const cmd = process.argv[2] || "check";
  if (cmd === "check") {
    try {
      const c = resolvePublicConsts(process.env, { target: process.env.BUILD_TARGET === "apk" ? "apk" : "web" });
      console.log("[public-consts] ✅ 통과: " + Object.entries(c).map(([k, v]) => `${k}=${v}`).join("  "));
    } catch (e) {
      console.error(e.message);
      process.exit(1);
    }
  } else {
    console.error(`[public-consts] 알 수 없는 명령: ${cmd}`);
    process.exit(2);
  }
}
