# 更新日志

本文件记录 FLOW CCM（音乐）所有**面向用户**的变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

`main` 上尚未发布的改动统一记在 `[Unreleased]` 下，发布时整体挪到新版本号。

## 怎么维护这个文件

1. **每条条目都必须能追溯到真实提交或 PR。** 在条目末尾用 `(#NN)` 或 `` `abcdefg` `` 标注来源。
   写不出出处的条目，就不要写 —— 宁可省略，也不要编。
2. 归类只能用这几个小节：`新增` / `变更` / `修复` / `移除` / `安全` / `文档`。
3. 面向用户的语言：写「能做什么了」，不写「哪个文件改了」。
4. 发布时：把 `[Unreleased]` 改成 `## [x.y.z] - YYYY-MM-DD`，并同步
   `frontend/package.json` 的 `version`；**日期取该版本最后一个提交的实际日期**，不预填。
5. 版本号怎么定、发布怎么走，见 `docs/RELEASE_PROCESS.md`。

## [未发布]

区间：`d003c81`（`0.2.0` 收尾）+ 合并提交 `141b87c` 之后，到 `ff0e101`（2026-10-16）为止的
**全部真实提交**（`git log --oneline d003c81..ff0e101`，去掉合并提交后 44 条）。
括号内为 PR 号或提交短 hash，逐条可 `git show` 复核。**本段尚未发布，日期待发布时再填。**

### 新增

- **产品分析看板**：只读聚合视图，无新表、无迁移 (`c3abdd1`)
- **通知通道**：邮件 + Web Push 双通道，零迁移落地 (`18f5a51`)
- **开发者 API / Webhook**：个人访问令牌 + 出站 SSRF 守卫 + HMAC 签名投递 (`297a2fe`)
- **公开资料页**：他人主页可访问，并修掉「带 token 的请求被静默降级为匿名」的问题 (#83)
- **可访问性**：全站键盘可达、ARIA 标注、对比度达标，配套 lint 与回归用例 (#92)
- **应用内更新日志**：`/release-notes` 页面 (#95)
- **多语言**：韩 / 中 / 英三语本地化，含词典一致性自检与 APK 静态导出修复 (#85)
- **后台运营扩展**：用户管理、财务结算、特性开关 (#91)
- **批量导出**：多选导出 zip + CSV (#94)
- **歌词能力**：歌词编辑器 + LRC 导出 / 导入 (#80)
- **播放器进阶**：波形 seek、A-B 重复、无状态分享链接 (#81)
- **PWA**：可安装 + 离线可用 (#86)
- **独立库 + 窄桥接架构**：token 交换 + `user_links`，不再跨库外键 (#72)
- **PG 就绪**：`psycopg[binary]` 驱动 + 按方言分支的 `connect_args` + Alembic 方言兼容 (`b454687`)
- **部署自动化**：香港服务器代码同步与 PG schema 切换脚本化 (#107)
- **音乐重建（OMR）工具链**：版本命名规范与批准本冻结 (#13)、和弦记录与规范化 (#50)、
  编曲前确认校验 (#48)、MIDI 生成 鼓/贝斯/和弦 (#52/#53/#54)、段落标注 (#51)、
  编曲提示词模板与 Agent 红线 (#57)、混音最小检查 (#58)、成品 WAV + 分轨 stem + 哈希 manifest (#59)、
  QC 文件校验 (#60)、G1 图像质量门 (#31)、G4 最终 QC 检查表、G1–G4 阶段标准与交付物映射
  (`f67714a`、`214e410`、`ff0e101`、`f5e889a`、`643c61c`、`f61cf2e`、`c35912d`、`54f4362`、`fbc50d0`、`663c90d`、`c50309d`、`d6dc48a`、`25f000c`、`d56c517`)
- **工具与文档**：CodeBuddy CLI 安装与真包名、MCP 连接验证、安装版本日志、诊断速查表、
  常见失败与恢复流程 (`6492c53`、`1d1426a`、`de58e12`)

### 变更

- **数据库**：`alembic upgrade head` 在真实 PostgreSQL 上可从零建表；迁移链方言无关化 (`6771963`、#106)
- **测试基线**：后端从 `66 passed / 3 skipped` 扩到 **`948 passed / 7 skipped / 0 failed`**（收集 955 项）；
  新增 OMR 乐谱→音乐端到端沙箱 mock（52 项）与 APK Cookie 同站契约护栏（9 项）
  (`3ff649b`、`test_apk_cookie_samesite.py`)

### 修复

- 修复 `main` 迁移链出现多个 head，`alembic upgrade head` 从零执行直接失败 (#104)
- 修复 `tests/conftest.py` 的 `REPO_ROOT` 未定义导致 `test_cli` 无法收集 (`b88d88a`、#51)
- 修复 2 个过期测试断言（引擎注册判定、JSON 分隔符语义） (`c8846e0`)
- 修复带 token 的请求被静默降级为匿名用户 (#83)
- 修复未定义的主题 token，统一品牌色 (`a262221`)

### 安全

- 出站 Webhook 投递增加 SSRF 守卫与 HMAC 签名校验 (`297a2fe`)
- 上传文件增加文件头魔数校验、体积上限与限流（`routers/songs.py`、`tests/test_upload_security.py`）

### 文档

- 新增 `docs/MOBILE_THIRD_PARTY_NOTICES_ANDROID.md`（`native/package.json` + Gradle 实测推导，非模板）
- 新增 `docs/PLAN_MUSIC_APP_2026-09-28.md`、`docs/PLAN_MOBILE_CROSSPLATFORM_2026-09-29.md`
  （移动端跨平台方案与遗漏审计）
- 新增 `docs/A11Y_CHECKLIST.md`、`docs/PWA_GAP018_RUNBOOK.md`、`docs/MINIMAX_API_COMPLIANCE_2026-09-26.md`
- 新增 `docs/COLOCATION_*`（同机部署方案、验证、缺陷轮次、Mock 测试计划）与
  `docs/SECURITY_SCAN_STALE_2026-09-25*.md`（过期安全扫描留痕）
- 新增 `docs/DEPLOY_HK_CHECKLIST_2026-09-14.md`、`docs/MUSIC_PLATFORM_MIGRATION_2026-09-10.md`、
  `docs/MUSIC_TAB_MERGE_PLAN_2026-09-21.md`、`docs/PLAN_ACCESS_CONTROL_2026-09-11.md`
- 新增 `docs/PLAN_PLAZA_SOCIAL_2026-09-03.md`、`docs/CHINA_PAYMENT_ACQUIRING_DECISION_2026-09-04.md`
- **本段订正**（#154）：`.impeccable.md` 技术栈对齐实测（Next 16 / Tailwind 4）、
  `MOCK_TEST.md` 测试规模刷新为 948 passed、`docs/PLAN_MUSIC_APK_2026-09-13.md`
  标注已失效路径（`mobile/` → `native/`）并指向后继方案

## [0.2.0] - 2026-10-12

本仓库自 `74d0be3`（平台从 FLOW ai-music-platform 子树抽出）起，到 `d003c81`
（GAP-019 CDN 主播放链路）为止的**全部真实提交**汇总。
下列条目逐条对应 `git log` 中的提交，括号内为 PR 号或提交短 hash。

### 新增

- **搜索与发现**：标签建模、按标签/流派/情绪分类浏览、相似作品推荐（附「为什么相似」理由）、
  7 天 / 30 天趋势榜 (#82)
- **音频格式可选**：下载侧支持 mp3 / wav / flac；无无损源时明确报错，不静默降级 (#105)
- **我的作品**：播放列表、文件夹、批量操作、回收站（删除后保留 30 天可恢复） (#108)
- **账号体系**：
  - 邮箱验证、密码重置、两步验证（MFA）、登录失败锁定 (#70)
  - Google / Kakao / Naver 授权码登录，含 state 防重放与邮箱合并防接管 (#99)
- **内容权利与审核**：授权类型（商用 / 非商用）、ASR 真实化、审核队列 (#84)
- **CDN 主播放链路**：音频优先走 CDN，含镜像回源、归档与保留策略 (#87)
- **应用内更新日志**：`/release-notes` 页面，展示各版本要点 (#117)

### 变更

- **数据库**：支持 PostgreSQL（`psycopg` 驱动），SQLite 仍为默认；
  字符集/方言相关行为按方言分支，迁移链在真实 PG 上可从零建表（`b454687`、`6771963`、#106）
- **架构**：落地独立库 + 窄桥接（token 交换 + user_links，不再跨库外键），
  移除 `UserLink.music_user_id` 外键 (#72)
- **续写方向**：`extend` 支持从头部 / 尾部衔接，方向选择贯通前后端 (`fd463fe`)
- **部署**：香港服务器代码同步与 PG schema 切换脚本化，防幽灵文件、防 `create_all` 抢建表 (#107)

### 修复

- 修复迁移链出现多个 head（`alembic upgrade head` 从零执行直接失败）的问题 (#104、#105)
- 修复 `extend_type` 只落在模型层、前后端未接线导致方向选择无效的问题 (`fd463fe`)
- 修复 CDN 降级路径会把无效链接写进歌曲记录的问题 (#87)
- 修复 2 个过期测试断言（引擎注册判定、JSON 分隔符语义） (`c8846e0`)

### 安全

- 认证令牌改存 HttpOnly Cookie，前端不再持有明文令牌 (`d003c81` 之后的合并态，见 `frontend/lib/api.ts`)
- 管理员密钥同样走 HttpOnly Cookie，管理端点拒绝普通用户令牌 (`backend/app/main.py`)
- 镜像回源增加逐跳 SSRF 守卫、指数退避、体积上限 (#87)
- 公开的默认 JWT 密钥（如 `change-me-please`）在启动时告警，签发与校验两侧均直接拒绝，避免任何人伪造令牌 (`backend/app/routers/auth.py`)

### 文档

- 新增 `docs/CDN_ARCHIVE_RUNBOOK.md`（CDN 归档运维手册） (#87)
- 新增 `docs/RELEASE_PROCESS.md`（发布流程与版本号约定）

> 说明：`[0.2.0]` 之前的版本没有可追溯的记录（仓库从 FLOW 子树抽出时只保留了初始提交
> `74d0be3`，更早的历史不在本仓库内），故本文件**没有 `[0.1.0]` 章节**。
> `frontend/package.json` 曾长期停留在 `0.1.0` 占位，不代表真实发布过 0.1.0。
