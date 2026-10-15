# env_2026-10-16 · CodeBuddy CLI 安装与 `EADDRINUSE` 端口冲突

> 按 #28 格式填写：现象 / 原因 / 处置 / 回滚点。只追加（L0/L1）。

## 环境

| 项 | 值 |
|---|---|
| 日期 (UTC+9) | 2026-10-16 |
| OS | Microsoft Windows NT 10.0.26200.0 |
| CPU | Snapdragon(R) X Elite - X1E78100 (ARM64) |
| Node | v22.22.2（workbuddy managed） |
| 目标包 | `@tencent-ai/codebuddy-code` v2.168.0 |

## 现象

1. `npm view @codebuddy/cli version` → `404 Not Found - GET https://registry.npmjs.org/@codebuddy%2fcli`
2. 安装成功后：
   ```
   $ codebuddy --version
   2.168.0
   $ codebuddy mcp list
   Error: listen EADDRINUSE: address already in use 127.0.0.1:63752 (use --port <number> to choose another port)
   ```
   `mcp add` / `mcp get` 同样报此错。`netstat -ano` 显示 `127.0.0.1:63752` 处于 LISTENING，PID **21288**。
3. `codebuddy --port 63921 mcp add ...` → `error: unknown option '--port' (Did you mean --port?)`；
   `mcp list --port ...` 同样被拒；环境变量 `CODEBUDDY_PORT` 无效。
   （根命令 `--help` 里确有 `--port <number>`，标注为 "Port for HTTP server (default: auto-assign; interactive mode reuses a stable port)"，但子命令解析器不接受。）

## 原因

- 现象 1：此前 runbook 猜测的包名 `@codebuddy/cli` 不存在；官方包名是 `@tencent-ai/codebuddy-code`（主页 `cnb.cool/codebuddy/codebuddy-code`）。
- 现象 2：CLI 启动时会在 `127.0.0.1` 绑定一个稳定端口（63752）用于本机交互/HTTP server，该端口已被**另一个正在运行的 CodeBuddy 实例**（PID 21288）占用。属运行时端口冲突，**不是安装或 MCP 配置问题**。
- 现象 3：`--port` 未被 `mcp` 子命令接受，暂无可用的命令行绕行方式。

## 处置

1. 安装：`npm install @tencent-ai/codebuddy-code` → 93 个包，bins `codebuddy` / `cbc` / `codebuddy-code`。
   落点 `%USERPROFILE%/node_modules`（因工作目录无 `package.json`，npm 向上查找所致）。
   要进 PATH 的可选动作：`npm i -g @tencent-ai/codebuddy-code`。
2. 确认 CLI 语法（与 issue #23 一致且受支持）：
   `codebuddy mcp add -s project <name> <commandOrUrl> [args...]`，`-t stdio|sse|http`，
   子命令 `add / remove / list|ls / get / add-json`。
3. **绕行端口冲突**：`--scope project` 写入的目标文件就是项目根 `.mcp.json`，故直接写入同等内容完成注册（#23），
   并用 `tools/checks/probe_ace_mcp.py` 从协议层验证连通（#24）。二者覆盖 `mcp list` / `mcp get` 的意图。
4. 未采用：kill PID 21288（会打断正在运行的实例，风险大于收益）。

## 回滚点

| 动作 | 回滚方式 |
|---|---|
| 安装 npm 包 | `npm uninstall @tencent-ai/codebuddy-code`（工作目录 `%USERPROFILE%`）；不改动任何仓库文件 |
| 仓库根 `.mcp.json` | `git rm .mcp.json`；未改全局配置 |
| `install_versions.csv` 回填 | `git revert` 该 commit；仅文档 |

## 附带修正（已同步到 runbook 与 #15）

`%LOCALAPPDATA%/Programs/CodeBuddy CN/bin/buddycn`（v1.106.1）是 **IDE 启动器**，非 CLI，没有 `mcp` 子命令；
排查「codebuddy 未识别」时勿把它当 CLI（见 `triage_cheatsheet.md` S1）。
