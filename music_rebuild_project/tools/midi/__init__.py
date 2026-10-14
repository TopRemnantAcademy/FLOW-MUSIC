"""`tools/midi` —— 编曲 E 段的确定性 MIDI 生成工具包（#52 / #53 / #54）。

纯 stdlib，无第三方依赖；输入是段落结构 + 调号/拍号/BPM + 和弦标记，
输出是可导入 DAW 的 SMF 骨架。

> 产出的是 **MIDI 骨架**，不等于已完成编曲录制，更不等于混音。
> 响度 / 真峰 / 掩蔽属 F 段（gate_G4 的 G4-1..G4-4），不在本包范围。

模块职责：
  - `writer`   —— 最小 SMF 写入器 + 自洽回读
  - `sections` —— 段落模型（#51）与调号/拍号/和弦前置（#48/#50）
  - `drums`    —— 鼓（#52），主歌/副歌独立 pattern + 力度变化
  - `bass`     —— 贝斯（#53），根音随和弦、落点对齐底鼓
  - `chords`   —— 和弦织体（#54），钢琴 / 吉他 / 铺底
"""

from . import bass, chords, drums, sections, writer

__all__ = ["bass", "chords", "drums", "sections", "writer"]
