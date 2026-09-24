# Translator service: the engine (VAD -> ASR -> translate -> TTS), the
# meeting-bot dashboard at /bot.html, and the /attendee/ws bridge an Attendee
# bot connects to. CPU build - see docker-compose.yml and README
# "Meeting Interpreter".
FROM python:3.11-slim

WORKDIR /srv/tts

COPY engine/requirements.txt engine/requirements.txt
# CPU-only torch first (silero-vad's metadata pulls torch in; the default
# wheel drags several GB of unused CUDA packages - see build-linux.yml).
# cryptography is for deploy/setup_secrets.py (the bundled Attendee's certificate).
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r engine/requirements.txt "cryptography>=42"

# Voices + Whisper baked in, before the code, so code changes don't re-download them.
COPY deploy/download_models.py deploy/download_models.py
RUN python deploy/download_models.py

COPY config.yaml glossary.yaml ./
COPY deploy/ deploy/
COPY engine/ engine/

WORKDIR /srv/tts/engine
ENV ENGINE_CONFIG_PATH=/srv/tts/deploy/config.docker.yaml
# 8000: dashboard, phones, web client. 8443: TLS, only for Attendee's bot
# (starts when TLS_CERT_FILE/TLS_KEY_FILE are set - see app/serve.py).
EXPOSE 8000 8443
CMD ["python", "-m", "app.serve"]
