#!/usr/bin/env bash
# Turn the trained adapter into an Ollama model: GGUF adapter + the 4B text
# model it sits on (the same Q4_K_M file the stack already uses).
#   needs a llama.cpp checkout:  git clone https://github.com/ggml-org/llama.cpp
set -euo pipefail
LLAMA_CPP=${LLAMA_CPP:-./llama.cpp}
ADAPTER=${1:-adapter}
BASE_MODEL=${BASE_MODEL:-hf.co/innerloop-dev/gemma3-4b-text:Q4_K_M}
NAME=${NAME:-gemma3-4b-ar-en-lora}

pip install -q -r "$LLAMA_CPP/requirements/requirements-convert_lora_to_gguf.txt"
python "$LLAMA_CPP/convert_lora_to_gguf.py" --base-model-id unsloth/gemma-3-4b-it --outfile "$ADAPTER/adapter.gguf" "$ADAPTER"

{ echo "FROM $BASE_MODEL"; echo "ADAPTER $PWD/$ADAPTER/adapter.gguf"; ollama show --modelfile "$BASE_MODEL" | grep -v '^FROM' | grep -v '^#'; } > Modelfile.lora
ollama create "$NAME" -f Modelfile.lora
ls -lh "$ADAPTER/adapter.gguf"
echo "Evaluate: python ../engine/test_translator_model.py --require-gpu --model $NAME --baseline $BASE_MODEL"
