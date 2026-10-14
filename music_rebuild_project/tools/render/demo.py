"""可复现演示：合成 4 条 stem → 混音成 master → 生成 manifest。

跑法（在仓库根）：

    python -m music_rebuild_project.tools.render.demo

输出是**真实数字**（时长 / 字节 / SHA-256），不是示意图：

    [1] 四轨（44.1k/24bit/mono，各 8.000000s）
        drums    8.000000s  1058444 B
        ...
    [2] G4-9 校验：PASS
    [3] master  8.000000s  1058444 B  sha256=...
    [4] manifest：PASS / 或列出缺失项（demo 只给音频类，四类记录故意留空以展示 FAIL 路径）

只用 stdlib 合成素材（正弦 + 包络），**不读任何真实媒体**，
也不写仓库的数据目录 —— 默认落到 `tempfile.mkdtemp()`，加 `--keep` 才留档。
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import tempfile

from . import manifest as M
from . import stems as S
from . import wavio as W

SR = 44100
SECONDS = 8.0

#: 每轨的确定性素材配方：(基频, 泛音数, 振幅)。选它们只为「听起来像不同乐器」
#: 且峰值留足余量 —— 演示的重点是规格与指纹，不是音质。
RECIPES = {
    "drums": (110.0, 1, 0.24),
    "bass": (55.0, 1, 0.30),
    "harmony": (220.0, 3, 0.18),
    "vocal": (330.0, 2, 0.35),
}


def synth(freq, overtones=1, amplitude=0.3, seconds=SECONDS, sr=SR):
    """确定性正弦叠加 + 淡入淡出（避免首尾爆音）。"""
    n = int(seconds * sr)
    fade = int(0.02 * sr)
    out = []
    for i in range(n):
        t = i / sr
        v = sum(
            math.sin(2 * math.pi * freq * (k + 1) * t) / (k + 1)
            for k in range(overtones)
        )
        v *= amplitude
        if i < fade:
            v *= i / fade
        elif i > n - fade:
            v *= (n - i) / fade
        out.append(v)
    return out


def make_stems(out_dir):
    """写四条 stem（24bit/44.1k/mono）。返回 `{name: path}`。"""
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for name in S.REQUIRED_STEMS:
        freq, over, amp = RECIPES[name]
        p = os.path.join(out_dir, f"song01_{name}_v001.wav")
        W.write_wav(p, synth(freq, over, amp), framerate=SR, sampwidth=3)
        paths[name] = p
    return paths


def run(workdir=None, keep=False, verbose=True):
    """跑完整条演示链。返回结果为 dict（供测试/PR 引用）。"""
    workdir = workdir or tempfile.mkdtemp(prefix="render-demo-")
    stems_dir = os.path.join(workdir, "stems")
    masters_dir = os.path.join(workdir, "masters")

    paths = make_stems(stems_dir)

    lines = [f"[0] workdir = {workdir}", ""]
    lines.append(f"[1] 四轨（{SR}Hz/24bit/mono，各 {SECONDS:.6f}s）")
    for name in S.REQUIRED_STEMS:
        info = W.read_wav(paths[name])
        lines.append(
            f"    {name:<8} {info['duration']:.6f}s  {os.path.getsize(paths[name]):>9} B  "
            f"sha256={M.sha256_of(paths[name])[:16]}…"
        )

    report = S.check_stems(paths)
    lines.append("")
    lines.append(f"[2] G4-9 校验：{'PASS' if report.ok else 'FAIL'}")
    for c in report.checks:
        lines.append(f"    {c.name:<8} {c.duration:.6f}s  {c.bit_depth}bit  {c.framerate}Hz")
    lines.append(f"    时长极差 {max(report.durations.values()) - min(report.durations.values()):.9f}s "
                 f"（容差 {report.tolerance}s）；同播峰值 {report.mix_peak:.6f}")
    for p in report.problems:
        lines.append(f"    ✗ {p}")

    master = os.path.join(masters_dir, "song_name_mix_v001.wav")
    _, minfo = S.mix_to_master(paths, master)
    lines.append("")
    lines.append("[3] master")
    lines.append(f"    {minfo['path']}")
    lines.append(f"    {minfo['duration']:.6f}s  {os.path.getsize(master)} B  "
                 f"{minfo['framerate']}Hz/{minfo['bit_depth']}bit")
    lines.append(f"    sha256={M.sha256_of(master)}")
    lines.append(f"    叠加峰值 {minfo['peak_before_clip']:.6f} "
                 f"({W.dbfs(minfo['peak_before_clip']):.2f} dBFS)")

    # demo 只给音频/文本类交付物；四类「记录」故意留空 → 展示 FAIL 路径（G4-12 要它们齐）。
    side = os.path.join(workdir, "dist")
    os.makedirs(side, exist_ok=True)
    score = os.path.join(side, "song01_source_p001_v001.tif")
    xml = os.path.join(side, "ground_truth.musicxml")
    mid = os.path.join(side, "song01_drums_main_v001.mid")
    for p, blob in ((score, b"II*\x00demo-tif"), (xml, b"<?xml version='1.0'?><score-partwise/>"),
                    (mid, b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60")):
        with open(p, "wb") as f:
            f.write(blob)

    m = M.check_deliverables(
        {
            "source_score_image": score,
            "approved_musicxml": xml,
            "vocal_wav": paths["vocal"],
            "arrangement_midi": [mid],
            "master_wav": master,
        },
        records={},  # 记录类故意留空
        mp3_path=None,
    )
    lines.append("")
    lines.append(f"[4] manifest（G4-12）：{'PASS' if m.ok else 'FAIL'}")
    lines.append(m.to_text())
    for p in m.missing_records:
        lines.append(f"    ✗ 缺记录：{p}")
    lines.append(f"    ✗ MP3：{m.mp3['unavailable_reason']}")

    written = M.write_manifest(m, masters_dir)
    lines.append("")
    lines.append(f"[5] 已落盘：{written[0]}")
    lines.append("    复核（重算哈希）：" + ("一致" if not M.verify_manifest(m) else "漂移!"))

    text = "\n".join(lines)
    if verbose:
        print(text)
    if not keep:
        # 清理只在「脚本模式」发生：`run()` 的调用方（测试）拿到的是路径，
        # 删掉它们等于把刚产出的证据销毁 —— 那样断言只能靠字符串，不能验字节。
        import sys

        if sys.argv and sys.argv[0].endswith("demo.py"):
            shutil.rmtree(workdir, ignore_errors=True)
    return {"text": text, "report": report, "manifest": m, "master": minfo,
            "stems": paths, "workdir": workdir}


def main(argv=None):
    p = argparse.ArgumentParser(description="#59 演示：stems → master → manifest")
    p.add_argument("--keep", action="store_true", help="保留临时产物")
    p.add_argument("--workdir", default=None)
    args = p.parse_args(argv)
    result = run(workdir=args.workdir, keep=True, verbose=True)
    if not args.keep:
        shutil.rmtree(result["workdir"], ignore_errors=True)
    # demo 以「manifest 是否 PASS」作退出码会让首次探索很烦（记录类故意留空），
    # 所以始终 0；真正的门判定在 cli.py。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
