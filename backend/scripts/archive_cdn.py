#!/usr/bin/env python
"""GAP-019 归档 / 保留：把「老歌」的音频对象搬进低频存储类别，并落 archived_at / storage_class。

为什么需要它：桶里绝大部分对象是「发布过就不再听」的老歌。它们长期占据标准存储
的价格，但读取频次极低 —— 这正是低频（STANDARD_IA）/智能分层（INTELLIGENT_TIERING）
的适用场景。省的是真金白银，代价是首次读取延迟升高（客户端 CDN 缓存命中时无感）。

⚠ 不要用 GLACIER / DEEP_ARCHIVE 类做点播音频归档：它们不支持直接 GET，播放会失败
   或需长时间解冻。真正的深冷归档请用桶生命周期规则 + 回热流程（见 runbook）。

用法（在 backend/ 目录下执行）：
  python scripts/archive_cdn.py --dry-run              # 看会归档哪些（默认 90 天阈值）
  python scripts/archive_cdn.py --days 180 --limit 500
  python scripts/archive_cdn.py --class STANDARD_IA    # 显式指定目标存储类别
  python scripts/archive_cdn.py --unarchive --ids 12,34 # 回热：把对象搬回标准存储

幂等：目标行由 `archived_at IS NULL` 动态查询；成功一行写一行 archived_at，
重跑不会重复归档（S3 侧重复 copy 虽无害但会浪费带宽与请求额度）。
退出码：0=无失败  1=有失败  2=环境不满足（未配 storage / 归档被关闭）
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]  # backend/
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Song  # noqa: E402
from app.storage import s3 as storage  # noqa: E402
from app.storage.cdn import archive_after_days, archive_storage_class, default_storage_class  # noqa: E402

#: 归档操作**不得**使用的类别：不支持直接 GET，会把点播曲目变成「播不了」。
FORBIDDEN_CLASSES = ("GLACIER", "DEEP_ARCHIVE", "GLACIER_IR")


def _object_key(song) -> str:
    """从 audio_cdn_url 反推对象键 —— 归档作业只处理**已回源**的曲目。

    为什么不重新拼 key：拼错 key 会 copy 一个不存在的对象（部分实现静默成功），
    而归档的真相来源就是「它当前在桶里的位置」。URL 里取不到 key 就跳过该曲。
    """
    base = (settings.storage_public_base or "").rstrip("/")
    url = song.audio_cdn_url or ""
    if base and url.startswith(base + "/"):
        return url[len(base) + 1 :]
    marker = f"/{settings.storage_bucket}/"
    if marker in url:
        return url.split(marker, 1)[1]
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GAP-019 CDN 归档（低频存储类别迁移）")
    ap.add_argument("--days", type=int, default=None, help="归档阈值天数（默认取配置 storage_archive_after_days）")
    ap.add_argument("--limit", type=int, default=None, help="本批最多处理多少曲")
    ap.add_argument("--class", dest="target_class", type=str, default="", help="目标存储类别（默认配置 storage_class_archive）")
    ap.add_argument("--dry-run", action="store_true", help="只列将要归档的曲目，不打网络")
    ap.add_argument("--unarchive", action="store_true", help="反向操作：把对象搬回标准存储（回热）")
    ap.add_argument("--ids", type=str, default="", help="只处理这些 id（逗号分隔，用于回热）")
    args = ap.parse_args(argv)

    if not storage.is_configured():
        print("[archive] 对象存储未配置 —— 归档无对象可搬，进程早退。", file=sys.stderr)
        return 2

    days = args.days if args.days is not None else archive_after_days()
    if not args.unarchive and days <= 0:
        print(
            f"[archive] 归档已关闭（storage_archive_after_days={days}，<=0 = 关闭）——早退。\n"
            "          如需启用：设 STORAGE_ARCHIVE_AFTER_DAYS=90 等正数。",
            file=sys.stderr,
        )
        return 2

    target = (args.target_class or (default_storage_class() if args.unarchive else archive_storage_class())).strip()
    if not target:
        target = "STANDARD"
    if target.upper() in FORBIDDEN_CLASSES:
        print(
            f"[archive] 拒绝使用 {target}：该类不支持直接 GET，会导致曲目无法点播。\n"
            "          深冷归档请走桶生命周期规则 + 回热流程（见 docs/CDN_ARCHIVE_RUNBOOK.md）。",
            file=sys.stderr,
        )
        return 2

    db = SessionLocal()
    try:
        q = db.query(Song).filter(Song.audio_cdn_url.isnot(None), Song.audio_cdn_url != "")
        if args.unarchive:
            q = q.filter(Song.archived_at.isnot(None))
        else:
            q = q.filter(Song.archived_at.is_(None))
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            q = q.filter(Song.created_at < cutoff)
        if args.ids:
            ids = [int(x) for x in args.ids.split(",") if x.strip()]
            q = q.filter(Song.id.in_(ids))
        rows = q.order_by(Song.id.asc())
        if args.limit:
            rows = rows.limit(args.limit)
        songs = rows.all()
        print(
            f"[archive] 目标类别 {target}｜{'回热' if args.unarchive else f'阈值 {days} 天'}"
            f"｜本批 {len(songs)} 曲｜bucket={settings.storage_bucket}"
        )
        if not songs:
            print("[archive] 没有符合条件的曲目。")
            return 0
        if args.dry_run:
            for s in songs:
                print(f"  - song_id={s.id} key={_object_key(s) or '(无法反推 key，将跳过)'}")
            print("[archive] --dry-run：未打任何网络请求。")
            return 0

        ok, failed, skipped = [], [], []
        for s in songs:
            key = _object_key(s)
            if not key:
                skipped.append(s.id)
                print(f"  song_id={s.id} 跳过：audio_cdn_url 无法反推对象键", file=sys.stderr)
                continue
            try:
                storage.set_storage_class(key, target)
            except Exception as e:
                failed.append(s.id)
                print(f"  song_id={s.id} 失败：{e}", file=sys.stderr)
                continue
            s.storage_class = target
            s.archived_at = None if args.unarchive else datetime.datetime.now(datetime.timezone.utc)
            db.commit()
            ok.append(s.id)
            print(f"  song_id={s.id} -> {target}")

        print(f"[archive] 完成：成功 {len(ok)} / 失败 {len(failed)} / 跳过 {len(skipped)}")
        if failed:
            print(f"[archive] 失败 id（可重跑）：{','.join(str(x) for x in failed)}")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
