"""#59 输出规格工具包：master WAV + 分轨 stem + 哈希 manifest。

纯 stdlib（`wave`/`array`/`struct`/`hashlib`），不依赖 numpy/scipy/pydub/soundfile，
也不要求 ffmpeg —— 沙箱里就能跑，且结果可复现。

| 模块 | 对应门 | 干什么 |
|---|---|---|
| `wavio` | G4-10 前置 | 16/24bit、44.1k WAV 读写；`mix_down` / `duration_of` |
| `stems` | **G4-9** | 鼓/贝斯/和声/보컬 四轨组织 + 时长一致性 + 可同播 |
| `manifest` | **G4-12** | 交付物 + SHA-256 + 字节 + 时长 + 缺失项检查 |
| `mp3` | **G4-10** | MP3 320kbps 的规格声明与存在性校验（**不编码**） |

    from music_rebuild_project.tools.render import stems, manifest, wavio

不是 #13 命名校验的重复实现：命名规则由调用方传入（`stem=` 参数）或复用
`tools/naming`，本包只管「打包与指纹」。
"""

from . import manifest, mp3, stems, wavio  # noqa: F401

__all__ = ["wavio", "stems", "manifest", "mp3"]

#: G4-10 的输出规格（唯一一份，别在别处再写死 44100/24）。
MASTER_SPEC = wavio.Spec(44100, 3, 1)
STEM_SPEC = wavio.Spec(44100, 3, 1)
