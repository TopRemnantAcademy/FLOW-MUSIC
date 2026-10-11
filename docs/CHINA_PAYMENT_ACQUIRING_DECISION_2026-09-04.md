# 中国收款方案决策备忘 — 微信支付 / 支付宝收单

> 编制日期：2026-09-04
> 适用范围：ai-music-platform（AI 音乐生成订阅）+ FLOW（韩语福音/圣经音频）
> 编制原则：**全部结论引自官方一手来源，无推测。** 未经官方公开、需商务议价的项目一律标注「未公开」，不做数字填充。

---

## 0. 一句话结论

**韩国主体无法直接接入微信支付或支付宝收单。** 最低成本可行解是**新设中国香港公司**：
微信支付境外**直连**（香港是全球仅有的三个直连地区之一）+ **AntomHK** 受理支付宝（且支持订阅扣款）。

---

## 1. 决定性前提：主体所在地，而非平台选择

费率差异不是平台造成的，是**商户主体注册地**造成的，幅度 3–4 倍。

### 1.1 官方费率实证

| 主体 | 微信支付 | 支付宝 | 综合成本 | 官方来源 |
|---|---|---|---|---|
| **中国大陆主体** | 0.6% | 0.6%（非特殊行业）/ 1.0%（特殊行业） | **≈0.6%，零汇损** | `pay.weixin.qq.com` 账单文档「费率 0.60%」；`b.alipay.com` 手机网站支付产品页；`opendocs.alipay.com/mini/053llc` |
| **中国香港主体** | 境外直连，**未公开** | AntomHK，**未公开** | 议价，业界区间 1.9%–2.5% | 直连资格见 `act.weixin.qq.com/static/merchant_overseas/faq_en.html` |
| Stripe（任意可用主体） | **2.2% + $0.35** | **2.2% + $0.35** | 2.2% + 固定费 | `stripe.com/pricing` |
| Airwallex | $0.30 + Payment Method Fee（费率未公开） | 同左 | 需询价 | `airwallex.com/en-us/terms/fee-schedule` |

**支付宝「特殊行业」1.0% 的范围**（`b.alipay.com` 官方列举）：网络直播；休闲游戏；网络游戏点卡、游戏渠道代理；游戏系统商；旅游周边服务、交易平台；网游运营商（含网页游戏）。
> AI 音乐生成订阅不属于上述任一类，通常按 0.6% 核定；**最终以签约时支付宝核定的经营类目为准**。

### 1.2 两个必须先排除的陷阱

**陷阱一：连连国际 / PingPong / 万里汇的 0.3%–1% 不是收单费率。**
这些数字属于**跨境收款与结汇提现**（亚马逊、TikTok Shop 等平台货款 → 你的账户 → 换人民币），**不是中国消费者在你网站上付款的收单费率**。两者是完全不同的业务，混用会得出「0.3% 就能收微信支付宝」的错误结论。

**陷阱二：0.38% 的小微商户费率，线上业务用不了。**
微信支付官方声明（`pay.weixin.qq.com/doc/v2/partner/4014115330`）：

> 「目前仅限于餐饮、线下零售、居民生活服务、休闲娱乐、交通出行行业和其他**线下**行业申请小微商户，**暂不支持通过此渠道入驻线上行业（如直播及游戏行业等）**，一经发现，微信支付有权对该商户号进行限制，包含但不限于关闭支付权限。」

本产品为 100% 线上业务，**费率下限即 0.6%**。
（小微商户费率档位：0.38%、0.39%、0.4%、0.45%、0.48%、0.49%、0.5%、0.55%、0.58%、0.59%、0.6%，共 11 档 —— `pay.weixin.qq.com/core/affiliate/micro_fqa` Q8）

---

## 2. 韩国主体的实际处境（本项目的现状）

### 2.1 微信支付：只能走机构模式，且机构模式要求持牌

官方 FAQ（`act.weixin.qq.com/static/merchant_overseas/faq{,_en}.html`）：

- 49 个国家/地区可合规接入，**韩国在名单内**。
- 但分为两种模式：
  - **Merchant Model（直连）**：**仅中国香港、英国、新加坡**。条件 = 合法海外主体 + 自有网站/APP + 不销售禁售品。
  - **Institution Model（机构）**：适用于 49 国，条件 = 合法海外主体 + **持有该国金融服务或资金处理类牌照** + 自有网站/APP。该模式下**微信不与商户签约**，资金清算给机构，机构再清算给商户。
- 官方对无牌照者的指引：「Service providers may cooperate with our institution partners」——即**只能通过持牌机构伙伴间接接入**。

**结论：韩国公司若无支付牌照，不能直接与腾讯签约，必须经由韩国持牌机构，并承担其加价。**

已在册的韩国机构伙伴（官方查询页 `pay.weixin.qq.com/index.php/xphp/v/coversea_partner_search/view_search?type=service_providers&area=410`，2026-09-04 实取）：

KR PARTNERS(Eximbay)、NICE 信息通信、GOMINC、KSNET、Paygate、KICC（韩国信息通信）、SMARTRO、KCP、ICB、Payletter、BC Card、Paystory、COOCON、GLOBAL TAX FREE、KIS 信息通信。

### 2.2 支付宝：Antom 不接受韩国主体

Alipay 官方能力表（`global.alipay.com/docs/ac/pm/summary_capabilities_cp`，已迁移至 `docs.antom.com`）：

| 支付方式 | 收单机构 | **商户主体所在地** | 买家 | 币种 |
|---|---|---|---|---|
| Alipay | AlipaySG/EU/US/UK/JP/HK | **SG、AU、HK、US、EU、UK、JP** | China | CNY |
| AlipayCN | AlipayCN | **CN** | China | CNY |

**KR 不在商户主体列表内 → 韩国主体无法通过 Antom 受理中国用户支付宝付款。**

> 注意方向：Antom 公开资料中关于「韩国」的内容，讲的是**向韩国用户收钱**（Kakao Pay、Naver Pay、KRW），与本项目的需求方向相反。

### 2.3 PayPal：币种表同样排除 KRW，CNY 限本国账户

官方币种表（`developer.paypal.com/reference/currency-codes`）共 **25 种**，**不含 KRW**；`CNY/BRL/MYR` 官方脚注「仅支持 in-country PayPal accounts」（即需当地主体持有账户）。`HUF/JPY/TWD` 为零小数位币种（无小数位）。

**结论**：
- 韩国主体无法通过 PayPal 收取韩元（KRW 不在受理范围）—— 与微信/支付宝的「韩国主体受限」结论一致。
- 面向中国用户收 CNY 需 in-country 账户（中国本地主体），同样受主体约束。
- PayPal 在本项目中的定位是**多币种国际订阅通道**（USD/EUR/HKD/JPY/AUD/…），**不是**中国/韩国本地收单替代。
- 代码已按官方 **Subscriptions API（v1/billing）+ 服务器侧回传验签**实现（`backend/app/billing/pg/paypal.py`）：受理集合默认剔除 CNY/BRL/MYR、不含 KRW；建订阅用 `plan_id`（`PAYPAL_PLAN_IDS` 映射 `tier_key → plan_id`，支持 `"tier:CCY"` 粒度，因官方「一个 plan 只能一种 currency_code」）。

---

## 3. 三个可选方案与权衡

| 方案 | 微信 | 支付宝 | 综合成本 | 优势 | 代价 / 风险 |
|---|---|---|---|---|---|
| **① 新设中国香港公司（推荐）** | 境外**直连**，资金直接清算给你 | AntomHK，**支持 Alipay 订阅支付** | 议价，预计 1.9%–2.5% | 唯一绕开机构加价的境外路径；香港是三个直连地区之一；订阅制产品必需的定期扣款能力具备 | 需注册与维护 HK 公司；费率需商务谈判，官方未公开 |
| ② 新设中国大陆公司 | 0.6% | 0.6% | **≈0.6%，零汇损** | 全路径最低成本；代码已按直连官方 API 写好，几乎无需改造 | 外资主体注册、对公账户门槛；**FLOW 宗教内容在大陆主体下审批风险显著** |
| ③ 维持韩国主体 | 经韩国持牌机构 | Stripe 2.2%+$0.35 或 Airwallex | 最高 | 无需新增主体 | 微信与支付宝走两条互不相干的通道，对账与运营复杂；机构加价叠加 |

### 推荐方案 ① 的三条依据

1. **香港是微信支付全球仅有的三个直连地区之一**，资金直接清算给你，不经持牌机构转手 —— 直接省掉一层加价。
2. **AntomHK 支持 Alipay Subscription Payment（API-only）** —— 见 `docs.antom.com`。本项目是订阅制产品，定期扣款是必需能力，非可选项。
3. 项目已在中国香港部署服务器与业务据点，落地成本最低。

---

## 4. 合规红线（务必在投入开发前处理）

### 4.1 FLOW 的宗教内容不在微信境外合规行业清单内

官方清单存在**中英文版本不一致**，需以书面确认为准：

- 英文官方页（10 类）：Cargo Trade (incl. F&B)、Air Tickets、Hotel Accommodation、Overseas Education、International Conferences、International Logistics、International Car-rental、Travel Tickets、**Software Services**、Medical Services
- 中文官方页（12 类）：货物贸易、航空机票、酒店住宿、留学教育、国际会议、国际物流、国际租车、旅游票务、软件服务、医疗服务、**综合商场**、**办公用品**

**判定**：
- ai-music-platform（AI 音乐生成订阅）→ 可归入「软件服务」，申报合理性较高。
- **FLOW（韩语福音/圣经音频）→ 清单中无对应类目，属宗教内容。必须向 `wxpayglobal@tencent.com` 书面确认是否受理；不得先开发后确认。**
- 若走韩国持牌机构，机构自身同样会做内容合规审查，上述风险不会因换通道而消失。

### 4.2 官方口径不一致项（签约前一律书面确认）

| 事项 | 官方 A 处 | 官方 B 处 |
|---|---|---|
| 结算起付门槛 | 800 美元（`merchant_overseas/faq.html`） | 5000 美元（`merchant/overseas.html`） |
| 合规行业数量 | 10 类（英文页） | 12 类（中文页） |

### 4.3 费用承担

官方原文（英文页）：「WeChat Pay will pay fees to the sending bank… The receiver will receive the amount transferred, **minus the correspondent (intermediary) bank charges**」——
即：境内汇出手续费由微信承担，**中间行与入账行费用由商户承担**。

结算机制：单商户/机构清算资金达到门槛后，微信于 **T+1** 按即期汇率购汇并汇至你的海外银行账户；结算币种含 **KRW、HKD、USD、EUR、GBP、JPY、CAD、AUD、NZD**，不支持的币种以美元为中间币种。

---

## 5. 下一步执行清单

1. **先确认内容合规**：向 `wxpayglobal@tencent.com` 发函，书面确认两件事 ——
   ①「软件服务」类目是否覆盖 AI 音乐生成订阅；
   ② 宗教内容（FLOW）是否属于禁售/不受理范围（同时索取 **Restricted Products 禁销清单**，见 `act.weixin.qq.com/static/merchant_overseas/restrictedproducts.html`）。
2. **并行询价**：
   - 微信：`https://pay.wechat.com/cn` 提交 HK 主体直连申请（需先完成 HK 公司注册）；
   - 支付宝：`docs.antom.com` 申请 AntomHK 商户号，明确索取 **Alipay 费率**与**订阅支付**开通条件。
3. **若暂不新设主体**：向韩国机构伙伴（建议同时联系 3 家，如 KICC / KCP / KSNET）索取收单报价，并要求书面列明「微信费率 + 机构加价」的拆分。
4. **代码侧**（主体确定后执行）：
   - `backend/app/billing/pg/wechat.py`（微信支付 v3 Native 直连）与 `alipay.py`（openapi 直连）走大陆或中国香港主体路径均无需重构。
   - naver / kakao 两个 provider 保留在代码中但**不纳入配置**，默认 provider 切换为 `wechat` 或 `alipay`。
   - 当前后端 **202 passed / 3 skipped / 0 failed**，前端 `tsc --noEmit` 通过，改动前以此为基线。

   > ⚠️ **2026-09-04 更正**：本备忘初版称这两个 provider「已是官方直连结构」，该结论当时**未经实证**，
   > 实际只对了一半 —— 同日对两家官方规范做全量实证后，发现并修复了 4 处偏离，其中支付宝一处为**致命缺陷**：
   >
   > | # | 问题 | 官方依据 | 后果 |
   > |---|---|---|---|
   > | A1 | 支付宝异步通知验签只剔除 `sign`，未剔除 `sign_type` | 官方文档 `opendocs.alipay.com/open/02pa44` 验签第 1 条 + 官方 SDK Java `AlipaySignature.getSignCheckContentV1`、PHP `AopClient.rsaCheckV1` | **真实通知 100% 验签失败 → 用户付款后永不升档** |
   > | A2 | 微信回调完全忽略 `Wechatpay-Serial` | `native_notify.md` 2.1；官方 SDK `wechatpay-java NotificationParser` 对 serial 缺失直接抛异常 | 无法按官方要求选择「微信支付公钥」还是「平台证书」验签，密钥错配时静默失败无诊断 |
   > | A3 | 两家回调均未核对实收金额 | 支付宝验签步骤 5（「务必忽略」）；微信解密字段 `amount.total` | 金额不符仍升档 |
   > | A4 | 微信 `description` 未截断 | `native_prepay.md`：`string(127)` | 超长被拒单 |
   >
   > 修复已落地并回归通过（+6 条测试）。**教训**：「代码看起来对」不等于「已与官方规范核对过」，
   > 支付链路的参数必须以官方文档原文或官方 SDK 源码为准，不得凭记忆或惯例。

> ⚠️ **2026-09-05 增补：PayPal provider 已接入**（`backend/app/billing/pg/paypal.py`）。
> 基于 `developer.paypal.com` 官方 `.md` 全量实证（OAuth2 取 token / 建订阅 / 服务器侧回传验签 / 订阅事件 / 币种表），关键事实：
> - 回调验签采用**服务器侧回传**（`POST /v1/notifications/verify-webhook-signature`，`verification_status==SUCCESS` 才通过），与所有其它 PG 的本地 HMAC 不同；
> - 币种硬约束：官方 25 币种表**不含 KRW**，`CNY/BRL/MYR` 限 in-country 账户 → 默认受理集合剔除这三者；
> - `PAYMENT.SALE.*` 订阅 ID 字段名是 `billing_agreement_id`（**非** `subscription_id`）；
> - 建订阅 `user_action=SUBSCRIBE_NOW` 买家批准后自动激活；`checkout_url` = `links[].rel=="approve"` 的 href。
> 后端随 PayPal 新增 11 条用例，基线更新为 **213 passed / 3 skipped / 0 failed**，前端 `tsc --noEmit` 仍 EXIT 0。

---

## 附录：本备忘全部官方来源

| 结论 | 来源 |
|---|---|
| 微信境内费率 0.6% | `pay.weixin.qq.com` 交易账单文档（示例「费率 0.60%」）；`developers.weixin.qq.com/minigame/product/mini-store/shoptypedifferences.html` |
| 小微商户 0.38%–0.6% 十一档 + 线上行业排除 | `pay.weixin.qq.com/core/affiliate/micro_fqa`；`pay.weixin.qq.com/doc/v2/partner/4014115330` |
| 支付宝 0.6% / 1.0% | `b.alipay.com/page/product-mall/product-detail/I1080300001000041949` |
| 支付宝 JSAPI 0.6%–1% | `opendocs.alipay.com/mini/053llc` |
| 微信境外 49 国 / 直连仅港英新 / 机构需牌照 / 结算 T+1 / 费用分担 | `act.weixin.qq.com/static/merchant_overseas/faq.html` 与 `faq_en.html` |
| 韩国机构伙伴名录 | `pay.weixin.qq.com/index.php/xphp/v/coversea_partner_search/view_search?type=service_providers&area=410` |
| Antom 商户主体不含 KR | `global.alipay.com/docs/ac/pm/summary_capabilities_cp`（最新内容已迁 `docs.antom.com`） |
| AntomHK 支持 Alipay 订阅支付 | `docs.antom.com`（Alipay — Subscription Payment: For Acquirer AntomSG and AntomHK） |
| Stripe 2.2% + $0.35 | `stripe.com/pricing` |
| Airwallex $0.30 + Payment Method Fee | `airwallex.com/en-us/terms/fee-schedule` |
