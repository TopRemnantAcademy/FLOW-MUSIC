"""WeChat Pay v3 provider (CNY) — Native 下单 + 回调验签/解密。

官方规范(微信支付 APIv3):
  下单   : POST {api_base}/v3/pay/transactions/native
           body = {appid, mchid, description, out_trade_no, notify_url,
                   amount:{total(分), currency:"CNY"}}
           响应 = {"code_url": "weixin://wxpay/bizpayurl?pr=..."}  ← 生成二维码给用户扫
  认证头 : Authorization: WECHATPAY2-SHA256-RSA2048
           mchid="..",nonce_str="..",signature="..",timestamp="..",serial_no=".."
           待签名串 = f"{METHOD}\\n{URL_PATH_WITH_QUERY}\\n{timestamp}\\n{nonce_str}\\n{body}\\n"
           签名 = base64(RSA-SHA256(商户私钥, 待签名串))     (PKCS#1 v1.5)
  回调   : 头 Wechatpay-Timestamp / Wechatpay-Nonce / Wechatpay-Signature / Wechatpay-Serial
           待验签串 = f"{timestamp}\\n{nonce}\\n{body}\\n", 用微信支付公钥或平台证书 RSA-SHA256 验签
           Wechatpay-Serial 决定用哪把公钥: "微信支付公钥的序列号固定采用 PUB_KEY_ID_数字串
           格式……若请求头中的序列号不符合该格式, 则应使用平台证书进行验签" (native_notify.md 2.1)
           业务数据在 resource 里, 需用 APIv3 密钥做 AES-256-GCM 解密:
             key = APIv3Key(32B), nonce = resource.nonce, aad = resource.associated_data,
             ciphertext = base64decode(resource.ciphertext)  (含 16B GCM tag 后缀)
           解密后含 mchid / appid / out_trade_no / trade_state / amount.total(分, 官方标必填)

应答   : 验签通过 → HTTP 200/204, 无需报文; 不通过 → 4xx/5xx + {"code":"FAIL"} (native_notify.md 2.2)
探测流量: 官方会以 "WECHATPAY/SIGNTEST/" 前缀发送错误签名以检测商户是否正确验签.
          官方要求"不应对探测流量进行特殊处理, 而应将其视为正常的通知回调, 并对其签名进行验证",
          验签失败时"应返回失败(4xx/5xx), 等待微信支付携带正确签名重新发送" (4013053249 §5(1)).
          → 本实现仅加日志便于排障, **绝不因其前缀特殊放行**, 行为与验签失败完全一致.

零新增依赖: cryptography(已在 requirements) + httpx(已在 requirements) + 标准库.

官方来源(2026-09-04 实证, 均取 .md 原文):
  下单     https://pay.weixin.qq.com/doc/v3/merchant/4012791877.md
  回调通知 https://pay.weixin.qq.com/doc/v3/merchant/4012791882.md
  签名总述 https://pay.weixin.qq.com/doc/v3/merchant/4012365342.md
  签名生成 https://pay.weixin.qq.com/docs/merchant/development/interface-rules/signature-generation.html
  公钥验签 https://pay.weixin.qq.com/doc/v3/merchant/4013053249.md
  回调解密 https://pay.weixin.qq.com/doc/v3/merchant/4012071382.md
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time

from app.billing.pg._common import (
    ProviderNotConfigured,
    amount_cny_fen,
    out_trade_no,
    require_currency,
)
from app.billing.provider import PaymentProvider, WebhookEvent
from app.config import settings

logger = logging.getLogger(__name__)

NATIVE_PATH = "/v3/pay/transactions/native"
AUTH_TYPE = "WECHATPAY2-SHA256-RSA2048"
# 官方 native_prepay.md: description 为 string(127), "不能超过127个字符"
MAX_DESCRIPTION_LEN = 127
# 官方 native_notify.md 2.1: 微信支付公钥序列号固定采用 PUB_KEY_ID_数字串 格式
PUB_KEY_ID_PREFIX = "PUB_KEY_ID_"
# 官方 4013053249 §5(1): 探测流量的签名值都带此前缀
SIGNTEST_PREFIX = "WECHATPAY/SIGNTEST/"


def _require_config() -> tuple[str, str, str, str]:
    mch = (settings.wechat_pay_mch_id or "").strip()
    app_id = (settings.wechat_pay_app_id or "").strip()
    serial = (settings.wechat_pay_cert_serial_no or "").strip()
    pem = settings.wechat_pay_private_key_pem
    missing = [
        n
        for n, v in (
            ("WECHAT_PAY_MCH_ID", mch),
            ("WECHAT_PAY_APP_ID", app_id),
            ("WECHAT_PAY_CERT_SERIAL_NO", serial),
            ("WECHAT_PAY_PRIVATE_KEY(_PATH)", pem),
        )
        if not v
    ]
    if missing:
        raise ProviderNotConfigured(f"微信支付未配置: {', '.join(missing)}")
    return mch, app_id, serial, pem


def _sign(pem: str, message: str) -> str:
    """RSA-SHA256(PKCS#1 v1.5) 签名 → base64。私钥格式错误 → ProviderNotConfigured。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as e:
        raise ProviderNotConfigured(f"微信支付商户私钥无法解析(PEM 格式错误): {e}")
    sig = key.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _auth_header(method: str, path: str, body: str) -> str:
    mch, _app_id, serial, pem = _require_config()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16).upper()
    message = f"{method}\n{path}\n{ts}\n{nonce}\n{body}\n"
    signature = _sign(pem, message)
    return (
        f'{AUTH_TYPE} mchid="{mch}",nonce_str="{nonce}",'
        f'signature="{signature}",timestamp="{ts}",serial_no="{serial}"'
    )


def _check_serial(serial: str) -> None:
    """按官方 native_notify.md 2.1 用 Wechatpay-Serial 确认"该用哪把公钥验签"。

    - serial 缺失 → 拒绝。官方 SDK(wechatpay-java NotificationParser.validateRequest)对
      serialNumber 为空直接抛异常; 没有 serial 就无法判断该用微信支付公钥还是平台证书。
    - 公钥模式(配了 WECHAT_PAY_PUB_KEY_ID) → serial 必须完全一致, 不一致即拒绝,
      避免"拿 A 密钥验 B 密钥的签名"这种无诊断信息的静默失败。
    - 平台证书模式(未配) → 若 serial 形如 PUB_KEY_ID_*, 说明微信在用公钥签名而我们配的是
      平台证书(或反之), 明确报错引导配置, 而不是让验签默默失败。
    """
    serial = (serial or "").strip()
    if not serial:
        raise ValueError("微信支付回调缺少 Wechatpay-Serial 头: 官方要求以此选择验签密钥")
    expected = (settings.wechat_pay_pub_key_id or "").strip()
    if expected:
        if serial != expected:
            raise ProviderNotConfigured(
                f"回调 Wechatpay-Serial={serial!r} 与配置的 WECHAT_PAY_PUB_KEY_ID="
                f"{expected!r} 不一致: 可能微信侧已更换验签密钥, 或该项配置有误"
            )
        return
    if PUB_KEY_ID_PREFIX in serial.upper():
        raise ProviderNotConfigured(
            f"回调 Wechatpay-Serial={serial!r} 为微信支付公钥 ID 格式, 但 WECHAT_PAY_PUB_KEY_ID "
            "未配置(当前为平台证书模式): 两者不匹配时验签必失败, 请先配置该项"
        )


def _verify_platform_signature(
    timestamp: str, nonce: str, body: str, signature: str, serial: str = ""
) -> bool:
    """微信支付公钥/平台证书验签: RSA-SHA256(PKCS#1 v1.5) over f"{ts}\\n{nonce}\\n{body}\\n"。

    官方 4013053249 §5(1): 探测流量(签名以 WECHATPAY/SIGNTEST/ 开头)不得特殊处理,
    必须走同一条验签路径 —— 故这里只打日志, 返回 False 与普通验签失败完全一致。
    """
    _check_serial(serial)
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = settings.wechat_pay_platform_public_key_pem
    if not pem:
        raise ProviderNotConfigured(
            "微信支付公钥/平台证书未配置: WECHAT_PAY_PLATFORM_PUBLIC_KEY(_PATH)。"
            "未验签即接受回调等于允许任意人伪造支付成功, 绝不放行."
        )
    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise ProviderNotConfigured(f"微信支付公钥/平台证书无法解析(PEM 格式错误): {e}")
    message = f"{timestamp}\n{nonce}\n{body}\n".encode("utf-8")
    try:
        raw = base64.b64decode(signature)
    except (ValueError, TypeError):
        return False
    try:
        key.verify(raw, message, padding.PKCS1v15(), hashes.SHA256())
        return True
    except InvalidSignature:
        # 官方 4013053249 §5(1): 微信会发送 WECHATPAY/SIGNTEST/ 前缀的假签名检测商户是否正确验签。
        # 这里只记录, 行为(返回 False → 路由 400 → 微信重投正确签名)与官方要求一致。
        if signature.startswith(SIGNTEST_PREFIX):
            logger.info(
                "wechatpay signature probe detected and rejected as invalid "
                "(serial=%s): 商户系统正确验签, 等待微信重投",
                serial,
            )
        return False


def _decrypt_resource(resource: dict, api_v3_key: str) -> dict:
    """AES-256-GCM 解密 resource。密钥必须是 32 字节 APIv3 密钥。"""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = (api_v3_key or "").encode("utf-8")
    if len(key) != 32:
        raise ProviderNotConfigured(f"WECHAT_PAY_API_V3_KEY 必须为 32 字节, 当前 {len(key)} 字节。")
    algo = (resource.get("algorithm") or "AEAD_AES_256_GCM").upper()
    if algo != "AEAD_AES_256_GCM":
        raise ValueError(f"不支持的回调加密算法: {algo}")
    try:
        ciphertext = base64.b64decode(resource.get("ciphertext") or "")
    except (ValueError, TypeError):
        raise ValueError("回调 resource.ciphertext 非合法 base64")
    nonce = (resource.get("nonce") or "").encode("utf-8")
    aad = (resource.get("associated_data") or "").encode("utf-8")
    try:
        plain = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except (InvalidTag, ValueError) as e:
        raise ValueError(f"回调 resource 解密失败(密钥或数据不匹配): {e}")
    return json.loads(plain.decode("utf-8"))


def _paid_amount_minor(decrypted: dict) -> int | None:
    """官方 native_notify.md: 解密后 amount.total 为订单总金额(分, 官方标必填)。

    缺失时返回 None(不校验) —— "用户已付款却被判失败"是最坏结果, 金额校验是防御层,
    不能因为字段形态意外而误拒真实支付。
    """
    amount = decrypted.get("amount")
    if not isinstance(amount, dict):
        return None
    total = amount.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total if total > 0 else None


def _check_merchant_identity(decrypted: dict) -> None:
    """确认这笔钱是本商户的(官方 native_notify.md 解密字段表: mchid / appid 均为必填)。

    只在字段存在时校验: 字段缺失时由「订单号匹配 + 金额校验」兜底, 避免因字段形态意外
    误拒真实支付。不匹配 = 回调发错商户号(如测试/生产环境串号) → 明确拒绝。
    """
    mchid = (settings.wechat_pay_mch_id or "").strip()
    got_mchid = str(decrypted.get("mchid") or "").strip()
    if mchid and got_mchid and got_mchid != mchid:
        raise ValueError(f"微信支付回调 mchid 不匹配: 期望 {mchid}, 实收 {got_mchid}")
    appid = (settings.wechat_pay_app_id or "").strip()
    got_appid = str(decrypted.get("appid") or "").strip()
    if appid and got_appid and got_appid != appid:
        raise ValueError(f"微信支付回调 appid 不匹配: 期望 {appid}, 实收 {got_appid}")


class WeChatPayProvider(PaymentProvider):
    name = "wechat"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        require_currency("wechat", order)
        total = amount_cny_fen(order)
        mch, app_id, _serial, _pem = _require_config()
        notify = (settings.wechat_pay_notify_url or "").strip()
        if not notify:
            raise ProviderNotConfigured(
                "WECHAT_PAY_NOTIFY_URL 未配置: Native 下单必须提供公网 HTTPS 回调地址, "
                "否则支付成功后永远无法升档."
            )
        trade_no = out_trade_no(order)
        # 官方 native_prepay.md: description 为 string(127), "不能超过127个字符"。
        # 超长会被微信拒单(400 PARAM_ERROR) → 下单即失败, 故在此硬截断而非让上游报错.
        description = f"订阅套餐 · {order.tier_key}"[:MAX_DESCRIPTION_LEN]
        payload = {
            "appid": app_id,
            "mchid": mch,
            "description": description,
            "out_trade_no": trade_no,
            "notify_url": notify,
            "amount": {"total": total, "currency": "CNY"},
        }
        # 待签名串里的 body 必须与实际发送字节完全一致 → 自己序列化并作为原始 body 发送.
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "Authorization": _auth_header("POST", NATIVE_PATH, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ai-music-platform/billing",
        }
        base = (settings.wechat_pay_api_base or "").rstrip("/")
        data = _post_raw(f"{base}{NATIVE_PATH}", headers=headers, body=body)
        code_url = data.get("code_url")
        if not code_url:
            raise RuntimeError(f"微信支付 Native 下单响应缺少 code_url: {data}")
        return {
            "checkout_url": code_url,  # weixin://... 由前端渲染二维码
            "provider_order_id": trade_no,
            "provider_params": {"qr_code_url": code_url, "kind": "native_qr"},
        }

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        h = {k.lower(): v for k, v in (headers or {}).items()}
        ts = h.get("wechatpay-timestamp", "")
        nonce = h.get("wechatpay-nonce", "")
        serial = h.get("wechatpay-serial", "")
        body = raw_body.decode("utf-8", "replace")
        if not ts or not nonce or not signature:
            raise ValueError("微信支付回调缺少验签必需头(timestamp/nonce/signature)")
        # serial 参与"用哪把公钥验签"的判定(官方 2.1), 缺失即拒绝.
        if not _verify_platform_signature(ts, nonce, body, signature, serial):
            raise ValueError("invalid wechatpay signature")

        evt = json.loads(body)
        event_type = (evt.get("event_type") or "").upper()
        decrypted = _decrypt_resource(evt.get("resource") or {}, secret)
        trade_no = decrypted.get("out_trade_no") or ""
        trade_state = (decrypted.get("trade_state") or "").upper()
        _check_merchant_identity(decrypted)
        paid_amount_minor = _paid_amount_minor(decrypted)

        if event_type == "TRANSACTION.SUCCESS" and trade_state == "SUCCESS":
            status, kind = "paid", "order"
        elif event_type.startswith("REFUND."):
            refund_status = (decrypted.get("refund_status") or "").upper()
            # REFUND.SUCCESS → 全额/部分退款均由 amount 字段判断; 只有全额才撤销权限.
            total = (decrypted.get("amount") or {}).get("total")
            refunded = (decrypted.get("amount") or {}).get("refund")
            full = (
                isinstance(total, int)
                and isinstance(refunded, int)
                and total > 0
                and refunded >= total
            )
            if event_type == "REFUND.SUCCESS" or refund_status == "SUCCESS":
                status = "refunded" if full else "refund_partial"
            else:
                status = "failed"
            kind = "order"
        else:
            # 支付关闭/失败/未识别 → 保守按失败处理, 绝不升档
            status, kind = "failed", "order"

        return WebhookEvent(
            provider_order_id=str(trade_no),
            status=status,
            tier_key=None,
            user_id=None,
            raw=body,
            kind=kind,
            # 订单金额核对的官方依据见 provider.WebhookEvent.paid_amount_minor 注释.
            paid_amount_minor=paid_amount_minor if status == "paid" else None,
        )


def _post_raw(url: str, *, headers: dict[str, str], body: str) -> dict:
    """以「与签名完全一致的原始字节」发送 body(不能让 httpx 重新序列化 JSON)。"""
    import httpx

    from app.billing.pg._common import HTTP_TIMEOUT

    with httpx.Client(timeout=HTTP_TIMEOUT) as c:
        resp = c.post(url, headers=headers, content=body.encode("utf-8"))
    if resp.status_code >= 400:
        raise RuntimeError(f"{url} 返回 HTTP {resp.status_code}: {resp.text[:500]}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError(f"{url} 返回非 JSON 响应: {resp.text[:500]}")


# 注意: 本模块不使用 _common.post —— 微信 v3 的签名覆盖请求体字节,
# 必须发送与签名时完全一致的原始 bytes, 故统一走 _post_raw.
__all__ = ["WeChatPayProvider"]
