"""阶段与门的抽象契约（可插拔点）。

真实工具的落地方式（未来接 Audiveris / MuseScore / ACE / SynthV / REAPER 时）
只需实现这里的一个抽象类，`orchestrator.run_score_to_music` 不必改动 —— 见
`test_provider_interface_compliance`：那里用一个「假装是真实 provider」的 stub
证明接口是够用的，且整条流水线可以在不碰网络的前提下跑通。

门（Gate）不是阈值定义处：阈值唯一定义在
`music_rebuild_project/00_docs/gates/gate_G1.md` ~ `gate_G4.md`，
本包只是把其中的**可测量项**映射成断言。任何阈值分歧以那份文档为准。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: 门结论三态（gate_G1.md「通用约定」原文）。
GATE_PASS = "PASS"
GATE_PASS_WITH_NOTES = "PASS_WITH_NOTES"
GATE_FAIL = "FAIL"
GATE_STATUSES = (GATE_PASS, GATE_PASS_WITH_NOTES, GATE_FAIL)


@dataclass
class GateItem:
    """单条检查项的可测量结果。

    id 用门文档里的编号（G1-1 … G4-12）而非自造名：报告能直接对照原文逐项核。
    """

    id: str
    name: str
    status: str  # PASS | PASS_WITH_NOTES | FAIL
    measured: Any = None
    threshold: str = ""
    note: str = ""

    @property
    def failed(self) -> bool:
        return self.status == GATE_FAIL


@dataclass
class GateReport:
    """一道门的判定记录（对齐 gate_G*.md 的「判定记录文件」）。"""

    gate: str  # G1 | G2 | G3 | G4
    status: str
    items: list[GateItem] = field(default_factory=list)

    def item(self, item_id: str) -> GateItem:
        for it in self.items:
            if it.id == item_id:
                return it
        raise KeyError(f"{self.gate} 报告里没有检查项 {item_id}")

    def failed_ids(self) -> list[str]:
        return [it.id for it in self.items if it.failed]

    def to_dict(self) -> dict:
        return {
            "gate": self.gate,
            "status": self.status,
            "items": [
                {
                    "id": it.id,
                    "name": it.name,
                    "status": it.status,
                    "measured": it.measured,
                    "threshold": it.threshold,
                    "note": it.note,
                }
                for it in self.items
            ],
        }


class Gate(ABC):
    """门：吃上一步的产物，吐 `(status, GateReport)`。

    **fail-closed**：任何检查项 FAIL 即整门 FAIL，编排器据此 halt（不往下游走，
    更不把没审过的产物当"完成品"）。
    """

    #: 门名，用于报告与日志（G1…G4）。
    name: str = "Gate"

    @abstractmethod
    def check(self, **kwargs) -> tuple[str, GateReport]:
        ...


class OMRProvider(ABC):
    """阶段 B：乐谱图 → MusicXML 候选（真实实现：Audiveris / 付费 OMR）。"""

    #: 候选名，用于 A/B 记录（如 "audiveris" / "paid_api" / "mock"）。
    name: str = "omr"

    @abstractmethod
    def recognize(self, image_png: bytes, *, song_id: str) -> str:
        """返回该 OMR 的 MusicXML 文本（未审校的 raw 产物）。"""


class ScoreProofer(ABC):
    """阶段 C：全数审校（真实实现：MuseScore 人工 8 步审校 + 导出）。"""

    name: str = "proofer"

    @abstractmethod
    def proof(self, musicxml: str, *, song_id: str) -> str:
        """返回审校后的 MusicXML（可进入 G2 的基准候选）。"""


@dataclass
class VocalRender:
    """阶段 D 的一个渲染产物（一个引擎一次渲染 = 一个 VocalRender）。"""

    engine: str  # ace | synthv | ...
    mode: str  # 引擎渲染模式（ACE: cloud/high_perf；SV2: offline …）
    wav_path: str
    settings: dict

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "mode": self.mode,
            "wav_path": self.wav_path,
            "settings": dict(self.settings),
        }


class VocalEngine(ABC):
    """阶段 D：보컬 렌더（真实实现：ACE Studio / Synthesizer V）。

    接口吃「基准谱 + 歌词 + 声乐设置」，吐一个合法 WAV 文件路径 —— 这正好是
    gate_G3 的 G3-1/G3-7/G3-8 能测量的最小集合（能解码 / 非静音 / 长度=小节×拍×BPM /
    参数被记录）。
    """

    #: 引擎名（报告里出现的那个名字）。
    name: str = "vocal"

    @abstractmethod
    def render(self, musicxml: str, lyric: str, settings: dict, *, out_path: str) -> VocalRender:
        ...


class Arranger(ABC):
    """阶段 E：和弦/鼓/贝斯/和声 编曲（真实实现：DAW MIDI 编排）。"""

    name: str = "arranger"

    @abstractmethod
    def arrange(self, musicxml: str, *, lyric: str = "") -> dict:
        """返回编曲描述 JSON（和弦进行 / 段落 / 各声部音符）。"""


class DAWEngine(ABC):
    """阶段 F：混音（真实实现：REAPER 脚本自动化）。"""

    name: str = "daw"

    @abstractmethod
    def mix(self, arrangement: dict, vocals: dict, *, out_dir: str) -> dict:
        """返回混音产物清单：`{"master": path, "stems": {name: path}, ...}`。"""
