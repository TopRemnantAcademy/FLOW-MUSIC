"""GAP-022 开放平台：开发者凭证校验 + 出站 Webhook 投递。

拆成子包而不是塞进 routers/developer.py 的理由：SPF(签名/校验/重试) 是**领域逻辑**，
路由只是它的 HTTP 外壳。投递函数还必须能被 `songs._finalize_song` 直接 import ——
那段代码在后台任务里跑，没有 Request，不应为了触发 webhook 去反向依赖一个路由模块。
"""
