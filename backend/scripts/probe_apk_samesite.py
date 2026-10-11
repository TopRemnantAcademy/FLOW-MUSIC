"""APK 第一步门禁验证：Cookie / SameSite 登录保持（无真机、无浏览器引擎）。

背景
----
APK 方案（PLAN_MUSIC_APK_2026-09-13.md §约束A）的最大风险是：
登录接口成功返回 200，但 WebView 后续所有带凭据请求都 401。
根因是后端签发的是 `HttpOnly; SameSite=lax` Cookie（auth.py:108-114），
而 Lax 语义下**跨站**子请求不携带 Cookie。

本脚本在**不启动浏览器**的前提下，把这件事拆成两段可验证的事实：

  段 1（本机实测）：后端到底签发了什么 Cookie，`/api/auth/me` 是否只认 Cookie。
  段 2（规范求值）：给定「应用域 → API 域」对，按 RFC 6265bis §5.2 判定
                    同站/跨站，从而得出浏览器会不会带上 Cookie。

段 2 是规范推导，不是浏览器实测——本机 Chrome 无法启动（见文档 §8.3），
真机/带 Chrome 的机器上用 `--serve` 起 harness 可补上最后一公里。

用法
----
    # 段 1 + 段 2（默认，零外部依赖）
    python backend/scripts/probe_apk_samesite.py

    # 只跑段 2（纯算法，不需要后端可导入）
    python backend/scripts/probe_apk_samesite.py --algo-only

    # 起一个本地服务，供「任意有 Chrome 的机器」跑真浏览器验证
    python backend/scripts/probe_apk_samesite.py --serve 4174

退出码
------
    0 = 段 1 契约符合预期（Cookie 可签发、/me 只认 Cookie）
    1 = 契约不符（说明加固被回退或改动）
    2 = 环境错误（依赖缺失 / 应用导入失败）
"""

from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 段 2：RFC 6265bis §5.2 "same-site" 判定
# ---------------------------------------------------------------------------

# 仅覆盖 FLOW 实际会用到的后缀（不引入完整 PSL 依赖）。
# 规则：可注册域 = 公有后缀 + 1 个标签。
MULTI_LABEL_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.cn",
    "net.cn",
    "org.cn",
    "gov.cn",
    "co.kr",
    "or.kr",
    "ne.kr",
    "com.hk",
    "org.hk",
    "net.hk",
    "gov.hk",
    "com.tw",
    "com.br",
    "com.au",
    "co.jp",
    "co.nz",
    "co.za",
    "github.io",
    "vercel.app",
    "workers.dev",
}


def registrable_domain(host: str) -> str:
    """返回 host 的可注册域（eTLD+1）。host 可以是 IP 或带端口的字符串。"""
    host = host.strip().lower().rstrip(".")
    if not host:
        return ""
    # IPv4 / IPv6 没有"站点"概念，逐字节精确比较
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) or ":" in host:
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_same_site(url_a: str, url_b: str) -> bool:
    """RFC 6265bis §5.2：两个 URL 是否「同站」。

    同站 = 可注册域相等（scheme 不参与 SameSite 判定，但参与 Secure）。
    """
    a = registrable_domain(urlparse(url_a).hostname or "")
    b = registrable_domain(urlparse(url_b).hostname or "")
    return bool(a) and a == b


# ---------------------------------------------------------------------------
# 段 1：对真实后端做 Cookie 契约探测
# ---------------------------------------------------------------------------


def _probe_backend() -> int:
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover
        print(f"[段1] 跳过：缺少 fastapi.testclient（{exc}）")
        return 2

    import os
    import tempfile

    # 独立临时库，避免污染开发/生产数据
    tmpdir = tempfile.mkdtemp(prefix="apk_samesite_")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{tmpdir}/probe.db")

    try:
        import app.main as _app_main  # noqa: WPS433
        import app.models  # noqa: F401  确保模型注册
        from app.config import settings
        from app.db import Base, SessionLocal
        from sqlalchemy import create_engine
    except Exception as exc:
        print(f"[段1] 跳过：无法导入后端（{exc}）")
        print("      提示：需 PYTHONPATH=backend 且 CWD=backend")
        return 2

    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)
    settings.jwt_secret = settings.jwt_secret or "probe-secret-not-for-production"

    fastapi_app = _app_main.app
    email = "apk-probe@example.com"
    password = "Probe12345!"

    print("=" * 72)
    print("段 1 ｜ 后端 Cookie 契约实测")
    print("=" * 72)

    failures = []

    # ---- 1a. HTTPS 基址：注册应下发 Set-Cookie（且 secure=True）
    with TestClient(fastapi_app, base_url="https://api.music.test") as c:
        r = c.post("/api/auth/register", json={"email": email, "password": password})
        raw = c.post(
            "/api/auth/register",
            json={"email": email.replace("@", "+2@"), "password": password},
        ).headers.get_list("set-cookie")

    cookies = raw or []
    print(f"  POST /api/auth/register (https)  ->  {r.status_code}")
    print(f"  Set-Cookie 条数                  ->  {len(cookies)}")
    for ck in cookies:
        # 不落任何真实令牌：只打属性，值打码（安全基线：日志禁露密钥）
        masked = re.sub(r"(flow_auth=)[^;]+", r"\1<JWT len=N>", ck, flags=re.I)
        print(f"     {masked[:160]}")

    joined = " ; ".join(cookies)
    checks = [
        ("签发身份 Cookie", "flow_auth=" in joined),
        ("HttpOnly", "httponly" in joined.lower()),
        ("SameSite=lax", "samesite=lax" in joined.lower()),
        ("Path=/", "path=/" in joined.lower()),
        ("HTTPS 下 Secure", "secure" in joined.lower()),
    ]
    for name, ok in checks:
        print(f"     [{'OK ' if ok else 'FAIL'}] {name}")
        if not ok:
            failures.append(name)

    # ---- 1b. HTTP 基址：不应带 Secure（否则 http://localhost WebView 直接丢 Cookie）
    with TestClient(fastapi_app, base_url="http://api.music.test") as c2:
        http_cookies = c2.post(
            "/api/auth/register",
            json={"email": email.replace("@", "+3@"), "password": password},
        ).headers.get_list("set-cookie")
    http_joined = " ; ".join(http_cookies).lower()
    http_no_secure = "secure" not in http_joined
    print(
        f"     [{'OK ' if http_no_secure else 'FAIL'}] "
        f"HTTP 下不设 Secure（否则 localhost WebView 收不到 Cookie）"
    )
    if not http_no_secure:
        failures.append("HTTP 下不设 Secure")

    # ---- 1c. /me 只认 Cookie：无 Cookie -> 401，有 Cookie -> 200
    with TestClient(fastapi_app, base_url="http://api.music.test") as c3:
        reg = c3.post(
            "/api/auth/register",
            json={"email": email.replace("@", "+5@"), "password": password},
        )
        c3.cookies.clear()
        no_cookie = c3.get("/api/auth/me")
        c3.cookies.clear()
        token = None
        for ck in reg.headers.get_list("set-cookie"):
            m = re.search(r"flow_auth=([^;]+)", ck)
            if m:
                token = m.group(1)
        c3.cookies.set("flow_auth", token or "", domain="api.music.test")
        with_cookie = c3.get("/api/auth/me")

    print(f"  GET /api/auth/me  无 Cookie       ->  {no_cookie.status_code}（期望 401）")
    print(f"  GET /api/auth/me  带 Cookie       ->  {with_cookie.status_code}（期望 200）")
    if no_cookie.status_code != 401:
        failures.append("无 Cookie 应 401")
    if with_cookie.status_code != 200:
        failures.append("带 Cookie 应 200")

    print()
    print("  ⚠ 关键观察：上面「带 Cookie -> 200」是**非浏览器客户端**的结果。")
    print("    httpx/okhttp 不实现 SameSite，无论跨域与否都会带上 Cookie。")
    print("    这正是该缺陷只在 WebView 里暴露、本地 curl 永远测不出来的原因。")
    print()

    return 1 if failures else 0


# ---------------------------------------------------------------------------
# 段 2：候选拓扑求值
# ---------------------------------------------------------------------------

SCENARIOS = [
    (
        "A. 现状直连（capacitor 默认）",
        "http://localhost",
        "https://api.flow.example.com/api/auth/me",
        "WebView 源是 localhost，API 是另一个站点 → 跨站",
    ),
    (
        "B. 推荐方案（同父域兄弟子域）",
        "https://music.flow.example.com",
        "https://api.flow.example.com/api/auth/me",
        "capacitor server.hostname=music.<域>，与 API 同父域 → 同站",
    ),
    (
        "C. 反例：同父域但用完全不同主域",
        "https://music.flow.example.com",
        "https://api.other-example.com/api/auth/me",
        "父域不同 → 跨站（SameSite=Lax 依然失效）",
    ),
    (
        "D. 反例：多标签公有后缀下各自为站",
        "https://flow.github.io",
        "https://api.github.io",
        "github.io 是多标签公有后缀 → 两者是不同站点",
    ),
]


def _probe_algo() -> int:
    print("=" * 72)
    print("段 2 ｜ 同站判定（RFC 6265bis §5.2 可注册域比较）")
    print("=" * 72)
    print(f"{'场景':<34}{'应用域':<34}{'判定':<10}{'Lax 下结果'}")
    print("-" * 100)
    for name, app_origin, api_url, note in SCENARIOS:
        same = is_same_site(app_origin, api_url)
        verdict = "同站" if same else "跨站"
        result = "Cookie 会带上 ✅" if same else "Cookie 被丢弃 ❌ 登录态失效"
        print(f"{name:<34}{urlparse(app_origin).hostname or '':<34}{verdict:<10}{result}")
        print(f"    └ {note}")
    print()
    return 0


# ---------------------------------------------------------------------------
# --serve：给「任意有 Chrome 的机器」用的真浏览器 harness
# ---------------------------------------------------------------------------

HARNESS_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>apk-samesite-harness</title></head>
<body><pre id="out">running...</pre>
<script>
const p = new URLSearchParams(location.search);
const API = p.get("api"), TAG = p.get("tag");
const COLLECT = p.get("collect") || (location.protocol + "//127.0.0.1:4174/result");
const email = "apk-" + Math.random().toString(36).slice(2, 10) + "@example.com";
(async () => {
  const r = { tag: TAG, appOrigin: location.origin, api: API, email };
  try {
    const reg = await fetch(API + "/api/auth/register", {
      method: "POST", credentials: "include",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email, password: "Test12345!"})});
    r.register_status = reg.status;
    r.set_cookie_seen = (reg.headers.get("set-cookie") || "").slice(0, 120) || null;
    const me = await fetch(API + "/api/auth/me", {credentials: "include"});
    r.me_status = me.status;
    r.me_body = (await me.text()).slice(0, 200);
    r.cookie_sent = (r.me_status === 200);
  } catch (e) { r.error = String(e); r.cookie_sent = false; }
  document.getElementById("out").textContent = JSON.stringify(r, null, 2);
  try {
    await fetch(COLLECT, {method: "POST", mode: "cors",
      headers: {"Content-Type": "text/plain"}, body: JSON.stringify(r)});
  } catch (e) {}
})();
</script></body></html>
"""


def _serve(port: int) -> int:
    """起一个最小服务：GET /harness.html 返回探针页，POST /result 落盘。"""
    import http.server
    import json
    import pathlib
    import socketserver
    import threading

    out = pathlib.Path(__file__).with_name("apk_samesite_results.jsonl")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音
            pass

        def do_GET(self):  # noqa: N802
            body = HARNESS_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            with out.open("a", encoding="utf-8") as fh:
                try:
                    fh.write(json.dumps(json.loads(raw), ensure_ascii=False) + "\n")
                except Exception:
                    fh.write(raw.decode("utf-8", "replace") + "\n")
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with S(("0.0.0.0", port), H) as httpd:
        print(f"[serve] harness: http://<app 域名>:{port}/harness.html")
        print(f"[serve] 结果落盘: {out}")
        print("[serve] Ctrl+C 退出")
        httpd.serve_forever()
    return 0


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--algo-only", action="store_true", help="只跑段 2（同站判定）")
    ap.add_argument("--serve", type=int, metavar="PORT", help="启动浏览器 harness 服务")
    args = ap.parse_args()

    if args.serve:
        return _serve(args.serve)
    if args.algo_only:
        return _probe_algo()

    rc1 = _probe_backend()
    print()
    rc2 = _probe_algo()
    print("=" * 72)
    print("结论")
    print("=" * 72)
    print("  段 1：后端确实只以 HttpOnly+SameSite=lax Cookie 维持登录态。")
    print("  段 2：只有「应用域与 API 域同站」时，WebView 才会带上该 Cookie。")
    print("  → APK 第一步必须先定死域名拓扑（方案 B），再做静态导出与壳工程。")
    print()
    print("  未闭合：段 2 是规范推导，本机 Chrome 无法启动（PowerShell 执行")
    print("  chrome.exe 输出恒为空）。在带 Chrome 的机器上执行：")
    print("    python backend/scripts/probe_apk_samesite.py --serve 4174")
    print("    chrome --headless=new --no-proxy-server \\")
    print(
        '      --host-resolver-rules="MAP music.flow.test 127.0.0.1,'
        ' MAP api.flow.test 127.0.0.1" \\'
    )
    print("      --user-data-dir=%TEMP%\\p1 \\")
    print(
        '      "http://music.flow.test:4174/harness.html'
        '?api=http%3A%2F%2Fapi.flow.test%3A8000&tag=B"'
    )
    print("  预期：B 场景 cookie_sent=true；把 api 换成跨站域则 false。")
    return rc1 or rc2


if __name__ == "__main__":
    sys.exit(main())
