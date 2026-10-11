"""P1 跨域按次扣费轨 — 钱包预约对账脚本。

对账不变式（每条都必须成立，违反即 exit 1）：
  I1 无滞留 hold：held 超过 --max-hold-hours（默认 24h）视为泄漏
     （进程崩溃 / 后台任务未结算 / 结算异常）。
  I2 无结算缺口：job_id=song:{id} 对应的 Song 已 completed 或 failed，
     但预约仍 held → 本应 capture/release 却没做。
  I3 无重复 hold：同一 job_id 最多 1 条 held（hold_wallet_track 幂等保证）。
  I4 金额为正：amount_krw > 0（free 档 price_cny=0 也不能产生 0 额扣款）。
  I5 套利下限：krw_to_cny(amount_krw) 必须 ≥ 单首价下限 flow_wallet_per_song_cny
     （防止折算后比音乐站同档标价更便宜 → 套利）。

用法：
    python scripts/wallet_reconcile.py                 # 对账，异常即 exit 1
    python scripts/wallet_reconcile.py --json          # 机器可读输出
    python scripts/wallet_reconcile.py --max-hold-hours 1

建议 cron 每日一次；退出码 1 触发告警。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# 允许以 `python scripts/wallet_reconcile.py` 直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Song  # noqa: E402
from app.wallet_track import (  # noqa: E402
    WALLET_WORKSPACE,
    WalletReservation,
    krw_to_cny,
)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def reconcile(db, max_hold_hours: float = 24.0) -> dict:
    """执行全部对账检查，返回结构化结果（issues 为空即通过）。"""
    issues: list[dict] = []

    # ---- 汇总 ----
    rows = (
        db.query(
            WalletReservation.status,
            func.count(),
            func.coalesce(func.sum(WalletReservation.amount_krw), 0),
        )
        .filter(WalletReservation.workspace_id == WALLET_WORKSPACE)
        .group_by(WalletReservation.status)
        .all()
    )
    by_status = {s: {"count": c, "amount_krw": int(a or 0)} for s, c, a in rows}

    total = (
        db.query(func.count(), func.coalesce(func.sum(WalletReservation.amount_krw), 0))
        .filter(WalletReservation.workspace_id == WALLET_WORKSPACE)
        .first()
    )
    summary = {
        "workspace": WALLET_WORKSPACE,
        "total_count": int(total[0] or 0),
        "total_amount_krw": int(total[1] or 0),
        "by_status": by_status,
    }

    held = db.query(WalletReservation).filter_by(workspace_id=WALLET_WORKSPACE, status="held").all()

    # ---- I1 滞留 hold ----
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hold_hours)
    for r in held:
        created = r.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created is not None and created < cutoff:
            issues.append(
                {
                    "check": "I1_STALE_HOLD",
                    "reservation_id": r.reservation_id,
                    "job_id": r.job_id,
                    "age_hours": round(
                        (datetime.now(timezone.utc) - created).total_seconds() / 3600, 2
                    ),
                    "amount_krw": r.amount_krw,
                }
            )

    # ---- I2 结算缺口（Song 已终态但预约仍 held）----
    for r in held:
        if not (r.job_id or "").startswith("song:"):
            continue
        raw = r.job_id.split(":", 1)[1]
        if not raw.isdigit():
            continue
        song = db.get(Song, int(raw))
        if song is not None and song.status in ("completed", "failed"):
            issues.append(
                {
                    "check": "I2_SETTLEMENT_GAP",
                    "reservation_id": r.reservation_id,
                    "job_id": r.job_id,
                    "song_status": song.status,
                    "expected": "captured" if song.status == "completed" else "released",
                    "amount_krw": r.amount_krw,
                }
            )

    # ---- I3 重复 hold（同 job_id 多于 1 条 held）----
    dupes = (
        db.query(WalletReservation.job_id, func.count())
        .filter(
            WalletReservation.workspace_id == WALLET_WORKSPACE, WalletReservation.status == "held"
        )
        .group_by(WalletReservation.job_id)
        .having(func.count() > 1)
        .all()
    )
    for job_id, cnt in dupes:
        issues.append({"check": "I3_DUPLICATE_HOLD", "job_id": job_id, "held_count": int(cnt)})

    # ---- I4 / I5 金额为正 + 套利下限 ----
    floor = float(getattr(settings, "flow_wallet_per_song_cny", 0) or 0)
    for r in db.query(WalletReservation).filter_by(workspace_id=WALLET_WORKSPACE).all():
        if r.amount_krw is None or r.amount_krw <= 0:
            issues.append(
                {
                    "check": "I4_NONPOSITIVE_AMOUNT",
                    "reservation_id": r.reservation_id,
                    "amount_krw": r.amount_krw,
                }
            )
            continue
        if floor > 0 and krw_to_cny(r.amount_krw) < floor - 1e-9:
            issues.append(
                {
                    "check": "I5_ARBITRAGE_FLOOR",
                    "reservation_id": r.reservation_id,
                    "amount_krw": r.amount_krw,
                    "worth_cny": krw_to_cny(r.amount_krw),
                    "floor_cny": floor,
                }
            )

    return {
        "ok": not issues,
        "summary": summary,
        "issues": issues,
        "checked_at": _iso(datetime.now(timezone.utc)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="FLOW 钱包跨域按次扣费轨 — 对账")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument(
        "--max-hold-hours", type=float, default=24.0, help="held 超过该时长视为滞留（默认 24h）"
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        result = reconcile(db, max_hold_hours=args.max_hold_hours)
    finally:
        db.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    s = result["summary"]
    print(
        f"[wallet-reconcile] workspace={s['workspace']} total={s['total_count']} amount_krw={s['total_amount_krw']}"
    )
    for st, v in sorted(s["by_status"].items()):
        print(f"  - {st:<10} count={v['count']:<5} amount_krw={v['amount_krw']}")
    if result["ok"]:
        print("[wallet-reconcile] OK — 全部不变式通过")
        return 0
    print(f"[wallet-reconcile] FAILED — 发现 {len(result['issues'])} 项异常：")
    for it in result["issues"]:
        print(f"  ! {it['check']}: {it}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
