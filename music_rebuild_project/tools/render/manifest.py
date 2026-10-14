"""分发清单（manifest）：交付物 + SHA-256 + 字节数 + 时长 + G4-12 齐全性。

gate_G4.md · **G4-12 交付物齐全**（硬）：
    原谱图 / 批准 MusicXML / 보컬 WAV / 编曲 MIDI / master WAV 均在分发文件夹；
    许可·版本·设置·批准日志齐全。
落位文件：`07_mix/<song_id>/masters/*_manifest.txt`（gate_G4 的「判定记录文件」一节）。

本模块做两件事，都是**可执行**的，不是文档口号：

1. **算**：每个文件一条记录 —— 相对路径 / SHA-256 / 字节数 / 时长（音频才有时长）。
   哈希是唯一能证明「分发出去的 master 就是过门那一版」的东西（#13/#27 冻结语义）。
2. **问**：对照 G4-12 的五类分发物 + 四类记录，逐项检查在不在。
   **缺失就 FAIL，绝不静默**：`missing_required` 是显式字段，`ok` 由它决定。

**MP3 的边界（重要，也是本模块刻意不做的部分）**：
G4-10 要 `MP3 320kbps`，但沙箱里**没有编码器**（无 ffmpeg / lame / libmp3lame）。
本模块**不产 MP3、也不伪造一个改名成 .mp3 的 WAV** —— 那种文件能被 `os.path.exists`
骗过，却骗不过任何播放器，是比缺失更坏的缺陷。做法是：
  - 声明 `MP3_SPEC`（G4-10 的确切参数：320kbps CBR / 44.1kHz / stereo）；
  - `check_mp3()` 只校验**存在性 + 扩展名 + 可探测的格式头**，并把
    「未生成原因」原样写进 `unavailable_reason`；
  - 详情见 `mp3.py`：编码器探测 + 明确的「需要外部编码器」错误。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from . import wavio

#: G4-12 点名的分发物五类。键是清单里的逻辑名，值是「人话描述」。
REQUIRED_DELIVERABLES = {
    "source_score_image": "原谱图（扫描件/照片）",
    "approved_musicxml": "批准版 MusicXML（04_approved/ground_truth.musicxml）",
    "vocal_wav": "보컬 WAV（05_vocals/.../approved_vocal_pilot.wav）",
    "arrangement_midi": "编曲 MIDI（06_arrangement/.../*.mid）",
    "master_wav": "master WAV（07_mix/.../masters/*_mix_v001.wav）",
}

#: G4-12 后半句「许可·版本·设置·批准日志齐全」。
REQUIRED_RECORDS = {
    "license": "许可信息（曲目授权/来源声明）",
    "version": "版本记录（#13 命名与递增）",
    "settings": "设置快照（vocal_settings.json / DAW 工程参数）",
    "approval_log": "批准日志（08_qc/approval_log.md）",
}

#: 音频扩展名 —— 只有这些才去问时长，问别的会白抛异常。
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".aif", ".aiff")

#: G4-10 的 MP3 规格（本模块只声明与校验，不编码）。
MP3_SPEC = {
    "codec": "mp3",
    "bitrate_kbps": 320,
    "mode": "CBR",
    "sample_rate": 44100,
    "channels": 2,
}

#: 文件内容的哈希一律用 SHA-256（#37 冻结件同款），不用 md5。
HASH_ALGO = "sha256"


@dataclass
class Entry:
    """清单里的一条：一个文件 + 它的指纹。"""

    role: str
    path: str
    sha256: str = ""
    bytes: int = 0
    duration: float = None
    note: str = ""

    @property
    def basename(self):
        return os.path.basename(self.path)

    def to_dict(self):
        d = {
            "role": self.role,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
        }
        if self.duration is not None:
            d["duration"] = round(self.duration, 6)
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class Manifest:
    """G4-12 的判定记录。`missing_required` 非空即 FAIL。"""

    entries: list = field(default_factory=list)
    missing_required: list = field(default_factory=list)
    missing_records: list = field(default_factory=list)
    mp3: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)

    @property
    def ok(self):
        return not (self.missing_required or self.missing_records or self.problems)

    def add(self, role, path, **kw):
        self.entries.append(build_entry(role, path, **kw))
        return self.entries[-1]

    def to_dict(self):
        return {
            "gate": "G4-12",
            "status": "PASS" if self.ok else "FAIL",
            "hash_algo": HASH_ALGO,
            "deliverables": [e.to_dict() for e in self.entries],
            "missing_required": list(self.missing_required),
            "missing_records": list(self.missing_records),
            "mp3": dict(self.mp3),
            "problems": list(self.problems),
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=False)

    def to_text(self):
        """`*_manifest.txt` 的正文 —— 人工可读，机器可 diff。"""
        lines = [
            f"# manifest · gate G4-12 · {HASH_ALGO}",
            f"# status: {'PASS' if self.ok else 'FAIL'}",
            "",
        ]
        width = max((len(e.role) for e in self.entries), default=4)
        for e in self.entries:
            dur = f"{e.duration:10.6f}s" if e.duration is not None else " " * 11
            lines.append(
                f"{e.role:<{width}}  {e.bytes:>10}  {dur}  {e.sha256}  {e.path}"
            )
        if self.missing_required:
            lines.append("")
            lines.append("# 缺交付物：" + "、".join(self.missing_required))
        if self.missing_records:
            lines.append("# 缺记录：" + "、".join(self.missing_records))
        if self.mp3:
            lines.append("# mp3: " + json.dumps(self.mp3, ensure_ascii=False))
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 指纹
# ---------------------------------------------------------------------------
def sha256_of(path, *, chunk=1 << 20):
    """流式 SHA-256。master 动辄几十 MB，不整份读进内存。"""
    if not os.path.isfile(path):
        raise wavio.MissingFileError(f"文件不存在，无法计算哈希：{path}")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def build_entry(role, path, *, note="", duration=None):
    """为一个文件建清单条目。缺文件 → `MissingFileError`（不产出空条目）。"""
    if not os.path.isfile(path):
        raise wavio.MissingFileError(f"交付物缺失（{role}）：{path}")

    entry = Entry(
        role=role,
        path=path,
        sha256=sha256_of(path),
        bytes=os.path.getsize(path),
        note=note,
    )
    if duration is not None:
        entry.duration = duration
    elif path.lower().endswith(AUDIO_EXTS):
        if path.lower().endswith(".wav"):
            entry.duration = wavio.duration_of(path)
        else:
            # MP3 等非 WAV：没有 stdlib 解码器，时长只能标 N/A 而不是猜一个。
            entry.note = (entry.note + " " if entry.note else "") + "时长需外部工具"
    return entry


# ---------------------------------------------------------------------------
# G4-12 齐全性
# ---------------------------------------------------------------------------
def check_mp3(path=None, *, requires_encoder=True):
    """MP3 交付物的状态。**只校验，不编码**。

    返回 `{required, expected_spec, present, path, bytes, sha256, probe, available,
    unavailable_reason}`。

    `probe` 是对文件头的诚实探测（ID3 tag / MPEG frame sync），不是解码。
    文件缺失时 `present=False`，`unavailable_reason` 会写清「为什么没有」——
    G4-12 报表要的是这句话，不是一句 `False`。
    """
    status = {
        "required": True,
        "expected_spec": dict(MP3_SPEC),
        "present": False,
        "path": path or "",
        "bytes": 0,
        "sha256": "",
        "probe": "",
        "available": False,
        "unavailable_reason": "",
    }

    if not path or not os.path.isfile(path):
        status["unavailable_reason"] = (
            "MP3 未生成：本环境无 MP3 编码器（需 ffmpeg / lame / libmp3lame）；"
            "本模块只做规格校验与占位，不伪造 MP3 产物。"
            if requires_encoder
            else "MP3 未提供。"
        )
        return status

    status["present"] = True
    status["bytes"] = os.path.getsize(path)
    status["sha256"] = sha256_of(path)
    status["probe"] = _probe_mp3(path)
    if not path.lower().endswith(".mp3"):
        status["unavailable_reason"] = f"扩展名不是 .mp3：{path}"
    elif status["probe"].startswith("foreign:") or status["probe"] in ("not-mp3", "too-short"):
        status["unavailable_reason"] = (
            "文件头不是 MP3（疑似改名伪装）—— G4-10 要的是真 320kbps MP3，不是改名的 WAV。"
        )
    else:
        status["available"] = True
    return status


#: 常见容器/头签名 —— 命中即判定「这不是 MP3」，先于 frame sync 扫描。
#: 少了这一步，「把 WAV 改名成 .mp3」会以不低的概率被 frame sync 误判为合法
#: （PCM 里 0xFF 后跟高 3 位全 1 的字节组合并不罕见），而识破改名文件
#: 正是这个占位模块存在的意义。
_FOREIGN_HEADS = (
    (b"RIFF", "wave/riff"),
    (b"FORM", "aiff"),
    (b"fLaC", "flac"),
    (b"OggS", "ogg"),
    (b"\x1aE\xdf\xa3", "matroska"),
    (b"\x00\x00\x00\x18ftyp", "mp4"),
    (b"\x00\x00\x00\x20ftyp", "mp4"),
)


def _probe_mp3(path, head=4096):
    """不依赖解码器的格式探测：ID3v2 tag，或 MPEG-1/2 Layer III frame sync。

    只能证明「像 MP3」，**不能**证明「320kbps」。所以即便探测通过，
    清单里也只写 `probe`，不写位率 —— 不把猜测写成交付事实。

    返回 `id3` / `frame-sync` / `foreign:<容器>` / `not-mp3` / `too-short`。
    """
    with open(path, "rb") as f:
        buf = f.read(head)
    if len(buf) < 4:
        return "too-short"

    for sig, kind in _FOREIGN_HEADS:
        if buf.startswith(sig):
            return f"foreign:{kind}"

    if buf[:3] == b"ID3":
        return "id3"

    # frame sync 要有**连续两帧**才算数：单帧在任意二进制里都可能偶然出现，
    # 而两帧间距还必须等于按位率表算出的帧长 —— 这个校验既便宜又难被蒙对。
    for i in range(0, max(1, len(buf) - 4)):
        b0, b1, b2 = buf[i], buf[i + 1], buf[i + 2]
        if b0 != 0xFF or (b1 & 0xE0) != 0xE0:
            continue
        version = (b1 >> 3) & 0x03
        layer = (b1 >> 1) & 0x03
        if version not in (2, 3) or layer != 1:  # MPEG-1/2 Layer III
            continue
        if _next_frame_at(buf, i, version, b2):
            return "frame-sync"
    return "not-mp3"


def _next_frame_at(buf, offset, version, b2):
    """在 `offset` 处看似有帧头时，判断下一帧是否出现在预期位置。"""
    bitrate_index = (b2 >> 4) & 0x0F
    samplerate_index = (b2 >> 2) & 0x03
    if bitrate_index in (0, 0x0F) or samplerate_index == 3:
        return False

    # MPEG-1 Layer III 位率表；MPEG-2 减半（128..160 kbps 段）
    v1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    rates = {3: (44100, 48000, 32000)}.get(version, (22050, 24000, 16000))
    kbps = v1[bitrate_index]
    if version != 3:
        kbps //= 2
    if not kbps:
        return False

    frame_len = (144 * kbps * 1000) // rates[samplerate_index]
    nxt = offset + frame_len
    if nxt + 1 >= len(buf):
        return True  # 窗口内看不到下一帧，不能据此否定
    return buf[nxt] == 0xFF and (buf[nxt + 1] & 0xE0) == 0xE0


def check_deliverables(files, *, records=None, mp3_path=None, status="draft"):
    """对照 G4-12 检查分发文件夹。

    `files`：`{role: path}` —— role 取 `REQUIRED_DELIVERABLES` 的键（多给的自定义键
    会按「额外交付物」记录，但**不**算齐）。缺 role 或缺文件都会落进
    `missing_required`（带人话描述），不是抛异常 —— 门要的是完整报告。
    `records`：`{role: value}`，值为真才算这项记录在（路径存在 / bool）。
    """
    m = Manifest()

    for role, desc in REQUIRED_DELIVERABLES.items():
        path = files.get(role)
        if not path:
            m.missing_required.append(f"{role}（{desc}）：未提供")
            continue
        if isinstance(path, (list, tuple)):
            # 多文件角色（如编曲 MIDI 有鼓/贝斯/和声多个 .mid）
            if not path:
                m.missing_required.append(f"{role}（{desc}）：空列表")
                continue
            for i, p in enumerate(path):
                try:
                    m.add(f"{role}[{i}]", p)
                except wavio.MissingFileError:
                    m.missing_required.append(f"{role}（{desc}）：文件不存在 {p}")
            continue
        try:
            m.add(role, path)
        except wavio.MissingFileError:
            m.missing_required.append(f"{role}（{desc}）：文件不存在 {path}")

    records = records or {}
    for role, desc in REQUIRED_RECORDS.items():
        val = records.get(role)
        if isinstance(val, str):
            if not os.path.isfile(val):
                m.missing_records.append(f"{role}（{desc}）：声明路径不存在 {val}")
            else:
                m.add(f"record:{role}", val)
        elif not val:
            m.missing_records.append(f"{role}（{desc}）：未提供")

    m.mp3 = check_mp3(mp3_path)
    if not m.mp3["present"]:
        # MP3 缺失**不计入** missing_required：G4-12 的五类是 WAV/MIDI/XML/图，
        # MP3 属 G4-10 的「附加分发格式」。但必须出现在报告里，且写明原因。
        m.mp3["counted_as_missing"] = False
    m.mp3["status_at_build"] = status
    return m


def write_manifest(manifest, out_dir, *, stem="song_name_mix_v001", write_json=True):
    """落盘 `*_manifest.txt`（+ 可选 `.json`）。返回写出的路径列表。

    文本给人看（diff 友好），JSON 给 `08_qc/verify_report.json` 那条链（#60）。
    """
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    txt = os.path.join(out_dir, f"{stem}_manifest.txt")
    with open(txt, "w", encoding="utf-8", newline="\n") as f:
        f.write(manifest.to_text())
    paths.append(txt)
    if write_json:
        js = os.path.join(out_dir, f"{stem}_manifest.json")
        with open(js, "w", encoding="utf-8", newline="\n") as f:
            f.write(manifest.to_json() + "\n")
        paths.append(js)
    return paths


def verify_manifest(manifest, *, sample=()):
    """复核：对（指定的或全部）条目重算哈希，返回不一致清单。

    分工（避免和 #60 重复）：#60 管**冻结件**的常驻校验；本函数只回答
    「这份 manifest 写完之后，文件有没有被动过」—— 分发前一秒的自检。
    返回 `[]` 表示一致；不一致时返回 `[{role, path, expected, actual}]`。
    """
    roles = set(sample) if sample else {e.role for e in manifest.entries}
    drift = []
    for e in manifest.entries:
        if e.role not in roles:
            continue
        try:
            actual = sha256_of(e.path)
        except wavio.MissingFileError:
            actual = ""
        if actual != e.sha256:
            drift.append(
                {"role": e.role, "path": e.path, "expected": e.sha256, "actual": actual}
            )
    return drift
