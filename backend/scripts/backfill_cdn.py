#!/usr/bin/env python
"""GAP-019 历史数据回填：把 `audio_cdn_url` 为 NULL/空的既有曲目搬进自有桶/CDN。

性质：**幂等、可断点续跑、带限速**的运维作业。不是一次性「跑完就算」的脚本 ——
桶回源必然遇到上游链接过期、网络抖动、限流，所以它的契约是「反复跑，每次都推进一点」。

用法（在 backend/ 目录下执行）：
  python scripts/backfill_cdn.py --dry-run              # 只列将要回填的曲目，不打网络
  python scripts/backfill_cdn.py                        # 全量回填（默认 20 曲/分钟）
  python scripts/backfill_cdn.py --limit 100 --sleep 0.5
  python scripts/backfill_cdn.py --since-id 4200        # 断点续跑：只处理 id > 4200
  python scripts/backfill_cdn.py --only-failed          # 只看上次失败清单

幂等 / 续跑机制：
  · 目标行由 `audio_cdn_url IS NULL OR = ''` **动态查询**得到 → 成功一行就少一行，
    无需外部状态文件；中断后重跑自动从剩下没做的那批继续（这就是天然的断点）。
  · 对象键 = `app.storage.cdn.song_key()`（与 sync-flow / 播放链路同一约定）→
    重复跑是**同键覆盖**，不会在桶里堆出第二份对象。
  · 单曲失败只记进度、不中断整批；末尾打印失败 id 列表，可直接用
    `--only-failed --ids ...`（或下轮全量）重试。
  · `--state-file` 可选：额外落一份「已处理 id + 上次失败 id」，便于对账（不是必须，
    因为 DB 本身就是真相来源）。

退出码：0=整批无失败  1=有失败项  2=环境不满足（未配 storage / 无待回填数据）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]  # backend/
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Song  # noqa: E402
from app.storage import mirror  # noqa: E402
from app.storage import s3 as storage  # noqa: E402


def _pending_ids(db, *, since_id: int, limit: int | None, only_ids: list[int] | None):
    """待回填曲目 id：有上游链接 + 尚无 CDN 链接（= 动态断点）。"""
    from sqlalchemy import or_

    q = (
        db.query(Song.id)
        .filter(or_(Song.audio_cdn_url.is_(None), Song.audio_cdn_url == ""))
        .filter(Song.audio_url.isnot(None), Song.audio_url != "")
        .filter(Song.id > since_id)
        .order_by(Song.id.asc())
    )
    if only_ids:
        q = q.filter(Song.id.in_(only_ids))
    if limit:
        q = q.limit(limit)
    return [row[0] for row in q.all()]


def _total_candidates(db) -> int:
    from sqlalchemy import or_

    return (
        db.query(Song.id)
        .filter(or_(Song.audio_cdn_url.is_(None), Song.audio_cdn_url == ""))
        .filter(Song.audio_url.isnot(None), Song.audio_url != "")
        .count()
    )


async def _run(args) -> int:
    if not storage.is_configured():
        print(
            "[backfill] 对象存储未配置（storage_endpoint/bucket/access_key/secret_key）——\n"
            "           回填无意义（没有桶可放），进程早退。请先在 .env 配好 storage_* 再跑。",
            file=sys.stderr,
        )
        return 2

    db = SessionLocal()
    try:
        only_ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
        todo = _pending_ids(db, since_id=args.since_id, limit=args.limit, only_ids=only_ids)
        total = _total_candidates(db)
        print(f"[backfill] 待回填候选 {total} 曲，本批处理 {len(todo)} 曲（bucket={settings.storage_bucket}）")
        if not todo:
            print("[backfill] 没有需要回填的曲目（全部已有 CDN 链接）。")
            return 0
        if args.dry_run:
            for sid in todo:
                print(f"  - would backfill song_id={sid}")
            print("[backfill] --dry-run：未打任何网络请求。")
            return 0

        interval = 0.0
        if args.rate > 0:
            interval = 60.0 / float(args.rate)
        ok, failed, skipped = [], [], []
        started = time.time()
        for idx, sid in enumerate(todo, start=1):
            song = db.get(Song, sid)
            if song is None:
                skipped.append(sid)
                continue
            url = await mirror.mirror_song_audio(song, db)
            if url:
                ok.append(sid)
                print(f"  [{idx}/{len(todo)}] song_id={sid} -> {url}")
            else:
                failed.append(sid)
                print(f"  [{idx}/{len(todo)}] song_id={sid} 失败（下轮重试）", file=sys.stderr)
            # 限速：避免把上游/CDN 打崩（也避免触发桶的 QPS 限流）。
            if interval and idx < len(todo):
                await asyncio.sleep(max(interval, args.sleep))

        elapsed = time.time() - started
        print(
            f"[backfill] 完成：成功 {len(ok)} / 失败 {len(failed)} / 跳过 {len(skipped)}，"
            f"耗时 {elapsed:.1f}s"
        )
        if failed:
            print(f"[backfill] 失败 id（可直接重跑，本作业幂等）：{','.join(str(x) for x in failed)}")
        if args.state_file:
            path = pathlib.Path(args.state_file)
            path.write_text(
                json.dumps(
                    {"ok": ok, "failed": failed, "skipped": skipped, "elapsed_sec": round(elapsed, 1)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"[backfill] 状态写入 {path}")
        return 1 if failed else 0
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GAP-019 历史曲目 CDN 回填（幂等 / 可续跑 / 限速）")
    ap.add_argument("--limit", type=int, default=None, help="本批最多处理多少曲（默认不限）")
    ap.add_argument("--since-id", type=int, default=0, help="断点：只处理 id 大于该值的曲目")
    ap.add_argument("--ids", type=str, default="", help="只处理这些 id（逗号分隔，用于重试失败清单）")
    ap.add_argument("--rate", type=float, default=20.0, help="每分钟最多处理多少曲（<=0 表示不限速）")
    ap.add_argument("--sleep", type=float, default=0.0, help="每曲额外固定间隔（秒）")
    ap.add_argument("--dry-run", action="store_true", help="只列待回填曲目，不打网络")
    ap.add_argument("--state-file", type=str, default="", help="可选：把本批结果写成 JSON 便于对账")
    args = ap.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
