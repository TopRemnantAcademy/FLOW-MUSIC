# `tests/omr_e2e/` · 乐谱→音乐 全环节沙箱 mock test

> 对应 issue：本仓 `#122`（需求 brief）、`#1`（Epic 全景）、`#9`（管道 A→F 与门位置）、
> `#29`（输入分类）、`#34`（OMR 胜者选定）、`#43`/`#44`/`#45`（보컬 A/B）、`#51`/`#55`（编曲）、
> `#58`~`#61`（混音与最终 QC）。
> 门阈值**唯一定义处**：`music_rebuild_project/00_docs/gates/gate_G1.md` ~ `gate_G4.md`。

一句话：**在 CNB 沙箱里（无桌面软件 / 无网络 / 无密钥）把「乐谱图 → MusicXML → ground_truth 冻结 →
보컬 WAV → 编曲 → master/stem → 注册成平台 Song」整条链路跑通，并且每一道门都真的会拦。**

```bash
cd backend
python -m pytest tests/omr_e2e/ -q      # 52 passed
```

---

## 1. 目录与职责

| 文件 | 职责 |
|---|---|
| `interfaces.py` | 五个抽象类（`OMRProvider` / `ScoreProofer` / `VocalEngine` / `Arranger` / `DAWEngine`）+ `Gate` 协议 + `GateItem`/`GateReport`/`VocalRender` 数据结构 |
| `fixtures/__init__.py` | 内置 16 小节 canon 小样谱（MusicXML 字符串）、韩文歌词、**代码合成**的 PNG（zlib+struct，零大二进制） |
| `mocks.py` | 真实工具的确定性替身：`MockPreprocessor` / `MockOMR` / `MockMuseScore` / `MockVocalEngine(ace\|synthv)` / `MockArranger` / `MockDAW` + 四道 `GateG1`~`GateG4` |
| `orchestrator.py` | `run_score_to_music(image_bytes, lyric, song_meta, *, work_dir, register_song=...)` → `PipelineResult`；**任一门 FAIL 即 halt（fail-closed）** |
| `platform.py` | 平台闭环桥：把成品注册成 Song（复用 `conftest.py` 的 `TestClient` + `MockProvider`），并取回 Song id / explore 命中 |
| `test_*.py` | 见第 4 节 |

产物目录名刻意与 `music_rebuild_project/` 的约定一一对齐：
`02_source → 03_omr_raw → 04_approved → 05_vocals → 06_arrangement → 07_mix`。
（`work_dir` 由调用方给，测试里是 `tmp_path`，**不写仓库**。）

---

## 2. 管道与门的映射

```text
stage A  输入验证 / 图像预处理            → 门 G1  →  gate_G1.md G1-1 ~ G1-9
stage B  OMR 并行候选（Audiveris / 付费） → 选胜者 →  #34：旋律+节奏错误最少者胜
stage C  MuseScore 全数审校              → 门 G2  →  gate_G2.md G2-1 ~ G2-9
   └─ ground_truth.musicxml 冻结（SHA-256 + 时间 + 批准人）
stage D  보컬 A/B（ACE ↔ SynthV）        → 门 G3  →  gate_G3.md G3-1 ~ G3-10
   └─ approved_vocal_pilot.wav 冻结 + vocal_settings.json
stage E  编曲（和弦 / 鼓 / 贝斯 / 和声）  →  #51 段落标注、#55 声乐-伴奏
stage F  DAW 混音 → master + 4 stem      → 门 G4  →  gate_G4.md G4-1 ~ G4-12
   └─ qc_file_check（#60 的替身）
平台     成品 → Song 注册 → /api/songs/explore 匿名命中
```

`GateReport.items` 里的 `id` **就是门文档里的编号**（`G1-1`…`G4-12`），
所以报告可以直接对着原文逐项核；`GateItem.measured` 一定带实测值，不是布尔。

门的结论三态沿用原文约定：`PASS` / `PASS_WITH_NOTES` / `FAIL`。
`PASS_WITH_NOTES` **不阻断**流水线（例如 G1-1 的 200dpi + 线距 6px 可调档），
但会在报告里留下降级记录 —— 降级通过不等于没发生。

---

## 3. 换成真实 provider

接口就是契约。真实落地时**不要**改 `orchestrator.run_score_to_music`：

```python
class RealAudiveris(OMRProvider):
    name = "audiveris"
    def recognize(self, image_png: bytes, *, song_id: str) -> str:
        ...  # 调 audiveris CLI，读回 MusicXML 文本

class RealMuseScore(ScoreProofer):
    name = "musescore"
    def proof(self, musicxml: str, *, song_id: str) -> str:
        ...  # 打开 → 人工/脚本审校 → 导出

class RealAce(VocalEngine):
    name = "ace"
    def render(self, musicxml, lyric, settings, *, out_path) -> VocalRender: ...

class RealReaper(DAWEngine):
    name = "reaper"
    def mix(self, arrangement, vocals, *, out_dir) -> dict: ...
```

然后：

```python
run_score_to_music(image, lyric, meta, work_dir=d,
                   omr_candidates=[RealAudiveris(), RealPaidOMR()],
                   proofer=RealMuseScore(),
                   vocal_engines={"ace": RealAce(), "synthv": RealSynthV()},
                   daw=RealReaper(),
                   register_song=my_platform_hook)
```

门也能换（`gate_g1=…` 等）。加严可以，放松不行 ——
`test_provider_interface_compliance.py` 里有一个自定义加严门 + 一个第三方 OMR 的样例，
证明「换成没自报错误数的真实 OMR」照样能被评分（用**原谱**做唯一裁判比对）。

**第三方 OMR 的评分口子**：实现了 `score(truth) -> dict` 的候选直接自报错误数；
没实现的（真实工具都是这样）走 `orchestrator._score_by_diff`，拿原谱真值逐项 diff。

---

## 4. 测试清单

| 文件 | 覆盖 |
|---|---|
| `test_e2e_full_pipeline.py` | A→F + G1-G4 全过 + Song 注册；两次运行**逐字节确定**；各门检查项齐全 |
| `test_stage_contracts.py` | 各阶段产物契约：合法 MusicXML / 合法 WAV 头 / 非静音 / 时长 = 小节×拍×BPM / stem 时长一致 |
| `test_gate_fail_halts.py` | 逐门失败输入 → 对应项 FAIL + 流水线 halt（G1 八项、G2 解析/音高/往返、G3 静音/时长/门槛、G4 削波/静音/缺轨/未听） |
| `test_omr_candidate_selection.py` | #34 选优语义（旋律+节奏优先于歌词、不假设全局同优、同分确定、注入错误可测量） |
| `test_vocal_ab_selection.py` | #43/#44/#45 A/B：两引擎都出 pilot、7 项全打分、**不唯总分**、参数可复现、冻结哈希 |
| `test_provider_interface_compliance.py` | 接口可插拔（含第三方 OMR 与原谱比对评分、自定义门） |
| `test_platform_handoff.py` | 成品 → Song → explore 匿名命中 / 私有不进流 / 未审核不进流 / 订阅门控 / CDN 出口 / 播放计数 |
| `test_sandbox_safety.py` | 静态禁依赖（出网客户端/桌面软件绑定）+ 零密钥扫描 + **毒化 socket 后仍全绿** + 不污染仓库 |

---

## 5. 诚实的边界（mock 覆盖不到什么）

这些点**必须**真实工具或 `ffmpeg`/`librosa` 才能验证。沙箱里没有，所以报告里一律标注
`note="mock 代理指标"`，**不冒充真实测量**：

| 真实指标 | mock 用什么代理 | 为什么沙箱做不到 |
|---|---|---|
| 响度 **LUFS**（G4-1） | master 的真峰余量 ≤ −3 dB 且 ≥ −30 dB | 需要 `ffmpeg loudnorm` / `loudgain` |
| 真峰 **dBTP**（G4-2） | 样本峰值 | 需要 4× 过采样（intersample peak） |
| 2~5kHz 掩蔽（G4-7） | 보컬 与伴奏族的整段能量比 | 需要频谱分析（`ffmpeg`/`librosa`） |
| 音节起始 ±30 ms（G3-4） | 渲染器自报 0 偏差 | 需要 onset 检测对齐基准网格 |
| 音高偏差 ≤30 音分（G3-2） | 渲染器自报 0 偏差 | 需要 F0 提取（`pyin`） |
| 气声 / 音素 / 力度参数（G3-8） | `vocal_settings.json` 字段齐全性 + PCM 指纹可复现 | 参数**真实生效**只有引擎自己能证明 |
| MP3 320kbps / 24bit WAV（G4-10） | 44.1kHz/16bit（stdlib `wave` 写不了 24bit） | 需要 `ffmpeg`/`lame` |
| 段落听感（G4-6）、单声道试听（G4-8） | 段落 RMS 差、单声道下无静音 | 需要人耳 |
| MuseScore / Audiveris / ACE / REAPER 的**真实**渲染结果 | 接口同形的替身 | 桌面软件，沙箱里没有 |

另外两条：

- **平台闭环不走 HTTP 音频**：`platform.py` 直接构造「已完成」的 Song 行，而不是
  `POST /api/songs/generate`。原因是生成路径会去**拉音频**做 CDN 回源与试听片段，
  而沙箱里给不出可拉取的音频 URL（伪造一个可拉取的服务 = 偷偷起网络）。
  被验证的是「成品 → Song 行 → 审核态 → 公开流 → 订阅门控」；没被验证的是
  「HTTP 上的生成提交」。mock 的上游 URL 故意用回环地址 `http://127.0.0.1:9/…`，
  让「外面拉不到也不影响注册」成为被测事实。
- **本包不含任何 DB 变更**：不碰 `app/models.py`、不加迁移、不加表。
  所用的字段（`status` / `moderation_status` / `is_public` / `audio_url` / `audio_cdn_url` /
  `external_id` / `custom_id` / `duration` …）全部是既有的。

---

## 6. 确定性

- fixture 由代码生成，无随机数；音乐素材是「固定公式 + 固定相位」的正弦/和声；
- OMR 错误注入是**计数式**的（改前 N 个音符），不是随机翻转；
- 素材缓存存的是 **bytes**（`array.array` 可变，缓存对象被调用方原地改过一次，
  详见 `mocks._PCM_CACHE` 的注释）—— 也因此「同进程跑两次结果一致」是被断言过的。

`test_e2e_pipeline_is_deterministic` 与 `test_sandbox_safety.test_fixtures_do_not_touch_repo_binaries`
分别钉住「跑两次一致」与「同一参数两次生成一致」。
