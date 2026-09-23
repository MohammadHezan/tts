// Meeting-bot dashboard: sends an Attendee bot into a Zoom/Meet call via our
// /api/bots endpoints (the Attendee API key never reaches the browser), then
// shows what the bot heard and what it said, live. Everything rendered from
// the meeting goes through textContent - never innerHTML - since it's
// whatever anyone in the call happened to say.

const meetingUrlInput = document.getElementById('meeting-url');
const botNameInput = document.getElementById('bot-name');
const sendBtn = document.getElementById('send-bot');
const removeBtn = document.getElementById('remove-bot');
const statusEl = document.getElementById('status');
const hintEl = document.getElementById('bot-hint');
const warningsEl = document.getElementById('warnings');
const turnsEl = document.getElementById('turns');

const LAST_BOT_KEY = 'interpreter.lastBotId';
const FINISHED_STATES = new Set(['ended', 'fatal_error']);

let botId = null;
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

async function loadConfig() {
  let config;
  try {
    config = await api('GET', '/api/bots/config');
  } catch (err) {
    addWarning([`Could not reach the translator server: ${err.message}`]);
    return;
  }
  if (!config.attendee_configured) {
    addWarning([
      'Attendee is not configured. Set ',
      { code: 'ATTENDEE_BASE_URL' },
      ' and ',
      { code: 'ATTENDEE_API_KEY' },
      ' in .env and restart - see README "Meeting Interpreter".',
    ]);
    sendBtn.disabled = true;
  }
  if (!config.tts_enabled) {
    addWarning([
      'Speech output is off (tts.provider: none in config.yaml) - the bot will listen and caption here, but stay silent in the meeting.',
    ]);
  }
  if (config.callback_is_localhost) {
    addWarning([
      'Attendee will be told to connect back to ',
      { code: config.callback_ws_url },
      '. If Attendee runs in Docker or on another machine, that address points at itself, not at this server - set ',
      { code: 'ATTENDEE_CALLBACK_WS_URL' },
      ' to this machine\'s LAN address, or open this page via that address instead of localhost.',
    ]);
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
    hintEl.textContent = `Pipeline error: ${event.text}`;
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
    setStatus(`Bot: ${state}`, FINISHED_STATES.has(state) ? 'disconnected' : 'connected');
    if (FINISHED_STATES.has(state)) stopTracking();
  } catch (err) {
    setStatus('Bot: status unavailable', 'error');
    hintEl.textContent = err.message;
  }
}

function track(id) {
  botId = id;
  remember(id);
  removeBtn.hidden = false;
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
  sendBtn.disabled = false;
}

sendBtn.addEventListener('click', async () => {
  const meetingUrl = meetingUrlInput.value.trim();
  if (!meetingUrl) {
    hintEl.textContent = 'Paste a Zoom or Google Meet link first.';
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
