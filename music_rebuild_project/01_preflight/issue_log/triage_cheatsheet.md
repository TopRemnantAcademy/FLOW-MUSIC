# 问题诊断速查表（#28）

> 症状 → 处置。五类症状为 #28 指定（来源 `02 §6`）。
> 每条含「首查 / 验证命令 / 处置 / 证据落盘」。
> **标注口径**：`【实测】`= 本机已跑出结果；`【预案】`= 尚未遇到，按协议与文档推导，遇到后回填实测值。
> 只追加（L0/L1）。详细个案写 `env_YYYY-MM-DD_<slug>.md`。

---

## S1 · codebuddy 未识别（命令不存在 / 参数被吞）

| 项 | 内容 |
|---|---|
| 症状 | `codebuddy: command not found`，或命令能跑但参数被忽略并打印 `Warning: 'xxx' is not in the list of known options, but still passed to Electron/Chromium.` |
| 首查 | 该可执行文件到底是 **agent CLI** 还是 **IDE 启动器** |
| 验证命令 | `codebuddy --help`；看输出是 agent 子命令（`mcp add` 等）还是 IDE 参数（`--new-window` / `--user-data-dir`） |
| 处置 | 若是 IDE 启动器（Electron），**它没有 `mcp` 子命令**；改走项目级配置文件 `.mcp.json`（等价 issue #23 所述 GUI 路径），或先安装真正的 agent CLI |
| 证据落盘 | `env_*_<slug>.md`：记录可执行文件路径（隐去个人文件夹，用 `%LOCALAPPDATA%` 等）、版本号、`--help` 关键行 |
| 本机状态 | **【实测】** `%LOCALAPPDATA%/Programs/CodeBuddy CN/bin/buddycn` v1.106.1 是 IDE 启动器，无 `mcp` 子命令；PATH 内无 `codebuddy` |

---

## S2 · MCP 列表缺项（客户端里看不到 ace-studio）

| 项 | 内容 |
|---|---|
| 症状 | 客户端 `mcp list` 为空，或只有别的 server |
| 首查 | 项目根是否有 `.mcp.json`，JSON 是否合法，`command` 路径是否真实存在 |
| 验证命令 | `python tools/checks/probe_ace_mcp.py --json`（先确认 server 本体可握手）；再看客户端 `mcp list` |
| 处置 | 写/修项目根 `.mcp.json`：`{"mcpServers":{"ace-studio":{"type":"stdio","command":"<绝对路径>","args":[],"env":{}}}}`。Windows 路径可用正斜杠。改完重启客户端 |
| 证据落盘 | `.mcp.json` 本身 + `mcp list` 输出行 |
| 本机状态 | **【实测】** 写入后 `claude mcp list` 已显示 `ace-studio: … ⏸ Pending approval` —— 配置已被识别，剩一次性人工批准 |

---

## S3 · connected 无工具（连上了但工具列表为空）

| 项 | 内容 |
|---|---|
| 症状 | server 状态显示 connected / 绿灯，但调用时无可用工具 |
| 首查 | server 是否真的回了 `tools/list`，回了几个 |
| 验证命令 | `python tools/checks/probe_ace_mcp.py --json` → 看 `tool_count`；**0 个工具 = FAIL（退出码 1）** |
| 处置 | ① 确认 `initialize` 里 `protocolVersion` 与 server 支持的一致；② 确认已发 `notifications/initialized`（部分 server 未收到该通知就不给工具）；③ 仍为 0 则换 server 版本或查 server 日志（探针会回传 `stderr` 前 2000 字符） |
| 证据落盘 | 探针 JSON 报告（含 `stderr`） |
| 本机状态 | **【实测】** ACE MCP 0.17.0 返回 **63** 个工具，`audio_plugin_read` … `transport_edit`（命名遵循 `<group>_read` / `<group>_edit`） |

---

## S4 · 读错项目（操作了别的工程 / 空工程）

| 项 | 内容 |
|---|---|
| 症状 | 工具调用成功返回，但内容是另一个工程的数据，或空 |
| 首查 | 宿主里当前聚焦的工程，与 agent 的工作目录是否同一个 |
| 验证命令 | `python tools/checks/probe_ace_mcp.py --call project_read`（**注意**：当前版本探针不发 `tools/call`，仅记录 SKIP；实际读取需在客户端内执行并人工比对工程名） |
| 处置 | ① 客户端里显式打开目标 `.acep`；② agent 工作目录切到 `music_rebuild_project/`（项目级 `.mcp.json` 在仓库根，工作目录错会读到别的工程配置）；③ 每次写操作前先 `project_read` 确认工程名再动手（对应 `risk_register.md` D-05） |
| 证据落盘 | `env_*_<slug>.md`：工程名 + 工作目录 + 上一次工具调用名 |
| 本机状态 | **【预案】** 尚未遇到；ACE Studio 主程序未启动，暂无工程可比对 |

---

## S5 · 有输出无声（有渲染结果/返回值，但听不到声音）

| 项 | 内容 |
|---|---|
| 症状 | 渲染/导出显示成功、文件也生成了，播放无声音或全静音 |
| 首查 | ① 宿主主程序是否在运行（MCP 只是 Remote Control Bridge）；② 音频设备/路由；③ 渲染件本身是否静音 |
| 验证命令 | `acestudio-cli.exe --version`（末尾出现 `ACE Studio is not reachable from here.` 即主程序未运行）；渲染件静音判定用既有 `tools/checks/qc_file_check.py`（#60） |
| 处置 | ① 启动 ACE Studio 主程序后重试；② 检查系统输出设备与 ACE 的音频设备设置；③ 渲染件若本身静音，回到宿主检查轨道增益/独奏/静音与导出范围 |
| 证据落盘 | `env_*_<slug>.md`：主程序是否运行、设备名、`qc_file_check.py` 报告 |
| 本机状态 | **【实测】** `acestudio-cli --version` 输出 `ACE Studio is not reachable from here.`（ACE 2.1.8 / cli 0.17.0） → 主程序未启动。**MCP server 二进制本身健康**（握手 PASS、63 工具），所以此症状先查主程序，不要误判为 MCP 故障 |

---

## 通用留证要求（每条日志必含）

| 字段 | 说明 |
|---|---|
| 日期 | `YYYY-MM-DD`（本地时区注明） |
| OS | 版本字符串 + CPU 架构（本机：`Microsoft Windows NT 10.0.26200.0` / Snapdragon X Elite ARM64） |
| 工具版本 | ACE Studio / cli / mcp-server / 客户端 CLI 各自版本 |
| MCP 命令路径 | **隐去个人文件夹**（用 `%LOCALAPPDATA%`、`%USERPROFILE%` 等变量） |
| 错误信息 | 原文照抄，不改写 |
| 上一工具调用 | 出错前最后一次调用的名称与参数摘要 |

## 回滚点（必写）

每条日志结尾列「动作 → 回滚方式」。本轮已落地的两个动作：
- 仓库根 `.mcp.json`：回滚 = 删除该文件（未改任何全局配置）
- `00_docs/ace_skills/`：回滚 = 删除该目录（纯新增，来自上游 `BeatMagic/acestudio_agent_plugin` @ `ca4bab8`）
