"""亚洲本地支付供应商实现 (GAP-004 step-4).

各文件一家 PG, 全部实现 app.billing.provider.PaymentProvider 接口:
  kakao.py  — KakaoPay  (KRW, ready→approve 同步终审, 无 webhook)
  naver.py  — NaverPay  (KRW, 前端 JS SDK handoff + 服务端 approve)
  wechat.py — WeChat Pay v3 (CNY, Native 下单 + 回调验签/AES-256-GCM 解密)
  alipay.py — Alipay    (CNY, alipay.trade.page.pay + RSA2 签名/异步通知验签)

零新增依赖: httpx + cryptography 均已在 requirements.txt(被 orchestrator / security.crypto 使用).
"""
