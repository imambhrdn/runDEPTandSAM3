FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    TRANSFORMERS_CACHE=/models/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir torch transformers mediapipe opencv-python-headless fastapi uvicorn requests \
    && SAM2_BUILD_CUDA=0 pip install --no-cache-dir 'git+https://github.com/facebookresearch/sam2.git'

COPY worker/ /app/worker/
COPY app.py /app/app.py

EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
