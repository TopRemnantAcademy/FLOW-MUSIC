# song01 · 原始乐谱来源清单

| 项 | 值 |
|---|---|
| song_id | song01 |
| 标题 | 小小望楼 |
| 圣经参考 | 弗 3:8 |
| 来源文件 | `C:/Users/piaoy/Downloads/小小望楼.docx` |
| 提取方式 | docx zip → `word/media/image1.jpeg` |
| 页数 | 1 |
| 容器 | JPEG |
| 子类型 | raster |
| 尺寸 | 2480 × 3508 px |
| 隐含 DPI（A4） | 300 dpi |
| 原始 embedded DPI | 无（导致 `originals/` G1-1 FAIL） |
| 预处理说明 | 仅设置 JFIF 300 dpi，像素未改；保存为 `preprocessed/song01_preprocessed_v001.jpeg` |
| 原始 sha256 | `454b033f70981cd160367c37404e4ced52387e71458c96563e912a03273c599b` |
| 预处理 sha256 | `cf33c4f744374c4f1ad9f348d3f7868f5a3634f62c8673dac13722f4fb760b9f` |
| 采集人 | irewood |
| 采集时间 | 2026-10-16 |

## 文件落位

```text
02_source/song01/
├── originals/
│   ├── song01_original_v001.jpeg        # 原样保留（L0 只进不改）
│   └── source_manifest.txt              # G1-9 哈希清单
├── preprocessed/
│   ├── song01_preprocessed_v001.jpeg      # 仅补 DPI 元数据（L1 可重建）
│   └── source_manifest.txt
└── manifest.md                          # 本文件
```
