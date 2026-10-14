"""`python -m ...render.cli` —— 把三段（stem 校验 / 混音 / manifest）串成一条命令。

用法：

    # ① 校验 stems/ 四轨（G4-9）
    python -m music_rebuild_project.tools.render.cli check-stems --stems-dir 07_mix/song01/stems

    # ② 合成 master（G4-10 规格）并落盘
    python -m music_rebuild_project.tools.render.cli mix \
        --stems-dir 07_mix/song01/stems \
        --out 07_mix/song01/masters/song_name_mix_v001.wav

    # ③ 生成 manifest（G4-12 齐全性）
    python -m music_rebuild_project.tools.render.cli manifest \
        --master 07_mix/song01/masters/song_name_mix_v001.wav \
        --vocal 05_vocals/song01/vocal_wav/approved_vocal_pilot.wav \
        --musicxml 04_approved/song01/ground_truth.musicxml \
        --midi 06_arrangement/song01/drums/song01_drums_main_v001.mid \
        --score 02_source/song01/originals/song01_source_p001_v001.tif \
        --approval-log 08_qc/approval_log.md \
        --out-dir 07_mix/song01/masters

退出码：0 = PASS，1 = FAIL（缺件/规格不符/时长不一致），**不静默退出**。
与 `--json` 搭配时把结构化结果打进 stdout，方便接进 CI。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import manifest as M
from . import mp3 as MP3
from . import stems as S
from . import wavio as W


def _emit(payload):
    """统一走 JSON 到 stdout：人看得懂，CI 也能 `jq`。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_check_stems(args):
    try:
        report = S.check_stems(args.stems_dir, tolerance=args.tolerance)
    except W.WavError as exc:
        _emit({"gate": "G4-9", "status": "FAIL", "problems": [str(exc)]})
        return 1
    _emit(report.to_dict())
    return 0 if report.ok else 1


def cmd_mix(args):
    try:
        report, info = S.mix_to_master(
            args.stems_dir, args.out, tolerance=args.tolerance
        )
    except W.WavError as exc:
        _emit({"gate": "G4-9", "status": "FAIL", "out": args.out, "problems": [str(exc)]})
        return 1
    _emit(
        {
            "gate": "G4-9",
            "status": "PASS",
            "out": info["path"],
            "duration": round(info["duration"], 6),
            "framerate": info["framerate"],
            "bit_depth": info["bit_depth"],
            "bytes": os.path.getsize(info["path"]),
            "sha256": M.sha256_of(info["path"]),
            "peak_before_clip": round(info.get("peak_before_clip", 0.0), 6),
            "dbfs": round(W.dbfs(info.get("peak_before_clip", 0.0)), 2),
            "stems": report.to_dict(),
        }
    )
    return 0


def cmd_manifest(args):
    files = {
        "source_score_image": args.score,
        "approved_musicxml": args.musicxml,
        "vocal_wav": args.vocal,
        "arrangement_midi": args.midi,
        "master_wav": args.master,
    }
    # 记录项：给了路径就校验存在性；给了 `--setting` 之类的自由文本值则视为已提供。
    records = {
        "license": args.license,
        "version": args.version,
        "settings": args.settings,
        "approval_log": args.approval_log,
    }
    m = M.check_deliverables(files, records=records, mp3_path=args.mp3)
    if args.out_dir:
        written = M.write_manifest(m, args.out_dir, stem=args.stem)
        m.to_dict()["written"] = written
    _emit(m.to_dict())
    return 0 if m.ok else 1


def build_parser():
    p = argparse.ArgumentParser(prog="render", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("check-stems", help="按 G4-9 校验四轨")
    a.add_argument("--stems-dir", required=True)
    a.add_argument("--tolerance", type=float, default=S.TOLERANCE_SECONDS)
    a.set_defaults(func=cmd_check_stems)

    b = sub.add_parser("mix", help="四轨 → master（G4-10 规格）")
    b.add_argument("--stems-dir", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--tolerance", type=float, default=S.TOLERANCE_SECONDS)
    b.set_defaults(func=cmd_mix)

    c = sub.add_parser("manifest", help="生成 G4-12 分发清单")
    c.add_argument("--master"); c.add_argument("--vocal")
    c.add_argument("--musicxml"); c.add_argument("--midi", action="append")
    c.add_argument("--score"); c.add_argument("--mp3")
    c.add_argument("--license"); c.add_argument("--version")
    c.add_argument("--settings"); c.add_argument("--approval-log")
    c.add_argument("--out-dir"); c.add_argument("--stem", default="song_name_mix_v001")
    c.set_defaults(func=cmd_manifest)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    if __package__ in (None, ""):  # 支持 `python path/to/cli.py`
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))))
        __package__ = "music_rebuild_project.tools.render"
        from music_rebuild_project.tools.render import cli  # noqa: F401
        raise SystemExit(cli.main())
    raise SystemExit(main())
