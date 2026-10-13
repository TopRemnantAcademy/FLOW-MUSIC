"""沙箱安全线：零网络 / 零外部 API / 零密钥 / 零桌面软件。

「不联网」这种事不能靠"我记得没写 httpx"来保证。这里用两条可执行的检查把它钉住：
  1. 整个包**不 import** 任何出网客户端与桌面软件绑定；
  2. 真跑一遍流水线，任何出网尝试都会被"毒化"的 socket 直接打回。

第 2 条尤其重要：issue #122 要求「无网络也能全绿」，那就必须在链路中间放一个
会炸的出口，而不是把流水线跑通就算数。
"""

import ast
import pathlib
import socket

import pytest

from . import fixtures as fx
from .orchestrator import run_score_to_music

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent

#: 出网客户端 / 桌面软件绑定：一旦被 import 就说明这条测试不再"沙箱可跑"。
FORBIDDEN_IMPORTS = {
    "httpx",
    "requests",
    "aiohttp",
    "urllib3",
    "boto3",
    "botocore",
    "websockets",
    "grpc",
    # 桌面软件 / 重型音频栈（sandbox 里没有，也不该成为测试的隐含前提）
    "music21",
    "librosa",
    "numpy",
    "scipy",
    "soundfile",
    "pydub",
    "pedalboard",
    "noisereduce",
    "mido",
    "pretty_midi",
}

#: 允许出现的"看起来像网络"的名字（哈希/sha 之类的误报源）。
ALLOWED_NAMES = {"sha256", "sha1", "socket"}  # socket 只用于 test 里的毒化，不在包内使用


def _imported_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_package_imports_only_stdlib_and_app():
    """静态检查：本包只依赖 stdlib（+ 平台侧 app.* 与 pytest）。"""
    offenders: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        bad = _imported_names(path) & FORBIDDEN_IMPORTS
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"发现禁用依赖：{offenders}"


def test_no_hardcoded_secrets():
    """零密钥：不留 API key / token 形态的字面量（仓库是 Public）。"""
    import re

    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{16,}"),
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"']{12,}[\"']"),
    ]
    hits = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pat in patterns:
            for m in pat.finditer(text):
                hits.append(f"{path.name}: {m.group(0)[:40]}")
    assert not hits, f"疑似硬编码凭据：{hits}"


def test_pipeline_run_makes_zero_network_calls(tmp_path, monkeypatch):
    """运行时检查：把 socket 连接毒化，流水线照样全绿。"""

    def boom(*args, **kwargs):  # pragma: no cover - 只会在"有人偷偷联网"时触发
        raise AssertionError(f"流水线尝试建立网络连接：{args} {kwargs}")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a, **k: 1, raising=False)

    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
    )
    assert result.ok is True, f"断网后流水线没跑完：{result.halt_reason}"
    for gate, report in result.gate_reports.items():
        assert report.status in ("PASS", "PASS_WITH_NOTES"), f"{gate}={report.status}"


def test_fixtures_do_not_touch_repo_binaries():
    """fixture 全部由代码生成（不提交大二进制）—— 免得仓库被几张 A4@300dpi 撑肥。"""
    image = fx.synth_score_png()
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(image) < 200 * 1024, f"合成 PNG 太大了：{len(image)} bytes"
    # 同一参数两次生成必须逐字节一致（确定性 fixture）
    assert fx.synth_score_png() == image
    assert fx.build_canon_musicxml() == fx.build_canon_musicxml()


def test_workspace_dir_does_not_touch_repo(tmp_path):
    """所有产物写在传入的 work_dir（测试用 tmp_path），不污染仓库。"""
    repo_backend = pathlib.Path(__file__).resolve().parents[2]  # backend/
    before = {p.name for p in repo_backend.iterdir()}
    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
    )
    assert result.ok
    after = {p.name for p in repo_backend.iterdir()}
    assert before == after, f"仓库目录被写入了新文件：{after - before}"
    for path in result.artifacts.values():
        assert str(path).startswith(str(tmp_path)), f"{path} 不在 work_dir 内"
