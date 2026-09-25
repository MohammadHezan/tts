// Meeting-bot dashboard: sends an Attendee bot into a Zoom/Meet call via our
// /api/bots endpoints (the Attendee API key never reaches the browser), then
// shows what the bot heard and what it said, live. Everything rendered from
// the meeting goes through textContent - never innerHTML - since it's
// whatever anyone in the call happened to say.

const meetingUrlInput = document.getElementById('meeting-url');
const botNameInput = document.getElementById('bot-name');
const sendBtn = document.getElementById('send-bot');
const removeBtn = document.getElementById('remove-bot');
const muteBtn = document.getElementById('mute-bot');
const statusEl = document.getElementById('status');
const hintEl = document.getElementById('bot-hint');
const warningsEl = document.getElementById('warnings');
const turnsEl = document.getElementById('turns');
const readinessEl = document.getElementById('readiness');
const phoneHintEl = document.getElementById('phone-hint');

const LAST_BOT_KEY = 'interpreter.lastBotId';
const FINISHED_STATES = new Set(['ended', 'fatal_error']);
const STATE_HINTS = {
  joining: 'Bot is joining - admit it from the meeting\'s waiting room if the host has one.',
  waiting_room: 'Bot is in the waiting room - admit it from the meeting.',
  joined_not_recording: 'Bot is in the meeting. Everyone in the call will hear its translations.',
  joined_recording: 'Bot is in the meeting. Everyone in the call will hear its translations.',
  leaving: 'Bot is leaving the meeting.',
  ended: 'Bot has left the meeting.',
  fatal_error: 'Bot could not stay in the meeting.',
};

let botId = null;
let botMuted = false;
let hintedState = null;
let eventsSocket = null;
let pollTimer = null;
const turnEls = new Map();

function remember(id) {
  try {
    if (id) localStorage.setItem(LAST_BOT_KEY, id);
    else localStorage.removeItem(LAST_BOT_KEY);
  } catch (_) {
    // Private mode / blocked storage - reconnect-on-refresh just won't happen.
  }
}

function recall() {
  try {
    return localStorage.getItem(LAST_BOT_KEY);
  } catch (_) {
    return null;
  }
}

function showMuted(muted) {
  botMuted = muted;
  // Muted: the bot stops speaking in the meeting; its captions keep coming here.
  muteBtn.textContent = muted ? 'Unmute interpreter' : 'Mute interpreter';
}

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = `status status-${kind}`;
}

function addWarning(parts) {
  const p = document.createElement('p');
  for (const part of parts) {
    if (typeof part === 'string') {
      p.appendChild(document.createTextNode(part));
    } else {
      const code = document.createElement('code');
      code.textContent = part.code;
      p.appendChild(code);
    }
  }
  warningsEl.appendChild(p);
  warningsEl.hidden = false;
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `${response.status} ${response.statusText}`);
  }
  return data;
}

let meetingServiceReady = false;

function describeHardware(config) {
  const hw = config.hardware || {};
  const speech = hw.speech_model ? ` (speech: ${hw.speech_model})` : '';
  if (config.speech_on_gpu) {
    const translation = hw.translation_on_gpu === false ? ' Translation is on the processor, though.' : '';
    return `Running on the graphics card${speech}.${translation}`;
  }
  // Why not - the start script's check, or Whisper failing on the card.
  let why = '';
  if (hw.speech_gpu_error) why = ` Whisper couldn't run on the graphics card: ${hw.speech_gpu_error}`;
  else if (hw.gpu_check && !hw.gpu_check.startsWith('used')) why = ` Graphics card: ${hw.gpu_check}.`;
  else if (!hw.gpu_check) why = ' The graphics-card check didn\'t run - start it with "Start Interpreter".';
  return `Running on the processor${speech}: each sentence takes about 10-15 seconds.${why}`;
}

async function loadConfig() {
  let config;
  try {
    config = await api('GET', '/api/bots/config');
  } catch (err) {
    readinessEl.textContent = `Could not reach the translator server: ${err.message}`;
    setTimeout(loadConfig, 5000);
    return;
  }
  warningsEl.replaceChildren();
  warningsEl.hidden = true;
  meetingServiceReady = config.attendee_ready;
  readinessEl.classList.toggle('ready', config.attendee_ready);
  if (config.attendee_ready) {
    readinessEl.textContent = 'Ready. Paste a meeting link and send the interpreter in. ' + describeHardware(config);
    // Whisper still loading (it tells for sure whether the graphics card works) - look again shortly.
    if (config.hardware && config.hardware.speech_model === null) setTimeout(loadConfig, 5000);
  } else {
    // Usually the bundled meeting service still starting - check again shortly.
    readinessEl.textContent = config.attendee_problem || 'The meeting service is not ready yet.';
    setTimeout(loadConfig, 5000);
  }
  if (!botId) sendBtn.disabled = !config.attendee_ready;
  if (!config.tts_enabled) {
    addWarning([
      'Speech output is off (tts.provider: none in config.yaml) - the bot will listen and caption here, but stay silent in the meeting.',
    ]);
  }
  if (!config.callback_is_secure) {
    addWarning([
      'Attendee only streams audio to wss:// addresses, but this server would give it ',
      { code: config.callback_ws_url },
      ' - set ',
      { code: 'ATTENDEE_CALLBACK_WS_URL' },
      ' to a wss:// address it can reach. The bundled docker-compose.yml does this for you.',
    ]);
  } else if (config.callback_is_localhost) {
    addWarning([
      'Attendee will be told to connect back to ',
      { code: config.callback_ws_url },
      '. If Attendee runs in Docker or on another machine, that address points at itself, not at this server - set ',
      { code: 'ATTENDEE_CALLBACK_WS_URL' },
      ' to an address Attendee can reach.',
    ]);
  }
  if (config.phone_url) {
    phoneHintEl.textContent = `On your phone: the Interpreter app's Meeting Bot screen finds this computer by itself, or open ${config.phone_url}/bot.html (same Wi-Fi).`;
    phoneHintEl.hidden = false;
  }
}

function turnEl(turnId) {
  let el = turnEls.get(turnId);
  if (!el) {
    el = document.createElement('div');
    el.className = 'turn';
    turnEls.set(turnId, el);
    turnsEl.prepend(el);
  }
  return el;
}

function appendLine(el, tagClass, tagText, lineClass, text) {
  const tag = document.createElement('span');
  tag.className = tagClass;
  tag.textContent = tagText;
  const line = document.createElement('p');
  line.className = lineClass;
  line.textContent = text;
  el.append(tag, line);
}

function onPipelineEvent(event) {
  if (event.type === 'final' && event.text) {
    appendLine(turnEl(event.turn_id), 'heard-tag', `Heard (${event.lang})`, 'source', event.text);
  } else if (event.type === 'translation' && event.text) {
    appendLine(turnEl(event.turn_id), 'said-tag', `Bot said (${event.lang})`, 'translation', event.text);
  } else if (event.type === 'error') {
    // One sentence failed (e.g. a translation timeout); the bot carries on.
    appendLine(turnEl(event.turn_id), 'error-tag', 'Could not interpret this', 'error-text', event.error || 'unknown error');
  }
}

function openEvents(id) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  eventsSocket = new WebSocket(`${proto}//${window.location.host}/api/bots/${encodeURIComponent(id)}/events`);
  eventsSocket.onmessage = (msg) => onPipelineEvent(JSON.parse(msg.data));
  eventsSocket.onclose = () => {
    eventsSocket = null;
  };
}

async function pollState() {
  if (!botId) return;
  try {
    const bot = await api('GET', `/api/bots/${encodeURIComponent(botId)}`);
    const state = bot.state || 'unknown';
    showMuted(Boolean(bot.muted));
    setStatus(`Bot: ${state}${bot.muted ? ' (muted)' : ''}`, FINISHED_STATES.has(state) ? 'disconnected' : 'connected');
    if (state !== hintedState && STATE_HINTS[state]) {
      hintEl.textContent = [STATE_HINTS[state], bot.problem].filter(Boolean).join(' ');
      hintedState = state;
    }
    if (FINISHED_STATES.has(state)) stopTracking();
  } catch (err) {
    setStatus('Bot: status unavailable', 'error');
    hintEl.textContent = err.message;
  }
}

function track(id) {
  botId = id;
  hintedState = null;
  remember(id);
  removeBtn.hidden = false;
  muteBtn.hidden = false;
  sendBtn.disabled = true;
  openEvents(id);
  pollState();
  pollTimer = setInterval(pollState, 3000);
}

function stopTracking() {
  clearInterval(pollTimer);
  pollTimer = null;
  if (eventsSocket) eventsSocket.close();
  botId = null;
  remember(null);
  removeBtn.hidden = true;
  muteBtn.hidden = true;
  showMuted(false);
  sendBtn.disabled = !meetingServiceReady;
}

sendBtn.addEventListener('click', async () => {
  const meetingUrl = meetingUrlInput.value.trim();
  if (!meetingUrl) {
    hintEl.textContent = 'Paste a meeting link first.';
    return;
  }
  hintEl.textContent = '';
  sendBtn.disabled = true;
  setStatus('Sending bot…', 'connecting');
  try {
    const bot = await api('POST', '/api/bots', {
      meeting_url: meetingUrl,
      bot_name: botNameInput.value.trim() || 'AI Interpreter',
    });
    hintEl.textContent = 'Bot is joining - admit it from the meeting\'s waiting room if the host has one.';
    track(bot.id);
  } catch (err) {
    setStatus('Could not send bot', 'error');
    hintEl.textContent = err.message;
    sendBtn.disabled = false;
  }
});

muteBtn.addEventListener('click', async () => {
  if (!botId) return;
  try {
    const result = await api('POST', `/api/bots/${encodeURIComponent(botId)}/mute`, { muted: !botMuted });
    showMuted(result.muted);
    hintEl.textContent = result.muted
      ? 'Muted: the interpreter stays in the meeting but stops speaking. Translations still appear here. Anyone in the call can type "unmute" in the meeting chat to bring it back.'
      : 'Unmuted: the interpreter speaks its translations in the meeting again. Anyone in the call can type "mute" in the meeting chat to silence it.';
  } catch (err) {
    hintEl.textContent = err.message;
  }
});

removeBtn.addEventListener('click', async () => {
  if (!botId) return;
  try {
    await api('POST', `/api/bots/${encodeURIComponent(botId)}/leave`);
    setStatus('Bot leaving…', 'connecting');
  } catch (err) {
    hintEl.textContent = err.message;
  }
});

loadConfig();
const previous = recall();
if (previous) track(previous);
