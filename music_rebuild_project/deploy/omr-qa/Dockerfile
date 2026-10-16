# deploy/omr-qa/Dockerfile — 对外 OMR/质检服务镜像
#
# 许可原则（硬约束）：本镜像**只含** MIT / BSD / Apache / LGPL 组件；
# 构建期会跑两次许可门禁（静态 + 动态），命中 AGPL/GPL 即**构建失败**。
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

# ---- 3) 许可门禁（动态）：装完再扫环境，防止传递依赖带入 GPL/AGPL ----
RUN python /app/tools/checks/check_licenses.py --env

# ---- 4) oemer 适配补丁（HoughLinesP 形状；幂等，位点变了会响亮失败） ----
COPY music_rebuild_project/deploy/omr-qa/apply_oemer_patch.py /app/deploy/omr-qa/apply_oemer_patch.py
RUN python /app/deploy/omr-qa/apply_oemer_patch.py

# ---- 5) 应用代码（G1 自动门 + 准确率工具） ----
COPY music_rebuild_project/tools /app/tools
COPY music_rebuild_project/00_docs /app/00_docs

WORKDIR /app/tools/checks
ENTRYPOINT ["python", "g1_image_quality.py"]
CMD ["--help"]
