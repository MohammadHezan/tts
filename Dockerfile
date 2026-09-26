# Translator service: the engine (VAD -> ASR -> translate -> TTS), the
# meeting-bot dashboard at /bot.html, and the /attendee/ws bridge an Attendee
# bot connects to. CPU build by default (VARIANT=gpu: see below) - see
# docker-compose.yml and README
# "Meeting Interpreter".
FROM python:3.11-slim

# VARIANT=gpu adds PyTorch and NVIDIA's CUDA libraries for the GPU speech
# models (docker-compose.gpu.yml builds/pulls that variant, the start scripts
# pick it when a GPU is usable).
ARG VARIANT=cpu
ARG WHISPER_MODEL=small

WORKDIR /srv/tts

COPY engine/requirements.txt engine/requirements.txt
# PyTorch first, the build for this variant (silero-vad's metadata pulls torch
# in). CPU: the CPU-only wheel - the default drags several GB of unused CUDA
# packages. GPU: the CUDA 12.8 build, for the speech model
# (app/providers/asr_cohere.py); it shares cuBLAS and cuDNN 9 with CTranslate2
# (faster-whisper). Triton goes: nothing here compiles kernels, and without a C
# compiler it only breaks. The GPU driver itself comes from the host.
# cryptography is for deploy/setup_secrets.py (the bundled Attendee's certificate).
RUN if [ "$VARIANT" = "gpu" ]; then \
      pip install --no-cache-dir "torch==2.11.*" "torchaudio==2.11.*" --index-url https://download.pytorch.org/whl/cu128 \
   && pip install --no-cache-dir -r engine/requirements.txt "cryptography>=42" \
        nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" \
        "transformers>=5.4,<6" "accelerate>=1.0" "torchao>=0.13" sentencepiece protobuf librosa \
   && pip uninstall -y triton; \
    else \
      pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
   && pip install --no-cache-dir -r engine/requirements.txt "cryptography>=42"; \
    fi
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib

# Voices + Whisper baked in, before the code, so code changes don't re-download them.
COPY deploy/download_models.py deploy/download_models.py
RUN WHISPER_MODEL=$WHISPER_MODEL python deploy/download_models.py

COPY config.yaml glossary.yaml ./
COPY deploy/ deploy/
COPY engine/ engine/

WORKDIR /srv/tts/engine
ENV ENGINE_CONFIG_PATH=/srv/tts/deploy/config.docker.yaml
# 8000: dashboard, phones, web client. 8443: TLS, only for Attendee's bot
# (starts when TLS_CERT_FILE/TLS_KEY_FILE are set - see app/serve.py).
EXPOSE 8000 8443
CMD ["python", "-m", "app.serve"]
