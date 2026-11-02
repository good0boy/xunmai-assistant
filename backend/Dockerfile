FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/data/models \
    FASTEMBED_CACHE_PATH=/data/models

WORKDIR /app

# 国内源：apt 换阿里镜像（bookworm 的源在 /etc/apt/sources.list.d/debian.sources）
# onnxruntime 需要 libgomp；curl 用于调试探活
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

# 国内源：pip 走阿里 PyPI 镜像
COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir -U pip -i https://mirrors.aliyun.com/pypi/simple/ \
 && pip install --no-cache-dir -e . -i https://mirrors.aliyun.com/pypi/simple/

COPY scripts ./scripts
COPY tests ./tests

EXPOSE 8000

# Qdrant 的 local 模式对索引目录是独占锁，多 worker 会直接起不来，
# 所以按运行模式决定 worker 数（server 模式可以多进程）。
CMD ["sh", "-c", "if [ \"${QDRANT_MODE:-server}\" = \"local\" ]; then exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1; else exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2; fi"]
