# deploy/omr-qa/Dockerfile — 对外 OMR/质检服务镜像
#
# 许可原则（硬约束）：本镜像**只含** MIT / BSD / Apache / LGPL 组件；
# 构建期会跑两次许可门禁（静态 + 动态），命中 AGPL/GPL 即**构建失败**。
#
# 构建期断言（三步，缺一不可）：
#   ① 静态许可门禁 ② 动态许可门禁 ③ **CV 冒烟**（smoke_cv.py，见 #165 T2/T3）
# CV 冒烟 = 无头容器里 `import cv2` + 真跑一次线距测量；装了非 headless 的
# opencv-python 会在此红（libxcb.so.1 缺失），把该缺陷挡在镜像外。
# 明确禁止：audiveris、homr（均为 AGPL-3.0，仅可作内部离线对照，不得进对外服务）。
# 依据：00_docs/plan/10_oss_best_stack_plan.md §2/§3/§5
#
# 构建（**以仓库根为上下文**）：
#   docker build -f music_rebuild_project/deploy/omr-qa/Dockerfile -t flow-omr-qa .
#
# 运行示例（对单页跑 G1 自动门）：
#   docker run --rm -v "$PWD/02_source:/data" flow-omr-qa \
#     /data/song01/preprocessed --auto --expected-pages 1 --json

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---- 系统依赖：Tesseract（OCR 第二票，Apache-2.0）+ 韩/中语言包；ffmpeg 供音频后处理 ----
# 注意：**不得**安装 audiveris / homr（AGPL-3.0）
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-kor tesseract-ocr-chi-sim ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- 1) 许可门禁（静态）：依赖清单逐行须标许可，且不得含 AGPL/GPL ----
COPY music_rebuild_project/tools/checks/check_licenses.py /app/tools/checks/check_licenses.py
COPY music_rebuild_project/deploy/omr-qa/requirements-omr.txt /app/deploy/omr-qa/requirements-omr.txt
RUN python /app/tools/checks/check_licenses.py --requirements /app/deploy/omr-qa/requirements-omr.txt

# ---- 2) 安装对外栈依赖 ----
RUN pip install --no-cache-dir -r /app/deploy/omr-qa/requirements-omr.txt

# ---- 2b) 强制 headless OpenCV（关键，勿删）----
# rapidocr-onnxruntime 硬声明依赖 `opencv-python`（**非** headless），而它与
# `opencv-python-headless` 共用同一个 `cv2` 包名 —— 装完后者会被前者**覆盖**，
# 于是无头容器里 `import cv2` 炸 `libGL.so.1`（GUI 构建链带 Qt/X11 共享库）。
# 这里显式卸掉非 headless 版并重装 headless，保证镜像里只剩 headless。
# 若卸不掉（pip 版本行为变化），下面的 smoke_cv.py 会以构建失败把问题拦下。
RUN pip uninstall -y opencv-python \
 && pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless

# ---- 3) 许可门禁（动态）：装完再扫环境，防止传递依赖带入 GPL/AGPL ----
RUN python /app/tools/checks/check_licenses.py --env

# ---- 4) oemer 适配补丁（HoughLinesP 形状；幂等，位点变了会响亮失败） ----
COPY music_rebuild_project/deploy/omr-qa/apply_oemer_patch.py /app/deploy/omr-qa/apply_oemer_patch.py
RUN python /app/deploy/omr-qa/apply_oemer_patch.py

# ---- 5) 应用代码（G1 自动门 + 准确率工具） ----
COPY music_rebuild_project/tools /app/tools
COPY music_rebuild_project/00_docs /app/00_docs

# ---- 6) CV 冒烟断言（构建期；与 CI 重活 stage 共用同一脚本，防"两处各写一份"）----
# 断言：无头容器内 cv2 可 import 且**真能测到线距**（unit_size_px is not None）。
# 失败即构建失败 —— 拒绝"引擎坏了但镜像照样出"的静默通过。
COPY music_rebuild_project/deploy/omr-qa/smoke_cv.py /app/deploy/omr-qa/smoke_cv.py
COPY music_rebuild_project/02_source/song01/preprocessed/song01_preprocessed_v001.jpeg \
     /app/smoke/song01_preprocessed_v001.jpeg
RUN python /app/deploy/omr-qa/smoke_cv.py /app/smoke/song01_preprocessed_v001.jpeg

WORKDIR /app/tools/checks
ENTRYPOINT ["python", "g1_image_quality.py"]
CMD ["--help"]
