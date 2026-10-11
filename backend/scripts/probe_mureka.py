"""Mureka 连通性/鉴权/地域/账单探测（免费：GET /v1/account/billing 查询不计费）。

用途（对应 P0-CRIT「出口区域探测」）：
  · 本机预检：key 是否有效、当前出口能否到达 Mureka；
  · **部署侧（中国香港服务器）上线前必跑**：403 = 区域封锁确认，200 = 出口可用。
  ⚠ 本机出口 ≠ 生产出口：本机 200 不代表中国香港出口可用，反之亦然；
    最终以部署侧执行结果为准。

本脚本直接复用运行时同一套分类逻辑（app.mureka_health.probe_egress →
orchestrator.mureka._classify_mureka_error），保证「脚本看到的」与「服务运行时
判断的」完全一致，不会出现两套口径。

用法：
  cd backend && ../.venv_test/Scripts/python.exe scripts/probe_mureka.py
  MUREKA_API_KEY=sk-xxx python scripts/probe_mureka.py --base https://api.mureka.ai/v1

读取顺序（先到先得，不覆盖已有环境变量）：
  backend/.env → 仓库根 .env → 进程环境变量

退出码：0=成功  2=401(key 无效)  3=403(区域封锁/无权限)  4=网络错误  1=其他/未配置
"""

import asyncio
import os
import pathlib
import sys


def load_env(paths: list[pathlib.Path]) -> None:
    """轻量 .env 读取（key=value，# 注释，不覆盖已有环境变量）。"""
    for p in paths:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def parse_args(argv: list[str]) -> tuple[str | None, str | None]:
    """解析 --base / --key（成对取值，忽略未知参数）。"""
    base: str | None = None
    key: str | None = None
    i = 0
    while i < len(argv):
        if argv[i] == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            i += 2
        elif argv[i] == "--key" and i + 1 < len(argv):
            key = argv[i + 1]
            i += 2
        else:
            i += 1
    return base, key


#: kind → 退出码（与既有文档/运维脚本约定保持一致）
_EXIT_BY_KIND = {
    "ok": 0,
    "auth": 2,  # 401
    "region": 3,  # 403
    "network": 4,
}


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent  # backend/scripts
    backend_dir = here.parent  # backend
    load_env([backend_dir / ".env", backend_dir.parent / ".env"])
    arg_base, arg_key = parse_args(sys.argv[1:])
    if arg_key:
        os.environ["MUREKA_API_KEY"] = arg_key
    if arg_base:
        os.environ["MUREKA_API_BASE"] = arg_base

    # 使 `import app.*` 可用（脚本以 backend/scripts 为 sys.path[0]）
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    try:
        from app.mureka_health import format_report, probe_egress
    except Exception as e:  # 依赖未装 / 路径不对
        print(f"[FAIL] 无法导入 app.mureka_health: {type(e).__name__}: {e}")
        print("       请确认在 backend 目录下用项目 venv 运行：")
        print("       cd backend && ../.venv_test/Scripts/python.exe scripts/probe_mureka.py")
        return 1

    base = os.environ.get("MUREKA_API_BASE", "https://api.mureka.ai/v1").strip().rstrip("/")
    key = os.environ.get("MUREKA_API_KEY", "").strip()
    print(f"[PROBE] GET {base}/account/billing  (key: ****{key[-4:] if len(key) >= 4 else '??'})")

    result = asyncio.run(probe_egress())
    kind = result.get("kind", "unknown")
    if result.get("status_code") is not None:
        print(f"[HTTP] {result['status_code']}")
    print(f"[KIND] {kind}")

    billing = result.get("billing") or {}
    for k in (
        "account_id",
        "balance",
        "total_recharge",
        "total_spending",
        "concurrent_request_limit",
    ):
        if k in billing:
            print(f"  {k} = {billing.get(k)}")

    # 一行结论（与运行日志/管理端点完全同源）
    print(f"[REPORT] {format_report(result)}")
    if result.get("remediation"):
        print(f"[ACTION] {result['remediation']}")

    if kind == "ok":
        print("[OK] 该出口可达 Mureka 且 key 有效（注意：本机出口 ≠ 生产出口）")
    return _EXIT_BY_KIND.get(kind, 1)


if __name__ == "__main__":
    sys.exit(main())
