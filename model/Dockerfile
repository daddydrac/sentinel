FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --index-url https://download.pytorch.org/whl/cu124 \
        "torch==2.6.0" \
    && python3 -m pip install \
        "transformers==4.51.0" \
        "accelerate==1.6.0" \
        "safetensors==0.5.3" \
        "fastapi==0.115.12" \
        "uvicorn[standard]==0.34.2"

COPY inference.py /opt/program/inference.py
WORKDIR /opt/program

ENTRYPOINT ["python3", "/opt/program/inference.py"]
