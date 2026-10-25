"""通知通道端点（GAP-021）：Web Push 订阅登记 + 通道自检 + 模板化发送。

三条通道状态各有**明确的失败语义**，不混在一起：

  · 邮件      —— 复用 GAP-002 的 `app/services/email.py`（boto3 → AWS SES）。
  · Web Push  —— VAPID(JWT/ES256) 签名只用 `cryptography`（已在 requirements）；
                 实际 POST 需要可选的 `pywebpush`，未装 → 降级为待推送队列。
  · 未配置    —— 一律 **202 Accepted-but-not-sent**，响应体里给 `reason` 与
                 需要的环境变量名。绝不 200 假装已发送（用户会永远等一封不会到的信），
                 也绝不 500（通知是旁路，没有通道不是"服务器错误"）。

## 为什么订阅不落表

见 `app/services/notification_store.py` 模块头（迁移槽归 #90；本 PR 零迁移）。
端点因此只需要：校验负载形状 → 调 store → 回报。

## 权限与信息面

  · 订阅/退订/自检**全部要求登录**：订阅是"我的设备"，与用户绑定；
    匿名写入等于开放一个任意 endpoint 的写入面（可被用来把推送网关当探针）。
  · 响应体**从不回显 endpoint 与 keys**：`keys.p256dh`/`keys.auth` 是能向该设备
    推消息的凭据，回显只会增加泄漏面（客户端自己就有）。
  · 日志只出现 endpoint 的**主机名**（`notifications._host`）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User
from app.ratelimit import rate_limit_notification_subscribe, rate_limit_notification_test
from app.routers.auth import get_current_user
from app.services import notification_store as store
from app.services import notifications as notify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# ─────────────────────────── 契约 ───────────────────────────


class PushKeys(BaseModel):
    """浏览器 `PushSubscription.toJSON().keys` 的两把密钥（都是 base64url）。

    `p256dh` 是客户端公钥（用于 ECDH 加密载荷），`auth` 是 16 字节认证密钥。
    长度做下限校验：明显截断的值会让推送网关拒收，早失败好过晚静默（推送失败
    目前只在服务端日志可见，用户侧表现为"怎么没提醒"）。
    """

    p256dh: str = Field(min_length=20, max_length=255)
    auth: str = Field(min_length=8, max_length=255)


class SubscribeReq(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2048)
    keys: PushKeys


class SubscribeResp(BaseModel):
    ok: bool
    # 存下来的订阅总数（该用户）。前端据此提示"已在 N 台设备上开启"。
    subscription_count: int
    # Web Push 是否真的能发（VAPID + 发送器齐备）。False 时订阅仍被登记，
    # 只是推送会走"待推送队列"——把这件事明说，别让前端以为一切就绪。
    push_ready: bool


class UnsubscribeReq(BaseModel):
    endpoint: str = Field(min_length=8, max_length=2048)


class TestChannelResult(BaseModel):
    channel: str
    sent: bool
    reason: str = ""


class TestResp(BaseModel):
    # 202 语义：请求已被受理，但不保证已送达（未配置通道时 sent=False）。
    accepted: bool
    results: list[TestChannelResult]
    # 未配置时告诉运维"该配哪些变量"——省掉翻代码猜变量名的一步。
    required_env: list[str]


class VapidKeyResp(BaseModel):
    """前端 `pushManager.subscribe({applicationServerKey})` 需要应用服务器公钥。

    公钥是**公开**的（它的作用就是让浏览器把订阅绑定到本服务器），故可匿名获取
    也无风险；但仍要求登录，理由同订阅（本端点属于"我的通知设置"这一面）。
    """

    configured: bool
    public_key: str
    subject: str


# 未配置时该配的环境变量名（与 .env.example 逐字一致，测试会核对）。
_EMAIL_ENV = ["SMTP_HOST", "SMTP_PORT", "SMTP_FROM"]
_PUSH_ENV = ["VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_SUBJECT"]


# ─────────────────────────── 端点 ───────────────────────────


@router.get("/vapid-public-key", response_model=VapidKeyResp)
def get_vapid_public_key(user: User = Depends(get_current_user)):
    """下发应用服务器公钥（未配置时 `configured=false` + 空串，前端据此隐藏开关）。"""
    pub = notify.public_key_b64()
    return VapidKeyResp(
        configured=notify.vapid_configured(),
        public_key=pub,
        subject=(settings.vapid_subject or "").strip(),
    )


@router.post("/subscribe", response_model=SubscribeResp)
def subscribe(
    body: SubscribeReq,
    request: Request,
    user: User = Depends(get_current_user),
):
    """登记 Web Push 订阅（用户级）。

    端点只接受 **https** 的推送服务地址：RFC 8030 §3.1 明确要求推送端点走 TLS，
    且浏览器只会给出 https 端点。放行 http 等于允许把"设备凭据"发去明文地址。
    """
    rate_limit_notification_subscribe(f"user:{user.id}")
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="推送端点必须是 https://")
    if not store.store_path():
        # 存储未配置 = 无法登记。明确 503 + 变量名，而不是"看起来存下了"。
        raise HTTPException(
            status_code=503,
            detail="订阅存储未配置（NOTIFICATION_STORE_PATH 为空），无法登记 Web Push 订阅。",
        )
    try:
        store.save(
            user_id=user.id,
            endpoint=body.endpoint,
            keys={"p256dh": body.keys.p256dh, "auth": body.keys.auth},
            user_agent=(request.headers.get("user-agent") or "")[:200],
        )
    except RuntimeError:
        raise HTTPException(status_code=507, detail="订阅存储已达上限，请联系运维。")
    return SubscribeResp(
        ok=True,
        subscription_count=len(store.list_for_user(user.id)),
        push_ready=notify.vapid_configured(),
    )


@router.post("/unsubscribe", response_model=SubscribeResp)
def unsubscribe(
    body: UnsubscribeReq,
    user: User = Depends(get_current_user),
):
    """退订：只删**自己的**端点。

    归属校验不可省 —— 否则任何人拿到（或猜到）别人的 endpoint 就能替对方关掉通知
    （把 endpoint 当 capability 用，但它本来就已经是凭据了，不该再有第二个用途）。
    """
    if not store.store_path():
        raise HTTPException(status_code=503, detail="订阅存储未配置（NOTIFICATION_STORE_PATH 为空）。")
    mine = {r.endpoint for r in store.list_for_user(user.id)}
    if body.endpoint not in mine:
        # 不存在 / 不是自己的 → 同一个 404：不泄露"这个端点存在但属于别人"。
        raise HTTPException(status_code=404, detail="订阅不存在")
    store.delete(body.endpoint)
    return SubscribeResp(
        ok=True,
        subscription_count=len(store.list_for_user(user.id)),
        push_ready=notify.vapid_configured(),
    )


@router.post("/test", response_model=TestResp, status_code=202)
def test_channel(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """自检：给当前用户发一封测试邮件 + 向他已登记的设备推一条测试推送。

    刻意**返回 202 而非 200**：通知是异步旁路，"受理"与"送达"是两件事。
    `results[].sent` 才回答后者；未配置时 sent=False + reason，仍 202。
    """
    rate_limit_notification_test(f"user:{user.id}")
    results: list[TestChannelResult] = []
    required: list[str] = []

    # ① 邮件
    mail = notify.send_self_test_email(user.email)
    results.append(TestChannelResult(channel="email", sent=mail.sent, reason=mail.reason))
    if not mail.sent:
        required.extend(_EMAIL_ENV if mail.reason == "email_not_configured" else [])

    # ② Web Push（对每个已登记端点各推一次）
    subs = store.list_for_user(user.id)
    if not subs:
        results.append(TestChannelResult(channel="push", sent=False, reason="no_subscription"))
    else:
        for sub in subs:
            r = notify.send_web_push(
                notify.NOTIFY_SYSTEM,
                sub.endpoint,
                sub.keys,
                user.id,
                {"subject": "通知通道自检", "message": "这条推送说明你的设备已能收到 FLOW MUSIC 通知。"},
            )
            # 端点已注销（换设备/卸载 PWA）→ 就地清理，别让死端点一直占位。
            if r.reason == "push_gone":
                store.delete(sub.endpoint)
            results.append(TestChannelResult(channel="push", sent=r.sent, reason=r.reason))
            if r.reason in ("vapid_not_configured", "vapid_invalid", "pywebpush_not_installed"):
                required.extend(_PUSH_ENV)

    return TestResp(accepted=True, results=results, required_env=sorted(set(required)))


# ─────────────────── 供业务代码调用的模板化发送 ───────────────────


def notify_generation_done(db: Session, user: User, song_id: int, title: str) -> list[TestChannelResult]:
    """生成完成通知（邮件 + 全部已登记设备的推送）。

    供 songs.py 的完成回调接线 —— 本 PR 只提供能力与端点，**不改动生成主流程**
    （生成路径是计费/编排的关键路径，把通知塞进去需要单独的回归面，
    见 PR 说明的"未接线"一节）。异常一律吞掉：通知失败绝不能影响作品状态。
    """
    out: list[TestChannelResult] = []
    ctx = {"song_id": song_id, "title": title}
    m = notify.send_notification_email(notify.NOTIFY_GENERATION_DONE, user.email, ctx)
    out.append(TestChannelResult(channel="email", sent=m.sent, reason=m.reason))
    for sub in store.list_for_user(user.id):
        r = notify.send_web_push(notify.NOTIFY_GENERATION_DONE, sub.endpoint, sub.keys, user.id, ctx)
        if r.reason == "push_gone":
            store.delete(sub.endpoint)
        out.append(TestChannelResult(channel="push", sent=r.sent, reason=r.reason))
    return out


def notify_share(db: Session, user: User, title: str, link: str, sender: str = "") -> list[TestChannelResult]:
    """分享通知（收件人 = user）。"""
    ctx = {"title": title, "link": link, "sender": sender or ""}
    m = notify.send_notification_email(notify.NOTIFY_SHARE, user.email, ctx)
    out = [TestChannelResult(channel="email", sent=m.sent, reason=m.reason)]
    for sub in store.list_for_user(user.id):
        r = notify.send_web_push(notify.NOTIFY_SHARE, sub.endpoint, sub.keys, user.id, ctx)
        if r.reason == "push_gone":
            store.delete(sub.endpoint)
        out.append(TestChannelResult(channel="push", sent=r.sent, reason=r.reason))
    return out


def notify_system(db: Session, user: User, subject: str, message: str) -> list[TestChannelResult]:
    """系统公告（邮件 + 推送）。"""
    ctx = {"subject": subject, "message": message}
    m = notify.send_notification_email(notify.NOTIFY_SYSTEM, user.email, ctx)
    out = [TestChannelResult(channel="email", sent=m.sent, reason=m.reason)]
    for sub in store.list_for_user(user.id):
        r = notify.send_web_push(notify.NOTIFY_SYSTEM, sub.endpoint, sub.keys, user.id, ctx)
        if r.reason == "push_gone":
            store.delete(sub.endpoint)
        out.append(TestChannelResult(channel="push", sent=r.sent, reason=r.reason))
    return out
