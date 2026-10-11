# GAP-004 step-4 真实 PG · 生产 live 上线清单

> 适用范围：把本地 `manual`/`offline` 模式切到生产真实支付（`BILLING_MODE=live`）。
> 代码范围：`backend/app/billing/**`、`backend/app/routers/billing.py`、`backend/app/config.py`、迁移 `backend/migrations/versions/e1f2a3b4c5d6_*`。
> 设计约束（不可妥协）：**任何升档都必须以"供应商侧确认"为唯一依据** ——
> - webhook 型(stripe/wechat/alipay)：异步回调**验签成功**才算数；
> - approve 型(kakao/naver)：服务端同步 approve 调用返回 **2xx** 才算数。
> 客户端自述"已支付"绝不能成为升档依据。

---

## 0. 上线前置（一次性）

| 项 | 命令 / 位置 | 说明 |
|---|---|---|
| 数据库迁移 | `alembic upgrade head` | 必须包含 `e1f2a3b4c5d6`（退款闭环所需的 `subscriptions.provider_customer_id` 列）。缺它 → 退款事件无法反查用户 → 退款后权限不撤销。 |
| 关闭 mock 后门 | `.env` 设 `BILLING_MODE=live` | `backend/app/routers/auth.py:207` 的 `subscribe` 在 live 模式返回 **410**，断开"免费直接升档"后门。 |
| 选择默认 provider | `.env` 设 `BILLING_PROVIDER=<provider>` | 可选：`manual \| stripe \| kakao \| naver \| wechat \| alipay \| paypal`。`manual` 在 live 模式被 `routers/billing.py:98` 显式拒绝(400)。 |

---

## 1. 按 provider 填密钥（`.env` / `.env.production`）

变量名与 `backend/app/config.py` 字段一一对应（pydantic 大小写不敏感）。

### 1.1 Stripe（webhook 型）
- `STRIPE_SECRET_KEY` —— checkout 创建会话（懒加载 `stripe` SDK，`requirements` 已含）。
- `STRIPE_WEBHOOK_SECRET` —— 回调 HMAC 验签。缺失 → `routers/billing.py:325` 返回 **503**（绝不放行未验签回调）。
- 控制台配置 webhook 指向 `POST /api/billing/webhook/stripe`。

> 以下 KakaoPay / NaverPay 参数均于 2026-09-04 以官方开发者文档原文实证（非推测）。来源：
> - KakaoPay：`https://developers.kakaopay.com/docs/payment/online/single-payment`、`.../restapi`
> - NaverPay：`https://docs.pay.naver.com/docs/onetime-payment/payment/payment-auth-window`、
>   `.../onetime-payment/payment/apply`、`.../common/url-format`、`.../more-information/security`

### 1.2 KakaoPay（approve 型 · KRW）
- `KAKAO_PAY_CID`（가맹점 코드，테스트 `TC0ONETIME`）、`KAKAO_PAY_CREDENTIAL`（Secret key）。
- `KAKAO_PAY_AUTH_SCHEME`：`SECRET_KEY`（**官方形式，默认**）｜`KakaoAK` 仅作旧 Kakao Developers 兼容保留。
- `KAKAO_PAY_API_BASE`：固定 `https://open-api.kakaopay.com`（官方文档：「호스트 도메인은 open-api.kakaopay.com 으로 호출해야 합니다. 개발환경은 지원하고 있지 않습니다」）。
  ⚠️ 旧 `https://kapi.kakao.com/v1/payment/*` 已由 Kakao 官方公告终止，切勿再使用。
- 路径 `/online/v1/payment/ready`、`/online/v1/payment/approve`；请求体为 **JSON**（`Content-Type: application/json`），非 form。
- **`pg_token` 传递方式（官方原文）**：「결제 준비 API 요청 시 전달받은 `approval_url` 에 `pg_token` 파라미터를 붙여 대기화면을 `approval_url` 로 redirect 합니다」→ 即回跳 URL 的 **query string** 携带 `pg_token`。
- **前端必须**在 approval 回跳 URL 带上 `provider=kakao&order_id=<id>`，随后调用 `POST /api/billing/kakao/approve`（带 `pg_token`）才升档。

### 1.3 NaverPay（approve 型 · KRW）
- `NAVER_PAY_CLIENT_ID` / `NAVER_PAY_CHAIN_ID` / `NAVER_PAY_SECRET_KEY` / `NAVER_PAY_MODE`(`development|production`)。
- **SDK 脚本地址固定为 `https://nsp.pay.naver.com/sdk/js/naverpay.min.js`**（官方文档要求必须通过该链接引入）。
- `NAVER_PAY_APPLY_URL`：**可留空**。官方 승인 API 已确认，按 `NAVER_PAY_MODE` 自动派生：
  - `development` → `https://dev-pay.paygate.naver.com/naverpay-partner/naverpay/payments/v2.2/apply/payment`
  - `production`  → `https://pay.paygate.naver.com/naverpay-partner/naverpay/payments/v2.2/apply/payment`
  - 仅当 파트너 계약상 별도 도메인을 쓸 때 전체 URL 로 덮어쓴다。
  - `NAVER_PAY_SECRET_KEY` 未设 → `ProviderNotConfigured` → **503**（不静默放行）。승인 호출 timeout 은 공식 권고 **60초**。
- **回跳参数（官方）**：成功 `?resultCode=Success&paymentId=...`；失败 `?resultCode=UserCancel|TimeExpired|...&resultMessage=...&reserveId=...`。
  → 前端**必须先校验 `resultCode == "Success"`** 再拿 `paymentId` 调 `POST /api/billing/naver/approve`，否则会拿空 token 去 승인。
- **金额校验（官方 보안 가이드 필수）**：승인 응답 `body.detail.totalPayAmount` 必须等于订单金额，不一致 즉시 승격 중단。

### 1.4 WeChat Pay v3（webhook 型 · CNY）
- `WECHAT_PAY_MCH_ID` / `WECHAT_PAY_APP_ID` / `WECHAT_PAY_CERT_SERIAL_NO` / `WECHAT_PAY_PRIVATE_KEY`（或 `_PATH`）。
- `WECHAT_PAY_PLATFORM_PUBLIC_KEY`（微信支付公钥或平台证书 PEM，用于验签）/
  `WECHAT_PAY_API_V3_KEY`（回调 AES-256-GCM 解密，**须 32 字节**）—— 任一缺失 → `ProviderNotConfigured` → 503。
- `WECHAT_PAY_PUB_KEY_ID`：**用微信支付公钥时必填**（形如 `PUB_KEY_ID_3000000001`）；用平台证书时**留空**。
- `WECHAT_PAY_NOTIFY_URL`（公网 HTTPS，Native 下单必填）。
- 控制台配置回调 `POST /api/billing/webhook/wechat`。

> 以下参数均于 2026-09-04 以官方 `.md` 原文实证（非推测）。来源：
> - 下单 `https://pay.weixin.qq.com/doc/v3/merchant/4012791877.md`
> - 回调 `https://pay.weixin.qq.com/doc/v3/merchant/4012791882.md`
> - 签名 `https://pay.weixin.qq.com/doc/v3/merchant/4012365342.md` 与 `.../development/interface-rules/signature-generation.html`
> - 公钥验签 `https://pay.weixin.qq.com/doc/v3/merchant/4013053249.md`；回调解密 `.../4012071382.md`

- 下单：`POST /v3/pay/transactions/native`，必填头 `Authorization` + `Accept: application/json` + `Content-Type: application/json`。
  `description` 上限 **127 字符**（官方 `string(127)`），代码已硬截断。`out_trade_no` 6–32 位、仅 `数字/字母/_-|*`。
- **回调验签必须带 `Wechatpay-Serial`**：官方原文「微信支付公钥的序列号固定采用 `PUB_KEY_ID_数字串` 格式……若请求头中的序列号不符合该格式，则应使用平台证书进行验签」。
  官方 SDK（`wechatpay-java` `NotificationParser.validateRequest`）对 serialNumber 缺失直接抛异常 → 本实现同样拒绝。
  公钥模式下 serial 与 `WECHAT_PAY_PUB_KEY_ID` 不一致 → 503 并指出配置问题（避免"拿 A 密钥验 B 密钥签名"的静默失败）。
- 应答：验签通过 → **200/204**（官方：「无需返回应答报文」）；不通过 → 4xx/5xx，微信按 15s/15s/30s/3m/… 共 **15 次**重投。
- 官方会以 `WECHATPAY/SIGNTEST/` 前缀发送**假签名探测**。官方要求「不应对探测流量进行特殊处理，而应将其视为正常的通知回调，并对其签名进行验证」，验签失败时「应返回失败（4xx/5xx）」。
  实现只加了一行 info 日志便于排障，**绝无特殊放行**（`pg/wechat.py`）。

### 1.5 Alipay（webhook 型 · CNY）
- `ALIPAY_APP_ID` / `ALIPAY_PRIVATE_KEY`（签名）/ `ALIPAY_PUBLIC_KEY`（验签）。**仅支持 RSA2**。
- `ALIPAY_GATEWAY`（默认 `https://openapi.alipay.com/gateway.do`）/ `ALIPAY_NOTIFY_URL`（公网 HTTPS 异步通知）。

- ⚠️ **请求签名与通知验签是两套不同规则，代码里也是两个函数，切勿合并**（2026-09-04 修正）：

  | 方向 | 规则 | 官方依据 |
  |---|---|---|
  | **请求签名**（`create_checkout`） | 只剔除 `sign`，**`sign_type` 参与** | 官方 SDK PHP `AopClient.getSignContent`（只 `unset($params['sign'])`） |
  | **通知验签**（`verify_alipay_notify`） | **同时剔除 `sign` 与 `sign_type`**，其余参数 URLDecode 后字母序拼接 | 官方文档 `opendocs.alipay.com/open/02pa44` 验签第 1–2 条；官方 SDK Java `AlipaySignature.getSignCheckContentV1`（`remove("sign"); remove("sign_type")`）；官方 SDK PHP `AopClient.rsaCheckV1`；官方全球站 `global.alipay.com/docs/ac/gr/apispec`（"Remove sign and sign_type fields"） |

  修正前只剔除 `sign` → 真实通知（必带 `sign_type=RSA2`）**100% 验签失败，用户付款后永不升档**。
  测试 `test_alipay_sign_verify_roundtrip` 已把该行为锁死（按旧规则算出的签名必须验签失败）。

- 应答必须返回纯文本 **`success`**（官方响应值说明：`fail` = 重试，`success` = 不重试）。
- 官方验签步骤 5 要求签名正确后**再核对订单号/金额/收款方**，任一不符「务必忽略」。
  实现：金额取自 `total_amount`（元 → 分），与订单 `amount_cny` 比对，不符 → **400** 且拒绝升档（`service.AmountMismatchError`）。
  商户归属（`app_id`）同理，仅在该字段存在时校验（缺失时由订单号 + 金额兜底，避免误拒真实支付）。

### 1.6 PayPal（webhook 型 · 多币种订阅）

> 所有参数均于 2026-09-05 以 `developer.paypal.com` 官方 `.md` 原文实证（非推测）。来源：
> - OAuth2 / 取 token：`/api/rest/authentication-and-authorization`
> - 建订阅：`/subscriptions/integrate`（含 `user_action=SUBSCRIBE_NOW` 自动激活）、事件名表 `event-names`
> - 回调验签：`/api/webhooks/overview` + `verify-webhook-signature`
> - 币种表：`/reference/currency-codes`

- `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` —— OAuth2 `client_credentials` 取 `access_token`（进程内缓存，提前 60s 过期）。任一缺失 → `ProviderNotConfigured` → **503**。
- `PAYPAL_MODE`：`sandbox`（`api-m.sandbox.paypal.com`）｜`live`（`api-m.paypal.com`）。非二者 → `ProviderNotConfigured` → 503。
- `PAYPAL_WEBHOOK_ID` —— Developer Portal 里该 webhook 的 ID；**回调验签必传**（缺失 → 503，绝不放行未验签回调）。
- `PAYPAL_PLAN_IDS` —— `tier_key → plan_id` 的 JSON 映射，**支持 `"tier:CCY"` 粒度**（官方：「Only one currency_code is allowed per subscription plan」→ 多币种必须多 plan）。缺失/不匹配 → `ProviderNotConfigured` → 503。
- `PAYPAL_RETURN_URL` —— 买家批准后回跳（留空则回退 `BILLING_RETURN_URL`）。
- `PAYPAL_CURRENCIES` —— 受理币种集合（逗号分隔）。默认剔除 `CNY/BRL/MYR`（官方脚注"仅支持 in-country 账户"），**不含官方未列的 KRW**；`HUF/JPY/TWD` 为零小数位币种（金额不乘 100）。

- **回调验签与所有其它 PG 都不同 —— 不是本地 HMAC，而是把回调原样回传给 PayPal 服务器核验**：
  `POST /v1/notifications/verify-webhook-signature`，body 携带 `auth_algo/cert_url/transmission_id/transmission_sig/transmission_time/webhook_id/webhook_event`（前 5 个取自头 `PAYPAL-AUTH-ALGO / -CERT-URL / -TRANSMISSION-ID / -TRANSMISSION-SIG / -TRANSMISSION-TIME`），成功 = `{"verification_status":"SUCCESS"}`。
  本实现采用回传方式（不依赖本地证书链与算法名映射），只信 PayPal 自己的判定，最不易出错；非 2xx / 验签 `!=SUCCESS` → **400** 且**不**升档（fail-closed）。
- 建订阅：`POST /v1/billing/subscriptions`，必填仅 `plan_id`；额外传 `custom_id=本地商户订单号`（回调回贴）、`application_context.user_action=SUBSCRIBE_NOW`（批准后自动激活）。响应 `links[].rel=="approve"` 的 href 即买家批准页。`checkout_url` 指向该页，`provider_order_id` = 订阅 ID（`I-...`）。
- 事件映射（`pg/paypal.py:_SUB_STATUS_BY_EVENT` + `PAYMENT.SALE.*`）：
  - `BILLING.SUBSCRIPTION.ACTIVATED` → 订单 `paid` + 订阅 `active`（首次付款升档）
  - `CANCELLED/EXPIRED` → 订阅 `cancelled`；`SUSPENDED/PAYMENT.FAILED` → 订阅 `past_due`
  - `PAYMENT.SALE.COMPLETED` → 订阅 `active`（⚠ 订阅 ID 字段名是 `billing_agreement_id`，**不是** `subscription_id`）
  - `PAYMENT.SALE.REFUNDED/REVERSED` → 订单 `refunded`（已知限制：sale 事件不带原交易总额，按全额处理，部分退款需运营对账或回查 `/v1/payments/sale/{id}`）
- 官方验签应答：2xx 视为收到；非 2xx 时 PayPal 最多重试 25 次 / 3 天。

---

## 2. 上线后自检（必须全绿）

1. **mock 后门已封**：`BILLING_MODE=live` 下 `POST /api/auth/subscribe` 返回 **410**。
2. **未知 provider 干净拒绝**：`billing_provider` 拼错（如 `bitcoin`）→ checkout 返回 **400**（非 500）。
3. **凭据缺失 → 503 而非放行**：未配 `STRIPE_WEBHOOK_SECRET` / `WECHAT_PAY_API_V3_KEY` / `NAVER_PAY_APPLY_URL` 时，对应回调/approve 返回 **503**（便于运维定位，且绝不未验签升档）。
4. **退款闭环**：制造一笔已支付订单（带 `provider_customer_id`），投递退款事件 → 确认 `subscriptions` 置 `cancelled`、`user.tier_key` 降回 `free`（`service.apply_refund_event`，`service.py:217`）。
5. **验签失败 → 400**：用伪造签名打 webhook → `routers/billing.py` 返回 **400**，且**不**触发升档。
6. **ack 规范**：alipay 回调须回纯文本 `success`（否则反复重投）；wechat 验签通过须回 **2xx**（官方只看状态码，报文非必需）。
7. **金额不符 → 400 且不升档**：构造一笔实收金额与订单不符的回调 → 返回 400（`service.AmountMismatchError`），用户权限不变。
   与"订单未预创建"的 **409** 区分：金额不符重投也不会改变，故不再让供应商反复重投。
8. **微信回调必须带 `Wechatpay-Serial`**：缺该头 → 400；公钥模式下 serial 与配置不符 → 503。
9. **PayPal 回调走服务器侧回传验签**：`PAYPAL_WEBHOOK_ID` 缺失 → 503；伪造/被篡改回调会被 PayPal 判 `verification_status!=SUCCESS` → 400 且不升档。控制台 webhook 指向 `POST /api/billing/webhook/paypal`，且 `PAYPAL_PLAN_IDS` 必须为每个在用 `tier_key`（及多币种时的 `"tier:CCY"`）预置 plan_id，否则 checkout 返回 503。

---

## 3. 安全红线（违反任一条 = 阻断上线）

- ❌ 未验签的 webhook / notify 一律拒绝，**绝不**据此升档。
- ❌ approve 响应即便 2xx，也要拦截显式失败信号(`error_code` / `code!=Success`)，但**不对未知响应形态贸然判失败**（避免"用户已付却未升档"最坏结果）。
- ❌ 各 provider 币种硬约束（kakao/naver=KRW，wechat/alipay=CNY）；币种不符 → `ProviderNotConfigured`/`ValueError` 在 checkout 阶段即拒绝，**绝不**用臆造汇率换算。
- ❌ `/api/billing/providers` 端点**不回显任何密钥/配置状态**，避免向外暴露部署状态（`routers/billing.py:223`）。

---

## 4. 回滚

- 紧急熔断：`BILLING_MODE=offline` + `BILLING_PROVIDER=manual`，支付类改走运维对账确认（不影响已升档用户）。
- DB 迁移 `e1f2a3b4c5d6` 可逆：`alembic downgrade -1` 仅删 `provider_customer_id` 列（退款闭环失效，但正常支付/订阅不受影响）。
