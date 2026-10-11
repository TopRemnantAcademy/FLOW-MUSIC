"""docker-compose **部署前置校验**：确认所有 ${VAR} 能解析出非空值。

为什么需要：
    compose 里的 `:?` 必填项（JWT_SECRET / MUREKA_API_KEY 等）在**变量缺失时
    `docker compose up` 会直接失败**。过去正是这样——postgres/redis 两个**代码根本
    不用**的服务带着 `:?`，把部署卡死在“必须给未使用组件配密码”上（2026-09-14 修）。
    本脚本让这件事在**部署前**用一条命令查出来，而不是等到服务器上报错。

用法（在仓库根目录）：
    ../.venv_test/Scripts/python.exe scripts/validate_compose_env.py
    ../.venv_test/Scripts/python.exe scripts/validate_compose_env.py --env .env.prod

退出码：0=全部可解析（可部署）  1=存在必填缺失（禁止部署）
安全：**只打印长度，绝不打印任何密钥明文**。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import yaml

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([-?])([^}]*))?\}")


def load_env(path: str) -> dict:
    env: dict = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def resolve(expr: str, env: dict) -> tuple[str, bool]:
    """解析 ${VAR} / ${VAR:-默认} / ${VAR:?提示}，返回 (值, 是否必填缺失)。"""
    m = _VAR_RE.search(expr)
    if not m:
        return expr, False
    name, op, arg = m.group(1), m.group(2), m.group(3)
    val = env.get(name, "")
    if op == "-":
        return (val or arg), False
    return val, not val  # 无修饰符或 :? 都要求非空


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env", help="compose 变量来源文件（默认 .env）")
    ap.add_argument("--compose", default="docker-compose.yml")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = args.env if os.path.isabs(args.env) else os.path.join(root, args.env)
    compose_path = args.compose if os.path.isabs(args.compose) else os.path.join(root, args.compose)

    env = load_env(env_path)
    print(f"[ENV] {env_path}  ({len(env)} keys)")
    with open(compose_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    services = doc.get("services") or {}
    print(f"[COMPOSE] {compose_path}  services={list(services)}")

    missing: list[str] = []
    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        print(f"\n== service: {svc_name} ==")
        for k, v in (svc.get("environment") or {}).items():
            if not isinstance(v, str) or "${" not in v:
                print(f"  {k} = {v!r}")
                continue
            val, is_missing = resolve(v, env)
            if is_missing:
                missing.append(f"{svc_name}.{k}")
                print(f"  {k} -> MISSING (必填但为空)")
            else:
                print(f"  {k} -> OK <len={len(val)}>")

    print()
    if missing:
        print(f"[FAIL] 必填缺失 {len(missing)} 项 —— 禁止部署：")
        for m in missing:
            print(f"   - {m}")
        print("       请在 .env 中补齐后再部署（compose 的 `:?` 会让 up 直接失败）。")
        return 1
    print("[OK] 全部变量可解析 —— 可以部署")
    return 0


if __name__ == "__main__":
    sys.exit(main())
