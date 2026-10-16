# 10 · 开源最佳方案选型与规划（实测事实版）

> 来源：用户指令「找出实际的 opensource 上的最佳方案，继续规划先」（2026-10-16）。
> 方法：**不凭记忆**，逐仓用 GitHub REST API 拉真实字段（license.spdx_id / stargazers / pushed_at / archived），数据时点 **2026-10-16**。
> 约束（用户既有决策）：方案要**上传到服务器供他人使用**（网络服务）→ **许可证是第一优先轴，能力其次**。

## 1. 实测总表（真实数据，非推测）

| 项目 | 真实许可 | Stars | 最近 push | 语言 | 归档 | 定位 |
|---|---|---|---|---|---|---|
| **Audiveris** | **AGPL-3.0** ⚠️ | 2,889 | 2026-10-14 | Java | 否 | OMR 引擎（成熟、批处理 CLI） |
| **oemer** | **MIT** ✅ | 807 | 2025-04-18 | Python | 否 | 端到端 OMR（fork；父仓 meteo-team/oemer 已归档） |
| **SMT**（Sheet Music Transformer） | **MIT** ✅ | 86 | 2026-09-27 | Python | 否 | DL OMR（ICDAR 2024，HF 权重） |
| **homr** | **AGPL-3.0** ⚠️ | 433 | 2026-10-15 | Python | 否 | 拍照谱 → MusicXML（很活跃） |
| **music21** | **BSD-3-Clause** ✅ | 2,602 | 2026-10-13 | Python | 否 | 乐谱分析/解析工具包 |
| **Verovio** | LGPL-3.0 | 950 | 2026-10-16 | C++ | 否 | MusicXML/MEI → SVG/PDF 渲染 |
| **MuseScore** | GPL-3.0（NOASSERTION） | 15,252 | 2026-10-16 | C++ | 否 | 记谱软件（CLI 可转 PDF/MIDI） |
| **RapidOCR** | **Apache-2.0** ✅ | 8,156 | 2026-10-16 | Python | 否 | OCR（ONNX，自带模型） |
| **DocAligner** | **Apache-2.0** ✅ | 116 | 2026-01-13 | Python | 否 | 文档四角/边界 |
| Tesseract | Apache-2.0 ✅ | — | — | C++ | 否 | OCR（需系统二进制） |

## 2. 🔴 关键发现：许可证轴 → 现有基线必须修正

**`plan/02_tool_selection_matrix.md:10` 把 Audiveris 记为「GPL，开源」——实测为 `AGPL-3.0`。**
这不是文字差异，而是**质变**：

- **AGPL-3.0 §13（网络条款）**：若用户通过网络与「本程序」交互，必须向其提供**对应源码**（含修改版/衍生版）。
- 对「**上传服务器给别人用**」的服务形态，把 AGPL 组件**编入对外服务**是最危险的做法（可能触发整体源码披露义务）。
- **缓解路径（法务灰区，非零风险）**：以**独立进程/独立容器、不做任何修改**的方式调用，仅取输出（MusicXML）——通常不构成衍生（arm's-length）。但这需要法务确认，且不能作为默认策略。
- **结论（我的决策）**：**对外服务栈一律只用 MIT/BSD/Apache**；AGPL 组件（Audiveris、homr）**降级为内部离线评估/对照工具**，不进对外服务。仓库矩阵的「Audiveris = 基线」需同步修正（见 §6）。

> 换句话说：**"最佳 OSS" 在本项目里首先意味着"许可干净"**——能力再强，AGPL 编进 SaaS 就是埋雷。

### 2.1 「去拿对方许可」可行性（2026-10-16 核实）

用户问：「拿到对方认可就可以用了吗？」——**方向对，但有三道关**：

| 关 | 事实 | 影响 |
|---|---|---|
| ① **"对方"是谁** | Audiveris 仓根**只有 `LICENSE`**（AGPL-3.0），**未见 CLA / CONTRIBUTING**；贡献者各自保留版权 | **单个维护者的同意不覆盖其他贡献者代码** → 要谈须覆盖**全部版权人**（实操上极难，除非项目有 CLA 把权利集中到某一实体） |
| ② **有没有现成商业许可** | 检索未见 Audiveris 提供双许可/商业许可（项目自述"published under the AGPL V3"）；第三方 `audiveris-api` 包装器同样是 AGPL | **买不到"一纸授权"** → 只能走"逐版权人书面例外"，或走隔离方案 |
| ③ **义务是什么** | AGPL 的义务是**提供源码**，不是禁止使用 | 三条路：**(a) 内部使用**（不对外提供网络交互）→ 零义务；**(b) 对外服务** → 触发 §13；**(c) 隔离方案**：**不修改**、独立进程/容器调用、仅取输出（arm's-length）＋ 对外提供 Audiveris 源码 → 社区通行做法，但**属法务灰区** |

**结论（我的建议）**：**不要为许可谈判卡住产品**。MIT 的 oemer/SMT **零谈判、零义务**，先用它们跑精度；只有当实测确认 oemer/SMT 精度不足**且**必须 Audiveris 时，再二选一：**(c) 隔离方案**（工程可做，法务需确认）或**逐版权人书面许可**（成本高、覆盖难）。
> 注：以上为工程侧合规判断，非法律意见；商业化上线前应经法务复核。

## 3. 最佳栈（逐槽位决策）

| 槽位 | 选定（许可安全） | 为什么是它 | 弃用/降级 |
|---|---|---|---|
| **OMR 主引擎** | **oemer（MIT）** | 已在本项目**实跑通过**（song01：线距/歪斜/符号/密度全部实测）；MIT 可商用；onnxruntime CPU 即可 | Audiveris（AGPL）→ 内部对照；homr（AGPL）→ 弃 |
| **OMR 第二引擎（共识/升级）** | **SMT（MIT，活跃）** | DL 端到端（无五线谱线检测依赖，抗畸变）；MIT；2026-09 仍活跃；HF 权重可下载 | — |
| **MusicXML 解析/校验** | **music21（BSD-3）** | 2.6k stars、3 天前仍 push；BSD 极友好；小节/音长/拍号校验现成 | 自写解析（不必要） |
| **渲染/PDF 校验** | **Verovio（LGPL，独立进程）** | MusicXML→SVG/PDF，用于"渲染即校验"（渲染失败=结构错） | MuseScore CLI（GPL）→ 仅内部用 |
| **OCR（文字/字高/页码）** | **RapidOCR（Apache-2.0）** | **已集成并实测**（94% 高置信、字高 47px、内容余量 3.99%）；无需系统二进制 | Tesseract（Apache）→ 服务器可并行装，作第二票 |
| **裁切/文档边界** | **DocAligner（Apache-2.0）** | 四角检测，ONNX 推理；Apache 许可 | 纯 CV 余量法（已实现，作降级） |

**许可净结果**：对外服务只含 **MIT(oemer/SMT) + BSD(music21) + Apache(RapidOCR/DocAligner) + LGPL(Verovio，动态链接/独立进程)** —— 无 AGPL、无 GPL 传染。

## 4. 分阶段规划

### P0 — 已完成（本轮之前）
- G1 七项自动化（`g1_auto.py` + `--auto`）· RapidOCR 集成 · 准确率测量骨架（`g1_accuracy.py`，false_pass=0 红线 + 样本护栏）。

### P1 — 许可合规栈固化（已完成，2026-10-16）
1. ✅ 修正 `02_tool_selection_matrix.md` §1（AGPL 事实 + 对外服务禁用 AGPL）。
2. ✅ `oemer` 钉版固化：`onnxruntime==1.20.0` + `bbox.find_lines` 补丁（`plan/patches/`）。
3. ✅ **music21 接入**：MusicXML → 小节/音长/拍号一致性校验（G2 可复用）。
4. ✅ 服务镜像（`deploy/omr-qa/Dockerfile`）：oemer + RapidOCR + DocAligner + music21 + Tesseract；**检查点/模型预置入镜像**。
5. ✅ **CI 固化（#165）**——见下节。

### P1-5 — CI 已固化（#165，2026-10-16）

**门禁不再靠人肉执行**：仓库根 `.cnb.yml` 把两类门禁钉进流水线。

| Stage | 触发 | 做什么 | 实测耗时 |
|---|---|---|---|
| **快活** `gate-fast` | `pull_request` + `push main` | 单测 93 例（`test_g1_auto` / `test_check_licenses` / `test_g1_image_quality`）+ 静态许可门禁（`--requirements`） | **< 60s**（容器 `python:3.12-slim`，无重依赖） |
| **重活** `gate-heavy` | **仅** `api_trigger`（手动） | 装 79 包对外栈 → `apply_oemer_patch.py` → 动态许可门禁（`--env`）→ `smoke_cv.py` CV 冒烟 | ~2.4 min（装栈为主） |

**设计要点（每一条都是踩过坑换来的）**：

- `.cnb.yml` **必须在仓库根**；放 `.cnb/` 目录内会注册失败 `[CONFIG_YAML_EMPTY]`。
- **重活不进 push 路径**：否则每次提交都要装 79 个包，把反馈周期从秒级拖到分钟级。
- **门禁失败必须非零退出**：全文件禁止 `|| true` / `allowFailure: true` —— 否则门禁只是"绿灯装饰"。
- **脚本不得依赖 `git`/`node`**（slim 镜像里没有）；需要状态就用读文件断言。
- 抽出可复用冒烟脚本 `deploy/omr-qa/smoke_cv.py`，**CI 重活 stage 与 Dockerfile 构建期共用**同一断言，避免两处各写一份而漂移。

**`smoke_cv.py` 为什么必要**：`collect_metrics(..., engines=("cv",))` 的语义是**降级不抛出** ——
引擎挂了只往 `m.errors` 里记一笔，照常返回近乎全空的对象，判定层的 `SKIP` 也不会计入失败。
没人查 `errors`，"引擎坏了"就静默滑过。本脚本只做一件事：**跑一次 cv，断言真测到了线距**
（`unit_size_px is not None` 且 `errors` 为空），否则非零退出。

**opencv 策略（`opencv-python-headless`）**：无头镜像里装非 headless 的 `opencv-python`，
`import cv2` 会炸 `libxcb.so.1: cannot open shared object file`（无 X11 运行时）。
本栈钉 **`opencv-python-headless`**（`requirements-omr.txt` 已标注 Apache-2.0），
而 `smoke_cv.py` 就是这条策略的**可执行断言**——换回非 headless 版本，CI 立刻红。
`smoke_cv` 对 song01 实测线距 **18.00px**，与 oemer 的 18.32px 交叉一致（#163/#164 基线）。

### P2 — 双引擎共识（提准确率）
5. **SMT（MIT）** 接入为第二 OMR：与 oemer 独立判定；一致→高置信，分歧→NOTE 不放行（对齐 09 §5 的 L2）。
6. **L1 合成标定集**（`g1_calibrate.py`）：注入已知真值（线距/歪斜/系统数），零人工、上千样本 → 让 99% **可测量**。

### P3 — 服务化与合规收尾
7. 多用户并发/超时/缓存/压测；`/api/score/assess` 端点。
8. `logs/license_notes.md`（#66）登记全部组件许可与来源；对外服务**不含 AGPL/GPL 组件**的自动化断言（CI 检查）。
9. 渲染校验链（Verovio 独立进程）。

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| AGPL 组件被误编入对外服务 | CI 断言：镜像内**不得**出现 audiveris/homr；许可清单进 `logs/license_notes.md` |
| oemer 上游活跃度下滑（fork，父仓已归档） | 钉版 + 本地补丁已具备；SMT(MIT) 作为第二引擎，避免单点 |
| oemer 老检查点与新 ORT 不兼容 | 已实证：钉 `onnxruntime==1.20.0`；镜像内固化 |
| 99% 被空口声明 | `g1_accuracy.py` 护栏：样本<100 → `INSUFFICIENT_SAMPLES`；`false_pass>0` → 直接不合格 |
| 视觉模型在多语言（韩/中）歌词上偏差 | RapidOCR + Tesseract 双票；分歧记 NOTE |
| **oemer 输出缺拍号**（实测：MusicXML 中 `<time>` 计数 = **0**，仅有 divisions/key/clef/staves） | `score_xml_validate.py` 已改为**如实报 `PASS_WITH_NOTES` 并给出覆盖 0/23**（不静默通过）；G2「小节音长=拍号」须**从源补拍号**（manifest/曲首标记或人工确认）——已列为 G2 前置 |
| `test_probe_ace_mcp.py` 既有**不稳定**（三轮失败集 4/4/3 各不相同，依赖活体 ACE MCP 进程时序） | 与本栈无关；单独排查（超时/启动时序），或加 `skipif(无 ACE MCP)` |

## 6. 与既有文档的关系
- `02_tool_selection_matrix.md:10`（Audiveris=基线）→ **本文件 §2 予以修正**（许可事实 + 对外服务禁用 AGPL）。
- `08_g1_auto_omr_solution.md` §3（组件清单）→ 本文件为其**许可校正版**；技术实现不变。
- `09_human_to_auto_decisions.md` §5（99% 协议）→ 本文件 P2 为其落地路径。

## 7. 待办清单

### P1 已完成（2026-10-16）
- [x] P1-1 修正选型矩阵许可事实（`02_tool_selection_matrix.md` §1：AGPL 事实 + 降级 + 基线改判 oemer/SMT）
- [x] P1-2 钉版与补丁固化：`deploy/omr-qa/requirements-omr.txt`（逐行标许可）+ `deploy/omr-qa/apply_oemer_patch.py`（幂等，位点变了响亮失败）
- [x] P1-3 music21 接入：`tools/checks/score_xml_validate.py`（结构校验；**如实报覆盖**）
- [x] P1-4 服务镜像：`deploy/omr-qa/Dockerfile`（**构建期三次断言**：静态许可 + 动态许可 + CV 冒烟）
- [x] P3-8（提前）许可断言工具：`tools/checks/check_licenses.py`（静态 12/12 OK；动态真实环境 40 OK/0 违规；35 用例）

### 待办
- [x] P1-5 装 Tesseract 后补齐 OCR 第二票（`--env` 复扫）—— 重活 stage 已含动态 `--env` 门禁
- [x] **P1-6 CI 固化（#165）**：根 `.cnb.yml`（快活门禁自动跑 + 重活走 `api_trigger`）+ `smoke_cv.py` 复用断言
- [ ] P2-5 SMT（MIT）接入为第二 OMR → 双引擎共识
- [ ] P2-6 L1 合成标定集生成器 `g1_calibrate.py`（注入已知真值，零人工，上千样本）
- [ ] P3-7 服务化：并发/超时/缓存/压测；`/api/score/assess`
- [ ] P3-9 渲染校验链（Verovio 独立进程）
- [ ] G2 前置：**补拍号**（oemer 输出无 `<time>`）
- [ ] 排查 `test_probe_ace_mcp.py` 不稳定性（与本质检栈无关）
- [ ] 法务口径确认：若确需 Audiveris 能力，仅限"独立未修改进程 + 输出即数据"（§2.1）
