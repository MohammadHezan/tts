# Translator service: the engine (VAD -> ASR -> translate -> TTS), the
# meeting-bot dashboard at /bot.html, and the /attendee/ws bridge an Attendee
# bot connects to. CPU build - see docker-compose.yml and README
# "Meeting Interpreter".
FROM python:3.11-slim

WORKDIR /srv/tts

COPY engine/requirements.txt engine/requirements.txt
# CPU-only torch first (silero-vad's metadata pulls torch in; the default
# wheel drags several GB of unused CUDA packages - see build-linux.yml).
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r engine/requirements.txt

COPY config.yaml glossary.yaml ./
COPY deploy/ deploy/
COPY engine/ engine/

WORKDIR /srv/tts/engine
ENV ENGINE_CONFIG_PATH=/srv/tts/deploy/config.docker.yaml
EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
