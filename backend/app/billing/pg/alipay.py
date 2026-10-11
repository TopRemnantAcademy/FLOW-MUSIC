"""Alipay provider (CNY) — alipay.trade.page.pay + RSA2 签名/异步通知验签。

官方规范(支付宝开放平台):
  网关   : https://openapi.alipay.com/gateway.do
  接口   : alipay.trade.page.pay (电脑网站支付) —— GET 跳转型, **无需服务端 HTTP 调用**,
           把公共参数 + biz_content 签名后拼成 URL 交给浏览器即可.
  公共参数: app_id, method, format=JSON, charset=utf-8, sign_type=RSA2,
           timestamp="yyyy-MM-dd HH:mm:ss"(北京时间), version=1.0, notify_url, return_url, biz_content
  签名   : 剔除 sign / 空值 → 参数名按字母序 → 拼 "k=v&k=v"(值不做 URL 编码)
           → base64(RSA-SHA256(应用私钥, 待签名串))       (即 RSA2 = SHA256withRSA)
           ⚠ 签名时 **sign_type 参与**(官方 SDK PHP AopClient.getSignContent 只 unset sign).

  异步通知: application/x-www-form-urlencoded POST, 用**支付宝公钥**验签.
  ⚠⚠ 验签与签名的待签串规则**不同** —— rsaCheckV1(官方指定用于支付接口)要
     **同时剔除 sign 与 sign_type**, 然后对其余参数做 URLDecode 再字母序拼接:
       - 官方文档 opendocs.alipay.com/open/02pa44「异步通知结果验签」:
         "1 在通知返回参数列表中, 除去 sign、sign_type 两个参数外, 凡是通知返回回来的参数
            皆是待验签的参数。2 将剩下参数进行 URLDecode, 然后进行字典排序"
       - 官方 SDK Java AlipaySignature.getSignCheckContentV1:
         params.remove("sign"); params.remove("sign_type");
       - 官方 SDK PHP AopClient.rsaCheckV1: unset($params['sign']); unset($params['sign_type']);
       - 官方全球站 global.alipay.com/docs/ac/gr/apispec: "a、Remove sign and sign_type fields"
     (rsaCheckV2 保留 sign_type, 仅用于生活号/服务窗异步, **不适用于电脑网站支付**)
     若只剔除 sign, 真实支付宝通知(必带 sign_type=RSA2)会 100% 验签失败 → 用户付款后永不升档.
  通知后校验: 官方同页验签步骤 5 要求核对订单号/金额/收款方, 任一不符"务必忽略".
  trade_status: TRADE_SUCCESS / TRADE_FINISHED = 成功.
  应答: 必须返回纯文本 success(官方响应值说明: fail=重试, success=不重试).

零新增依赖: cryptography(已在 requirements) + 标准库(无需 httpx —— 跳转型接口不发请求).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode

from app.billing.pg._common import (
    ProviderNotConfigured,
    amount_cny_yuan_str,
    out_trade_no,
    require_currency,
)
from app.billing.provider import PaymentProvider, WebhookEvent
from app.config import settings

METHOD_PAGE_PAY = "alipay.trade.page.pay"
# 支付宝要求 timestamp 为北京时间. 中国不实行夏令时 → 固定 +08:00 偏移是确定值,
# 且不依赖容器内是否安装 tzdata(与 D29 的显式偏移原则一致).
CST = timezone(timedelta(hours=8))


def _require_config() -> tuple[str, str]:
    app_id = (settings.alipay_app_id or "").strip()
    pem = settings.alipay_private_key_pem
    missing = [
        n for n, v in (("ALIPAY_APP_ID", app_id), ("ALIPAY_PRIVATE_KEY(_PATH)", pem)) if not v
    ]
    if missing:
        raise ProviderNotConfigured(f"支付宝未配置: {', '.join(missing)}")
    sign_type = (settings.alipay_sign_type or "RSA2").upper()
    if sign_type != "RSA2":
        raise ProviderNotConfigured(
            f"ALIPAY_SIGN_TYPE 仅支持 RSA2(SHA256withRSA), 当前为 {sign_type}; "
            "RSA(SHA1) 已被支付宝弃用, 不予实现."
        )
    return app_id, pem


def build_sign_content(params: dict[str, object]) -> str:
    """待签名/待验签串: 剔除 sign, 剔除空值, 按参数名字母序拼 "k=v&k=v"(值不做 URL 编码)。"""
    items = [
        (k, str(v)) for k, v in params.items() if k != "sign" and v is not None and str(v) != ""
    ]
    items.sort(key=lambda kv: kv[0])
    return "&".join(f"{k}={v}" for k, v in items)


def _sign_rsa2(pem: str, content: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as e:
        raise ProviderNotConfigured(f"支付宝应用私钥无法解析(PEM 格式错误): {e}")
    sig = key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def verify_alipay_notify(params: dict[str, str]) -> bool:
    """异步通知验签(rsaCheckV1): 剔除 sign **与 sign_type**, 其余按规则拼串, 支付宝公钥验签。

    官方依据(三方一致, 见模块顶部注释): 官方文档 02pa44 第 1 条 + 官方 SDK Java
    getSignCheckContentV1 + 官方 SDK PHP AopClient.rsaCheckV1。

    ⚠ 旧实现的注释断言"剔除 sign_type 会让验签失败"是**反的** —— 恰好相反:
      不剔除 sign_type 才会让真实通知验签失败(通知必带 sign_type=RSA2)。
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = settings.alipay_public_key_pem
    if not pem:
        raise ProviderNotConfigured(
            "ALIPAY_PUBLIC_KEY(_PATH) 未配置: 异步通知无法验签。"
            "未验签即接受通知等于允许任意人伪造支付成功, 绝不放行."
        )
    sign = params.get("sign") or ""
    if not sign:
        return False
    # V1: 剔除 sign 与 sign_type(官方), 其余非空参数按字母序拼接("k=v&k=v")。
    # 值必须是 URLDecode 后的: 官方第 2 步明确"将剩下参数进行 URLDecode"; parse_qsl 已解码。
    rest = {k: v for k, v in params.items() if k != "sign" and k != "sign_type"}
    content = build_sign_content(rest)
    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise ProviderNotConfigured(f"支付宝公钥无法解析(PEM 格式错误): {e}")
    try:
        raw = base64.b64decode(sign)
    except (ValueError, TypeError):
        return False
    try:
        key.verify(raw, content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except InvalidSignature:
        return False


def _paid_amount_minor(params: dict[str, str]) -> int | None:
    """把通知里的 total_amount(元, 字符串)换算为分。

    官方验签步骤 5 要求核对金额。缺失/无法解析时返回 None(不校验) ——
    "用户已付款却被判失败"是最坏结果, 金额校验是防御层, 不能因字段形态意外误拒真实支付。
    """
    raw = params.get("total_amount")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        fen = int(round(float(str(raw).strip()) * 100))
    except (TypeError, ValueError):
        return None
    return fen if fen > 0 else None


def _check_merchant_identity(params: dict[str, str]) -> None:
    """官方验签步骤 5 的收款方核对: 确认 app_id 是本应用。

    只在字段存在时校验(字段缺失时由订单号匹配 + 金额校验兜底), 避免误拒真实支付。
    """
    app_id = (settings.alipay_app_id or "").strip()
    got = str(params.get("app_id") or "").strip()
    if app_id and got and got != app_id:
        raise ValueError(f"支付宝通知 app_id 不匹配: 期望 {app_id}, 实收 {got}")


class AlipayProvider(PaymentProvider):
    name = "alipay"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        require_currency("alipay", order)
        total = amount_cny_yuan_str(order)
        app_id, pem = _require_config()
        notify = (settings.alipay_notify_url or "").strip()
        if not notify:
            raise ProviderNotConfigured(
                "ALIPAY_NOTIFY_URL 未配置: 无异步通知则支付成功后永远无法升档."
            )
        trade_no = out_trade_no(order)
        ret = (return_url or settings.billing_return_url or "").rstrip("/")
        biz = {
            "out_trade_no": trade_no,
            "total_amount": total,
            "subject": f"订阅套餐 · {order.tier_key}",
            "product_code": "FAST_INSTANT_TRADE_PAY",
        }
        params: dict[str, object] = {
            "app_id": app_id,
            "method": METHOD_PAGE_PAY,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": notify,
            "biz_content": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
        }
        if ret:
            params["return_url"] = ret
        params["sign"] = _sign_rsa2(pem, build_sign_content(params))
        gateway = (settings.alipay_gateway or "").strip()
        if not gateway:
            raise ProviderNotConfigured("ALIPAY_GATEWAY 未配置.")
        sep = "&" if "?" in gateway else "?"
        return {
            # 跳转型接口: 直接把签名后的 URL 交给浏览器, 不发服务端请求(官方即此设计).
            "checkout_url": f"{gateway}{sep}{urlencode(params)}",
            "provider_order_id": trade_no,
            "provider_params": {"kind": "redirect"},
        }

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        # 支付宝异步通知是 form-urlencoded, 不是 JSON. parse_qsl 已做 URL 解码,
        # 与官方"用解码后的值参与验签"一致.
        params = dict(parse_qsl(raw_body.decode("utf-8", "replace"), keep_blank_values=True))
        if not verify_alipay_notify(params):
            raise ValueError("invalid alipay notify signature")
        # 官方验签步骤 5: 签名正确后仍须核对收款方/金额, 不符"务必忽略".
        _check_merchant_identity(params)
        paid_amount_minor = _paid_amount_minor(params)
        trade_status = (params.get("trade_status") or "").upper()
        trade_no = params.get("out_trade_no") or ""
        if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            status = "paid"
        elif trade_status == "TRADE_CLOSED":
            # 已关闭: 未付款超时关闭 or 全额退款后关闭 —— 用 refund_fee 区分.
            refund_fee = params.get("refund_fee")
            total_amount = params.get("total_amount")
            status = "failed"
            try:
                if refund_fee is not None and float(refund_fee) > 0:
                    if total_amount is not None and float(refund_fee) >= float(total_amount):
                        status = "refunded"
                    else:
                        status = "refund_partial"
            except (TypeError, ValueError):
                status = "failed"
        else:
            # WAIT_BUYER_PAY 等中间态 → 不升档
            status = "failed"
        return WebhookEvent(
            provider_order_id=str(trade_no),
            status=status,
            tier_key=None,
            user_id=None,
            raw=raw_body.decode("utf-8", "replace"),
            kind="order",
            # 订单金额核对的官方依据见 provider.WebhookEvent.paid_amount_minor 注释.
            paid_amount_minor=paid_amount_minor if status == "paid" else None,
        )
