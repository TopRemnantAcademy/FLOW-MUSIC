"""MP3 交付物：**规格校验 + 占位**，不做编码（沙箱无编码器）。

## 为什么这里没有「编码」函数

G4-10 要 `MP3 320kbps`。Python 标准库**没有** MP3 编码器：
`wave` 只写 PCM，`audioop` 已废弃且也不编码 MP3。可行路线全都要外部依赖：

    ffmpeg -i master.wav -c:a libmp3lame -b:a 320k -ar 44100 master.mp3
    lame --preset insane master.wav master.mp3

沙箱里这些都不存在（`shutil.which` 全 None）。于是只有两个选择：

  1. 伪造：把 WAV 改名成 `.mp3` 塞进 manifest —— **被本模块明确拒绝**。
     这种文件 `os.path.exists` 为真、哈希也算得出，能骗过一系列「存在性」检查，
     却骗不过任何播放器。它把「缺一个交付物」变成「分发一个坏文件」，更坏。
  2. 诚实：把「需要什么、为什么没生成、怎么补」写清楚，让 manifest 带着
     `unavailable_reason` 出门。**本模块选这个。**

所以这里提供的是：`MP3_COMMAND`（真做的时候该跑什么）、`find_encoder()`（探测）、
`transcode_placeholder()`（无编码器时返回结构化失败，有编码器时给出可执行命令但**不执行**）。
"""

from __future__ import annotations

import os
import shutil

#: G4-10 的 MP3 规格。这是**声明**，不是实测值。
MP3_BITRATE_KBPS = 320
MP3_SAMPLE_RATE = 44100
MP3_CHANNELS = 2

#: 真转码时的命令行模板（`{in}` / `{out}` 占位）。
#: ffmpeg 在前：它同时覆盖 loudnorm 两步（G4-1/G4-2 也需要它）。
MP3_COMMAND = (
    "ffmpeg -y -i {in} -c:a libmp3lame -b:a 320k -ar 44100 -ac 2 "
    "-write_xing 1 {out}"
)
#: lame 后备（无 ffmpeg 时）。`-b 320` = CBR 320kbps。
MP3_COMMAND_LAME = "lame --cbr -b 320 --resample 44.1 {in} {out}"

#: 编码器探测顺序：先 ffmpeg（顺带给出 loudnorm 能力），再 lame。
ENCODER_CANDIDATES = ("ffmpeg", "lame")

#: 无编码器时的标准说明。manifest / PR / issue 都用这一句，避免三种说法。
NO_ENCODER_REASON = (
    "本环境无 MP3 编码器（未找到 ffmpeg / lame）；MP3 320kbps 转码需外部编码器，"
    "本模块只校验存在性与命名，不伪造 MP3 产物。"
)


def find_encoder(candidates=ENCODER_CANDIDATES):
    """返回第一个可用的编码器路径，都没有则 None。

    只做 `shutil.which` 探测，**不**执行 —— 探测本身就是副作用最小的检查。
    """
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def encoder_available():
    return find_encoder() is not None


def planned_command(src, dst):
    """给出「若有编码器」会跑的命令。有 ffmpeg 用 ffmpeg，否则 lame，都没有也返回
    ffmpeg 模板 —— 它是推荐做法，比返回空串有用。"""
    exe = find_encoder()
    if exe and os.path.basename(exe).startswith("lame"):
        return MP3_COMMAND_LAME.format(**{"in": src, "out": dst})
    return MP3_COMMAND.format(**{"in": src, "out": dst})


def transcode_placeholder(src, dst, *, run=False):
    """MP3 转码的占位实现。

    默认**绝不执行**（`run=False`）：即使环境里真有编码器，编码也不该由
    「打包校验」这个环节偷偷做掉（会破坏可复现性，还拖慢 CI）。

    返回结构化结果：
      `{ok, can_encode, encoder, command, out, reason}`
      - 无编码器：`ok=False`，`reason=NO_ENCODER_REASON`；
      - 有编码器：`ok=False`（因为没跑），`can_encode=True`，`command` 可直接复制执行；
      - `run=True` 且 src 不存在：`ok=False`，reason 指出源文件缺失。
    """
    result = {
        "ok": False,
        "can_encode": False,
        "encoder": find_encoder(),
        "command": planned_command(src, dst),
        "out": dst,
        "reason": "",
    }
    if not os.path.isfile(src):
        result["reason"] = f"源 WAV 不存在：{src}"
        return result
    if not result["encoder"]:
        result["reason"] = NO_ENCODER_REASON
        return result

    result["can_encode"] = True
    if not run:
        result["reason"] = (
            f"探测到编码器 {result['encoder']}；转码需显式执行，"
            f"本模块默认不代跑（保持可复现）。命令：{result['command']}"
        )
        return result

    import subprocess  # 仅在此分支需要

    args = result["command"].split()
    proc = subprocess.run(args, capture_output=True, text=True)
    result["ok"] = proc.returncode == 0 and os.path.isfile(dst)
    result["reason"] = (
        "转码完成" if result["ok"] else f"转码失败（exit={proc.returncode}）：{proc.stderr[-400:]}"
    )
    return result


def validate_spec(path):
    """校验一个已存在的 MP3 的**可探测**属性。

    诚实的边界：stdlib 读不出 MP3 的位率（要解 MPEG 帧头表）。所以这里只报：
      - 扩展名；
      - 文件头像不像 MP3（ID3 / frame sync）；
      - 字节数是否 > 0。
    `bitrate_kbps` 一律 `None` 并附 `note` 说明需 `ffprobe` —— 不猜。
    """
    from .manifest import _probe_mp3  # 复用同一套探测，避免两处逻辑漂移

    if not os.path.isfile(path):
        return {"path": path, "ok": False, "reason": f"文件不存在：{path}"}
    probe = _probe_mp3(path)
    ext_ok = path.lower().endswith(".mp3")
    ok = ext_ok and probe in ("id3", "frame-sync")
    return {
        "path": path,
        "ok": ok,
        "bytes": os.path.getsize(path),
        "extension": os.path.splitext(path)[1],
        "probe": probe,
        "bitrate_kbps": None,
        "sample_rate": MP3_SAMPLE_RATE,
        "channels": MP3_CHANNELS,
        "note": "位率需 ffprobe -show_entries stream=bit_rate；本模块不猜。",
        "reason": "" if ok else "扩展名或文件头不像 MP3（疑似改名伪装）。",
    }
