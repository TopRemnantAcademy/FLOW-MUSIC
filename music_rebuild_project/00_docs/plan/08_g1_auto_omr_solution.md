# 08 · G1-2~G1-7 自动判定方案（去人工 · 服务器可部署 · 开源复用）

> 来源：本轮对话（2026-10-16），用户指令「不要让人参与，找出开源项目」「以后是要上传到服务器上，让别人也用的功能，能下载的就下载 opensource，能改过来用的就下载改动一下用」。
> 上游：`gates/gate_G1.md`（**阈值唯一来源**）、`plan/02_tool_selection_matrix.md`（**OMR 基线已定**）。
> 定位：把 G1 中 7 项「须人工补判」改为**零人工自动判定**，并作为**服务器对外开放功能**（多用户）部署；组件一律**下载开源 + 适配复用**，不从零手写。

## 0. 一句话结论

以**已定基线 Audiveris + Tesseract（Audiveris 的强制依赖）**为主引擎，以**oemer + DocAligner（pip 即得）**为程序化交叉校验，即可把 7 项 SKIP 全部换成自动判定；全部组件既能在 Windows（本机 x64 兼容层 + 骁龙 NPU 路径）安装，也能在 Linux 服务器 apt/pip 一键装 —— 可打包为**单个 Docker 镜像**作为多用户服务。

## 1. 对齐既有决策（不另起炉灶）

- `plan/02_tool_selection_matrix.md:10`：**Audiveris = OMR 基线**（GPL，开源，本地可脚本化，零成本，可复现）→ 本方案**必须以 Audiveris 为首选**，oemer 只作交叉校验/程序化补充。
- Audiveris 官方 README：**Tesseract is mandatory for both building and running** → OCR 与本方案共用，零额外决策成本。
- `plan/02_tool_selection_matrix.md:62`：许可与来源须记 `logs/license_notes.md`（#66 第 7 项）→ §3 已给许可清单，落地时同步登记。

## 2. 检查项 → 引擎 → 精确测量 → 阈值

阈值全部引自 `gates/gate_G1.md`（不得在此重复定义新阈值）。

| 检查项 | gate 编号 | 首选引擎 | 精确测量（可复现） | 阈值（gate_G1.md） |
|---|---|---|---|---|
| 五线谱线距 ≥ 8 px | G1-1 子项 | **Audiveris**（GRID 步骤）→ 备选 **oemer** | Audiveris `.omr` XML 的 staff line y 序列差分；oemer `oemer.utils.get_global_unit_size()`（源码：`utils.py`） | ≥8px（放宽 ≥6px → PASS_WITH_NOTES） |
| 歪斜角 | G1-2 | **Audiveris**（deskew 步骤输出的校正角）→ 备选 **oemer** | oemer `Staff.slope`（源码：`staffline_extraction.py:245-251`，`mean([l.slope for l in lines])`）→ `angle=atan(slope)` | 绝对值 <1°（<2° → NOTE；≥2° → FAIL） |
| 裁切完整性 | G1-3 | **Audiveris HEADERS** + 系统/谱表 bbox 触边检测；**DocAligner** 四角 | 系统 bbox 与图像四边余量；DocAligner 文档四角 vs 图像边界；页眉（标题/速度/调号/拍号）存在性 | 首末小节/页码/曲首标记完整（硬） |
| 页面顺序/缺页 | G1-4 | **Tesseract**（pytesseract） | `image_to_data` 在页眉/页脚区域 OCR 页码 → 序列单调、无缺号、无重号 | 缺页=0、重复=0（硬） |
| 文字可辨性 | G1-5 | **Tesseract**（pytesseract）→ 备选 PaddleOCR | 每词 `conf`(0–100) 高置信占比；词框高 ≥16px | 不可辨行=0、字高 ≥16px（硬） |
| 音符符号可区分 | G1-6 | **Audiveris**（SYMBOLS/HEADS，导出 MusicXML 的 note 数）→ 备选 **oemer** | 符号实例列表非退化（符头/临时记号/小节线/休止符均有检出）；或 MusicXML 音符数与谱面密度一致 | 符头/符尾/附点/连音线/临时记号可区分（硬） |
| 单页信息密度 | G1-7 | **Audiveris**（MEASURES 步骤）→ 备选 **oemer** | 每页 systems 数（谱表组）与 measures 数（小节线计数） | systems ≤6、measures ≤24 |

> 设计原则（对齐 P5）：凡打 PASS 的项都基于**可测字段**；引擎缺失/超时一律 `SKIP` 并标注「引擎不可用」，**绝不把 SKIP 当 PASS**。

## 3. 组件清单（全部可下载；许可已核）

| 组件 | 获取方式 | 许可 | 用途 | 适配点（改动量） |
|---|---|---|---|---|
| **Audiveris** | `github.com/Audiveris/audiveris` release / 源码构建 | **GPL-3.0** | OMR 基线：GRID→线距、MEASURES→小节数、SYMBOLS→符号、HEADERS→页码/曲首 | 批处理 CLI `-batch -transcribe -export -output <dir> <input>`；解析 `.omr`（XML）与 `.mxl`（MusicXML）→ 提取上表字段 |
| **Tesseract** | 服务器 `apt install tesseract-ocr tesseract-ocr-kor tesseract-ocr-chi-sim`；本机 UB-Mannheim 构建 | Apache-2.0 | OCR：文字 conf / 字高 / 页码 | 无（命令行） |
| **pytesseract** | `pip install pytesseract`（0.3.13） | Apache-2.0 | Tesseract Python 绑定 | `pytesseract.image_to_data(..., output_type=Output.DICT)` |
| **oemer** | `pip install oemer`（0.1.8） | MIT（含 Apache 代码） | 程序化 OMR 交叉校验：线距/斜率/符号 | **必需两处适配（已实证）**：① 钉 `onnxruntime==1.20.0`（≥1.31 对老检查点负 pad 报 `ShapeInferenceError`，1.20.0 是带 cp313 轮子且容忍的版本）；② 打 `bbox.find_lines` 的 `HoughLinesP` 形状补丁（见 `plan/patches/oemer-0.1.8-bbox-find_lines-shape.patch`）。首次运行自动下载 2 个 UNet 检查点 |
| **onnxruntime** | `pip install onnxruntime`（1.31.0） | MIT | oemer 推理后端（CPU） | 无 |
| **onnxruntime-qnn**（可选） | `pip install onnxruntime-qnn`（2.7.0） | MIT | 骁龙 NPU 加速（Windows ARM64 场景） | 注册 EP，见官方示例 |
| **DocAligner** | `pip install docaligner-docsaid`（1.1.1） | Apache-2.0 | G1-3 裁切：文档四角 → bbox/余量 | 四角 → 边缘余量判定（阈值入 gate_config.json） |
| **PaddleOCR**（备选） | `pip install paddleocr`（3.7.0） | Apache-2.0 | 韩/中 OCR 备选（conf 输出） | 仅在 Tesseract 韩文置信不足时启用 |
| **SMT**（研究级，暂不首选） | HF `antoniorv6/smt-grandstaff` | MIT | 端到端 OMR（pianoform 限定，torch 重） | 不用于生产，留作对照 |

> **手写代码边界**（对齐「download first, don't hand-write」）：本方案手写部分**仅限适配胶水**——Audiveris `.omr`/`.mxl` 解析、oemer API 读数、Tesseract conf 过滤、DocAligner 输出转判定。所有算法能力均来自下载组件；胶水须在提交信息中显式标注 provenance。

## 4. 服务器架构（多用户）

```
POST /api/score/assess  (multipart image)
        │
        ▼
 [任务目录] ── 预处理（deskew/裁切仅用下载组件，不过度加工）
        │
        ├─▶ Audiveris (subprocess, 超时守门)  ─▶ .omr/.mxl ─▶ 适配器 ─▶ 线距/歪斜/音符/密度
        ├─▶ oemer    (进程池, 懒加载)          ─▶ unit_size/slope/symbols ─▶ 交叉校验
        ├─▶ Tesseract(pytesseract)             ─▶ conf/bbox/页码 ─▶ 文字/字高/顺序
        └─▶ DocAligner                         ─▶ 四角 ─▶ 裁切余量
        │
        ▼
 G1 报告 JSON（每项 status + detail + 引擎 + 证据路径）--- 任一项 FAIL → FAIL
```

- **镜像**：`deploy/omr-qa/Dockerfile`（建议路径，落地时创建）
  - apt：`tesseract-ocr tesseract-ocr-kor tesseract-ocr-chi-sim openjdk-17-jre-headless ffmpeg`（+ Audiveris 发行包）
  - pip：`oemer onnxruntime pytesseract opencv-python-headless pillow docaligner-docsaid`
  - **预置资产**：oemer 检查点、Tesseract 语言包**打进镜像**（避免运行时下载 → 多用户并发稳定、离线可跑）
- **引擎隔离**：Audiveris 走 subprocess（JVM 内存独立）；oemer 走受限进程池；单任务超时 → 该引擎 SKIP
- **fail-closed**：引擎不可用/超时/输出不可解析 → 该项 `SKIP` 并注明原因；**绝不冒充 PASS**
- **并发**：每任务独立临时目录；结果按 `sha256(image)+引擎版本` 缓存
- **幂等**：报告含引擎版本+检查点哈希，保证可复现（P5）

## 5. 环境可行性（2026-10-16 本机实测）

| 事实 | 实测结果 |
|---|---|
| 本机平台 | Windows 11 **ARM64**（Snapdragon X Elite），Python 3.13.14 |
| Docker | **29.6.2 可用** → 本机可起 Linux 容器（=服务器同构验证） |
| Java | **OpenJDK 17.0.20 已装** → Audiveris 可直接跑 |
| oemer wheel | **已下载**：`oemer-0.1.8-py3-none-any.whl`（含源码，API 已核） |
| onnxruntime | **已下载**：`onnxruntime-1.31.0-cp313-cp313-win_amd64.whl`（在 ARM64 上以 x64 兼容层运行） |
| onnxruntime-qnn | **存在**：`onnxruntime_qnn-2.7.0-cp313-cp313-win_amd64.whl`（骁龙 X NPU 加速路径） |
| pytesseract / docaligner-docsaid / paddleocr | PyPI 均可得（0.3.13 / 1.1.1 / 3.7.0） |
| Tesseract 二进制 | 本机**未装**（服务器用 apt；本机可装 UB-Mannheim 构建） |
| onnxruntime 兼容性（实测） | **1.31.0 ❌**（oemer 老检查点负 pad → `Attribute pads must not contain negative values`）；**1.20.0 ✅**（最早带 cp313 轮子且容忍负 pad）→ 部署必须钉 `==1.20.0` |
| 安装陷阱（本机） | pip 覆盖安装被沙箱 safe-delete 拦截（`SHFileOperationW 失败 0x2`）→ 用 `--target` + `PYTHONPATH` 或全新 venv 规避，勿 `--force-reinstall` |
| 服务器目标 | Linux amd64 → 全部组件有官方包，无 GPU 亦可（CPU 推理） |

## 6. 落地步骤

1. **下载固化**：写 `deploy/omr-qa/Dockerfile`（§4），把 Audiveris 发行包 / Tesseract 语言包 / oemer 检查点预置进镜像。
2. **适配层**（唯一手写部分，标注 provenance）：
   - `tools/checks/g1_auto_audiveris.py`：解析 `.omr`(XML)/`.mxl` → 线距/小节数/符号数
   - `tools/checks/g1_auto_ocr.py`：pytesseract conf/字高/页码
   - `tools/checks/g1_auto_omr.py`：oemer unit_size/slope/symbols
   - `tools/checks/g1_auto_check.py`：编排 + 降级链 + 报告（`--auto` 供 `g1_image_quality.py` 调用）
3. **集成**：`g1_image_quality.py` 加 `--auto`；依赖缺失 → 保持现有 SKIP 行为（向后兼容，现有测试不受影响）。
4. **验证 spike**：用 `02_source/song01/preprocessed/` 实图跑全链 → 产出真实 G1-2~G1-7 数据，与现有 `08_qc/song01/g1_preprocessed_result.json` 合并。
5. **服务化**：FastAPI 端点 + 并发/缓存/超时（§4），多用户压测后上线。

## 7. 风险与降级

| 风险 | 降级 |
|---|---|
| Audiveris 对模糊/手写谱识别失败 | 该页 G1-8 已拦（手写恒 requires_human → 转人工录入路径 #65）；其余项由 oemer/Tesseract 补 |
| oemer 检查点下载失败（离线） | 镜像预置（§4）；缺失时该项 SKIP |
| Tesseract 韩文置信偏低 | 启用 PaddleOCR 备选（§3） |
| 服务器无 GPU | 全链 CPU 推理（已按 CPU 设计）；onnxruntime-qnn 仅在骁龙 NPU 场景可选 |
| GPL 传染（Audiveris） | Audiveris 以**独立进程/独立镜像层**调用（不链接进我方代码），许可登记 `logs/license_notes.md` |

## 8. 本次验证证据（可复现）

```
pip download oemer --no-deps -d oemer_pkg
  → oemer-0.1.8-py3-none-any.whl （源码已解出并核对 API）
pip download onnxruntime --only-binary=:all: -d ort_probe
  → onnxruntime-1.31.0-cp313-cp313-win_amd64.whl （成功）
pip download onnxruntime-qnn --only-binary=:all: -d ort_probe
  → onnxruntime_qnn-2.7.0-cp313-cp313-win_amd64.whl （成功）
docker --version → 29.6.2 ；java -version → 17.0.20 （本机可跑容器与 JVM）
oemer 源码核对：staffline_extraction.py:245-251 Staff.slope；utils.py get_global_unit_size
```

### 8.1 song01 实图 spike（2026-10-16，**已跑通 ok=true**）

```
PYTHONPATH=C:/Users/piaoy/ort_1.20.0 venv_omr_1/Scripts/python.exe oemer_probe.py \
  music_rebuild_project/02_source/song01/preprocessed/song01_preprocessed_v001.jpeg \
  --out-dir oemer_spike_out --json oemer_spike_result.json
```

| 指标 | 实测值 | 对应 gate 判定 |
|---|---|---|
| n_staves / zones | 64 / 8 | — |
| unit_size（模型空间 → 原像素） | 11.91 → **18.32 px**（min 18.08 / max 18.49） | G1-1 线距 ≥8px → **PASS** |
| slope 绝对值 | max **0.169°** / mean 0.029° | G1-2 <1° → **PASS** |
| 符号层 | barlines 29 · clefs 8 · sfns 26 · rests 14 · notes 179 | G1-6 符号非退化 → **PASS** |
| MusicXML | **193** `<note>` / **23** `<measure>` | G1-7 measures ≤24 → 边缘 PASS |
| 产物 | `oemer_spike_out/song01_preprocessed_v001.jpeg.musicxml`（MusicXML 4.0，Piano） | — |

结论：**oemer 路径在真实 song01 上端到端可用**，7 项中 4 项（线距/歪斜/音符/密度）已可直接自动判定；所需两处适配已固化为补丁（§3）。

## 9. 待办（下一步）

- [ ] 用户确认后：创建 `deploy/omr-qa/Dockerfile` + 四个适配模块（§6.2）
- [ ] 跑 song01 实图 spike，产出真实 G1-2~G1-7 数据
- [ ] 许可登记 `logs/license_notes.md`（#66）
- [ ] 服务化评审（端点形态、并发上限、缓存策略）
