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

## B. MCP 自动化类（#23–#26）— ACE MCP server 已就绪

`ace-mcp-server.exe` 已存在于 `C:\Program Files\ACE Studio\`。项目级注册（CodeBuddy CLI `mcp.json` 示例）：

```json
{ "mcpServers": { "ace": {
  "command": "C:\\Program Files\\ACE Studio\\ace-mcp-server.exe",
  "args": [],
  "env": {}
} } }
```

- #23 注册：把上面块写入项目 `mcp.json`（或 CodeBuddy CLI 配置）。
- #24 验证：ACE Studio 打开项目后，列出工具（只读查询）。
- #25 只读试点：先只读查项目一致性，禁改源/批准本（`automation_safety_rules.md`）。
- #26 副本小改 + 撤销：在 `pilot_copy/` 上试点，验证可撤销。

> 需 ACE Studio **保持打开对应项目**；MCP 连接验证这一步无法无头完成，须用户在场。

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
