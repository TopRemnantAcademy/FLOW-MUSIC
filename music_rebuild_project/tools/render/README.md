# tools/render · 输出格式工具包（#59）

把 gate_G4.md 的**输出规格**（G4-9 / G4-10 / G4-12）做成可执行的打包与校验。
落位在 F 段末尾：`07_mix/<song_id>/masters/` 与 `stems/` 生成之后、分发之前。

## 放什么

- `wavio.py`：16/24bit、44.1k WAV 读写；`mix_down(tracks)` / `duration_of(path)`
- `stems.py`：G4-9 四轨（鼓/贝斯/和声/보컬）组织 + 时长一致性 + 可同播验证
- `manifest.py`：G4-12 分发清单（SHA-256 + 字节 + 时长 + 缺失项）
- `mp3.py`：G4-10 的 MP3 320kbps —— **规格校验与占位，不编码**
- `cli.py` / `demo.py`：命令行与可复现演示
- `tests/`：合成素材的单元 + 端到端测试

## 不放什么

- 任何真实媒体文件（测试素材全部现场合成）
- 门阈值定义 → `00_docs/gates/gate_G4.md`（本包只引用，不重复定义）
- 命名规则实现 → #13 `tools/naming/`（本包通过 `stem=` 参数接收，不重写）

## 用法

```bash
# ① G4-9：校验 stems/ 四轨
python -m music_rebuild_project.tools.render.cli check-stems \
    --stems-dir 07_mix/song01/stems

# ② 四轨 → master（44.1k/24bit）
python -m music_rebuild_project.tools.render.cli mix \
    --stems-dir 07_mix/song01/stems \
    --out 07_mix/song01/masters/song_name_mix_v001.wav

# ③ G4-12：生成 manifest
python -m music_rebuild_project.tools.render.cli manifest \
    --master 07_mix/song01/masters/song_name_mix_v001.wav \
    --vocal  05_vocals/song01/vocal_wav/approved_vocal_pilot.wav \
    --musicxml 04_approved/song01/ground_truth.musicxml \
    --midi 06_arrangement/song01/drums/song01_drums_main_v001.mid \
    --score 02_source/song01/originals/song01_source_p001_v001.tif \
    --approval-log 08_qc/approval_log.md \
    --out-dir 07_mix/song01/masters

# 演示（合成素材，不碰真实媒体）
python -m music_rebuild_project.tools.render.demo
```

退出码 `0` = PASS，`1` = FAIL（缺件 / 规格不符 / 时长不一致）；stdout 恒为 JSON。

## 门 → 代码对照

| 门 | 本包落点 | 判据 |
|---|---|---|
| G4-9 stem 完整性 | `stems.check_stems` | 四轨齐 + 可重开 + 时长极差 ≤ 5ms + 逐采样同播 |
| G4-10 输出规格 | `wavio.Spec(44100, 3, 1)` + `mp3` | 44.1k/24bit WAV；MP3 320kbps 只校验不产 |
| G4-12 交付物齐全 | `manifest.check_deliverables` | 五类分发物 + 四类记录，缺一即 FAIL |
| G4-5 时长（借用） | `wavio.duration_of` | master vs 素材 ±1s（本包只提供测量，判定在 #61） |

## MP3：本包**不产**、也不伪造（重要）

G4-10 要 `MP3 320kbps`。Python 标准库**没有** MP3 编码器，沙箱里也没有
`ffmpeg` / `lame`。因此本包：

- 声明规格（`mp3.MP3_SPEC`：320kbps CBR / 44.1kHz / stereo）；
- `check_mp3()` 只校验**存在性 + 扩展名 + 文件头**，并把
  `unavailable_reason` 写进 manifest（「需外部编码器」）；
- **拒绝**把 WAV 改名成 `.mp3` 蒙混：探测层先查容器魔数，命中 `RIFF`/`FORM`/
  `fLaC`/`OggS`/`ftyp` 即判 `foreign:*`，并说明「疑似伪装」；
- 真转码的命令行在 `mp3.MP3_COMMAND` / `MP3_COMMAND_LAME`，默认**不执行**
  （`transcode_placeholder(run=False)`）—— 编码不该由校验环节偷跑，那会破坏可复现性。

位率**不猜**：stdlib 解不出 MP3 位率，`validate_spec()` 返回 `bitrate_kbps=None`
并注明需 `ffprobe -show_entries stream=bit_rate`。

## 纯 stdlib

`wave` / `array` / `struct` / `hashlib` / `json` / `math`。不引入
numpy / scipy / pydub / soundfile，不依赖 ffmpeg —— 沙箱里就能跑，结果可复现
（同输入 → 同字节，见 `test_mix_to_master_is_deterministic`）。

24bit 是唯一需要手写位打包的地方（`wavio._encode`）：`struct.pack('<i', v)`
取低 3 字节按小端写入 —— 这也是「stdlib 写不了 24bit」这一说法被打破的几十行。

## 只读规则

工具本身可迭代（L1）。**不写数据目录**：`cli.py` 只写你显式指定的 `--out` / `--out-dir`，
`demo.py` 默认落 `tempfile.mkdtemp()`。

## 交叉引用

- #11 门 G1-G4（`00_docs/gates/gate_G4.md`）
- #7 交付物落位（`00_docs/deliverables.md`）
- #13 命名与冻结（**本包不实现命名校验**，走 `stem=` 传入）
- #60 `08_qc/qc_file_check.py`（存在/大小/时长/静音/削波）—— 与本包互补，不重叠
- #61 G4 QC 表（消费本包产出的 `manifest.json`）
