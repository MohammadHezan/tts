// Interpreter web client: getUserMedia -> AudioWorklet (resample to 16kHz
// PCM16) -> WebSocket binary frames to the engine, JSON PipelineEvents back
// -> live captions + sequential AudioContext playback of translated speech.
// No build step, no dependencies - works on any modern browser.

const engineUrlInput = document.getElementById('engine-url');
const startStopBtn = document.getElementById('start-stop');
const statusEl = document.getElementById('status');
const micHint = document.getElementById('mic-hint');
const partialCaption = document.getElementById('partial-caption');
const partialLangEl = document.getElementById('partial-lang');
const partialTextEl = document.getElementById('partial-text');
const turnsEl = document.getElementById('turns');

let ws = null;
let audioContext = null;
let workletNode = null;
let mediaStream = null;
let isActive = false;

let playbackContext = null;
let playbackQueue = Promise.resolve();

const state = { partialText: '', partialLang: '', turns: [] };

function defaultEngineUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host || 'localhost:8000';
  return `${proto}//${host}/ws`;
}

function setStatus(kind) {
  const labels = {
    disconnected: 'Not connected',
    connecting: 'Connecting…',
    connected: 'Connected — speak now',
    error: 'Connection error',
  };
  statusEl.textContent = labels[kind] || kind;
  statusEl.className = `status status-${kind}`;
  isActive = kind === 'connected' || kind === 'connecting';
  startStopBtn.textContent = isActive ? 'Stop' : 'Start';
  engineUrlInput.disabled = isActive;
}

async function start() {
  const url = engineUrlInput.value.trim();
  if (!url) return;

  setStatus('connecting');
  ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => setStatus('connected');
  ws.onerror = () => setStatus('error');
  ws.onclose = () => {
    setStatus('disconnected');
    stopMic();
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      handleEvent(JSON.parse(ev.data));
    }
  };

  try {
    await startMic();
  } catch (err) {
    micHint.textContent = `Microphone error: ${err.message}`;
    ws.close();
  }
}

function stop() {
  stopMic();
  if (ws) {
    ws.close(1000, 'client stop');
    ws = null;
  }
  setStatus('disconnected');
}

async function startMic() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('getUserMedia unavailable (needs HTTPS or localhost)');
  }
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });

  audioContext = new AudioContext();
  await audioContext.audioWorklet.addModule('pcm-worklet.js');

  const source = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, 'pcm-capture-processor', {
    processorOptions: { targetSampleRate: 16000 },
  });
  workletNode.port.onmessage = (ev) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(ev.data);
    }
  };
  source.connect(workletNode);

  // Some browsers throttle/stop AudioWorkletNodes that never reach the audio
  // graph's destination; route through a silent gain node to keep it alive
  // without producing any audible mic-monitoring feedback.
  const silentGain = audioContext.createGain();
  silentGain.gain.value = 0;
  workletNode.connect(silentGain);
  silentGain.connect(audioContext.destination);
}

function stopMic() {
  if (workletNode) {
    workletNode.port.onmessage = null;
    workletNode.disconnect();
    workletNode = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
}

function handleEvent(event) {
  switch (event.type) {
    case 'partial':
      state.partialText = event.text;
      state.partialLang = event.lang;
      break;
    case 'final':
      state.partialText = '';
      state.partialLang = '';
      state.turns.push({
        turnId: event.turn_id,
        sourceLang: event.lang,
        sourceText: event.text,
        targetLang: '',
        translations: [],
      });
      break;
    case 'translation': {
      const turn = findLastTurn(event.turn_id);
      if (turn) {
        turn.targetLang = event.lang;
        turn.translations.push(event.text);
      }
      break;
    }
    case 'audio':
      if (event.audio) enqueueAudio(event.audio, event.audio_sample_rate || 16000);
      break;
    case 'error':
      micHint.textContent = `Engine error: ${event.error || 'unknown'}`;
      break;
    default:
      break;
  }
  render();
}

function findLastTurn(turnId) {
  for (let i = state.turns.length - 1; i >= 0; i--) {
    if (state.turns[i].turnId === turnId) return state.turns[i];
  }
  return null;
}

function render() {
  if (state.partialText) {
    partialCaption.hidden = false;
    partialLangEl.textContent = state.partialLang.toUpperCase();
    partialTextEl.textContent = state.partialText;
  } else {
    partialCaption.hidden = true;
  }

  turnsEl.innerHTML = '';
  for (const turn of [...state.turns].reverse()) {
    turnsEl.appendChild(renderTurn(turn));
  }
}

function renderTurn(turn) {
  const div = document.createElement('div');
  div.className = 'turn';

  const srcTag = document.createElement('span');
  srcTag.className = 'lang-tag';
  srcTag.textContent = turn.sourceLang.toUpperCase();

  const srcText = document.createElement('p');
  srcText.className = 'source';
  srcText.textContent = turn.sourceText;

  div.append(srcTag, srcText);

  if (turn.translations.length > 0) {
    div.append(document.createElement('hr'));
    const tgtTag = document.createElement('span');
    tgtTag.className = 'lang-tag';
    tgtTag.textContent = turn.targetLang.toUpperCase();
    div.append(tgtTag);
    for (const sentence of turn.translations) {
      const p = document.createElement('p');
      p.className = 'translation';
      p.textContent = sentence;
      div.append(p);
    }
  }

  return div;
}

function enqueueAudio(base64, sampleRate) {
  playbackQueue = playbackQueue
    .then(() => playAudio(base64, sampleRate))
    .catch((err) => console.error('playback error', err));
}

async function playAudio(base64, sampleRate) {
  if (!playbackContext) playbackContext = new AudioContext();
  const pcm16 = base64ToInt16Array(base64);
  const float32 = new Float32Array(pcm16.length);
  for (let i = 0; i < pcm16.length; i++) {
    const sample = pcm16[i];
    float32[i] = sample / (sample < 0 ? 0x8000 : 0x7fff);
  }

  const buffer = playbackContext.createBuffer(1, float32.length, sampleRate);
  buffer.copyToChannel(float32, 0);

  const source = playbackContext.createBufferSource();
  source.buffer = buffer;
  source.connect(playbackContext.destination);

  return new Promise((resolve) => {
    source.onended = resolve;
    source.start();
  });
}

function base64ToInt16Array(base64) {
  // The engine (pydantic's ser_json_bytes="base64") encodes with the
  // URL-safe alphabet (- and _ instead of + and /), which atob() rejects -
  // convert back to standard base64 before decoding.
  const standard = base64.replace(/-/g, '+').replace(/_/g, '/');
  const padded = standard.padEnd(standard.length + ((4 - (standard.length % 4)) % 4), '=');
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

startStopBtn.addEventListener('click', () => {
  if (isActive) {
    stop();
  } else {
    micHint.textContent = '';
    start();
  }
});

if (!window.isSecureContext) {
  micHint.textContent = 'This page needs HTTPS (or localhost) for microphone access - see the engine README\'s "HTTPS" section.';
}

engineUrlInput.value = defaultEngineUrl();
setStatus('disconnected');
