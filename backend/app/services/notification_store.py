"""Web Push 订阅存储（GAP-021）—— **零迁移**的追加式存储。

## 为什么不建表（这是本模块存在的全部理由）

#89 的硬性约束是「禁新增迁移、禁改 models.py」，迁移槽归 #90。三条路：

  a. **给既有业务表加列**（如 `users.push_endpoint`）→ 被禁。而且语义也错：
     一个用户多设备多端点，一列装不下；真装得下也会把"通知订阅"这种可丢弃的
     旁路状态钉进身份表。
  b. **独立最小模型 + 独立 metadata**（仓库里 wallet_track.py 是这种范式）→ 表建
     出来了，但**迁移槽已归 #90**，生产（PostgreSQL + `alembic upgrade head`）
     这条路径上没有任何迁移会创建它 —— 于是它只在一个跑 `create_all` 的库里存在。
     一个"生产上根本建不出来"的表，比没有表更危险：本地全绿、上线即 500。
  c. **不落表**：订阅是 (user_id, endpoint, keys) 三元组，天然是追加型小记录。
     → 用追加式 JSONL，路径由 `NOTIFICATION_STORE_PATH` 指定（生产务必指向持久卷）。

据此选 (c)。**代价写清楚**：多实例部署时进程间不共享（内存限流本来就是单实例假设，
与之同构）；文件是整读，因此不适合把百万级订阅塞进来 —— 到 #90 迁移槽放开后，
应把它换成真表并把本模块的 4 个函数（`save` / `list_for_user` / `delete` / `count`）
原样接上去。本模块刻意做成这个最小面，就是为了让那一步是"换实现"而不是"重写调用方"。

## 并发与安全

  · 进程内 `threading.Lock` 串行化读写 —— FastAPI 的 sync handler 跑在线程池里，
    read-modify-write 必须互斥，否则并发 subscribe 会丢订阅（后写覆盖先写）。
  · **写入用 temp + os.replace 原子替换**：进程被 kill 时文件要么是旧的完整内容、
    要么是新的完整内容，不会留下半行 JSON（半行会让整个文件解析失败 = 全部订阅丢失）。
  · 文件权限 0600：`keys.p256dh` / `keys.auth` 是**能向该设备推消息的凭据**。
  · endpoint 只存不改；日志里只出现主机名（见 notifications._host）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# 单文件软上限：超过就拒绝新写入（而不是把文件撑到内存扛不住）。
# 该上限同时是"没换成真表"这件事的告警线：真到 5000 条就该切 #90 的表了。
MAX_RECORDS = 5000


def store_path() -> str:
    """存储文件路径。空 = 功能关闭（返回 None 由调用方降级）。"""
    return (settings.notification_store_path or "").strip()


@dataclass
class PushSubscriptionRecord:
    user_id: int
    endpoint: str
    keys: dict
    user_agent: str = ""
    created_at: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


def _read_all(path: str) -> list[PushSubscriptionRecord]:
    """整读。文件不存在 → 空列表；**单行损坏不影响其余行**（跳过并记账）。"""
    if not os.path.exists(path):
        return []
    out: list[PushSubscriptionRecord] = []
    broken = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    out.append(
                        PushSubscriptionRecord(
                            user_id=int(raw["user_id"]),
                            endpoint=str(raw["endpoint"]),
                            keys=dict(raw.get("keys") or {}),
                            user_agent=str(raw.get("user_agent") or ""),
                            created_at=int(raw.get("created_at") or 0),
                        )
                    )
                except (ValueError, KeyError, TypeError):
                    broken += 1
    except OSError as exc:
        logger.error("[notify] 订阅存储读取失败 path=%s err=%s", path, type(exc).__name__)
        return []
    if broken:
        logger.warning("[notify] 订阅存储跳过 %d 行损坏记录（其余正常）", broken)
    return out


def _write_all(path: str, records: list[PushSubscriptionRecord]) -> None:
    """原子替换写入。temp 文件与目标同目录（跨设备 rename 会失败）。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(r.to_json() + "\n")
        os.chmod(tmp, 0o600)  # 含推送凭据
        os.replace(tmp, path)  # 原子
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save(user_id: int, endpoint: str, keys: dict, user_agent: str = "") -> Optional[PushSubscriptionRecord]:
    """登记（或刷新）一个订阅端点。

    同一 endpoint 重复提交 = **幂等刷新**（`created_at` 保持首次时间，`keys` 更新）：
    浏览器重新 subscribe 会返回同一端点但密钥轮换过，若当成新记录插入会迅速堆满。
    超过单用户上限时逐出**最旧**的一条（而不是拒绝新订阅 —— 用户刚换了设备，
    拒掉新的等于把通知永久断在当前这台旧机器上）。
    """
    path = store_path()
    if not path:
        return None
    now = int(time.time())
    with _lock:
        records = _read_all(path)
        existing = next((r for r in records if r.endpoint == endpoint), None)
        if existing is not None:
            existing.user_id = user_id
            existing.keys = dict(keys or {})
            existing.user_agent = user_agent or existing.user_agent
            result = existing
        else:
            if len(records) >= MAX_RECORDS:
                logger.error("[notify] 订阅存储已达上限 %d，拒绝新订阅", MAX_RECORDS)
                raise RuntimeError("subscription store full")
            result = PushSubscriptionRecord(
                user_id=user_id, endpoint=endpoint, keys=dict(keys or {}),
                user_agent=user_agent, created_at=now,
            )
            records.append(result)

        mine = [r for r in records if r.user_id == user_id]
        cap = int(settings.web_push_max_subscriptions_per_user)
        if cap > 0 and len(mine) > cap:
            evict = {r.endpoint for r in sorted(mine, key=lambda r: r.created_at or 0)[: len(mine) - cap]}
            records = [r for r in records if r.endpoint not in evict]
            logger.info("[notify] 用户 %s 订阅超上限 %d，逐出最旧 %d 条", user_id, cap, len(evict))

        _write_all(path, records)
    return result


def list_for_user(user_id: int) -> list[PushSubscriptionRecord]:
    path = store_path()
    if not path:
        return []
    with _lock:
        return [r for r in _read_all(path) if r.user_id == user_id]


def delete(endpoint: str) -> bool:
    """删除订阅（推送网关回 404/410 = 端点已注销时调用）。返回是否删掉了东西。"""
    path = store_path()
    if not path:
        return False
    with _lock:
        records = _read_all(path)
        kept = [r for r in records if r.endpoint != endpoint]
        if len(kept) == len(records):
            return False
        _write_all(path, kept)
    return True


def count() -> int:
    path = store_path()
    if not path:
        return 0
    with _lock:
        return len(_read_all(path))
