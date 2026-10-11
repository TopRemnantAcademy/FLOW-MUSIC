# FLOW CCM（Suno-like）· MVP 项目骨架

> **品牌（2026-09-11 更新）**：FLOW CCM —— FLOW 主品牌下的音乐子品牌（CCM=Contemporary Christian Music；曾用名 FlowSong、「灵歌 FLOW CCM」，2026-09-11 起统一为 FLOW CCM）。"灵歌"出自圣经（弗 5:19"诗章、颂词、灵歌"）；本仓库原称《AI 音乐平台规划书 v1.1》的落地骨架，采用 **Route A：商业 API 优先**，主引擎 **Mureka（昆仑万维 Skywork AI）** + **Suno 国际版网关** 作 failover，免 GPU、按需付费、快速验证。
> 开发方式：**Vibe Coding**——先用 AI 生成本骨架，后续每个模块按 `PROMPT.md` 由你 + AI 编程工具迭代。

## 架构一句话
- 前端 Next.js（创作页 + 播放器）
- 后端 FastAPI（用户 / 歌曲任务 / 审核）
- 编排层 `orchestrator` 统一封装音乐 API（主引擎 Mureka，Suno 国际版作 failover；可换 Route B 开源模型）
- 重算力在外部 API，香港轻量服务器只做编排 + 后处理 + 审核 + 存储

## 目录
```
.
├── README.md
├── .env.example
├── docker-compose.yml        # postgres + redis（可选）
├── PROMPT.md                 # 各模块续写提示词（vibe coding 用）
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── config.py         # 配置（读 .env）
│   │   ├── db.py             # SQLAlchemy
│   │   ├── models.py         # User / Song
│   │   ├── schemas.py        # 请求/响应契约
│   │   ├── moderation.py     # 歌词机审（敏感词）
│   │   ├── routers/          # auth / songs / flow / admin
│   │   ├── orchestrator/     # Provider 接口 + Mureka/Suno 实现 + client + 路由/计费
│   │   └── storage/          # 对象存储抽象（COS/S3/MinIO）
└── frontend/
    └── app/                  # Next.js (App Router)
        ├── page.tsx          # 落地/发现（占位）
        ├── create/page.tsx   # 创作页
        ├── components/       # CreateForm / Player
        └── lib/api.ts        # 前端 API 客户端
```

## 快速开始

### 0. 准备引擎凭证
- **主引擎 Mureka（昆仑万维 Skywork AI）**：在 `https://platform.mureka.ai/` 注册，控制台生成 `MUREKA_API_KEY` 填入 `.env`（`MUREKA_API_KEY`）。官方 API、按首计费（V9 $0.045/首≈¥0.33），中文+英文质量强、gospel 原生风格、无上游宗教误拦。
- **failover Suno（国际版网关）**：Suno 截至 2026 无公开官方 API，经国际第三方网关访问（如 `sunoapi.org`/`api.suno.ai`，**非** `open.suno.cn` 国内网关）。在 `.env` 填 `SUNO_API_KEY`，基址 `SUNO_API_BASE` 默认 `https://open.suno.cn/api/v1`，切换国际版时改为对应网关基址（见 `app/config.py:32`）。
- 把 `SUNO_API_KEY` 或 `SUNO_EMAIL`+`SUNO_PASSWORD` 填入 `.env`（docker-compose 也已注入，见 `docker-compose.yml` 的 `SUNO_*` 段）。
- ⚠️ 商业 API 有**内容政策限制**（宗教/仇恨/敏感题材会被拦截），这是你选择 Route A 时已接受的取舍；要彻底无限制请按 `PROMPT.md` 切到 Route B（Replicate/Fal 跑开源模型）。

### 1. 后端
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env         # 填好 SUNO_*/JWT_SECRET/STORAGE_*
alembic upgrade head            # 首次/部署建库与 schema 演进（开发也可仅靠 create_all 兜底）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 文档： http://localhost:8000/docs
```

### 2. 前端
```bash
cd frontend
npm install
npm run dev
# http://localhost:3000/create
```
> 前端默认连 `http://localhost:8000`，可在 `frontend/lib/api.ts` 改 `API_BASE`。

## 核心数据流
```
用户填风格/主题/歌词
  → POST /api/songs/generate（先过歌词机审）
  → 建 Song(pending) + 后台任务按路由调 Mureka/Suno API
  → 轮询直到 completed → 写回 audio_url/cover_url/lyric
  → 前端轮询 GET /api/songs/{id} 拿到播放链接
```

## 契约（关键字段）
- `POST /api/songs/generate` → `{mode, style, prompt, lyric?, title?, vocal_gender?, make_instrumental}` → `{song_id, status}`
- `GET /api/songs/{id}` → `{id, title, style, audio_url, cover_url, lyric, status, created_at}`
- `GET /api/songs` → 我的作品列表

## 数据库迁移（Alembic）

schema 演进以 Alembic 为准（不再依赖 `create_all` 自动建表）：

```bash
cd backend
alembic upgrade head     # 应用所有迁移，建库 / 演进 schema
alembic check            # CI 用：校验 models.py 与迁移是否一致（防漂移）
alembic revision --autogenerate -m "描述"   # 改了 models.py 后生成新迁移
```

`migrations/env.py` 复用项目 `Base.metadata`，数据库 URL 取 `ALEMBIC_DB_URL`（测试 / CI 隔离库）或 `settings.database_url`。

## 下一步（按 PROMPT.md 续写）
1. 接支付（微信/支付宝香港商户）
2. 音频机审 + 人工复核台
3. 发现页 / 社区 / 分享卡片
4. 多供应商备份（Fal.ai）+ 成本监控
5. 评估 Route C 自托管降本

> 本骨架为可运行的最小闭环，专注"生成一首歌"主链路；其余功能按 `PROMPT.md` 用 Vibe Coding 扩展。
