#!/usr/bin/env bash
# 命令行自测：合成 4 个 WAV，分别验证五项判定的结论与退出码。
# 用途：CI/门控调用前的 smoke；不用 pytest 也能验证脚本可用。
# 退出码：0 = 全部符合预期；非 0 = 有偏差。
set -u
cd "$(dirname "$0")" || exit 2
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PY=${PYTHON:-python3}

"$PY" - "$TMP" <<'PYEOF'
import math, struct, sys, wave
from pathlib import Path
d = Path(sys.argv[1])

def wav(name, seconds, rate=44100, amp=0.5, silent=False, dc=False, tail=0.0):
    n, t, mx = int(rate*seconds), int(rate*tail), 32767
    buf = bytearray()
    for i in range(n+t):
        v = 0.0 if (silent or i >= n) else (1.0 if dc else amp*math.sin(2*math.pi*440*i/rate))
        buf += struct.pack("<h", int(max(-1.0, min(1.0, v))*mx))
    with wave.open(str(d/name), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate); f.writeframes(bytes(buf))

wav("ok.wav", 3.0)                    # 正常
wav("silent.wav", 3.0, silent=True)   # 纯静音
wav("clipped.wav", 3.0, dc=True)      # 满幅削波
wav("wrong_duration.wav", 4.9)        # 时长不符（期望 3.0）
PYEOF

fail=0
expect() {  # expect <期望码> <描述> <参数...>
  want=$1; desc=$2; shift 2
  "$PY" qc_file_check.py "$@" >/dev/null 2>&1
  got=$?
  if [ "$got" = "$want" ]; then
    echo "  OK   exit=$got  $desc"
  else
    echo "  BAD  exit=$got (expected $want)  $desc"; fail=1
  fi
}

echo "qc_file_check.py 命令行自测"
expect 0 "正常 WAV + 时长匹配 → PASS"      "$TMP/ok.wav" --duration-expected 3.0
expect 1 "纯静音 → FAIL(silence)"           "$TMP/silent.wav" --duration-expected 3.0
expect 1 "满幅削波(-0.1dBFS) → FAIL(clipping)" "$TMP/clipped.wav" --clipping-dbfs -0.1
expect 0 "满幅削波(严格 0dBFS) → PASS(clipping=0)" "$TMP/clipped.wav"
expect 1 "时长 4.9s vs 3.0s → FAIL(duration)"  "$TMP/wrong_duration.wav" --duration-expected 3.0
expect 1 "文件不存在 → FAIL(exists)"           "$TMP/ghost.wav"
expect 2 "参数非法 → 用法错误"                  "$TMP/ok.wav" --silence-max-ratio 5

[ "$fail" = 0 ] && echo "self-test: ALL OK" || echo "self-test: FAILURES"
exit "$fail"
