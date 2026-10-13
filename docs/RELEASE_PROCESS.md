# 发布流程与版本号约定

> 适用范围：FLOW CCM（音乐，`flow.237/FLOW-MUSIC`）。
> 关联：[`CHANGELOG.md`](../CHANGELOG.md) · [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-CN/1.1.0/) · [SemVer 2.0.0](https://semver.org/lang/zh-CN/)

## 1. 单一真相来源

| 内容 | 唯一来源 | 说明 |
|---|---|---|
| 面向用户的变更说明 | 根 `CHANGELOG.md` | 人写的，按版本归集 |
| 当前版本号 | `frontend/package.json` 的 `version` | 发布时**必须是**最后一条 changelog 的版本号 |
| 版本结构（给程序读） | `backend/app/routers/release.py` | 从上述两者同步而来，不是第三份真相 |

**不要**在别处再抄一份版本号。已经出现过「`package.json` 写着 `0.1.0`、changelog 根本没这一版」
这种对不上的情况 —— 这类漂移是本节存在的唯一理由。

## 2. 版本号怎么定（SemVer，面向「用户能感知的变化」）

| 段 | 什么时候 +1 | 例子 |
|---|---|---|
| **MAJOR** | 用户必须改习惯才能继续用：删功能、改数据模型导致旧数据不可用、鉴权方式变更 | 移除旧版下载格式 |
| **MINOR** | 加功能，向后兼容 | 新增搜索/发现、下载格式可选、播放列表 |
| **PATCH** | 只修 bug、只改文案/样式/性能，不加功能不改契约 | 修迁移多 head、修过期断言 |

判断口诀：**用户会不会发现「多了东西」？** 会 → MINOR；只是「不疼了」 → PATCH。

补充约定：

- `0.y.z` 阶段（当前）：`y` 每加一批用户可见功能 +1，`z` 修 bug +1。
- **日期不预填。** 版本的日期取该版本**最后一个提交的实际日期**（`git log -1 --format=%cI <tag>`），
  不许写「计划发布日期」。
- 仓库历史上没有 tag 也没有 `[0.1.0]` 章节（见 CHANGELOG 末尾说明）——
  不要在回填时凭空造一个 0.1.0。

## 3. 发一次版的完整动作

```
1. 确认 main 处于可发布状态：CI 绿、alembic 单 head
     cd backend && alembic heads          # 必须只有一行
   （main 曾两次出现多 head，直接导致从零 `upgrade head` 失败，别跳过这步）

2. 写 CHANGELOG.md
   - 把 `## [未发布]` 下的条目整体挪到 `## [x.y.z] - YYYY-MM-DD`
   - 日期 = 今天（或该版本最后一个提交的日期），不是计划日期
   - 每条条目都能指向真实提交/PR；没有出处的条目删掉

3. 同步前端版本号
     cd frontend && npm version <x.y.z> --no-git-tag-version

4. 同步给程序读的版本结构
   - 更新 backend/app/routers/release.py 的 `CURRENT_VERSION` 与 `notes`
   - 单测会校验「package.json.version == release.py 的 CURRENT_VERSION」，
     故意不同步会让测试红（这是设计内行为，不是麻烦）

5. 验证
     cd backend  && pytest tests/ -q --ignore=tests/test_oauth.py
     cd frontend && npx tsc --noEmit && npx next build
     node frontend/tests/mureka-ui-contract.test.cjs

6. 合并到 main；如需 tag，打 `vX.Y.Z`（本仓库当前无 tag 约定，可后补）
```

## 4. 应用内怎么显示

- 页面：`/release-notes`（前端静态内容 = CHANGELOG 的摘要，随包发布，断网可见）。
- 后端接口：`GET /api/release/notes`，返回「应用已安装版本 / 最新版本 / 是否需要更新 / 要点」。
  前端拿到后与内置内容**合并展示**，接口不可用时静默降级为纯静态页 —— 更新日志不该因为
  接口挂了就白屏。
- 判断「有新版本」的口径：`latest_version` 按 SemVer 解析后**大于** `app_version`。
  解析失败的版本号一律视为「无法比较」，不下发 `has_update: true`，避免误报。

## 5. 禁止事项

- ❌ 写没有出处的条目（「优化了若干体验」这类无锚点的话）。
- ❌ 预填未来日期。
- ❌ 把 `[未发布]` 留着不搬，却又改了 `package.json` 的版本号 —— 两边必须同时动。
- ❌ 为了好看造一个从未发布过的历史版本。
