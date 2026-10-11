"""P1 — FLOW 钱包跨域「按次扣费」轨（基础件，flag 关时零行为变更）。

复用 FLOW 已实装闲置的 ``wallet_reservation`` 表（与音乐后端同库 ``.gospel.db``）。
本模块在音乐后端**镜像同一张表**并提供 hold/capture/release 封装 + 套利下限校验。

设计约束（详见 docs/PAID_USER_DOMAINS_PLAN_2026-09-12.md §4 P1）：
  · 同库共享表：FLOW ``backend/app/models/compliance.py:85`` 的 ``WalletReservation``
    与此处的 ``WalletReservation`` 指向物理同一张 ``wallet_reservation`` 表（列定义逐一镜像）。
  · 套利下限：折算价 = ``ceil(price_cny / FX)`` → ``krw_to_cny(amount_krw) >= price_cny``，
    保证用 FLOW credit 支付不会比音乐站同档标价更便宜。
  · 幂等：同 ``(workspace_id, job_id)`` 已 held 则返回原 reservation_id。

⚠ 本文件只提供基础件。生成端点接线、上线开关、前端「用 FLOW credit 支付」选项、
以及 FLOW token 池（token_quota_enabled）去留决策，均不在本文件 —— 待用户拍板后由
``routers/songs.py`` 接线（见规划文档 §4 P1 收尾清单）。
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings
from app.models import UserLink


class _WalletBase(DeclarativeBase):
    """独立元数据：wallet_reservation 是 FLOW 拥有的共享表，不属于音乐 migration。

    故不放进音乐 app 的 Base.metadata（避免 Alembic 表级对等测试误判，也避免音乐端
    误声称拥有该表）。运行期与音乐 SessionLocal 共用同一 engine，可正常读写共享表。
    """


# 暴露元数据供测试库建表（生产由 FLOW migration 建，音乐端不拥有该表）
wallet_metadata = _WalletBase.metadata

# ── 汇率（与 FLOW compliance.py:42 同一环境变量，P2 收敛单一来源前的临时副本）──
FX_RATE_CNY_PER_KRW = float(os.environ.get("FX_RATE_CNY_PER_KRW", "0.19"))

# 音乐域固定 workspace，便于跨域对账（FLOW 侧 workspace 概念在此统一为常数）。
WALLET_WORKSPACE = "music"


def cny_to_krw(amount_cny: float) -> int:
    """音乐成本/标价(CNY) → FLOW credit(KRW)。向上取整以保证折算价 ≥ 原价（防套利）。"""
    if amount_cny <= 0:
        raise ValueError("price_cny must be > 0")
    return int(math.ceil(amount_cny / FX_RATE_CNY_PER_KRW))


def krw_to_cny(amount_krw: int) -> float:
    """FLOW credit(KRW) → 音乐成本/标价(CNY)，用于对账与套利下限校验。"""
    return round(amount_krw * FX_RATE_CNY_PER_KRW, 4)


class WalletReservation(_WalletBase):
    """镜像 FLOW ``wallet_reservation``（compliance.py:85），同库共享表。

    仅声明音乐端读写所需的列，与 FLOW 物理表逐列对齐；FLOW migration 负责建表，
    本模型不触发任何 DDL（create_all 在表已存在时跳过）。
    """

    __tablename__ = "wallet_reservation"

    reservation_id = Column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex)
    workspace_id = Column(String(40), index=True)
    subscriber_id = Column(String(40), index=True)
    job_id = Column(String(80), index=True)
    amount_krw = Column(Integer, nullable=False)
    currency = Column(String(8), default="KRW")
    status = Column(String(16), default="held", index=True)  # held | captured | released | failed
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    captured_at = Column(DateTime, nullable=True)
    released_at = Column(DateTime, nullable=True)


def _lookup_subscriber(db: Session, music_user_id: int) -> str:
    """通过 user_links 反查 FLOW subscriber_id；未关联则钱包轨不可用。"""
    link = db.query(UserLink).filter(UserLink.music_user_id == music_user_id).first()
    if not link or not link.flow_subscriber_id:
        raise ValueError("music user not linked to a FLOW subscriber; wallet track unavailable")
    return link.flow_subscriber_id


def hold_wallet_track(
    db: Session,
    *,
    music_user_id: int,
    song_id: int,
    price_cny: float,
    reason: str | None = None,
) -> str:
    """生成前置：为单首歌向 FLOW 钱包发起 hold（按次扣费轨）。

    折算价下限 + 幂等见模块 docstring。返回 reservation_id。
    """
    if not settings.enable_flow_wallet_track:
        raise RuntimeError("wallet track disabled (settings.enable_flow_wallet_track=False)")
    subscriber_id = _lookup_subscriber(db, music_user_id)
    amount_krw = cny_to_krw(price_cny)
    # 不变式（套利下限）：折算回 CNY 不得低于原价
    if krw_to_cny(amount_krw) < price_cny - 1e-9:
        raise ValueError("arbitrage floor violated: converted KRW worth less than music price")
    job_id = f"song:{song_id}"
    existing = (
        db.query(WalletReservation)
        .filter_by(workspace_id=WALLET_WORKSPACE, job_id=job_id, status="held")
        .first()
    )
    if existing:
        return existing.reservation_id  # 幂等：避免重复 hold
    rec = WalletReservation(
        workspace_id=WALLET_WORKSPACE,
        subscriber_id=subscriber_id,
        job_id=job_id,
        amount_krw=amount_krw,
        currency="KRW",
        status="held",
        reason=reason or f"music generation song:{song_id}",
    )
    db.add(rec)
    db.flush()
    return rec.reservation_id


def capture_wallet_track(db: Session, reservation_id: str) -> bool:
    """held → captured（结算）。已 captured 幂等返回 True；非 held 拒绝。"""
    if not settings.enable_flow_wallet_track:
        raise RuntimeError("wallet track disabled (settings.enable_flow_wallet_track=False)")
    rec = db.get(WalletReservation, reservation_id)
    if rec is None:
        raise KeyError("reservation not found")
    if rec.status == "captured":
        return True
    if rec.status != "held":
        raise ValueError(f"cannot capture from status={rec.status}")
    rec.status = "captured"
    rec.captured_at = datetime.now(timezone.utc)
    db.flush()
    return True


def release_wallet_track(db: Session, reservation_id: str) -> bool:
    """held → released（退款）。已 released 幂等返回 True；已 settled 拒绝。"""
    if not settings.enable_flow_wallet_track:
        raise RuntimeError("wallet track disabled (settings.enable_flow_wallet_track=False)")
    rec = db.get(WalletReservation, reservation_id)
    if rec is None:
        raise KeyError("reservation not found")
    if rec.status == "released":
        return True
    if rec.status != "held":
        raise ValueError(f"cannot release from status={rec.status} (already settled)")
    rec.status = "released"
    rec.released_at = datetime.now(timezone.utc)
    db.flush()
    return True


def get_reservation(db: Session, reservation_id: str) -> WalletReservation | None:
    return db.get(WalletReservation, reservation_id)


def get_held_reservation_by_job(db: Session, job_id: str) -> WalletReservation | None:
    """对账/结算用：按 job_id（如 ``song:{id}``）取尚未结算（held）的钱包预约。

    无则返回 None —— 对应「免费额度内生成 / post-process 等无需扣费」路径，结算早退。
    """
    return (
        db.query(WalletReservation)
        .filter_by(workspace_id=WALLET_WORKSPACE, job_id=job_id, status="held")
        .first()
    )
