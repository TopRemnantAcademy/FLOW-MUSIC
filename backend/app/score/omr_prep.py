"""악보 이미지 전처리 (OMR 입력 품질 향상 → 인식 정확도 상승).

oemer 는 깨끗한 스캔/촬영에서 더 정확하다. 이 모듈은:
- 그레이스케일 변환
- 대비 정규화(auto-contrast)
- 최소 해상도 보장(upscale) — 저DPI 촬영 보정
를 수행한다. **PIL/numpy 부재 노드에서는 입력을 그대로 반환**(안전 degrade),
있을 때만 활성화되므로 샌드박스/배포 양쪽에서 안전.

opencv(deskew) 는 실패 시 깨끗한 악보를 망가뜨릴 수 있어 의도적으로 제외.
"""
from __future__ import annotations

# 전처리 최소 가로 해상도 (DPI 보정 목적). 그 미만이면 업스케일.
_MIN_WIDTH = 2000


def preprocess_image(data: bytes) -> bytes:
    """악보 이미지 바이트 → 전처리된 PNG 바이트.

    PIL/numpy 없으면 원본 반환.
    """
    try:
        from io import BytesIO
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return data  # 라이브러리 없음 → degrade (원본)
    try:
        img = Image.open(BytesIO(data))
        img = img.convert("L")              # 그레이스케일
        img = ImageOps.autocontrast(img)    # 대비 정규화
        if img.width < _MIN_WIDTH:
            ratio = _MIN_WIDTH / float(img.width)
            new_h = max(1, int(img.height * ratio))
            img = img.resize((_MIN_WIDTH, new_h), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        # 깨진 이미지 등 → 원본 그대로(OCR 단계에서 422 로 걸러짐)
        return data
