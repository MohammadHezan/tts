// Meeting-bot dashboard: sends an Attendee bot into a Zoom/Meet call via our
// /api/bots endpoints (the Attendee API key never reaches the browser), then
// controls it (voice, chat text, pause). What the bot hears and says is shown
// only in the meeting's own chat, never on this page.

const meetingUrlInput = document.getElementById('meeting-url');
const botNameInput = document.getElementById('bot-name');
const sendBtn = document.getElementById('send-bot');
const removeBtn = document.getElementById('remove-bot');
const muteBtn = document.getElementById('mute-bot');
// Typed translations in the meeting chat, and a pause for private asides.
const textBtn = document.getElementById('text-bot');
const heardBtn = document.getElementById('heard-bot');
const pauseBtn = document.getElementById('pause-bot');
const holdBtn = document.getElementById('hold-bot');
const logoutBtn = document.getElementById('logout');
const summaryBtn = document.getElementById('summary-bot');
const LAST_MEETING_KEY = 'interpreter.lastMeeting';
let lastMeeting = null;
const switchBtns = [textBtn, heardBtn, holdBtn, pauseBtn];
// One language at a time: the meeting mixes the bot's voice for everyone, so
// each side turns off the language it doesn't need (the other still hears theirs).
const languageBtns = [document.getElementById('mute-ar'), document.getElementById('mute-en')];
const LANGUAGE_NAMES = { ar: 'Arabic', en: 'English' };
const statusEl = document.getElementById('status');
const hintEl = document.getElementById('bot-hint');
const warningsEl = document.getElementById('warnings');
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
let mutedLanguages = [];
let textMode = 'translation';
let botPaused = false;
let holdVoice = true;
let hintedState = null;
let pollTimer = null;

// The last meeting stays downloadable after the bot has left it.
function rememberMeeting(id) {
  lastMeeting = id;
  summaryBtn.hidden = false;
  try {
    localStorage.setItem(LAST_MEETING_KEY, id);
  } catch (_) {
    // Blocked storage: the document just isn't offered after a refresh.
  }
}

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

function showMuted(muted, languages = []) {
  botMuted = muted;
  mutedLanguages = languages;
  // Muted: the bot stops speaking in the meeting; its captions keep coming here.
  muteBtn.textContent = muted ? 'Voice (TTS): off' : 'Voice (TTS): on';
  for (const btn of languageBtns) {
    const off = languages.includes(btn.dataset.lang);
    btn.textContent = `${LANGUAGE_NAMES[btn.dataset.lang]} voice: ${off ? 'off' : 'on'}`;
  }
}

function showSwitches(state) {
  textMode = state.text_mode || 'translation';
  botPaused = Boolean(state.paused);
  holdVoice = state.hold !== false;
  holdBtn.textContent = `Speak when I stop: ${holdVoice ? 'on' : 'off'}`;
  textBtn.textContent = `Chat text: ${textMode === 'off' ? 'off' : 'on'}`;
  heardBtn.textContent = `Also type what was heard: ${textMode === 'both' ? 'on' : 'off'}`;
  heardBtn.disabled = textMode === 'off';
  pauseBtn.textContent = botPaused ? 'Resume interpreter' : 'Pause interpreter';
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
  if (response.status === 401) {
    window.location.href = `/login.html?next=${encodeURIComponent(window.location.pathname)}`;
    throw new Error('Log in first.');
  }
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
    let translation = '';
    if (hw.translation_on_gpu === false) translation = ' Translation is on the processor, though.';
    else if (hw.translation_gpu_share != null && hw.translation_gpu_share < 0.95) {
      translation = ` But only ${Math.round(hw.translation_gpu_share * 100)}% of the translation model fits on it - other programs are using its memory (AutoCAD, SketchUp, CapCut, games...), so translations are several times slower. Close them: the next bot you send moves the model fully onto the graphics card.`;
    }
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
    // Speech model still loading: look again shortly. Otherwise keep the line
    // current (the translation model loads, or gets squeezed off the card).
    setTimeout(loadConfig, config.hardware && config.hardware.speech_model === null ? 5000 : 15000);
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

async function pollState() {
  if (!botId) return;
  try {
    const bot = await api('GET', `/api/bots/${encodeURIComponent(botId)}`);
    const state = bot.state || 'unknown';
    if (bot.stale) {
      // Its worker died with a restart - it can't hear anything. Let go of it
      // so a new one can be sent.
      stopTracking();
      setStatus('Bot: disconnected', 'disconnected');
      hintEl.textContent = bot.problem;
      return;
    }
    showMuted(Boolean(bot.muted), bot.muted_languages || []);
    showSwitches(bot);
    setStatus(`Bot: ${state}${bot.paused ? ' (paused)' : bot.muted ? ' (voice off)' : ''}`, FINISHED_STATES.has(state) ? 'disconnected' : 'connected');
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
  rememberMeeting(id);
  hintedState = null;
  remember(id);
  removeBtn.hidden = false;
  muteBtn.hidden = false;
  for (const btn of languageBtns) btn.hidden = false;
  for (const btn of switchBtns) btn.hidden = false;
  sendBtn.disabled = true;
  pollState();
  pollTimer = setInterval(pollState, 3000);
}

function stopTracking() {
  clearInterval(pollTimer);
  pollTimer = null;
  botId = null;
  remember(null);
  removeBtn.hidden = true;
  muteBtn.hidden = true;
  for (const btn of languageBtns) btn.hidden = true;
  for (const btn of switchBtns) btn.hidden = true;
  showMuted(false);
  showSwitches({});
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
    showMuted(result.muted, result.muted_languages || []);
    hintEl.textContent = result.muted
      ? 'Voice off: the interpreter stays in the meeting but does not speak (and no voice is generated). Translations still appear in the meeting chat.'
      : 'Voice on: the interpreter speaks its translations in the meeting again.';
  } catch (err) {
    hintEl.textContent = err.message;
  }
});

for (const btn of languageBtns) {
  btn.addEventListener('click', async () => {
    if (!botId) return;
    const lang = btn.dataset.lang;
    const muted = !mutedLanguages.includes(lang);
    try {
      const result = await api('POST', `/api/bots/${encodeURIComponent(botId)}/mute`, { muted, language: lang });
      showMuted(result.muted, result.muted_languages || []);
      const other = LANGUAGE_NAMES[lang === 'ar' ? 'en' : 'ar'];
      hintEl.textContent = muted
        ? `${LANGUAGE_NAMES[lang]} voice off: the interpreter stops speaking ${LANGUAGE_NAMES[lang]} but keeps speaking ${other}.`
        : `${LANGUAGE_NAMES[lang]} voice back on.`;
    } catch (err) {
      hintEl.textContent = err.message;
    }
  });
}

async function sendSwitch(feature, value, hint) {
  if (!botId) return;
  try {
    const result = await api('POST', `/api/bots/${encodeURIComponent(botId)}/switch`, { feature, value });
    showMuted(Boolean(result.muted), result.muted_languages || []);
    showSwitches(result);
    hintEl.textContent = hint;
  } catch (err) {
    hintEl.textContent = err.message;
  }
}

textBtn.addEventListener('click', () => {
  const on = textMode === 'off';
  sendSwitch(
    'text', on ? 'on' : 'off',
    on ? 'Chat text on: each phrase is typed in the meeting chat as soon as it is ready.'
       : 'Chat text off: nothing is typed in the meeting chat.',
  );
});

heardBtn.addEventListener('click', () => {
  const both = textMode === 'both';
  sendSwitch(
    'text', both ? 'on' : 'both',
    both ? 'The meeting chat shows the translation only.' : 'The meeting chat shows what was said, then its translation.',
  );
});

holdBtn.addEventListener('click', () => {
  sendSwitch(
    'hold', holdVoice ? 'off' : 'on',
    holdVoice ? 'The voice speaks as soon as it is ready, even while someone is still talking.'
              : 'The voice is made while someone talks, and speaks once they stop.',
  );
});

pauseBtn.addEventListener('click', () => {
  const resume = botPaused;
  sendSwitch(
    'interpreter', resume ? 'resume' : 'pause',
    resume ? 'Resumed: the interpreter is listening again.'
           : 'Paused: the interpreter hears nothing and translates nothing until you resume.',
  );
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

summaryBtn.addEventListener('click', async () => {
  if (!lastMeeting) return;
  summaryBtn.disabled = true;
  hintEl.textContent = 'Getting the meeting document...';
  try {
    const response = await fetch(`/api/bots/${encodeURIComponent(lastMeeting)}/summary.docx`);
    if (response.status === 401) {
      window.location.href = `/login.html?next=${encodeURIComponent(window.location.pathname)}`;
      return;
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `${response.status} ${response.statusText}`);
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob());
    link.download = (response.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'meeting-summary.docx';
    link.click();
    URL.revokeObjectURL(link.href);
    hintEl.textContent = 'Downloaded. During a meeting it lists the sentences only; the summaries are written automatically once the meeting has ended (and kept in the meeting-data folder).';
  } catch (err) {
    hintEl.textContent = err.message;
  } finally {
    summaryBtn.disabled = false;
  }
});

logoutBtn.addEventListener('click', async () => {
  try {
    await fetch('/api/logout', { method: 'POST' });
  } finally {
    window.location.href = '/login.html';
  }
});

try {
  const earlier = localStorage.getItem(LAST_MEETING_KEY);
  if (earlier) rememberMeeting(earlier);
} catch (_) {
  // Blocked storage.
}

loadConfig();
const previous = recall();
if (previous) track(previous);
