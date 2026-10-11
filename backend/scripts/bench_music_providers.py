#!/usr/bin/env python
"""Mureka vs MiniMax Music 3 —— 同题同词 A/B 质量评估（免费档优先）。

性质：**客观指标采集器，不是主观质量打分器**。
它能确定地给你：耗时、时长、采样率/声道/码率、文件体积、成功与否、trace_id；
它**不能**确定地告诉你「哪首更好听」。报告里会明确列出需要人耳比对的文件清单。

协议基线（2026-09-26 核对官方文档，见 app/orchestrator/minimax_music.py 文件头）：
  POST https://api.minimax.io/v1/music_generation
  Authorization: Bearer <MINIMAX_API_KEY>，同步返回，output_format=hex。

⚠️ 使用前必读（官方 2026-08-20 公告）：
  免费音乐生成接口 music-3.0-free 等**已停止服务**；付费音乐生成接口不再面向新用户。
  所以本脚本在「缺 key / 接口拒绝」时会**以非零码退出并打印公告**，
  绝不会把「没跑成」写成「跑过了」或跳过。

用法（在 backend/ 目录下执行）：
  cd ai-music-platform/backend
  ../venv/Scripts/python.exe scripts/bench_music_providers.py --preflight   # 只验配置，不打网络
  ../venv/Scripts/python.exe scripts/bench_music_providers.py --only minimax --model music-3.0
  ../venv/Scripts/python.exe scripts/bench_music_providers.py --both --cases 2

退出码：0=全部成功  1=有失败项  2=配置缺失/环境变量不足
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import time
import traceback

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.orchestrator.base import GenerateRequest  # noqa: E402
from app.orchestrator.minimax_music import (  # noqa: E402
    DISCONTINUATION_NOTICE,
    MiniMaxMusicClient,
    describe_specs,
    get_model_spec,
)
from app.orchestrator.mureka import MurekaProvider  # noqa: E402

# 评测用例由 build_cases() 构造（见下）：直接对齐本产品真实场景（敬拜/福音：韩·中·英 + 纯器乐）。


def load_env(paths: list[pathlib.Path]) -> None:
    """轻量 .env 读取（k=v，# 注释，不覆盖已有环境变量）——与 probe_mureka.py 同款。"""
    for p in paths:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def ensure_outdir(path: str) -> pathlib.Path:
    """同步建目录（避免在 async main 里做阻塞 IO，触发 ASYNC240）。"""
    d = pathlib.Path(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_cases() -> list[dict]:
    """返回评测用例（歌词自撰，含结构标签，符合官方 [Intro]/[Verse]/[Chorus] 约定）。"""
    return [
        {
            "name": "ko-worship",
            "prompt": (
                "Korean Christian worship ballad, female vocal, acoustic piano and strings, "
                "slow, reverent, radio-ready"
            ),
            "lyrics": (
                "[Intro]\n"
                "주님의 자비가 여기 있네\n"
                "[Verse]\n"
                "조용한 밤, 나의 마음 고요해\n"
                "그 손길이 나를 다시 일으켜 세우네\n"
                "[Chorus]\n"
                "감사합니다 주님, 사랑합니다 주님\n"
                "오늘도 나의 노약함을 새로이 하시네\n"
                "[Outro]\n"
                "평안함이 나의 노래 되게 하소서"
            ),
        },
        {
            "name": "zh-gospel",
            "prompt": "中文福音流行敬拜, 女声独唱, 钢琴与弦乐铺底, 中速, 温暖",
            "lyrics": (
                "[Intro]\n"
                "【预备】\n"
                "[Verse]\n"
                "讲台上的灯光 照亮了稀疏的人\n"
                "我把手举起 声音却很轻\n"
                "[Chorus]\n"
                "主啊 谢谢你 从不曾走远\n"
                "每一次呼求 你都在我旁边\n"
                "[Outro]\n"
                "让这首歌 留在今天的夜里"
            ),
        },
        {
            "name": "en-worship",
            "prompt": (
                "English contemporary worship, male vocal, guitar-driven, "
                "moderate tempo, clean modern mix"
            ),
            "lyrics": (
                "[Intro]\n"
                "[Verse]\n"
                "Morning breaks, the quiet finds me\n"
                "Every step you hold is steady\n"
                "[Chorus]\n"
                "Here I am, Lord, bring your healing\n"
                "Every doubt you turn to freedom\n"
                "[Outro]\n"
                "Stay with us through the evening"
            ),
        },
        {
            "name": "instrumental",
            "prompt": " instrumental worship pad, cinematic strings, slow build, no vocals",
            "lyrics": None,  # is_instrumental=True，官方此时 lyrics 非必填
        },
    ]


def _is_instrumental(case: dict) -> bool:
    return case.get("lyrics") is None


async def run_mureka(case: dict, outdir: pathlib.Path, ext: str) -> dict:
    started = time.monotonic()
    rec: dict = {"provider": "mureka", "status": "unknown", "error": None, "file": None}
    try:
        req = GenerateRequest(
            prompt=case["prompt"],
            style=case["prompt"],
            lyric=case["lyrics"] or "",
            task="generate",
        )
        provider = MurekaProvider()
        res = await provider.create(req)
        if res.status != "processing":
            rec["status"] = "error"
            rec["error"] = f"create 未进入处理态：{res.status}"
            return rec
        deadline = time.monotonic() + max(settings.song_poll_timeout, 1)
        while time.monotonic() < deadline:
            await asyncio.sleep(max(settings.song_poll_interval, 1))
            res = await provider.get(res.external_id)
            if res.status in ("completed", "failed"):
                break
        if res.status != "completed":
            rec["status"] = res.status if res.status != "unknown" else "timeout"
            rec["error"] = res.moderation_note or f"轮询结束仍为 {res.status}"
            return rec
        rec["status"] = "completed"
        rec["duration_s"] = res.duration
        rec["size_kb"] = None
        if res.audio_url:
            path = outdir / f"{case['name']}-mureka.{ext}"
            import httpx

            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.get(res.audio_url)
                r.raise_for_status()
                path.write_bytes(r.content)
            rec["file"] = str(path)
            rec["size_kb"] = round(len(r.content) / 1024, 1)
        else:
            rec["error"] = "completed 但无 audio_url"
            rec["status"] = "error"
    except Exception as e:  # 不吞异常：任何失败都要出现在报告里
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        rec["elapsed_s"] = round(time.monotonic() - started, 1)
    return rec


async def run_minimax(case: dict, outdir: pathlib.Path, model: str, ext: str) -> dict:
    started = time.monotonic()
    rec: dict = {"provider": "minimax", "status": "unknown", "error": None, "file": None}
    try:
        client = MiniMaxMusicClient(model=model)
        result = await client.generate(
            prompt=case["prompt"],
            lyrics=case["lyrics"] or "",
            is_instrumental=_is_instrumental(case),
        )
        rec["status"] = "completed"
        rec["duration_s"] = (result.duration_ms / 1000) if result.duration_ms else None
        rec["sample_rate"] = result.sample_rate
        rec["channels"] = result.channels
        rec["bitrate"] = result.bitrate
        rec["size_kb"] = round(len(result.audio_bytes) / 1024, 1) if result.audio_bytes else None
        rec["trace_id"] = result.trace_id
        if result.audio_bytes is not None:
            path = outdir / f"{case['name']}-minimax.{ext}"
            path.write_bytes(result.audio_bytes)
            rec["file"] = str(path)
        else:
            rec["error"] = "有 url 无 hex，未落盘"
            rec["status"] = "error"
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        rec["elapsed_s"] = round(time.monotonic() - started, 1)
    return rec


def write_report(rows: list[dict], meta: dict, model: str, outdir: pathlib.Path) -> pathlib.Path:
    lines = [
        "# 音乐生成供应商 A/B 评估（客观指标）",
        "",
        f"- 生成时间：{meta['ts']}",
        f"- MiniMax 模型：`{model}`",
        f"- Mureka 模型：`{meta['mureka_model']}`",
        f"- 输出目录：`{outdir}`",
        "",
        "> ⚠️ **本表只记录客观指标。**",
        "> 音质/人声自然度/编曲好不好听**无法由这些字段判定**，必须由人耳逐一试听下面列出的文件。",
        "> 试听顺序建议：先看同一 case 的两个文件交替听（A/B/X），再回到本表看差异。",
        "",
        "| case | 供应商 | 结果 | 耗时(s) | 时长(s) | 采样率 | 声道 | 码率 | 体积(KB) | 文件 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            "| {case} | {p} | {s} | {el} | {dur} | {sr} | {ch} | {br} | {sz} | {f} |".format(
                case=r["case"],
                p=r["provider"],
                s=r["status"],
                el=r.get("elapsed_s", "-"),
                dur=r.get("duration_s", "-") if r.get("duration_s") else "-",
                sr=r.get("sample_rate", "-"),
                ch=r.get("channels", "-"),
                br=r.get("bitrate", "-"),
                sz=r.get("size_kb", "-") if r.get("size_kb") else "-",
                f=(r.get("file") or "-").replace("\\", "/"),
            )
        )
    failed = [r for r in rows if r["status"] != "completed"]
    if failed:
        lines += ["", "## 失败项（必须处理，未产生有效对照）", ""]
        for r in failed:
            lines.append(f"- **{r['case']} / {r['provider']}**：{r.get('error')}")
    lines += [
        "",
        "## 试听清单",
        "",
    ]
    for r in rows:
        if r["status"] == "completed" and r.get("file"):
            lines.append(f"- `{r['case']}` → `{r['file'].replace(chr(92), '/')}`")
    report = outdir / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


async def main_async() -> int:
    ap = argparse.ArgumentParser(description="Mureka vs MiniMax 同题 A/B 评估")
    ap.add_argument("--only", choices=["mureka", "minimax", "both"], default="both")
    ap.add_argument(
        "--model", default=None, help="MiniMax 模型 id，默认取 settings.minimax_music_model"
    )
    ap.add_argument("--cases", type=int, default=0, help="只跑前 N 个用例（0=全部）")
    ap.add_argument("--outdir", default=str(BACKEND_DIR.parent / "bench-out"))
    ap.add_argument("--preflight", action="store_true", help="只校验配置与请求体，不发网络请求")
    ap.add_argument(
        "--spec",
        action="store_true",
        help="只打印官方 model 规格表（UI 能力开关同源），不发任何请求",
    )
    ap.add_argument(
        "--mureka-model", default=None, help="覆盖 Mureka 模型（默认 settings.mureka_model）"
    )
    args = ap.parse_args()

    if args.spec:
        for row in describe_specs():
            print(
                f"{row['model']:<18} RPM={row['rpm']:<4}"
                f" instrumental={'Y' if row['is_instrumental'] else 'N'}"
                f" optimizer={'Y' if row['lyrics_optimizer'] else 'N'}"
                f" cover={'Y' if row['cover'] else 'N'}"
                f" prompt={row['prompt_range']} lyrics={row['lyrics_range']}"
                f"{'  [已停服]' if row['discontinued'] else ''}"
            )
        return 0

    model = args.model or settings.minimax_music_model
    if args.mureka_model:
        os.environ["MUREKA_MODEL"] = args.mureka_model

    outdir = ensure_outdir(args.outdir)
    cases = build_cases()
    if args.cases > 0:
        cases = cases[: args.cases]

    print(f"[preflight] MiniMax model = {model}")
    print(f"[preflight] MiniMax base  = {settings.minimax_api_base}")
    print(f"[preflight] MiniMax key   = {'已配置' if settings.minimax_api_key else '未配置'}")
    print(f"[preflight] Mureka  base  = {settings.mureka_api_base}")
    print(f"[preflight] Mureka  key   = {'已配置' if settings.mureka_api_key else '未配置'}")

    # —— 请求体预检：不发网络，先把「字段是否合官方约束」验完 ——
    try:
        client = MiniMaxMusicClient(model=model)
        spec = get_model_spec(model)
    except Exception as e:
        print(f"[preflight] 客户端初始化失败：{e}", file=sys.stderr)
        return 2
    if spec.discontinued:
        print(f"[preflight] ⚠ 该 model 已被官方公告停服：{DISCONTINUATION_NOTICE}", file=sys.stderr)
    print(
        f"[preflight] 官方规格      = RPM {spec.rpm}"
        f"（instrumental={'Y' if spec.supports_instrumental else 'N'}"
        f" / lyrics_optimizer={'Y' if spec.supports_lyrics_optimizer else 'N'}"
        f" / cover={'Y' if spec.cover else 'N'}）"
    )
    if not settings.minimax_api_key:
        print(
            "[preflight] ⚠ MINIMAX_API_KEY 未配置。"
            "请至 MiniMax 开放平台「账户管理>接口密钥」获取（官方入口）；\n"
            "            未配置即无法完成任何一次有效生成，本脚本不会静默跳过。",
            file=sys.stderr,
        )
    for case in cases:
        try:
            client.build_payload(
                prompt=case["prompt"],
                lyrics=case["lyrics"] or "",
                is_instrumental=_is_instrumental(case),
            )
            print(f"[preflight] payload OK  : {case['name']}")
        except Exception as e:
            print(f"[preflight] payload FAIL: {case['name']} -> {e}", file=sys.stderr)
            return 2

    if args.preflight:
        print("[preflight] 通过（未发任何网络请求）")
        return 0

    wants_mureka = args.only in ("mureka", "both")
    wants_minimax = args.only in ("minimax", "both")

    rows: list[dict] = []
    for case in cases:
        if wants_mureka:
            rows.append({**await run_mureka(case, outdir, "mp3"), "case": case["name"]})
        if wants_minimax:
            rows.append({**await run_minimax(case, outdir, model, "mp3"), "case": case["name"]})

    meta = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mureka_model": os.environ.get("MUREKA_MODEL", settings.mureka_model),
    }
    report = write_report(rows, meta, model, outdir)
    print(f"\n[report] {report}")
    for r in rows:
        print(
            f"  {r['case']:<12} {r['provider']:<8} {r['status']:<12} "
            f"{r.get('elapsed_s', '-')}s" + (f"  ERROR: {r.get('error')}" if r.get("error") else "")
        )
    failed = [r for r in rows if r["status"] != "completed"]
    if failed:
        if any(r["provider"] == "minimax" for r in failed):
            print(f"\n[notice] {DISCONTINUATION_NOTICE}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    load_env([BACKEND_DIR / ".env", REPO_ROOT / ".env"])
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[abort] 用户中断", file=sys.stderr)
        return 1
    except Exception:  # 兜底：把 traceback 打全，不做「表面成功」
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
