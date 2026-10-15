"""probe_ace_mcp.py 回归测试（#23/#28 MCP 连通性探针）。

策略
  **不依赖真实 ACE Studio**：被测对象是「stdio MCP 协议」，因此用
  ``sys.executable`` 跑一个写在 ``tmp_path`` 里的**假 MCP 服务器**（纯 stdlib），
  覆盖成功 / 零工具 / 无响应 / 非 JSON 噪声 四种真实会遇到的形态。
  假服务器写入临时 .py 文件（而非 ``-c`` 内联），避免 Windows 引号转义差异。

钉死的契约（对齐 #28 速查表三症状）::

    initialize+tools/list 成功且工具 ≥1      → 退出码 0（PASS）
    connected 但 tools/list 返回 0 个工具     → 退出码 1（FAIL，不是 PASS）
    握手超时 / 可执行文件不存在              → 退出码 1（FAIL）
    --call 写类工具(*_edit) 未给 --allow-edit → 退出码 2（拒绝，防止误改工程）
    --call-args 非 JSON                       → 退出码 2

  另有一条硬约束：**探针只读**——即便给了 ``--call`` 也不真正发 ``tools/call``。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]              # music_rebuild_project/tools
_REPO_ROOT = _HERE.parents[4]              # 仓库根
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from checks import probe_ace_mcp as P            # noqa: E402

SCRIPT = str(_TOOLS_DIR / "checks" / "probe_ace_mcp.py")


# --------------------------------------------------------------------------
# 假 MCP 服务器（纯 stdlib，行为由 FAKE_* 常量注入）
# --------------------------------------------------------------------------

FAKE_OK = textwrap.dedent(
    '''
    import json, sys
    sys.stderr.write("fake server boot\\n"); sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        m = req.get("method")
        if m == "initialize":
            sys.stdout.write("not-a-json-noise-line\\n")   # 应被探针跳过
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Fake MCP", "version": "9.9.9"},
                "instructions": "fake",
            }}) + "\\n")
        elif m == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {
                "tools": [{"name": "note_read"}, {"name": "note_edit"},
                          {"name": "project_read"}]}}) + "\\n")
        sys.stdout.flush()
    '''
)

FAKE_ZERO_TOOLS = textwrap.dedent(
    '''
    import json, sys
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("method") == "initialize":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "Empty"}}}) + "\\n")
        elif req.get("method") == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                "result": {"tools": []}}) + "\\n")
        sys.stdout.flush()
    '''
)

FAKE_SILENT = textwrap.dedent(
    '''
    import time
    time.sleep(30)
    '''
)

FAKE_PROTOCOL_ERROR = textwrap.dedent(
    '''
    import json, sys
    for line in sys.stdin:
        req = json.loads(line)
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"],
            "error": {"code": -32601, "message": "Method not found"}}) + "\\n")
        sys.stdout.flush()
    '''
)


def _write_fake(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _run(tmp_path: Path, body: str, extra: list[str] | None = None,
         timeout: float = 60.0) -> subprocess.CompletedProcess:
    fake = _write_fake(tmp_path, "fake_mcp.py", body)
    argv = [sys.executable, SCRIPT, "--exe", sys.executable,
            "--args", str(fake), "--timeout", "3", "--json"]
    if extra:
        argv += extra
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------
# 握手成功
# --------------------------------------------------------------------------

def test_handshake_ok_lists_tools(tmp_path):
    r = _run(tmp_path, FAKE_OK)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["status"] == "PASS"
    assert rep["tool_count"] == 3
    assert rep["tools"] == ["note_read", "note_edit", "project_read"]
    assert rep["protocol_version"] == "2024-11-05"
    assert rep["server_info"]["name"] == "Fake MCP"


def test_non_json_stdout_lines_are_skipped(tmp_path):
    """stdout 混入非 JSON 行（日志）不得让探针误判失败。"""
    r = _run(tmp_path, FAKE_OK)
    assert r.returncode == 0
    assert json.loads(r.stdout)["status"] == "PASS"


def test_stderr_is_captured(tmp_path):
    r = _run(tmp_path, FAKE_OK)
    rep = json.loads(r.stdout)
    assert "fake server boot" in rep["stderr"]


def test_text_output_contains_tools(tmp_path):
    fake = _write_fake(tmp_path, "fake_mcp.py", FAKE_OK)
    r = subprocess.run([sys.executable, SCRIPT, "--exe", sys.executable,
                        "--args", str(fake), "--timeout", "3"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "note_read" in r.stdout
    assert "tool_count      : 3" in r.stdout


# --------------------------------------------------------------------------
# 失败形态（#28 症状）
# --------------------------------------------------------------------------

def test_connected_but_zero_tools_is_fail(tmp_path):
    """「connected 无工具」必须是 FAIL，不能算 PASS。"""
    r = _run(tmp_path, FAKE_ZERO_TOOLS)
    assert r.returncode == 1
    rep = json.loads(r.stdout)
    assert rep["status"] == "FAIL"
    assert rep["tool_count"] == 0
    assert "0 tools" in rep["error"]


def test_silent_server_times_out(tmp_path):
    """「有输出无声」类：服务器起来但不回包 → 超时 FAIL。"""
    r = _run(tmp_path, FAKE_SILENT)
    assert r.returncode == 1
    rep = json.loads(r.stdout)
    assert "timeout" in (rep["error"] or "")


def test_protocol_error_is_fail(tmp_path):
    r = _run(tmp_path, FAKE_PROTOCOL_ERROR)
    assert r.returncode == 1
    assert "Method not found" in json.loads(r.stdout)["error"]


def test_missing_executable_is_fail(tmp_path):
    r = subprocess.run(
        [sys.executable, SCRIPT, "--exe", str(tmp_path / "nope-not-here.exe"),
         "--timeout", "3", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 1
    assert "not found" in json.loads(r.stdout)["error"]


# --------------------------------------------------------------------------
# 只读护栏（D-05 MCP 误操作）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("note_edit", True),
    ("project_edit", True),
    ("note_read", False),
    ("project_read", False),
    ("export_edit", True),
])
def test_is_write_tool(name, expected):
    assert P.is_write_tool(name) is expected


def test_call_write_tool_without_allow_edit_is_rejected(tmp_path):
    r = _run(tmp_path, FAKE_OK, extra=["--call", "note_edit"])
    assert r.returncode == 2
    assert "--allow-edit" in r.stderr


def test_call_read_tool_is_accepted_but_not_executed(tmp_path):
    """即便允许调用，探针也不真正发 tools/call（只读承诺）。"""
    r = _run(tmp_path, FAKE_OK, extra=["--call", "project_read"])
    assert r.returncode == 0
    rep = json.loads(r.stdout)
    assert rep["call"]["status"] == "SKIPPED"


def test_invalid_call_args_is_usage_error(tmp_path):
    r = _run(tmp_path, FAKE_OK, extra=["--call", "project_read", "--call-args", "{oops"])
    assert r.returncode == 2
    assert "not valid JSON" in r.stderr


def test_allow_edit_flag_allows_write_tool(tmp_path):
    r = _run(tmp_path, FAKE_OK, extra=["--call", "note_edit", "--allow-edit"])
    assert r.returncode == 0
    assert json.loads(r.stdout)["call"]["status"] == "SKIPPED"


# --------------------------------------------------------------------------
# 纯 stdlib 约束
# --------------------------------------------------------------------------

def test_no_third_party_imports():
    code = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "import checks.probe_ace_mcp;"
        "bad = [m for m in sys.modules if m.split('.')[0] in {'numpy','scipy','requests','mcp'}];"
        "print('BAD:' + ','.join(bad));"
        "sys.exit(1 if bad else 0)"
    ) % str(_TOOLS_DIR)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
