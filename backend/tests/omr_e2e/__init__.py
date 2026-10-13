"""乐谱→音乐 全环节端到端沙箱 mock test 包。

零网络 / 零外部 API / 零密钥 / 零桌面软件：每个真实工具（Audiveris / MuseScore /
ACE Studio / Synthesizer V / REAPER）都由 `mocks.py` 里的确定性替身实现，
门 G1-G4 的判定标准出自 `music_rebuild_project/00_docs/gates/gate_G*.md`。
"""
