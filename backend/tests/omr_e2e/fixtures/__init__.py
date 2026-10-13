"""内置 fixture：不引入任何大二进制，全部由代码在内存里生成。

- `CANON_MUSICXML`：hymn「찬양하라 (Praise)」16 小节小样谱（MusicXML 3.1 字符串）。
- `SAMPLE_LYRIC`：韩文+英文歌词（G2-5 要求「韩文无乱码」）。
- `synth_score_png()`：用 zlib+struct 手写 PNG（stdlib，零依赖），
  画五线谱谱线 + 标题/词行黑线，够 G1 的尺寸/线距/可辨性断言用。
"""

from __future__ import annotations

import struct
import zlib

# —— 曲目常量（整条流水线的算长度都靠这三个数）——
SONG_ID = "flow-demo-001"
TITLE = "찬양하라 (Praise, mock canon)"
COMPOSER = "FLOW OMR Mock"
MEASURES = 16
BEATS = 4  # 4/4
BEAT_TYPE = 4
BPM = 96
KEY_FIFTHS = 0  # C major
BARS = 4  # 系统数（G1-7 每页系统数 ≤ 6）

#: 预期时长（秒）= 小节 × 拍数 × 60/BPM。小样总长 40s，按 G3-1/G4-5 的「8 小节基准」
#: 口径裁剪为前 8 小节 = 20s（见 orchestrator 的 G3 probe 说明）。
FULL_SECONDS = MEASURES * BEATS * 60.0 / BPM  # 40.0
BASELINE_MEASURES = 8
BASELINE_SECONDS = BASELINE_MEASURES * BEATS * 60.0 / BPM  # 20.0

#: 8 小节基准里要放进去的音节（G3-3 音节-音符映射 / G3-4 节拍位置）。
#: 恰好 16 个音节 ↔ 16 个音符（每小节 2 个 2 拍音，铺满 8 小节 4/4）——
#: 一一对应才好断言「映射错误数 = 0」，多一个少一个都不行（见 build_canon_musicxml 的 assert）。
SAMPLE_LYRIC = "찬양 하라 주 를 높 이 네 온 맘 다 해 소 리 높 여 다"

#: 韩文乱码探针：这一段（一个完整「어절」，非空格拆分后仍是单字）在 UTF-8 → latin-1
#: 往返时会直接抛 UnicodeEncodeError。若某个环节把韩文按 latin-1/cp949 静默改写，
#: 这里就会先炸 —— G2-5「韩文无乱码」是硬项，不能靠肉眼看。
KOREAN_PROBE = "찬양하라"


# ---------------------------------------------------------------------------
# MusicXML
# ---------------------------------------------------------------------------
def _pitch(step: str, octave: int, alter: int = 0) -> str:
    alter_xml = f"<alter>{alter}</alter>" if alter else ""
    return (
        f"<pitch><step>{step}</step>{alter_xml}<octave>{octave}</octave></pitch>"
    )


def _note(syllable: str | None, beats: int, *, step: str, octave: int, type_: str) -> str:
    """一个音符；带歌词音节与 `<voice>1</voice>`（往返一致性的必备形状）。"""
    lyric = ""
    if syllable:
        lyric = (
            f"<lyric number=\"1\"><syllabic>begin</syllabic>"
            f"<text>{syllable}</text></lyric>"
        )
    return (
        "<note>"
        f"{_pitch(step, octave)}"
        f"<duration>{beats}</duration>"
        "<voice>1</voice>"
        f"<type>{type_}</type>"
        f"{lyric}"
        "</note>"
    )


def _measure(number: int, notes: list[str], right: bool = True) -> str:
    barline = "" if right else ' barline="no"'
    return (
        f'<measure number="{number}"{barline}>'
        f"{''.join(notes)}"
        "</measure>"
    )


def build_canon_musicxml(measures: int = MEASURES) -> str:
    """生成 16 小节 canon 小样谱（确定性：无随机、无外部文件）。

    歌词只落在前 8 小节（G3 基准口径），后 8 小节留给编曲段落（#51 段落标注）。
    """
    syllables = SAMPLE_LYRIC.split()
    assert len(syllables) == BASELINE_MEASURES * 2, (
        "歌词音节数必须恰好铺满 8 小节（每小节 2 个音节）——"
        "G3-3「音节-音符映射正确率=100%」才有可断言的一一对应"
    )
    melody = [  # (step, octave) 每小节两个音，共 16 音符 = 8 小节
        ("C", 5), ("E", 5), ("D", 5), ("F", 5),
        ("E", 5), ("G", 5), ("F", 5), ("A", 5),
        ("G", 5), ("B", 4), ("A", 5), ("C", 6),
        ("G", 5), ("E", 5), ("D", 5), ("C", 5),
    ]
    parts = []
    for i in range(BASELINE_MEASURES):
        first = _note(syllables[i * 2], 2, step=melody[i * 2][0], octave=melody[i * 2][1], type_="half")
        second = _note(
            syllables[i * 2 + 1], 2, step=melody[i * 2 + 1][0], octave=melody[i * 2 + 1][1], type_="half"
        )
        parts.append(_measure(i + 1, [first, second]))
    for j in range(BASELINE_MEASURES, measures):
        notes = [
            _note(None, 1, step="C", octave=5, type_="quarter"),
            _note(None, 1, step="G", octave=4, type_="quarter"),
            _note(None, 1, step="E", octave=5, type_="quarter"),
            _note(None, 1, step="C", octave=5, type_="quarter"),
        ]
        parts.append(_measure(j + 1, notes))

    attributes = (
        "<attributes>"
        "<divisions>1</divisions>"
        f'<key><fifths>{KEY_FIFTHS}</fifths></key>'
        f"<time><beats>{BEATS}</beats><beat-type>{BEAT_TYPE}</beat-type></time>"
        "<clef><sign>G</sign><line>2</line></clef>"
        "</attributes>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">\n'
        '<score-partwise version="3.1">'
        f"<work><work-title>{TITLE}</work-title></work>"
        '<identification><creator type="composer">'
        f"{COMPOSER}</creator></identification>"
        '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>'
        '<part id="P1">'
        f"{attributes}"
        + "".join(parts)
        + "</part></score-partwise>"
    )


CANON_MUSICXML = build_canon_musicxml()


def broken_musicxml() -> str:
    """损坏候选：小节闭合缺失 + 音长合计=5/4（违反 G2-3）+ 音高越界。

    用于 `test_gate_fail_halts`：证明 G2 会真的拦，而不是走过场。
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<score-partwise version="3.1">'
        "<work><work-title>broken</work-title></work>"
        '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part></part-list>'
        '<part id="P1">'
        "<attributes><divisions>1</divisions>"
        f"<time><beats>{BEATS}</beats><beat-type>{BEAT_TYPE}</beat-type></time>"
        "</attributes>"
        '<measure number="1">'
        '<note><pitch><step>H</step><octave>99</octave></pitch>'
        "<duration>5</duration><voice>1</voice></note>"
        "</measure>"
        "</part>"
    )


# ---------------------------------------------------------------------------
# PNG 合成（stdlib：zlib + struct，不引入 Pillow）
# ---------------------------------------------------------------------------
def _png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0
        raw += rgba[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def synth_score_png(
    *,
    width: int = 2480,
    height: int = 3508,
    systems: int = BARS,
    line_spacing: int = 10,
) -> bytes:
    """合成一张「像乐谱」的 PNG。

    A4@300dpi = 2480×3508 px（G1-1 的短边 ≥2400 px）。每行五线谱画 5 条实线，
    线距 = `line_spacing`（G1-1 要求 ≥ 8 px）；再画标题行与 2 行词（G1-5 字高 ≥16 px）。

    只画黑/白两色，确定性（同参数 → 同字节，可哈希）。
    """
    px = bytearray(width * height * 4)
    white = 255

    def put(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 4
            px[i] = px[i + 1] = px[i + 2] = 0
            px[i + 3] = white

    def hline(x0: int, x1: int, y: int, thickness: int = 2) -> None:
        for t in range(thickness):
            for x in range(max(0, x0), min(width, x1)):
                put(x, y + t)

    def vline(y0: int, y1: int, x: int, thickness: int = 3) -> None:
        for t in range(thickness):
            for y in range(max(0, y0), min(height, y1)):
                put(x + t, y)

    # 白色底（含 alpha=255）
    for i in range(3, len(px), 4):
        px[i] = 255

    # 标题行（字高 40px）+ 词 2 行（字高 24px ≥ 16px）
    hline(400, width - 400, 200, 12)
    hline(500, 1200, 280, 8)
    for k in range(2):
        hline(400, width - 700, 340 + k * 60, 6)

    top = 460
    for s in range(systems):
        base = top + s * 360
        for n in range(5):  # 五线谱
            hline(300, width - 300, base + n * line_spacing, 2)
        # 小节线（每行 4 小节）
        for b in range(5):
            vline(base, base + 4 * line_spacing + 2, 300 + b * ((width - 600) // 4), 3)
        # 音符头（粗点）
        for b in range(4):
            cx = 380 + b * ((width - 600) // 4)
            for dy in range(-line_spacing // 2, line_spacing // 2 + 1):
                for dx in range(0, line_spacing):
                    put(cx + dx, base + 2 * line_spacing + dy)
    return _png_bytes(width, height, bytes(px))
