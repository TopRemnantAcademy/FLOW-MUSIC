"""omr_runner 超时护栏回归测试（#172）—— 「超时即红、不挂起」。

为什么放在 fast gate 里
    这条护栏防的是**挂起**，而挂起最难在 CI 里被发现：进程不退，构建就一直转。
    所以这里不依赖 oemer / onnxruntime / numpy / PIL —— 用**假引擎**（一个睡很久的子进程）
    复现「病态图 → 12min 不返回」，然后断言护栏在预算内把它掐掉。
    fast gate 只装 pytest，正好能跑；重活 stage 里也照跑不误。

覆盖
    预算解析（默认 600 / env 覆盖 / 非法值不静默回落）、超时抛 OMRTimeoutError 且
    **不挂起**、半成品被清理、正常路径原样返回、异常路径也清理、
    ``g1_auto._oemer_collect`` 把超时翻成 ``EngineUnavailable("oemer timeout")``（→ SKIP）。
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]                 # music_rebuild_project/tools
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))       # 同目录其它测试文件（test_g1_auto）也是这么做的

from checks import g1_auto as A               # noqa: E402
from omr import omr_runner as R               # noqa: E402

#: 测试预算。取 1s 而非 30s：这条护栏证明的是「会停」，不是「能跑多快」，
#: 而 fast gate 的预算是 < 60s —— 三个超时用例各睡 30s 会把门禁拖成 90s+。
BUDGET_S = 1
#: 允许「收尾」多花的时间（杀进程 + 清理半成品）。超出即视为挂起。
PATIENCE_S = 10

#: 假引擎 = 进程内**不返回**的推理。``os._exit`` 保证它连异常处理都走不到，
#: 只能被父进程杀掉 —— 这正是 oemer 卡在 onnxruntime C++ 推理里的样子。
_FAKE_ENGINE = """
import json, sys, time, pathlib, os
args = sys.argv[1:]
img, out = args[0], pathlib.Path(args[1])
out.mkdir(parents=True, exist_ok=True)
mode = pathlib.Path(args[2]).read_text().strip()
pathlib.Path(args[3]).write_text(str(os.getpid()))     # 让测试能收尸（不漏孤儿进程）
if mode == "slow":                 # 病态图：永不返回（无护栏时断言会撞 pytest 超时）
    pathlib.Path(out, "half_baked.mxl").write_text("<score-partwise/>")
    time.sleep(3600)
if mode == "boom":
    pathlib.Path(out, "half_baked.mxl").write_text("<score-partwise/>")
    sys.stderr.write("fake onnxruntime: ShapeInferenceError\\n")
    raise SystemExit(7)
mxl = pathlib.Path(out, "fake.mxl")          # 成功路径：与真子进程同一协议（末行 JSON）
mxl.write_text("<score-partwise/>")
print(json.dumps({"mxl": str(mxl.resolve())}))
"""


@pytest.fixture()
def fake_engine(tmp_path, monkeypatch):
    """把 ``omr_runner`` 的子进程命令换成假引擎（不动真实 oemer）。"""
    script = tmp_path / "fake_oemer.py"
    script.write_text(_FAKE_ENGINE, encoding="utf-8")
    (tmp_path / "mode.txt").write_text("ok", encoding="utf-8")
    img = tmp_path / "pathological.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")      # 内容无关：假引擎不读图
    out = tmp_path / "out"
    out.mkdir()
    pid_file = tmp_path / "child.pid"

    def wire(_img, _out):                      # 替身 _prepare：只给命令所需的三元组
        return {"img": str(_img), "out": str(_out), "ete_file": str(script),
                "module_path": ""}

    monkeypatch.setattr(R, "_prepare", wire)
    monkeypatch.setattr(R, "_run_child", _make_runner(script, pid_file, monkeypatch))
    yield img, out
    _reap(pid_file)


def _reap(pid_file: Path) -> None:
    """收掉假引擎（它与被测进程不在同一进程组，Popen 杀掉时不会被连带）。

    真实 oemer 子进程由 ``omr_runner`` 自己 ``killpg`` 收干净；这里只是替身的卫生问题。
    """
    import os
    import signal

    if not pid_file.is_file():
        return
    with contextlib.suppress(ValueError, OSError):
        os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)


def set_mode(tmp_path: Path, mode: str) -> None:
    """切换假引擎行为（ok / slow / boom）。"""
    (tmp_path / "mode.txt").write_text(mode, encoding="utf-8")


def _make_runner(script: Path, pid_file: Path, monkeypatch):
    """把**真实的** ``_run_child`` 指向假引擎：只换命令，超时/杀进程/清理逻辑一行不改。

    命令里最后两段（harness + JSON payload）换成假引擎脚本与它自己的参数：
    ``omr_runner`` 用「绝对路径传给子进程」的设计，正好让这里只需替换命令行。
    """
    import json

    real_popen = R.subprocess.Popen

    def fake_popen(cmd, *a, **kw):                  # noqa: ANN001
        payload = json.loads(cmd[-1])
        mode_file = str(Path(script).with_name("mode.txt"))
        return real_popen([cmd[0], str(script), payload["img"], payload["out"],
                           mode_file, str(pid_file)], *a, **kw)

    monkeypatch.setattr(R.subprocess, "Popen", fake_popen)
    return R._run_child


# ------------------------------------------------------------------ 预算
class TestBudget:
    def test_default_is_600s(self):
        assert R.DEFAULT_TIMEOUT_S == 600
        assert R.resolve_timeout_s() == 600.0

    def test_env_overrides_default(self):
        assert R.resolve_timeout_s(env={R.OMR_TIMEOUT_ENV: "42"}) == 42.0

    def test_explicit_beats_env(self):
        assert R.resolve_timeout_s(30, env={R.OMR_TIMEOUT_ENV: "42"}) == 30.0

    @pytest.mark.parametrize("bad", ["0", "-5", "abc", "", "inf", "nan"])
    def test_illegal_budget_is_not_silently_defaulted(self, bad):
        """写错预算不该悄悄变成 600s —— 那会让「设了护栏」成为幻觉。"""
        with pytest.raises(ValueError):
            R.resolve_timeout_s(env={R.OMR_TIMEOUT_ENV: bad})

    def test_documented_env_name_is_stable(self):
        assert R.OMR_TIMEOUT_ENV == "OMR_TIMEOUT_S"


# ------------------------------------------------------------------ 超时（核心）
class TestTimeoutGuard:
    def test_pathological_image_times_out_within_budget(self, fake_engine):
        """病态图：预算 3s，护栏必须在 ~3s 内以 OMRTimeoutError 退出（非挂起）。"""
        img, out = fake_engine
        set_mode(out.parent, "slow")            # 病态图：推理永不返回
        t0 = time.monotonic()
        with pytest.raises(R.OMRTimeoutError) as ei:
            R.omr_with_timeout(img, out, timeout_s=BUDGET_S)
        elapsed = time.monotonic() - t0
        assert elapsed < BUDGET_S + PATIENCE_S, \
            f"超时后仍未退干净：{elapsed:.1f}s（预算 {BUDGET_S}s）"
        msg = str(ei.value)
        assert f"{BUDGET_S:g}s" in msg and img.name in msg, msg
        assert R.OMR_TIMEOUT_ENV in msg            # 报错要给出「怎么放宽」的线索

    def test_timeout_error_is_a_timeout_error(self):
        """语义上就是超时：调用方按 TimeoutError 抓也抓得到。"""
        assert issubclass(R.OMRTimeoutError, TimeoutError)

    def test_timeout_cleans_half_baked_output(self, fake_engine):
        """半成品 .mxl 必须删掉，不能让下游把半个谱当成真结果。"""
        img, out = fake_engine
        set_mode(out.parent, "slow")
        with pytest.raises(R.OMRTimeoutError):
            R.omr_with_timeout(img, out, timeout_s=BUDGET_S)
        assert list(out.rglob("*")) == [], f"半成品没清干净：{list(out.rglob('*'))}"

    def test_env_budget_is_honoured(self, fake_engine, monkeypatch):
        """不传参数时用 env 预算（运维/CI 的调参口）。"""
        img, out = fake_engine
        set_mode(out.parent, "slow")
        monkeypatch.setenv(R.OMR_TIMEOUT_ENV, str(BUDGET_S))
        t0 = time.monotonic()
        with pytest.raises(R.OMRTimeoutError):
            R.omr_with_timeout(img, out)        # 不传 timeout_s → 走 env
        assert time.monotonic() - t0 < BUDGET_S + PATIENCE_S


# ------------------------------------------------------------------ 非超时路径
class TestHappyAndErrorPaths:
    def test_normal_run_returns_musicxml(self, fake_engine):
        img, out = fake_engine
        mxl = R.omr_with_timeout(img, out, timeout_s=30)
        assert mxl.is_file() and mxl.name == "fake.mxl"

    def test_engine_crash_cleans_up_and_raises(self, fake_engine, tmp_path):
        img, out = fake_engine
        set_mode(tmp_path, "boom")
        with pytest.raises(RuntimeError) as ei:
            R.omr_with_timeout(img, out, timeout_s=30)
        assert not isinstance(ei.value, R.OMRTimeoutError)
        assert "rc=7" in str(ei.value)
        assert list(out.rglob("*")) == []

    def test_missing_image_is_reported_not_hung(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            R.omr_with_timeout(tmp_path / "nope.png", tmp_path / "out", timeout_s=5)


# ------------------------------------------------------------------ g1_auto 接入
def _patch_oemer_as_timeout(monkeypatch) -> None:
    """把 oemer 伪装成「装着、但一跑就超时」，用来验证 g1_auto 的降级链路。

    ``_oemer_collect`` 依赖三处外部：``import oemer.ete`` / ``_oemer_checkpoint_path()`` /
    ``_omr_runner()``。三者都替身掉，真实 oemer 无需存在（fast gate 里本来也没有），
    也不会去下模型（无网环境）。
    """
    import types

    stub = types.SimpleNamespace(
        ete=types.SimpleNamespace(CHECKPOINTS_URL={}),   # 即便走到下载分支也立刻返回
        layers=types.SimpleNamespace(get_layer=lambda _n: []),
    )
    monkeypatch.setitem(sys.modules, "numpy", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "oemer", stub)
    monkeypatch.setitem(sys.modules, "oemer.ete", stub.ete)

    runner = R                                    # 模块对象在上面 import 时已拿到
    monkeypatch.setattr(runner, "omr_with_timeout", _raise_timeout)
    monkeypatch.setattr(A, "_omr_runner", lambda: runner)
    monkeypatch.setattr(A, "_oemer_checkpoints_dir", lambda: "")   # 跳过检查点下载分支


def _raise_timeout(*_a, **_kw):
    raise R.OMRTimeoutError("oemer 推理超出预算 30s")


class TestG1AutoWiring:
    def test_oemer_collect_turns_timeout_into_engine_unavailable(self, fake_engine,
                                                                 monkeypatch):
        """超时必须变成 ``EngineUnavailable("oemer timeout")``，而不是异常上抛/挂起。"""
        img, _out = fake_engine
        _patch_oemer_as_timeout(monkeypatch)
        with pytest.raises(A.EngineUnavailable) as ei:
            A._oemer_collect(img)
        assert "oemer timeout" in str(ei.value)

    def test_collect_metrics_skips_instead_of_hanging(self, fake_engine, monkeypatch):
        """编排层把 EngineUnavailable 记为 errors，判定项全部 SKIP（诚实，不冒充 PASS）。"""
        img, _out = fake_engine
        _patch_oemer_as_timeout(monkeypatch)

        m = A.collect_metrics(img, engines=("oemer",))    # 只跑 oemer，Tesseract/CV 不掺和
        assert "oemer" in m.errors and "timeout" in m.errors["oemer"]
        for key in ("G1-1L", "G1-6", "G1-7"):
            status, detail = A.VERDICTS[key](m)
            assert status == "SKIP", f"{key} 应为 SKIP，实际 {status}"
            assert detail, f"{key} 的 SKIP 必须带原因"


# ------------------------------------------------------------------ 结构约束
def test_runner_has_no_third_party_imports():
    """护栏本身零依赖（fast gate 只有 pytest）：重依赖只能在子进程里现拿。"""
    src = Path(R.__file__).read_text(encoding="utf-8")
    for heavy in ("import numpy", "import onnxruntime", "import PIL"):
        assert heavy not in src, f"omr_runner 顶层不应出现 {heavy}"
    # oemer 只在子进程里按文件路径加载（父进程只做 find_spec 探活）
    assert "find_spec" in src and "importlib.util" in src


def test_child_protocol_is_json_on_last_line():
    """父子进程协议：子进程 stdout 最后一行是 JSON（其余输出走 stderr）。"""
    src = Path(R.__file__).read_text(encoding="utf-8")
    assert json.dumps({"mxl": "x"}).split(":")[0] in src
    assert "redirect_stdout(sys.stderr)" in src
