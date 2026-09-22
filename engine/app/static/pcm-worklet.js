// AudioWorkletProcessor: captures mic audio at the browser's native sample
// rate and resamples it to 16kHz mono PCM16 (the engine's wire format),
// posting each batch back to the main thread to send over the WebSocket.
//
// Runs on the real-time audio thread - process() is called every 128-sample
// quantum regardless of what we do with it, so we accumulate a few
// AudioWorklet quanta (~20ms of native audio) before resampling+posting, to
// avoid flooding postMessage with tiny buffers.
//
// The resampler is a simple linear interpolation per batch (not phase-
// continuous across batch boundaries) - adequate for ASR input, not hi-fi
// audio; keeping it simple was the right trade-off here.
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const targetSampleRate = options.processorOptions?.targetSampleRate ?? 16000;
    this.resampleRatio = targetSampleRate / sampleRate; // `sampleRate` is a worklet global
    this.minNativeSamples = Math.round(sampleRate * 0.02); // ~20ms batches
    this.buffer = [];
  }

  process(inputs) {
    const channelData = inputs[0]?.[0];
    if (!channelData) return true;

    for (let i = 0; i < channelData.length; i++) {
      this.buffer.push(channelData[i]);
    }

    if (this.buffer.length >= this.minNativeSamples) {
      const nativeChunk = this.buffer;
      this.buffer = [];
      const int16 = this._resampleToInt16(nativeChunk);
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }
    return true;
  }

  _resampleToInt16(samples) {
    const outLength = Math.max(1, Math.round(samples.length * this.resampleRatio));
    const out = new Int16Array(outLength);
    for (let i = 0; i < outLength; i++) {
      const srcPos = i / this.resampleRatio;
      const idx0 = Math.floor(srcPos);
      const idx1 = Math.min(idx0 + 1, samples.length - 1);
      const frac = srcPos - idx0;
      const sample = samples[idx0] * (1 - frac) + samples[idx1] * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }
    return out;
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
