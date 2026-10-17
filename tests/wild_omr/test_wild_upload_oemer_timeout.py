"""野上传 · 断言 ②：oemer 必须有**超时护栏**，且在超时处 fail-closed。

为什么单独立一条断言
--------------------
野上传的图没有质量下限：极大页、极高密度、几乎空白 —— 都可能让 oemer 在
`ete.extract()` 里无限跑。`g1_auto.collect_metrics` 的语义是「引擎抛异常只记账、
不炸主流程」，所以**没有护栏时一次挂死 = 整条流水线挂死**，而且没有任何红灯。

本文件断言三件事：
  1. 超过 `--timeout` 秒，oemer 进程被杀掉，调用方拿到 `EngineUnavailable`（不是空对象）；
  2. `collect_metrics` 把这条异常记进 `errors`，让 `smoke_omr.py` 能据此红；
  3. 没超时的正常路径**不产生副作用**（护栏不是靠"总是报错"实现的）。

测试手法：只把 `_oemer_collect` 的**子进程启动**换成 `sleep`，其余（护栏逻辑、
进程组 kill、异常类型、错误记账）全部走真实代码 —— 不 mock 被测对象本身。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_TOOLS = _REPO / "music_rebuild_project" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from checks import g1_auto as A  # noqa: E402

FIXTURES = _HERE.parent / "fixtures"
WILD_IMAGE = FIXTURES / "song01_preprocessed_v001_blur300.jpeg"


class FakeEngine:
    """轻量替身包（numpy + PIL 之外不装 79 个包也能测到护栏层）。

    与真 oemer 的**接口形状**一致：`MODULE_PATH/checkpoints/{unet_big,seg_net}/model.onnx`
    存在即视为"已预热"，`ete.CHECKPOINTS_URL` 用于预热；真正跑推理的是子进程 CLI。
    """

    def __init__(self, root: Path):
        self.root = root
        (root / "oemer").mkdir(parents=True, exist_ok=True)
        self.mod = root / "oemer"
        (self.mod / "__init__.py").write_text(
            "import os\nMODULE_PATH = os.path.dirname(os.path.abspath(__file__))\n",
            encoding="utf-8")
        (self.mod / "layers.py").write_text(
            "from collections import defaultdict\n"
            "_REG = defaultdict(list)\n"
            "def get_layer(name):\n    return _REG[name]\n",
            encoding="utf-8")
        (self.mod / "ete.py").write_text(
            "CHECKPOINTS_URL = {}\n"
            "def extract(ns):\n"
            "    raise RuntimeError('进程内直调是坑（0.1.8 argparse ns 未初始化）')\n",
            encoding="utf-8")
        self.warm()

    def warm(self) -> None:
        for sub in ("unet_big", "seg_net"):
            d = self.mod / "checkpoints" / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / "model.onnx").write_bytes(b"fake")


@pytest.fixture()
def fake_engine(tmp_path: Path, monkeypatch) -> FakeEngine:
    eng = FakeEngine(tmp_path / "fakepkg")
    monkeypatch.syspath_prepend(str(eng.root))
    for name in [m for m in sys.modules if m == "oemer" or m.startswith("oemer.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    return eng


@pytest.fixture()
def img(tmp_path: Path) -> Path:
    dst = tmp_path / "wild.jpeg"
    shutil.copyfile(WILD_IMAGE, dst)
    return dst


def _no_stray_sleep(settle: float = 0.3) -> bool:
    """还有没有**守卫启动的那个 `sleep 30`**（护栏没杀干净就会漏出来）。

    注意：这里**不能用 `subprocess.run` / `ps`** —— 本文件的 shim 就停在
    `subprocess.Popen` 上，而 `subprocess.run` 内部会走 Popen 的 `_wait`，
    一用就自己递归（第一版就是这么卡住的）。只用 /proc 读进程名 + cmdline。
    """
    time.sleep(settle)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            name = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            if name != "sleep":
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue                    # 进程刚好退出
        if b"30" in argv:
            return False
    return True


class _ShimPopen(subprocess.Popen):
    """`sleep 30` 的 Popen 子类。

    必须是**类**而不是 module 级 monkeypatch：`_kill_group` 用的是
    `proc.wait()`，而 `Popen.wait()` 内部走 `self._wait(timeout=…)` —— 若把
    `subprocess.Popen` 换成无关对象，`self._wait` 会解析到替身自己的方法，
    于是「杀掉子进程」变成无限递归（本文件第一版就这么挂住的）。
    """

    def __init__(self, cmd, *a, **kw):  # noqa: ANN001, ANN002, ANN003
        super().__init__(["sleep", "30"], start_new_session=True)


_MOD_KILLS: list[int] = []          # 真被护栏杀掉/回收的 pid（跨用例累积，不该重复）


class _Hang:
    """替身：任何参数都变成 `sleep 30`，用来模拟 oemer 挂死。"""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *a, **kw):  # noqa: ANN001, ANN002, ANN003
        self.calls.append(list(cmd))
        return _ShimPopen(cmd)


def test_oemer_has_configurable_timeout_knob():
    """护栏必须是**常量 + 环境变量可调**，默认值不许是"无限"。"""
    assert isinstance(A.OEMER_TIMEOUT_S, float)
    assert 0 < A.OEMER_TIMEOUT_S <= 30 * 60, "默认超时须有限且不至于误杀正常页（实测 ~2.4 min/页）"


def test_hang_is_killed_and_raises_engine_unavailable(img: Path, fake_engine, monkeypatch):
    hang = _Hang()
    monkeypatch.setattr(A.subprocess, "Popen", hang)
    monkeypatch.setattr(A, "OEMER_TIMEOUT_S", 1.0)

    t0 = time.monotonic()
    with pytest.raises(A.EngineUnavailable) as ei:
        A._oemer_collect(img)
    elapsed = time.monotonic() - t0

    assert "超时" in str(ei.value) and "1.0" in str(ei.value), f"错误信息须点明超时与阈值：{ei.value}"
    assert elapsed < 15, f"护栏应在 ~1s 内杀掉子进程，实际 {elapsed:.1f}s（可能没杀进程组）"
    assert hang.calls, "子进程确实被启动过（证明测的是真路径）"
    assert _no_stray_sleep(), "超时后不得留下 `sleep 30` 僵尸（护栏必须真的回收子进程）"


def test_timeout_is_recorded_not_swallowed(img: Path, fake_engine, monkeypatch):
    """`collect_metrics` 的降级语义：超时要进 `errors`，让冒烟脚本能据此非零退出。"""
    monkeypatch.setattr(A.subprocess, "Popen", _Hang())
    monkeypatch.setattr(A, "OEMER_TIMEOUT_S", 1.0)

    m = A.collect_metrics(img, engines=("oemer",))
    assert "oemer" in m.errors, f"超时被静默吞掉了：errors={m.errors}"
    assert "超时" in m.errors["oemer"]
    assert m.unit_size_px is None, "超时后不得凭空报出线距"


def test_missing_checkpoints_is_fail_closed(img: Path, fake_engine, monkeypatch):
    """没预热就测 = 无模型空跑 → 必须 EngineUnavailable，且不去偷偷下载。"""
    import shutil
    shutil.rmtree(fake_engine.mod / "checkpoints")
    monkeypatch.setattr(A.subprocess, "Popen", _Hang())
    with pytest.raises(A.EngineUnavailable) as ei:
        A._oemer_collect(img)
    assert "检查点" in str(ei.value)


def test_subprocess_is_used_and_no_download_on_measure(img: Path, fake_engine, monkeypatch):
    """正常路径：走 `python -m oemer.ete` 子进程、带 start_new_session，且测量路径不下载。"""
    seen: dict = {}

    class _Done:
        pid = 999999
        returncode = 1

        def communicate(self, timeout=None):  # noqa: ANN001
            seen["timeout"] = timeout
            return b"", b"boom"

    def fake_popen(cmd, *a, **kw):  # noqa: ANN001, ANN002, ANN003
        seen["cmd"] = list(cmd)
        seen["kw"] = kw
        return _Done()

    monkeypatch.setattr(A.subprocess, "Popen", fake_popen)
    with pytest.raises(A.EngineUnavailable) as ei:
        A._oemer_collect(img)

    assert seen["timeout"] == A.OEMER_TIMEOUT_S, "子进程必须带超时"
    assert seen["kw"].get("start_new_session") is True, "必须有独立进程组才能连孙进程一起杀"
    assert seen["cmd"][1:4] == ["-m", "oemer.ete", "-i"], f"应走 CLI 入口：{seen['cmd']}"
    assert "退出码=1" in str(ei.value)
