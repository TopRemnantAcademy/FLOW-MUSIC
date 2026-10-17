#!/usr/bin/env python3
"""omr_runner.py — OMR 推理的**超时护栏**（#172）。

为什么需要它
    实测 ``oemer.ete.extract`` 在一张 1357×1920 的手机照片上跑了 **704s**（~12min），
    全链路没有任何超时。图更糊/更大只会更久 → 调用方（G1 判定、CI）可能被无限挂起。
    挂起比失败更糟：失败能被记录，挂起会把整条流水线一起拖死。

设计（为什么是子进程 + 定时器）
    ``ete.extract`` 的绝大多数时间在 onnxruntime 的 C++ 推理里，**不回到 Python 字节码**，
    所以同进程内的纯信号方案会被延后到推理返回才触发 —— 那不是护栏，只是事后记账。
    因此主护栏是子进程：父进程用 ``threading.Timer`` 到点就 ``SIGTERM``、宽限后
    ``SIGKILL`` 整个进程组，与父进程此刻在读什么无关（实测 ``communicate(timeout=…)``
    会晚 5s，那个 5s 就是「stderr 安静」造成的瞎等）。

    子进程内还有一层 SIGALRM：让它在自己的预算内自退，收尾更干净（关 onnx 会话、
    写日志）。它是补充，不是依赖 —— C++ 里它不一定触发。

只用绝对路径传给子进程
    仓库里既可能是 ``tools/omr/`` 也可能被装成别的包名，子进程不猜包名：
    父进程先解析出 ``oemer.ete`` 的真实文件路径与它的 ``MODULE_PATH``，
    再把**绝对路径**传给子进程。这样导入不依赖 cwd / PYTHONPATH，也不跟调用方的
    ``sys.path`` 猜谜。

契约
    ``omr_with_timeout(img_path, out_dir, timeout_s=600) -> Path``（MusicXML 路径）
    超时抛 ``OMRTimeoutError``（继承 ``TimeoutError``），并清理半成品；
    其他失败原样抛出，但同样清理半成品。
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "OMR_TIMEOUT_ENV",
    "OMRTimeoutError",
    "resolve_timeout_s",
    "omr_with_timeout",
]

#: 默认预算（秒）。issue #172 定 600s：实测 704s 的页面在 600s 内结束不了 → 超时即失败。
DEFAULT_TIMEOUT_S = 600

#: env 覆盖变量名（非法值不静默吞掉，直接抛 ValueError —— 运维写错了应该当场看见）。
OMR_TIMEOUT_ENV = "OMR_TIMEOUT_S"

#: 到点先 SIGTERM，宽限这么多久仍活着就 SIGKILL。给引擎留一点收尾时间，但绝不无限等。
KILL_GRACE_S = 1.0

#: 传入子进程的 harness（bootstrap）。子进程 = 本文件的 ``__main__`` 分支。
_BOOTSTRAP = ("import sys; from omr_runner import _main; "
              "raise SystemExit(_main(sys.argv[1:]))")


class OMRTimeoutError(TimeoutError):
    """OMR 推理超出预算 → 已被强制终止（进程不再挂起）。"""


# ---------------------------------------------------------------- 预算
def resolve_timeout_s(timeout_s: Optional[float] = None,
                      env: Optional[dict] = None) -> float:
    """显式参数 > ``OMR_TIMEOUT_S`` > 默认 600s。非正数/非数字 → ``ValueError``。

    非法 env **不静默回落到默认值**：那会把「运维把预算写成 0」变成一声不响的 600s。
    """
    raw: Any = timeout_s
    if raw is None:
        raw = (os.environ if env is None else env).get(OMR_TIMEOUT_ENV, DEFAULT_TIMEOUT_S)
    try:
        val = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{OMR_TIMEOUT_ENV} 必须是正数秒，实际={raw!r}") from e
    if not val > 0 or val != val or val == float("inf"):
        raise ValueError(f"{OMR_TIMEOUT_ENV} 必须是有限正数秒，实际={val!r}")
    return val


# ---------------------------------------------------------------- 公共入口
def omr_with_timeout(img_path: str | Path, out_dir: str | Path,
                     timeout_s: Optional[float] = None) -> Path:
    """在**子进程**里跑 ``oemer.ete.extract``，超预算则硬杀并抛 ``OMRTimeoutError``。

    返回 MusicXML 路径。半成品（``out_dir`` 下本次产生的内容）在超时/失败时一律清除，
    避免下游把「半个 .mxl」当成真结果。
    """
    img = Path(img_path).resolve()
    out = Path(out_dir).resolve()
    if not img.is_file():
        raise FileNotFoundError(f"图像不存在：{img}")
    out.mkdir(parents=True, exist_ok=True)
    budget = resolve_timeout_s(timeout_s)

    wire = _prepare(img, out)
    try:
        mxl = _run_child(wire, budget)
    except BaseException:
        _cleanup(out)
        raise
    if not mxl.is_file():
        _cleanup(out)
        raise RuntimeError(f"oemer 子进程未产出 MusicXML：{mxl}")
    return mxl


# ---------------------------------------------------------------- 父进程：解析依赖 + 起子进程
def _prepare(img: Path, out: Path) -> dict:
    """解析 ``ete.extract`` 的真实位置与模块路径（**绝对路径**交给子进程）。

    oemer 缺失时抛 ``ImportError`` —— 上游 ``g1_auto`` 会把它翻成
    ``EngineUnavailable``，判定层因此 SKIP（诚实），而不是假装 PASS。
    """
    import importlib.util

    try:
        spec = importlib.util.find_spec("oemer.ete")
    except ModuleNotFoundError as e:        # 连 oemer 包本身都没有
        raise ImportError(f"oemer 不可用：{e}") from e
    if spec is None or not spec.origin:
        raise ImportError("oemer 不可用：找不到 oemer.ete")
    import oemer

    return {
        "img": str(img),
        "out": str(out),
        "ete_file": spec.origin,
        "module_path": str(getattr(oemer, "MODULE_PATH", "")),
    }


def _child_env() -> dict:
    env = dict(os.environ)
    here = str(Path(__file__).resolve().parent)
    env["PYTHONPATH"] = os.pathsep.join(
        [here] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _run_child(wire: dict, budget: float) -> Path:
    cmd = [sys.executable, "-c", _BOOTSTRAP, json.dumps(wire)]
    proc = subprocess.Popen(
        cmd,
        env=_child_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,      # oemer 的进度 print() 很多，不接走会撑爆管道
        stderr=subprocess.PIPE,
        # 独立进程组：oemer 若再 fork 子进程，杀组才能一起收掉，不留孤儿
        start_new_session=True,
        text=True,
    )
    # 硬截止用**定时器**而不是 ``communicate(timeout=…)``：后者的计时依附于父进程
    # 阻塞读管道的动作，stderr 一时安静就会晚到（实测 30s 预算跑到 35s）。
    # 定时器到点就杀，与父进程在读什么无关。
    expired = False

    def _on_deadline() -> None:
        nonlocal expired
        expired = True
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(proc.pid, signal.SIGTERM)   # 先礼：让子进程自己收尾
        _kill_later(proc, KILL_GRACE_S)           # 后兵：宽限后整组 SIGKILL

    killer = threading.Timer(budget, _on_deadline)
    killer.daemon = True
    killer.start()
    try:
        stdout, stderr = proc.communicate()
    finally:
        killer.cancel()

    if expired:
        with contextlib.suppress(Exception):      # 定时器的补刀可能还没走完
            _kill(proc)
        raise OMRTimeoutError(
            f"oemer 推理超出预算 {budget:g}s（图片 {Path(wire['img']).name}）→ "
            f"已强制终止，进程不挂起；可调 {OMR_TIMEOUT_ENV} 放宽（默认 {DEFAULT_TIMEOUT_S}s）"
        )

    if proc.returncode != 0:
        tail = (stderr or "").strip().splitlines()[-3:]
        raise RuntimeError(f"oemer 子进程异常退出 rc={proc.returncode}：{' | '.join(tail)}")
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except Exception as e:  # 子进程被灭/输出被截断
        raise RuntimeError(f"oemer 子进程输出不可解析：{e}") from e
    return Path(payload["mxl"])


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL 整个进程组（组内派生进程一并收掉），再回收；不留僵尸。"""
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=KILL_GRACE_S + 1)


def _kill_later(proc: subprocess.Popen, delay: float) -> None:
    """宽限之后补一刀（子进程若没理 SIGTERM）。"""
    t = threading.Timer(delay, _kill, args=(proc,))
    t.daemon = True
    t.start()


def _cleanup(out: Path) -> None:
    """清掉本次写进 out 的内容（half-baked .mxl / cache / 中间图）。

    只清内容，不删目录本身 —— 调用方给的 ``out_dir`` 归调用方所有。
    """
    if not out.is_dir():
        return
    for p in sorted(out.rglob("*"), reverse=True):
        with contextlib.suppress(OSError):
            if p.is_file() or p.is_symlink():
                p.unlink()
            elif p.is_dir():
                p.rmdir()


# ---------------------------------------------------------------- 子进程：真正的 oemer 调用
def _main(argv: list[str]) -> int:
    """子进程入口：SIGALRM 兜底 + 调用 ``ete.extract``，结果以 JSON 打到 stdout。"""
    wire = json.loads(argv[0])
    try:                                   # env 已由父进程放进 OMR_TIMEOUT_S
        budget = resolve_timeout_s()
    except ValueError as e:                # 只有手敲 CLI 才会到这里
        print(str(e), file=sys.stderr)
        return 2

    if os.name == "posix":                # 兜底：C++ 推理里也会在返回时触发
        def _on_alarm(signum, frame):     # noqa: ANN001
            raise OMRTimeoutError(f"oemer 推理超出预算 {budget:g}s（SIGALRM）")

        signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, budget)

    import importlib.util

    spec = importlib.util.spec_from_file_location("oemer.ete", wire["ete_file"])
    if spec is None or spec.loader is None:      # pragma: no cover
        print("oemer.ete 无法按文件路径加载", file=sys.stderr)
        return 2
    ete = importlib.util.module_from_spec(spec)
    sys.modules["oemer.ete"] = ete
    try:
        spec.loader.exec_module(ete)
        if wire.get("module_path"):
            import oemer
            oemer.MODULE_PATH = wire["module_path"]   # checkpoints 相对位置不能漂
    except OMRTimeoutError:
        raise
    except Exception as e:
        print(f"导入 oemer.ete 失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 2

    import argparse

    ns = argparse.Namespace(img_path=wire["img"], output_path=wire["out"],
                            use_tf=False, save_cache=False, without_deskew=False)
    try:
        with contextlib.redirect_stdout(sys.stderr):   # 子进程 stdout 只留机器可读结果
            ete.clear_data()
            mxl = ete.extract(ns)
    except OMRTimeoutError as e:
        print(str(e), file=sys.stderr)
        return 3
    finally:
        if os.name == "posix":                         # pragma: no cover
            with contextlib.suppress(Exception):
                signal.setitimer(signal.ITIMER_REAL, 0)

    if not mxl:                                        # pragma: no cover
        print("ete.extract 未返回 MusicXML 路径", file=sys.stderr)
        return 4
    print(json.dumps({"mxl": str(Path(str(mxl)).resolve())}))
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
