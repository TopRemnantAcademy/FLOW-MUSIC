# 07 · 剩余 issue 解锁runbook（unblock runbook）

> 来源：#148「解决所有问题」。定位：把剩余 40 个 open 里**依赖本机 / 真实谱面 / 凭证**的部分，
> 逐条给出**一键解锁方法**。本地 agent 已确认的前置：ACE Studio + `ace-mcp-server.exe` 已装、Java 17 就绪、
> cnb 1.16.35 / Node v22.22.2 在 PATH。版本基线见 `../01_preflight/install_versions.csv`。

## A. 本机安装类（#15 / #17–#21）— 需用户机器，已在下方给命令

| Issue | 工具 | 解锁命令 / 动作 |
|---|---|---|
| #15 | WorkBuddy / CodeBuddy CLI | WorkBuddy 已在运行（宿主）。CodeBuddy CLI：`npm i -g @codebuddy/cli`（若包名不符以官方为准） |
| #17 | Audiveris | Java17 已就绪。下载 Audiveris 稳定版 → 解压 → `java -jar audiveris.jar <score>`（桌面/CLI 均可） |
| #18 | MuseScore Studio | 官网稳定版安装；CLI 模式 `musescore -o out.musicxml in.mscz`（无头转换） |
| #19 | Synthesizer V 2 Pro | 官网 14 天试用 + 韩语声库 |
| #20 | REAPER / Ardour | REAPER 评估许可 或 Ardour 开源（#56 DAW Pilot 用） |
| #21 | MT Power Drum Kit 2 + BBC SO Discover | 官网免费下载（MT Power 需邮箱注册） |

> 授权后可交由本地 agent 代装开源项（Audiveris/MuseScore）；GUI/付费/试用项保留用户手动（SOUL 边界：对外动作谨慎）。

## B. MCP 自动化类（#23–#26）— ACE MCP server 已就绪且**已实测连通**

`ace-mcp-server.exe` 已存在于 `C:\Program Files\ACE Studio\`（12,417,880 B）。

### B.0 已落地（2026-10-16 实测，非预案）

| 动作 | 结果 |
|---|---|
| stdio 握手实测 | `initialize` → `serverInfo = ACE Studio MCP / 0.17.0 / Remote Control Bridge Surface 17.1`，`protocolVersion = 2024-11-05` |
| `tools/list` | **63 个工具**（`audio_plugin_read` … `transport_edit`，命名 `<group>_read` / `<group>_edit`） |
| 项目级注册 | 仓库根 `.mcp.json` 已建，server 名 **`ace-studio`** |
| 客户端识别 | `claude mcp list` → `ace-studio: … ⏸ Pending approval`（配置已读到，剩一次性人工批准） |
| 官方 agent skills | 上游 `github.com/BeatMagic/acestudio_agent_plugin` @ `ca4bab8` 已下载到 `00_docs/ace_skills/`（**下载而非手写**） |

一键复测（纯 stdlib，退出码 0=PASS / 1=FAIL / 2=用法错）：

```
python tools/checks/probe_ace_mcp.py            # 默认探测 ACE MCP
python tools/checks/probe_ace_mcp.py --json     # 机器可读（含 stderr 前 2000 字符）
```

### B.1 注册配置（与仓库根 `.mcp.json` 一致）

```json
{ "mcpServers": { "ace-studio": {
  "type": "stdio",
  "command": "C:/Program Files/ACE Studio/ace-mcp-server.exe",
  "args": [],
  "env": {}
} } }
```

> ⚠ 修正：server 名统一为 **`ace-studio`**（本文档旧版写的 `ace` 已作废）。
> ⚠ 修正：`%LOCALAPPDATA%/Programs/CodeBuddy CN/bin/buddycn`（v1.106.1）是 **IDE 启动器**，
> 不是 agent CLI，它**没有** `mcp` 子命令（`--scope` 会被透传给 Electron 并告警）。
> 因此 #23 走「项目级 `.mcp.json`」路径，与 issue 所述 GUI 写入等价。

- #23 注册：**已完成**（`.mcp.json` + skills 下载）；剩客户端一次性批准动作。
- #24 验证：ACE Studio 打开项目后，用 `probe_ace_mcp.py` 复测 + 客户端内只读查询。
- #25 只读试点：先只读查项目一致性，禁改源/批准本（`automation_safety_rules.md`）。
- #26 副本小改 + 撤销：在 `pilot_copy/` 上试点，验证可撤销。

> 需 ACE Studio **主程序保持运行并打开对应项目**。当前 `acestudio-cli --version` 报
> `ACE Studio is not reachable from here.` —— 即主程序未启动；**这不是 MCP 故障**
> （server 二进制握手 PASS）。故障速查见 `../01_preflight/issue_log/triage_cheatsheet.md`，
> 个案记录见 `../01_preflight/issue_log/env_2026-10-16_ace-mcp-registration.md`。

## C. OMR→谱面→声乐管线（#29–#47）— 需真实谱面输入

`02_source/` 当前为空（仅 .gitkeep）。解锁动作：
1. 用户把首曲原谱（PDF/图片）放入 `02_source/<song_id>/`（只读）。
2. 之后 A→F 全流程按 `01_pipeline_and_gates.md` 跑； Audiveris(#32/#33/#34) → MuseScore(#35/#36) → G2(#37) → ACE(#39/#43) → G3(#46) → 混音/QC(#59/#60/#61) → G4。
3. 每步用 `tools/` 既有脚本自证；精度只由测量报告声明（P5）。

> 无输入谱面前，OMR/审校/声乐执行类 issue 无法关闭（禁无据关闭）。

## D. DAW Pilot（#56）
依赖 #20（REAPER/Ardour 装好后）跑 8 小节 A/B，选型记 `02_tool_selection_matrix.md` §4。

## E. 凭证类（#74）— 仅用户可提供
- 5 个 PG 真实凭证（微信/支付宝/Kakao/Naver/PayPal）+ `MUREKA_API_KEY` → 走环境变量/密钥管理，禁入仓库。
- 到位后本地 agent 继续 #74 任务 2/3（付费 E2E + Mureka 生成）。

---

**结论**：剩余 open 的全部解锁方法已落此文档。A/B 需用户机器或授权；C 需谱面；E 需凭证。三者到位后即可全绿。
