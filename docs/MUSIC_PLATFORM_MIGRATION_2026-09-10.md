# 音乐平台迁入 FLOW + 数据库合一（2026-09-10）

## ⚠️ SUPERSEDED（部分）— 已被 ISSUE #72 决策取代（2026-10-11）

> **本文的「数据库合一 / 与 FLOW 共享 `.gospel.db`」结论已被正式推翻。**
> 用户于 2026-10-11 在 ISSUE #72 拍板 **决策 1 = A 独立库 + 窄桥**：
> 音乐平台与 FLOW **各库各主权，不共享 DB**；用户身份经 token 交换
> （`flow-token` / `flow-exchange`，scope=`sync:flow`）+ `user_links` 映射表关联。
>
> - 受影响项：本文「决策 / 迁移结果」表中的 **`数据库 → .gospel.db（与 FLOW 共享）`** 一行。
> - 连带清理：`UserLink.music_user_id` 的 `ForeignKey("users.id")` 已降级为**普通列**
>   （提交 `b8e4f1a2c3d7`）——跨库 FK 不成立。
> - **原文保留**（不删），仅作历史记录与决策轨迹。
>
> 取代指针：ISSUE #72；相关收敛见 `docs/MUSIC_TAB_MERGE_PLAN_2026-09-21.md`
> 与 `docs/PLAN_MUSIC_APP_2026-09-28.md` 头部同类 banner。

## 决策
用户拍板：只保留 FLOW 一个项目文件夹，korean-gospel-ai 文件夹将被删除。
ai-music-platform 整体迁入 FLOW，并与 FLOW 共享数据库（co-location D5 落地）。

## 迁移结果

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 代码位置 | `C:/Desktop/korean-gospel-ai/ai-music-platform`（子目录，历史在 korean-gospel-ai 仓库） | **`C:/Desktop/Project/FLOW/ai-music-platform`**（独立 git 仓库，6 提交完整历史） |
| 数据库 | `backend/app.db`（私有 SQLite） | **`C:/Desktop/Project/FLOW/.gospel.db`**（与 FLOW 共享） |
| git 历史 | 27 提交中仅 6 个涉及音乐平台 | `git subtree split -P ai-music-platform` 抽取为独立历史（head `7aeaa50d`） |
| remote | korean-gospel-ai（GitHub） | **无 remote**（推送目标等用户决定） |

## 关键事实（实测）
- 迁移用 robocopy 复制（源目录被树级句柄锁住无法 rename——3276 目录/23691 文件/663MB，0 失败）。
- 旧仓库 27 提交先 `git bundle` 全量备份再 split。
- 音乐 11 表（users/songs/song_likes/song_favorites/song_ratings 等）由音乐迁移链
  `alembic upgrade head`（head `7f0c40c8bae2`）在 .gospel.db 上创建（version_table=alembic_version_music，
  与 FLOW 的 alembic_version_flow 隔离——X3 已实现，tests/test_colocation_d5_shared_kernel.py:590）。
- 数据按**列名交集** INSERT 搬运，11 表行数对账全 OK，src_only/dst_only 列差异全空。
- 决定性验证：新路径后端注册探针用户 → `.gospel.db` users 8→9（探针已删）。
- FLOW colocation 门禁 32/32 passed（MUSIC_PLATFORM_REPO=C:/Desktop/Project/FLOW/ai-music-platform）。

## 配置变更
- `ai-music-platform/backend/.env` → `DATABASE_URL=sqlite:///C:/Desktop/Project/FLOW/.gospel.db`（根 .env 同步）。
- FLOW `.gitignore` → `/ai-music-platform/`、`/backups/`（两仓库并存，FLOW 主仓库不吞音乐平台）。
- 服务端口不变：音乐 8001/3000，FLOW 8000/8501。

## 备份（回滚点，均在 `C:/Desktop/Project/FLOW/backups/`）
- `korean-gospel-ai-full-20260910.bundle`（旧仓库全部分支完整历史）
- `music-backend-app-20260910.db` / `music-root-app-20260910.db`（迁移前两库快照）
- `music-backend-env-20260910.bak` / `music-root-env-20260910.bak`

## 遗留提醒
- korean-gospel-ai 文件夹内**另有복음 AI 本体**（backend/admin/mobile/android/payment-gateway/qdrant_local 等），
  删除前请确认这些不再需要；ai-music-platform 已完整迁出，可安全删除。
- 音乐平台根目录旧 `app.db`（106KB, Sep 3）是早期库，未迁移（backend/app.db 才是活跃库）。
- 推送：音乐平台 5 个未推送提交现在位于 `C:/Desktop/Project/FLOW/ai-music-platform`（分支
  feature/company-quality-2026-05-26，无 remote）——是否推送、推到哪里由用户决定。
