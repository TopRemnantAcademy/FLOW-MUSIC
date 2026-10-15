#!/usr/bin/env python3
"""probe_ace_mcp.py · MCP stdio 服务器连通性探针（纯 stdlib）(#23/#28)

把 #28 速查表里「MCP 列表缺项 / connected 无工具 / 有输出无声」三症状的
人工排查变成**一条命令**。对任意 stdio 型 MCP 服务器发起真实握手：

    initialize  →  tools/list  （可选 → tools/call）

只做**只读探测**：``initialize`` 与 ``tools/list`` 不修改任何工程；
``--call`` 默认被拒绝（需显式 ``--allow-edit`` 才放行），防止探针误改 ACE 工程
（对应 ``01_preflight/risk_register.md`` D-05 MCP 误操作）。

用法::

    # 默认探测 ACE Studio MCP
    python tools/checks/probe_ace_mcp.py
    python tools/checks/probe_ace_mcp.py --json
    # 指定其它 stdio 服务器
    python tools/checks/probe_ace_mcp.py --exe /path/to/server --args a b
    # 只读调用一个工具（白名单内）
    python tools/checks/probe_ace_mcp.py --call project_read
    # 调用写类工具（需显式放行）
    python tools/checks/probe_ace_mcp.py --call note_edit --args '{"subcommand":"x"}' --allow-edit

退出码语义（测试逐条断言）::

    0  PASS —— 握手成功且 tools/list 返回 ≥1 个工具
    1  FAIL —— 启动失败 / 握手超时 / 无工具 / 协议错误
    2  用法或参数错误（如 --call 写类工具但未给 --allow-edit）

## 不做的事

- 不注册、不写 ``.mcp.json``（注册属 #23，配置是文件，不是本脚本职责）
- 不判定具体工具的业务正确性（那要 ACE Studio 主程序在线才有数据）
- 不重试、不兜底：超时即报 FAIL，避免把「假连上」当成功（P5）
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading

DEFAULT_EXE = "C:/Program Files/ACE Studio/ace-mcp-server.exe"
PROTOCOL_VERSION = "2024-11-05"

#: 视为「写类」的工具名后缀 —— 未给 ``--allow-edit`` 时拒绝调用
EDIT_SUFFIXES = ("_edit",)


class ProbeError(RuntimeError):
    """探针自身无法继续（用法/环境问题），区别于被测服务器的失败。"""


def _spawn(exe: str, args: list[str]) -> subprocess.Popen:
    try:
        return subprocess.Popen(
            [exe, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise ProbeError("executable not found: %s" % exe) from exc
    except OSError as exc:  # pragma: no cover - 平台相关
        raise ProbeError("cannot spawn %s: %s" % (exe, exc)) from exc


def handshake(exe: str, args: list[str], timeout: float = 10.0) -> dict:
    """对 stdio MCP 服务器做 initialize + tools/list，返回结果字典。

    返回键：``ok`` / ``protocol_version`` / ``server_info`` / ``instructions``
    / ``tools`` / ``tool_count`` / ``error`` / ``stderr``。
    **不抛异常**（除 ``ProbeError`` 表示本地用法/环境问题），失败一律落 ``error``。
    """
    proc = _spawn(exe, args)
    lines: "queue.Queue[str]" = queue.Queue()

    def _reader() -> None:
        try:
            for line in proc.stdout:
                lines.put(line)
        except Exception:  # pragma: no cover
            pass

    threading.Thread(target=_reader, daemon=True).start()

    out = {
        "ok": False,
        "protocol_version": None,
        "server_info": None,
        "instructions": None,
        "tools": [],
        "tool_count": 0,
        "error": None,
        "stderr": "",
    }

    def _send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def _wait_id(want: int) -> dict | None:
        """等待 id == want 的响应；忽略通知与其它 id。"""
        try:
            while True:
                line = lines.get(timeout=timeout)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # 非 JSON 行（日志等）直接跳过
                if isinstance(obj, dict) and obj.get("id") == want:
                    return obj
        except queue.Empty:
            return None

    try:
        _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "probe_ace_mcp", "version": "1.0"},
                },
            }
        )
        resp = _wait_id(1)
        if resp is None:
            out["error"] = "initialize timeout (%.1fs)" % timeout
            return out
        if "error" in resp:
            out["error"] = "initialize error: %s" % json.dumps(resp["error"], ensure_ascii=False)
            return out
        result = resp.get("result") or {}
        out["protocol_version"] = result.get("protocolVersion")
        out["server_info"] = result.get("serverInfo")
        out["instructions"] = result.get("instructions")

        _send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = _wait_id(2)
        if resp is None:
            out["error"] = "tools/list timeout (%.1fs)" % timeout
            return out
        if "error" in resp:
            out["error"] = "tools/list error: %s" % json.dumps(resp["error"], ensure_ascii=False)
            return out
        tools = (resp.get("result") or {}).get("tools") or []
        names = []
        for item in tools:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
        out["tools"] = names
        out["tool_count"] = len(names)
        out["ok"] = len(names) > 0
        if not names:
            out["error"] = "server returned 0 tools (connected but no tools)"
        return out
    except (BrokenPipeError, OSError) as exc:
        out["error"] = "stdio broken: %s" % exc
        return out
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            err = proc.stderr.read()
        except Exception:
            err = ""
        out["stderr"] = (err or "")[:2000]


def is_write_tool(name: str) -> bool:
    """写类工具判定：以 ``_edit`` 结尾（ACE 的 ``<group>_read``/``<group>_edit`` 约定）。"""
    return any(name.endswith(sfx) for sfx in EDIT_SUFFIXES)


def build_report(exe: str, args: list[str], timeout: float) -> dict:
    rep = {"exe": exe, "args": args, "timeout": timeout}
    try:
        rep.update(handshake(exe, args, timeout))
    except ProbeError as exc:
        rep.update({"ok": False, "error": str(exc), "tool_count": 0, "tools": []})
    rep["status"] = "PASS" if rep.get("ok") else "FAIL"
    return rep


def _render_text(rep: dict) -> str:
    lines = [
        "MCP probe: %s %s" % (rep.get("exe"), " ".join(rep.get("args") or [])),
        "  status          : %s" % rep.get("status"),
        "  protocol_version: %s" % rep.get("protocol_version"),
        "  server_info     : %s" % json.dumps(rep.get("server_info"), ensure_ascii=False),
        "  tool_count      : %s" % rep.get("tool_count"),
    ]
    if rep.get("error"):
        lines.append("  error           : %s" % rep["error"])
    tools = rep.get("tools") or []
    if tools:
        lines.append("  tools           :")
        for name in tools:
            lines.append("    - %s" % name)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="probe_ace_mcp.py",
        description="Probe a stdio MCP server (initialize + tools/list). Read-only.",
    )
    ap.add_argument("--exe", default=os.environ.get("ACE_MCP_SERVER", DEFAULT_EXE),
                    help="path to the stdio MCP server executable")
    ap.add_argument("--args", nargs="*", default=[], help="extra args passed to the server")
    ap.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--call", metavar="TOOL", help="optionally call one tool after handshake")
    ap.add_argument("--call-args", metavar="JSON", default="{}", help='JSON object of tool arguments')
    ap.add_argument("--allow-edit", action="store_true",
                    help="allow calling write-class tools (*_edit); off by default")
    args = ap.parse_args(argv)

    if args.call:
        if is_write_tool(args.call) and not args.allow_edit:
            sys.stderr.write(
                "refusing to call write-class tool %r without --allow-edit\n" % args.call
            )
            return 2
        try:
            json.loads(args.call_args)
        except ValueError as exc:
            sys.stderr.write("--call-args is not valid JSON: %s\n" % exc)
            return 2

    rep = build_report(args.exe, list(args.args), args.timeout)

    if args.call and rep.get("ok"):
        rep["call"] = {"tool": args.call, "status": "SKIPPED",
                       "note": "tools/call is not executed by this read-only probe"}
    elif args.call:
        rep["call"] = {"tool": args.call, "status": "SKIPPED",
                       "note": "handshake failed; call not attempted"}

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(rep))
        if rep.get("call"):
            print("  call            : %s (%s)" % (rep["call"]["tool"], rep["call"]["status"]))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
