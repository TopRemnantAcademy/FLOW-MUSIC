# ai-music-platform backend — FastAPI (uvicorn) 이미지
# FLOW 와 동일 베이스(python:3.11-slim) 로 공존 서버 일관성 유지.
FROM python:3.11-slim

WORKDIR /app

# 오디오 후처리 옵션 의존성(pedalboard/noisereduce/ffmpeg-normalize) 런타임 라이브러리
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
