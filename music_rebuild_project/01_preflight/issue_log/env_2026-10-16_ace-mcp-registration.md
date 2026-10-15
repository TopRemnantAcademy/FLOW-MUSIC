# env_2026-10-16 · ACE MCP 注册与「CLI 不存在」故障

> 按 #28 格式填写：现象 / 原因 / 处置 / 回滚点。
> 只追加（L0/L1），本条目不改写。

## 环境

| 项 | 值 |
|---|---|
| 日期 (UTC+9) | 2026-10-16 |
| OS | Microsoft Windows NT 10.0.26200.0 |
| CPU | Snapdragon(R) X Elite - X1E78100 - Qualcomm(R) Oryon(TM) CPU（ARM64） |
| ACE Studio | 2.1.8 |
| acestudio-cli | 0.17.0（Remote Control Bridge Surface 17.1） |
| ace-mcp-server | 0.17.0，路径 `C:/Program Files/ACE Studio/ace-mcp-server.exe`（12,417,880 B） |
| CodeBuddy CN 启动器 | `%LOCALAPPDATA%/Programs/CodeBuddy CN/bin/buddycn` v1.106.1 |
| Claude Code | 2.1.201 |
| Java | 17.0.20.8-hotspot |

## 现象

1. `acestudio-cli.exe --version` 输出版本后追加一行：**`ACE Studio is not reachable from here.`**
2. `buddycn mcp add --scope project ace-studio -- <path>` 输出：
   `Warning: 'scope' is not in the list of known options, but still passed to Electron/Chromium.`
   随后 `buddycn mcp list` 仍为空。
3. `buddycn mcp --help` 打印的是 **IDE 启动器**用法（`-d/--diff`、`-n/--new-window`、`--user-data-dir` 等），无 `mcp` 子命令。

## 原因

- 现象 2/3：`buddycn` 是 **CodeBuddy CN IDE 的 Electron 启动器**，不是 agent CLI；未知参数被透传给 Electron，因此 `--scope` 被忽略、`mcp add` 实际未执行。真正的 `codebuddy` agent CLI 本机未安装（PATH 内无 `codebuddy`）。
- 现象 1：**ACE Studio 主程序未运行**，Remote Control Bridge 不可达。`ace-mcp-server.exe` 本身可独立启动（见下方处置证据），但所有 `_read/_edit` 工具需主程序在线才有实际数据。

## 处置

1. 直接对 `ace-mcp-server.exe` 做 stdio JSON-RPC 握手实测（Python 3.13，stdin/stdout 换行分隔）：
   - `initialize` → 返回 `serverInfo = {name: "ACE Studio MCP", version: "0.17.0", description: "Remote Control Bridge Surface 17.1"}`，`protocolVersion = 2024-11-05`，`capabilities = {tools}`
   - `tools/list` → **63 个工具**，形如 `<group>_read / <group>_edit`（`note_edit`、`phoneme_edit`、`lyric_edit`、`track_edit`、`tempo_edit`、`export_edit` …）
   - 结论：二进制与协议可用，问题不在 MCP server 本体。
2. 放弃依赖缺失的 `codebuddy` CLI，改用**项目级 `.mcp.json`**（仓库根），内容：
   ```json
   {"mcpServers": {"ace-studio": {"type": "stdio",
     "command": "C:/Program Files/ACE Studio/ace-mcp-server.exe", "args": [], "env": {}}}}
   ```
   该格式被 CodeBuddy CN IDE / Claude Code 等兼容 `.mcp.json` 的客户端直接读取，等价于 issue #23 所述「GUI 写入 .mcp.json」路径。
3. 用已安装的 Claude Code 验证读取：`claude mcp list` →
   `ace-studio: C:/Program Files/ACE Studio/ace-mcp-server.exe - ⏸ Pending approval (run claude to approve)`
   （配置已被识别；剩余为客户端一次性人工批准动作。）
4. 上游官方 agent skills 已随 server instructions 要求安装，采用**下载而非手写**：
   `https://github.com/BeatMagic/acestudio_agent_plugin` @ `ca4bab84bd4ffae7e776d91204d7b89f91cc4f82`
   → 存入 `00_docs/ace_skills/`（含 `UPSTREAM.txt` 记录源与 commit，禁止本地改写）。

## 回滚点

| 动作 | 回滚方式 |
|---|---|
| 新增仓库根 `.mcp.json` | `git rm .mcp.json` 即恢复无 MCP 状态；无全局配置被改动 |
| 新增 `00_docs/ace_skills/` | `git rm -r music_rebuild_project/00_docs/ace_skills`；为纯新增目录，不动既有文件 |
| 实测握手 | 仅起子进程 + `terminate()`，未写 ACE 工程、未改 ACE 设置 |

## 遗留（需用户侧动作）

- **一次性人工批准**：在客户端内对 `ace-studio` 点批准（Claude Code：运行 `claude` 后批准）。
- **「有输出无声」**：需先启动 ACE Studio 主程序，否则 MCP 工具无工程数据；处置后重测 `project_read` 验证。
- 若后续装上真正的 `codebuddy` agent CLI，可改用 `codebuddy mcp add --scope project ace-studio -- <path>`；效果与 `.mcp.json` 等价，二者保留其一即可。
