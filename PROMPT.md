# PROMPT.md · 用 Vibe Coding 续写本项目

本文件供你 + AI 编程工具（Cursor / Trae / Claude / 通义灵码）使用。每个模块给一段可直接粘贴的提示词。
原则：**契约先行 → 让 AI 按模块写 → 小步提交 → 人工验收**。

---

## 0. 项目背景（给 AI 的上下文）
```
这是一个对标 Suno 的 AI 音乐生成平台 MVP。
- 技术栈：前端 Next.js 14(App Router)+TS+Tailwind；后端 FastAPI+SQLAlchemy；编排层封装音乐 API。
- 当前走 Route A：商业 API（Suno）。重算力在外部 API，本服务只做编排+后处理+审核+存储。
- 服务器在香港，目标用户是中国客户。规划书见 规划/AI音乐平台规划书.md。
- 代码风格：中文注释，函数单一职责，契约见 backend/app/schemas.py 与 Pydantic 模型。
```

## 1. 接真实 Suno API（最重要）
提示词：
```
读取 backend/app/orchestrator/suno.py。当前字段名是常见形态的占位。
请：1) 对照 Suno 官方 API 文档，修正 create() 的请求体与 get() 的响应解析（字段名/嵌套结构）；
2) 处理 401 刷新 token；3) 增加超时与重试；4) 保持 MusicProvider 接口不变。
不要改动 base.py 的接口签名。改完给出自测命令。
```

## 2. 支付接入（微信/支付宝香港商户）
提示词：
```
在 backend 新增 app/routers/billing.py：订阅套餐(标准¥39/专业¥99)、创建订单、支付回调、积分扣减。
参考 backend/app/schemas.py 风格新增 Payment/Plan 模型与 Pydantic 契约。
支付用 Stripe 先打通，再补微信支付跨境方案。给出现金流与 webhook 校验要点。
```

## 3. 音频机审 + 人工复核台
提示词：
```
扩展 backend/app/moderation.py：增加音频 ASR 转写后审核（可接公有云审核 API）。
新增管理后台路由 app/routers/admin.py：列出待审歌曲、通过/驳回、统计。
前端 frontend 增加 /admin 页面（简单表格即可）。
```

## 4. 发现页 / 社区 / 分享卡片
提示词：
```
前端新增 app/explore/page.tsx：按风格筛选的热门歌曲流（后端需要歌曲公开/隐私字段与播放量，先加模型字段）。
新增分享卡片生成（前端用 canvas 把封面+歌名+二维码合成图片，便于朋友圈/抖音传播）。
```

## 5. 多供应商备份（切 Route B：开源模型，真正无宗教限制）
提示词：
```
在 backend/app/orchestrator/ 新增 fal_provider.py 实现 MusicProvider 接口，
调用 Fal.ai/Replicate 上的开源模型（YuE 伴奏 + CosyVoice/RVC 人声）。
在 app/config.py 增加 MUSIC_PROVIDER 选项，client.py 按配置选择。
这样可绕过商业 API 的内容政策，实现"无宗教限制"。保持接口与现有 songs 路由不变。
```

## 6. 成本监控与限流
提示词：
```
新增 per-user 每日生成配额（免费3首/日），Redis 计数；超量返回 402 引导订阅。
增加 API 调用成本埋点（单首推理耗时/费用），写入 metrics 表或日志，供运营看板使用。
```

## 7. 工程化
提示词：
```
把 backend 的 create_all 换成 Alembic 迁移；补 pytest 冒烟测试覆盖 提交→生成→查询 主链路；
前端补充 loading/错误态与移动端适配；docker-compose 增加 backend 服务。
```

---

## 关键契约（AI 写代码以此为准）
- `POST /api/songs/generate` ← `{mode,style,prompt,lyric?,title?,vocal_gender?,make_instrumental}` → `{song_id,status}`
- `GET /api/songs/{id}` → `{id,title,style,audio_url,cover_url,lyric,status,created_at}`
- `GET /api/songs` → 我的作品列表
- 鉴权：`Authorization: Bearer <token>`，注册/登录见 `app/routers/auth.py`

## 已知 TODO（优先处理）
1. `suno.py` 已对接 open.suno.cn 官方协议（2026-08-27 调研），并对 sunoapi.org / gcui-art 响应做容错；后续可补这两家的独立断言。
2. `blocklist.txt` / 完整敏感词库需补齐。
3. 对象存储（COS/S3）仅在需要回源到香港 CDN 时启用 `app/storage/s3.py`。
4. Route A 有内容政策限制；要彻底无限制按模块 5 切 Route B。

## 运行
```bash
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev   # http://localhost:3000/create
```
