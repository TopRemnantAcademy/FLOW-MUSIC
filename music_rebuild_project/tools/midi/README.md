# tools/midi · 编曲 MIDI 生成（#52 / #53 / #54）

管道 **E 段（编曲）** 的鼓 / 贝斯 / 和弦 MIDI 生成。**纯 stdlib**，无第三方依赖，
输出**可导入 DAW 的标准 MIDI（SMF format 1）**。

## 边界（先读这段）

- 产出的是 **MIDI 骨架**：音高、时值、力度、段落结构都对，进去就能继续编。
- **不等于编曲完成**，**不等于混音完成**。响度 / 真峰 / 削波 / 掩蔽属 F 段
  （gate_G4 的 G4-1..G4-4，#58/#59），本包**不实现、不代替**。
- 本包是独立工具包，**不接后端业务**：不改 `backend/`、`models.py`、`migrations/`，
  也不碰 `tools/checks/**` 与 `tools/naming/**`。
- 不依赖真实媒体、不依赖 DAW；读 MusicXML 只用 `xml.etree` 抽几个字段，
  **不是**完整 MusicXML 解析器。

## 文件

| 文件 | 职责 | 对应 issue |
|---|---|---|
| `writer.py` | 最小 SMF 写入器：`MThd`/`MTrk`、ticks-per-quarter、tempo/拍号 meta、EOT；`note_on/note_off` 抽象；自洽回读 | #52/#53/#54 共用 |
| `sections.py` | 段落模型（Intro/Verse/Pre-chorus/Chorus/Bridge/Outro）+ 调号/拍号/BPM；解析 `sections.md`(1-based→0-based) 与 `chords.txt` | #51 / #48 / #50 |
| `drums.py` | 鼓：六段落**各自独立 pattern** + 小节级重音 | #52 |
| `bass.py` | 贝斯：**根音随和弦**、落点**对齐底鼓**（硬约束） | #53 |
| `chords.py` | 和弦织体：钢琴 / 吉他 / 铺底，可单选可分层 | #54 |
| `render_demo.py` | 从 `sections.md` + `chords.txt` 一次生成整套骨架 + 时长预览 JSON | 联调用 |
| `tests/` | 五项硬断言（见下） | 验收 |

## 快速用

```bash
cd music_rebuild_project
python -m pytest tools/midi/tests/ -q

# 从段落标注与和弦记录生成一整套骨架
python -m tools.midi.render_demo \
  --sections 06_arrangement/song01/sections.md \
  --chords   06_arrangement/song01/chords.txt \
  --key F --tempo 120 \
  --out 06_arrangement/song01/_generated
```

产物：`song_arrangement.mid`（合成工程）、`drums.mid`、`bass.mid`、
`chords_{piano,guitar,pad}.mid`、`arrangement_preview.json`。

## 段落驱动（#51 是生成轴）

段落区间为 **0-based 左闭右开**，`chords.txt` 用 **1-based 行号**书写（音乐人习惯），
解析时自动减一：

```python
from tools.midi import sections, drums, bass, chords

plan = sections.SectionPlan.from_bars(
    [("Intro", 4), ("Verse", 8), ("Pre-chorus", 4), ("Chorus", 8), ("Bridge", 4), ("Outro", 6)]
)
ctx = sections.SongContext(
    key=sections.KeySignature("F"), beats_per_bar=4, bpm=120,
    plan=plan, chords={0: "F", 22: "C", 23: "F"},
).validate()

w, _ = drums.generate(ctx)
bass.generate(ctx, writer=w)                            # 与鼓共用同一 writer → 同一时间基准
chords.generate(ctx, voices=("piano", "guitar", "pad"), writer=w)
open("song.mid", "wb").write(w.to_bytes())
```

## 长度为什么可算准（G4-5 的前置）

`gate_G4.md` 的 **G4-5** 要求 master 时长与「小节 × 拍数 × BPM」误差 ≤ ±1s。
本包让**生成侧的预期时长可精确计算**，判据只有一处实现：

```python
ctx.seconds() == ctx.bars * ctx.beats_per_bar * 60 / ctx.bpm
writer.seconds_for_bars(bars)   # 同上，作曲式无关版本
```

tick 与秒的换算是精确的（`tick / tpq * 60 / bpm`），所以只要最后一轨铺满全曲，
结束 tick 换算出的秒数与公式**逐位一致**。实测：

```
34 小节 × 4 拍 × 60 / 120 = 68.0 秒
最后音符结束 tick = 65280（tpq=480 @120BPM）→ 68.000000 秒
误差 = 0.000000000 s
```

> 注意：这保证的是 **MIDI 侧预期长度**。master 实际时长仍由 F 段渲染决定，
> G4-5 的判定依旧在 #60 的 `ffprobe` 上做。

## 五项断言（`tests/`）

| # | 断言 | 位置 |
|---|---|---|
| ① | 产物是合法 MIDI：`MThd` 头校验、每轨以 EOT 收尾、能回读且音高/力度/时值无损 | `test_writer.py` |
| ② | 长度 = 小节 × 拍数 × BPM 换算（多组 BPM/拍号参数化） | `test_length.py` |
| ③ | 同输入两次生成字节完全一致（含乱序插入与多轨合成） | `test_determinism.py` |
| ④ | 段落切换处 pattern 确实不同（六段落两两不同；副歌密度 > 主歌） | `test_patterns.py` |
| ⑤ | 力度有变化（非全同值，动态范围 ≥ 8） | `test_patterns.py` |

另加 `test_musical_rules.py`（贝斯根音随和弦、落点 ⊆ 底鼓 tick、音区合法）
与 `test_render_demo.py`（CLI 端到端）。

## 诚实边界

- 鼓的 pattern 是**手写规则**，不是从音频反推的；风格多样性靠后续加 pattern 扩。
- 贝斯对齐底鼓时宁可**少弹一个音**也不脱离底鼓（吸附阈值 2 个十六分步），
  所以稀疏段落的贝斯会比 pattern 设计的更简 —— 这是有意的取舍。
- 和弦兜底走调内顺阶级数表，**不能替代人工在 `chords.txt` 里的批准结果**（#49）。
- 没有做：人性化摇摆（swing）、音色采样、自动化曲线、混音。
