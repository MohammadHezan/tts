# Handoff: test the interpreter's models on the GPU PC and continue

Paste this file into a new Claude Code chat opened **on the PC that has the NVIDIA GPU**, inside a fresh clone/pull of this repo. The previous session ran on a Dell laptop with only an Intel iGPU, so every number below is CPU-only. Your job: run the tests on the GPU, report real results, and finish the unfinished items at the bottom.

## 0. First, in the new chat
1. `git pull` on branch `claude/arabic-english-speech-translation-72v8pb` (latest pushed commit: `e9b454e`).
2. `nvidia-smi` must work. Ollama must be installed and running (`ollama list`).
3. Use a venv with: `torch` (CUDA build), `transformers`, `httpx`, `pydantic`, `pyyaml`, `python-dotenv`, `numpy`, `soundfile`, `edge-tts`, `faster-whisper`, `datasets`, `gguf`, `safetensors`, `sentencepiece`. (`engine/requirements.txt` has most.)
4. Do not commit or push unless the user asks.

## 1. What the project is
`engine/` is a live Arabic <-> English meeting interpreter bot: VAD (Silero) -> speech recognition -> translation (LLM through Ollama) -> neural voice (edge-tts, with Piper/Kokoro offline fallback). Configs: `config.yaml` (local), `deploy/config.docker.yaml` (CPU stack), `deploy/config.docker-gpu.yaml` (GPU stack, Cohere ASR), compose files `docker-compose.yml` + `docker-compose.gpu.yml`. The user is a Jordanian furniture retailer talking to Australian counterparts (Jordanian Arabic and Australian English are the only dialects that matter).

## 2. Done and pushed (commit e9b454e)
- **Translation model is now `hf.co/innerloop-dev/gemma3-4b-text:Q4_K_M`** in every config and compose pull (it is Ollama's gemma3:4b Q4_K_M with the vision tower removed; no fine-tune; 2.5 GB). `provider: ollama` is just the server.
- **Glossary-dumping fix**: `engine/app/prompts.py` + `glossary.py` now send only the glossary terms found in the sentence, as a bracketed line on that message (`Glossary.format_hint`), and the system prompt is shorter (~1,200 chars). Both translator providers pass the glossary through.
- `engine/test_translator_model.py`: 44-case benchmark (tiny/short/dialect/numbers/codes/glossary/no-reply/garbled/fragment/mixed/edge/context/medium/long/huge), timestamps per case, time-to-first-token (streaming), Ollama timing split, accuracy score (hard checks + chrF), `--baseline`, `--also`, `--require-gpu` (checks `/api/ps` that the model is fully in VRAM), `--json`.
- `deploy/prune_model.py`: GGUF vocabulary pruning (and optional `--drop-layers`) for the 4B model.
- Cohere ASR: `asr.cohere.trust_remote_code` loader (AutoProcessor / AutoModelForSpeechSeq2Seq, as the model card says), `model` may be a local folder, `processor: null` = use the model's own repo, `HF_TOKEN` passed to `asr-pull`, clear gated-repo message in `deploy/download_asr.py`.
- `engine/test_asr_voices.py`: ASR harness: edge-tts male/female voices (English US/UK/AU/IN/IE; Arabic JO/LB/SY/EG/SA/AE/MA) under 11 conditions (clean, very quiet, quiet, loud+clipped, deep/high pitch, no bass, boomy bass, phone line, two noise levels); WER/CER normalised for Arabic; `--model cohere:REPO[@PROCESSOR]` or `whisper:NAME`; `--manifest` for real recordings.
- `deploy/prune_asr.py`: drops non-en/ar language tags and non-Latin/Arabic pieces from the Cohere SentencePiece vocab (16,384 -> ~11.6k) and slices the embedding/head rows. Tested only with fake weights; real gain ~1-2% (vocabulary is 1.6% of the 2.07B params; languages live in the shared encoder and cannot be removed). Has `--inspect`.
- `training/` (prepare_data.py, train_lora.py, export_adapter.sh): QLoRA pipeline for an ar<->en adapter. **Never run** (needs a GPU). Syntax-checked; `prepare_data.py` run on 600 pairs.

## 3. Results so far (CPU only: 4 cores, no GPU) — translation, 44 cases
| | Llama 3.1 8B (old) | Gemma 3 4B | 4B safe-pruned | 4B aggressive-pruned |
|---|---|---|---|---|
| size | 4.9 GB | 2.5 GB | 2.3 GB | 2.1 GB |
| pass / score / chrF | 82% / 0.912 / 0.641 | **96% / 0.977 / 0.727** | same | same |
| en->ar score | 0.888 | 0.973 | 0.973 | 0.973 |
| tok/s | 3.6 | 7.4 | 7.8 | 8.1 |
| first token p50 | 3.2 s | 12.0 s | 12.1 s | 12.7 s |
| total p50 / p95 | 8.6 s / 86.9 s | 14.4 s / 50.6 s | 14.5 s / 56.7 s | 15.1 s / 46.6 s |
Why CPU TTFT is high for Gemma: Ollama cannot reuse the prompt cache for Gemma 3 (sliding-window attention), so every sentence re-reads the prompt (~400 tokens, ~12 s on 4 cores). Llama reuses it. **This should be negligible on a GPU: confirming that is the main thing to measure.** Estimate (unmeasured): 12B ~1 s/sentence -> 4B ~0.4-0.5 s.
ASR harness smoke test only: Whisper small on CPU, 12 clips x 5 conditions: WER 21-24% in all conditions, 100% language id (digits vs spelled-out numbers inflate WER; Cohere/Whisper write "$4850").

## 4. Lessons learned (do not repeat)
- The first pruned GGUFs scored badly because the keep-rule dropped `\n`/`\t` tokens (the chat template is made of newlines). Fixed in `prune_model.py` (range 0x09-0x0D). Always benchmark a pruned model against the unpruned one.
- Layer dropping (`--drop-layers 12-17`) without LoRA "healing" produced a model that echoes the English back. Needs training to be usable.
- Cohere Transcribe repos are **gated** (HTTP 401 without an accepted licence + `HF_TOKEN`). The previous session could not download `CohereLabs/cohere-transcribe-03-2026`; the user said they would download it manually. Existing config uses the dialect fine-tune `oddadmix/cohere-transcribe-arabic-07-2026-dialectal-v2` with processor from `Newmetrics/cohere-transcribe-arabic-07-2026` (the Newmetrics copy is public and already lists only en+ar).
- `engine/app/providers/translator_ollama.py` once had a broken uncommitted edit (stream=True with `httpx.JSONDecodeError(...)`) from outside the session; it was restored to the committed version. If you see `stream: True` in that file again, it is not from this work.

## 5. Run on the GPU PC (do these, report numbers)
```bash
ollama pull hf.co/innerloop-dev/gemma3-4b-text:Q4_K_M
ollama pull hf.co/unsloth/gemma-3-12b-it-GGUF:IQ4_XS          # old GPU model, as baseline
cd engine
python test_translator_model.py --require-gpu \
  --baseline hf.co/unsloth/gemma-3-12b-it-GGUF:IQ4_XS --runs 3 --json ../results_translator_gpu.json
python test_asr_voices.py --model whisper:large-v3-turbo \
  --model "cohere:oddadmix/cohere-transcribe-arabic-07-2026-dialectal-v2@Newmetrics/cohere-transcribe-arabic-07-2026" \
  --json ../results_asr_gpu.json
# only if the user has accepted the licence and downloaded it:
python test_asr_voices.py --model cohere:/path/to/cohere-transcribe-03-2026 --model whisper:large-v3-turbo
```
Report: GPU name/VRAM, % of each model in VRAM, TTFT and total latency (p50/p95), tok/s, pass rate/score, WER/CER by condition/voice/gender, and CPU-vs-GPU comparison with section 3. Flag any case where a model spills to the CPU.

## 6. Now written (this chat) and what is left
**Written and pushed:** `deploy/run_gpu_suite.py` and `engine/test_tts_silma.py`.
- `python deploy/run_gpu_suite.py` runs everything on the GPU PC (refuses without `nvidia-smi`), pulls the Ollama models, and writes `results/gpu_suite_<time>/SUMMARY.md` + JSON + logs. Options: `--cohere-dir <folder>` (your manual download of cohere-transcribe-03-2026), `--skip-pull`, `--runs N`, `--dry-run`, `--cpu-smoke`.
- Verified on the old CPU-only laptop: `--dry-run`, the no-GPU refusal, and `--cpu-smoke` (translator + Whisper-small ASR + edge-tts baseline all ran and the summary rendered).
- **NOT verified: the SILMA branch of `test_tts_silma.py`.** `silma-tts` was not installed in the test venv (and `ffmpeg` was missing), so only the edge-tts baseline path ran. Expect small fixes on first real run: `SilmaTTS().infer(...)` return values, the reference-audio handling, and device selection. Install: `pip install silma-tts` + `ffmpeg` on PATH.
- Cohere 03-2026 not downloaded (gated): the new chat should ask the user for the folder path, or have the user accept the licence and run `hf download CohereLabs/cohere-transcribe-03-2026 --local-dir <folder>` themselves.

**Next in the GPU chat:** run `python deploy/run_gpu_suite.py --cohere-dir <folder if available>`, fix whatever breaks in the SILMA test, then read `SUMMARY.md` and report: GPU translator numbers vs section 3, ASR WER/CER by condition (quiet/loud/bass/pitch/noise) for Whisper vs Cohere models, SILMA vs edge-tts (RTF, VRAM, WER/CER incl. diacritized Arabic and spelled letters). Then decide with the user whether to add SILMA as a `tts.provider`, and whether to train the LoRA (`training/`) and test it on real Jordanian / Australian recordings (`test_asr_voices.py --manifest`).

## 7. State of the old machine's working tree (HANDOFF.md and the two new scripts ARE pushed)
- `config.yaml`: uncommitted edit removing `domain_prompt: retail_furniture` (not from the AI; the code defaults to the same value). Ask the user whether to keep it.
- `engine/test_time_process.py`: untracked, the user's own latency tool; already updated to the new `build_messages(..., glossary)` signature on disk but not committed.
- Scratch artifacts (benchmark JSONs, pruned GGUFs, fake ASR dir) lived in a temp folder on the old machine and are not in the repo; rebuild pruned models with `deploy/prune_model.py <gemma3 gguf blob> out.gguf [--latin-frequent-only 50000]` then `ollama create` using the original model's Modelfile with `FROM` changed. Ollama's blob path is `/usr/share/ollama/.ollama/models/blobs/sha256-199388f8...` on Linux; use `ollama show --modelfile` to locate it elsewhere.
