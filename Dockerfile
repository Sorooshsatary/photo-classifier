FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install torch==2.7.0 torchvision==0.22.0 \
        --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

COPY server.py model_engine.py ./
COPY static ./static

RUN mkdir -p dataset models \
    && mkdir -p dataset/class_0 dataset/class_1 dataset/class_2

EXPOSE 7860

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
