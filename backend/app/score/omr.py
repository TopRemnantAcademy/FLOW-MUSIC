"""이미지/PDF 악보 → MusicXML (OMR).

oemer 는 별도 venv 에 설치되므로, product 프로세스에서 직접 import 할 수 없다.
따라서 ``omr_runner.py``(music_rebuild_project) 가 oemer 가 있는 python 으로
subprocess 기동되게 한다. ``omr_runner.omr_with_timeout`` 은 자체적으로 600s 자식
프로세스 타이머를 가지므로, 본 래퍼는 더 여유 있는 외부 백스톱만 건다.

oemer 미설치/미설정 노드에서는 ``OMRUnavailable`` → 엔드포인트 501 fail-closed.
(이 코드는 배포 노드에서만 실제 동작하며, dev 머신에서는 501 로 정직하게 거부한다.)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from app.score import OMRFailed, OMRTimeoutError, OMRUnavailable

__all__ = ["recognize_score"]

# omr_runner.omr_with_timeout(img, out) 를 기동하는 harness.
# omr_runner 가 있는 디렉터리를 sys.path 에 넣어 import 가능하게 한다.
_HARNESS = (
    "import sys; "
    "sys.path.insert(0, %r); "
    "from omr_runner import omr_with_timeout; "
    "p = omr_with_timeout(sys.argv[1], sys.argv[2]); "
    "print(str(p))"
)

# omr_runner 가 자체 600s 타이머를 가지므로 외부 백스톱은 여유 있게.
_OUTER_TIMEOUT_S = 660


def recognize_score(data: bytes, filename: str) -> bytes:
    try:
        from app.config import settings  # 지연 import: 미사용 경로가 config 에 의존하지 않게
    except Exception:
        # config/env 를 못 불러오면 OMR 비가용으로 처리(내부 500 방지, 501 fail-closed)
        raise OMRUnavailable(
            "OMR 설정을 불러올 수 없습니다 (enable_omr/omr_runner_path 미설정)"
        )

    if not getattr(settings, "enable_omr", False):
        raise OMRUnavailable(
            "이 노드에서 OMR 이 비활성화되어 있습니다 (설정: enable_omr=false)"
        )
    runner = getattr(settings, "omr_runner_path", "") or ""
    python = getattr(settings, "omr_python", "") or sys.executable
    if not runner or not Path(runner).is_file():
        raise OMRUnavailable(
            "omr_runner.py 경로가 설정되지 않았습니다 (설정: omr_runner_path)"
        )
    runner_dir = str(Path(runner).resolve().parent)

    tmp = Path(tempfile.mkdtemp(prefix="omr_"))
    try:
        img = tmp / (filename or "score.png")
        # 전처리: 라이브러리 있으면 그레이스케일/대비/업스케일 적용 → 인식 정확도 상승
        from app.score import omr_prep
        img.write_bytes(omr_prep.preprocess_image(data))
        out = tmp / "mxl"
        out.mkdir()

        cmd = [
            python, "-c", _HARNESS % runner_dir,
            str(img), str(out),
        ]
        env = {**os.environ, "PYTHONPATH": runner_dir}
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=_OUTER_TIMEOUT_S, env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise OMRTimeoutError(
                f"OMR 이 외부 타임아웃 {_OUTER_TIMEOUT_S}s 를 초과했습니다"
            ) from e

        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()[-5:]
            raise OMRFailed(
                "oemer 인식 실패: "
                + (" | ".join(tail) if tail else f"rc={proc.returncode}")
            )
        mxl = _find_mxl(out)
        if mxl is None:
            raise OMRFailed("oemer 가 MusicXML 을 출력하지 않았습니다")
        return mxl.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _find_mxl(out: Path) -> Optional[Path]:
    for ext in (".musicxml", ".xml", ".mxl"):
        hits = list(out.rglob(f"*{ext}"))
        if hits:
            return hits[0]
    return None
