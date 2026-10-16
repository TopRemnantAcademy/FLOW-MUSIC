/** @type {import('next').NextConfig} */
const { securityHeaders } = require("./security-headers");

// ── APK(정적 익스포트) 빌드 여부 ────────────────────────────────────────────
//   APK_BUILD=1 일 때만 `output: 'export'` 를 켠다.
//   이유: 정적 익스포트는 ① headers() 를 무시하고 ② 이미지 최적화 서버가 없으므로
//   Web(next dev / next start) 과 설정이 근본적으로 달라진다. 하나의 설정으로 둘을
//   동시에 만족시킬 수 없으므로 **명시적 플래그로만 전환** 해 Web 빌드가
//   예기치 않게 바뀌는 사고를 막는다(기본값 = 기존 Web 동작 유지).
//   산출물은 `out/`(정적 익스포트 기본 디렉터리).
const isApk = process.env.APK_BUILD === "1";

const nextConfig = {
  reactStrictMode: true,

  // [SEC] 보안 헤더 — 정책 문자열은 security-headers.js 단일 진실 공급원에 있다.
  // ⚠️ output: 'export' 에서는 이 headers() 가 **적용되지 않는다**(정적 파일).
  //    APK 경로는 scripts/build-apk.mjs 가 동일 정책을 <meta http-equiv> 로 주입해 보완한다.
  async headers() {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
    // connect-src cross-origin fetch targets (inquiry=FLOW 主站 / analytics) must be whitelisted in connect-src.
    const connectOrigins = [
      process.env.NEXT_PUBLIC_GOSPEL_API_BASE,
      process.env.NEXT_PUBLIC_FLOW_EVENTS_BASE,
    ].filter(Boolean);
    // Optional self-hosted CDN (STORAGE_PUBLIC_BASE): only when media leaves the API host.
    const cdnBase = process.env.NEXT_PUBLIC_STORAGE_PUBLIC_BASE || "";
    return [
      {
        source: "/:path*",
        headers: securityHeaders(apiBase, connectOrigins, cdnBase),
      },
    ];
  },
};

if (isApk) {
  nextConfig.output = "export";
  // 정적 익스포트에는 Next 이미지 최적화 런타임(서버)이 없음 → 원본 이미지 사용.
  nextConfig.images = { unoptimized: true };
  // 정적 호스팅/Capacitor 로컬 서버에서 디렉터리형 경로가 가장 안전하다.
  nextConfig.trailingSlash = true;
  // [FIX] workspace-root 오탐 방지: FLOW 모노레포 루트에 package-lock.json 이
  // 여러 개 있어 Turbopack 이 워크스페이스 루트를 FLOW 루트로 오인 →
  // "multiple package managers" 경고 + 빌드 캐시 위치 비정상. 프론트엔드 디렉터리
  // 를 명시적 루트로 핀한다.
  nextConfig.turbopack = { root: __dirname };
}

module.exports = nextConfig;
