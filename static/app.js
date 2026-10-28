'use strict';

const TARGET_RATE = 16000;
const VERDICTS = { ok: 'Хорошо', improve: 'Доработать', redo: 'Переделать' };

const $ = (id) => document.getElementById(id);

const state = {
  sid: null,
  questions: [],
  index: 0,
  settings: { max_answer_seconds: 300 },
  phase: 'idle', // idle | speaking | recording | transcribing | reviewing | feedback-speaking
  textVisible: false,
  audio: null,
  recorder: null,
  timer: null,
};

// ---------- утилиты ----------

function showError(message) {
  const box = $('error');
  box.textContent = message;
  box.hidden = !message;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Ошибка сервера (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch { /* не JSON */ }
    throw new Error(detail);
  }
  return response;
}

function fillList(id, items) {
  const list = $(id);
  list.replaceChildren(...items.map((text) => {
    const li = document.createElement('li');
    li.textContent = text;
    return li;
  }));
  $(id).parentElement.hidden = items.length === 0;
}

function formatTime(seconds) {
  const m = String(Math.floor(seconds / 60)).padStart(2, '0');
  const s = String(Math.floor(seconds % 60)).padStart(2, '0');
  return `${m}:${s}`;
}

// ---------- состояния интерфейса ----------

function setPhase(phase, label) {
  state.phase = phase;
  const mic = $('mic-btn');
  mic.classList.toggle('recording', phase === 'recording');
  mic.classList.toggle('busy', ['transcribing', 'reviewing', 'speaking', 'feedback-speaking'].includes(phase));
  const labels = {
    idle: 'Готов',
    speaking: 'Вопрос…',
    recording: 'Стоп',
    transcribing: 'Распознаю…',
    reviewing: 'Анализирую…',
    'feedback-speaking': 'Озвучиваю…',
  };
  mic.textContent = label || labels[phase];
  mic.disabled = !['idle', 'recording'].includes(phase);
  const free = phase === 'idle';
  $('retry-btn').disabled = !free || $('transcript-card').hidden;
  $('next-btn').disabled = !free || $('transcript-card').hidden;
  $('repeat-btn').disabled = !free;
}

function renderQuestion() {
  const question = state.questions[state.index];
  $('progress').textContent = `Вопрос ${state.index + 1} из ${state.questions.length}`;
  $('question-text').textContent = question.text;
  $('transcript-card').hidden = true;
  $('feedback-card').hidden = true;
  state.textVisible = false;
  applyTextVisibility();
  $('next-btn').textContent = state.index + 1 === state.questions.length ? 'Завершить' : 'Следующий вопрос';
}

function applyTextVisibility() {
  $('question-text').classList.toggle('hidden-text', !state.textVisible);
  $('toggle-text-btn').textContent = state.textVisible ? 'Скрыть текст' : 'Показать текст';
}

function renderFeedback(review) {
  $('verdict').textContent = VERDICTS[review.verdict];
  $('verdict').className = `verdict ${review.verdict}`;
  $('score').textContent = `${review.score} / 5`;
  fillList('strengths', review.strengths);
  fillList('issues', review.issues);
  fillList('how-to-fix', review.how_to_fix);
  $('outline').textContent = review.improved_outline;
  $('feedback-card').hidden = false;
}

// ---------- звук: воспроизведение ----------

async function speak(text) {
  const response = await api('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  const url = URL.createObjectURL(await response.blob());
  await new Promise((resolve, reject) => {
    const audio = new Audio(url);
    state.audio = audio;
    audio.onended = resolve;
    audio.onerror = () => reject(new Error('Не удалось воспроизвести звук.'));
    audio.play().catch(reject);
  });
  URL.revokeObjectURL(url);
  state.audio = null;
}

async function speakQuestion() {
  showError('');
  setPhase('speaking');
  try {
    await speak(state.questions[state.index].text);
  } catch (error) {
    showError(error.message);
  }
  setPhase('idle');
}

// ---------- звук: запись ----------

function resample(input, fromRate, toRate) {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < output.length; i++) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(left + 1, input.length - 1);
    output[i] = input[left] + (input[right] - input[left]) * (position - left);
  }
  return output;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeString = (offset, text) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  });
  return new Blob([buffer], { type: 'audio/wav' });
}

async function startRecording() {
  showError('');
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch {
    showError('Нет доступа к микрофону. Разрешите его для этой страницы в браузере.');
    return;
  }
  const context = new AudioContext();
  await context.audioWorklet.addModule('recorder-worklet.js');
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, 'recorder');
  const chunks = [];
  node.port.onmessage = (event) => chunks.push(event.data);
  source.connect(node);

  const startedAt = Date.now();
  state.recorder = { stream, context, node, source, chunks, startedAt };
  setPhase('recording');
  state.timer = setInterval(() => {
    const seconds = (Date.now() - startedAt) / 1000;
    $('mic-btn').textContent = `Стоп ${formatTime(seconds)}`;
    if (seconds >= state.settings.max_answer_seconds) stopRecording();
    else if (seconds >= state.settings.max_answer_seconds - 30) showError('Скоро лимит длины ответа.');
  }, 250);
}

async function stopRecording() {
  const { stream, context, node, source, chunks } = state.recorder;
  state.recorder = null;
  clearInterval(state.timer);
  source.disconnect();
  node.disconnect();
  stream.getTracks().forEach((track) => track.stop());
  const sampleRate = context.sampleRate;
  await context.close();

  const total = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const samples = new Float32Array(total);
  let offset = 0;
  chunks.forEach((chunk) => { samples.set(chunk, offset); offset += chunk.length; });

  await processAnswer(encodeWav(resample(samples, sampleRate, TARGET_RATE), TARGET_RATE));
}

// ---------- ответ: распознавание и разбор ----------

async function processAnswer(wav) {
  const q = state.index;
  try {
    setPhase('transcribing');
    const transcription = await (await api(`/api/sessions/${state.sid}/transcribe?q=${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav' },
      body: wav,
    })).json();
    $('transcript').textContent = transcription.text;
    $('transcript-card').hidden = false;
    $('feedback-card').hidden = true;

    setPhase('reviewing');
    const review = await (await api(`/api/sessions/${state.sid}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q }),
    })).json();
    renderFeedback(review);

    if ($('speak-feedback').checked) {
      setPhase('feedback-speaking');
      await speak(review.spoken_summary);
    }
  } catch (error) {
    showError(error.message);
  }
  setPhase('idle');
}

function toggleRecording() {
  if (state.phase === 'idle') startRecording();
  else if (state.phase === 'recording') stopRecording();
}

// ---------- навигация ----------

async function startSession() {
  showError('');
  try {
    const session = await (await api('/api/sessions', { method: 'POST' })).json();
    Object.assign(state, { sid: session.sid, questions: session.questions, index: 0, settings: session.settings });
  } catch (error) {
    showError(error.message);
    return;
  }
  $('speak-feedback').checked = state.settings.speak_feedback;
  $('start-screen').hidden = true;
  $('finish-screen').hidden = true;
  $('interview').hidden = false;
  renderQuestion();
  await speakQuestion();
}

async function nextQuestion() {
  if (state.index + 1 >= state.questions.length) {
    $('interview').hidden = true;
    $('finish-screen').hidden = false;
    return;
  }
  state.index += 1;
  renderQuestion();
  await speakQuestion();
}

async function refreshStatus() {
  const status = $('status');
  try {
    const health = await (await api('/api/health')).json();
    const lm = health.lmstudio;
    status.className = `status ${lm.ok ? 'ok' : 'bad'}`;
    status.textContent = lm.ok ? `LM Studio: ${lm.model}` : 'LM Studio недоступен';
    if (!lm.ok) showError(lm.error);
  } catch (error) {
    status.className = 'status bad';
    status.textContent = 'Сервер недоступен';
  }
}

// ---------- события ----------

$('start-btn').addEventListener('click', startSession);
$('restart-btn').addEventListener('click', startSession);
$('mic-btn').addEventListener('click', toggleRecording);
$('repeat-btn').addEventListener('click', speakQuestion);
$('retry-btn').addEventListener('click', () => {
  $('transcript-card').hidden = true;
  $('feedback-card').hidden = true;
  setPhase('idle');
  startRecording();
});
$('next-btn').addEventListener('click', nextQuestion);
$('toggle-text-btn').addEventListener('click', () => {
  state.textVisible = !state.textVisible;
  applyTextVisibility();
});

function isTypingTarget(target) {
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

document.addEventListener('keydown', (event) => {
  if (event.code !== 'Space' || isTypingTarget(event.target) || $('interview').hidden) return;
  event.preventDefault();
  if (!event.repeat) toggleRecording();
});
document.addEventListener('keyup', (event) => {
  if (event.code === 'Space' && !isTypingTarget(event.target)) event.preventDefault();
});

refreshStatus();
