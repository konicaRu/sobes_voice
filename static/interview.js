import { $, api, element, json, showError, showScreen, STATUS_LABELS } from './ui.js';

const TARGET_RATE = 16000;
const VERDICTS = { ok: 'Хорошо', improve: 'Доработать', redo: 'Переделать' };

const state = {
  sid: null,
  questions: [],
  statuses: [], // new | answered | ok | improve | redo | skipped
  index: 0,
  settings: { max_answer_seconds: 300 },
  phase: 'idle', // idle | speaking | recording | transcribing | reviewing | feedback-speaking
  textVisible: false,
  audio: null,
  recorder: null,
  timer: null,
  phaseTimer: null,
};

let callbacks = { onBack: () => {}, onFinish: async (_sid) => {} };

const SHORT_TEXT_CHARS = 60;

function renderSidebar() {
  $('question-list').replaceChildren(...state.questions.map((question, index) => {
    const status = state.statuses[index];
    const button = element('button', `${status}${index === state.index ? ' current' : ''}`);
    const short = question.text.length > SHORT_TEXT_CHARS ? `${question.text.slice(0, SHORT_TEXT_CHARS)}…` : question.text;
    button.append(element('span', 'q-label', `${index + 1}. ${short}`), element('span', 'q-state', STATUS_LABELS[status]));
    button.disabled = state.phase !== 'idle';
    button.addEventListener('click', () => goTo(index));
    const item = document.createElement('li');
    item.append(button);
    return item;
  }));
}

async function goTo(index) {
  if (state.phase !== 'idle' || index === state.index) return;
  state.index = index;
  renderQuestion();
  await speakQuestion();
}

function fillList(id, items) {
  const list = $(id);
  list.replaceChildren(...items.map((text) => {
    const li = document.createElement('li');
    li.textContent = text;
    return li;
  }));
  list.parentElement.hidden = items.length === 0;
}

function formatTime(seconds) {
  const m = String(Math.floor(seconds / 60)).padStart(2, '0');
  const s = String(Math.floor(seconds % 60)).padStart(2, '0');
  return `${m}:${s}`;
}

// ---------- состояния интерфейса ----------

function setPhase(phase) {
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
  mic.textContent = labels[phase];
  clearInterval(state.phaseTimer);
  if (['transcribing', 'reviewing'].includes(phase)) {
    // пока модель работает, показываем секунды, чтобы было видно, что процесс идёт
    const started = Date.now();
    state.phaseTimer = setInterval(() => {
      mic.textContent = `${labels[phase]} ${Math.floor((Date.now() - started) / 1000)} с`;
    }, 500);
  }
  mic.disabled = !['idle', 'recording'].includes(phase);
  const free = phase === 'idle';
  const answered = !$('transcript-card').hidden;
  $('retry-btn').disabled = !free || !answered;
  $('next-btn').disabled = !free || !answered;
  $('repeat-btn').disabled = !free;
  $('back-btn').disabled = !free;
  $('skip-btn').disabled = !free;
  $('finish-btn').disabled = !free;
  renderSidebar();
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
  renderSidebar();
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
    const transcription = await json(`/api/sessions/${state.sid}/transcribe?q=${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav' },
      body: wav,
    });
    $('transcript').textContent = transcription.text;
    $('transcript-card').hidden = false;
    $('feedback-card').hidden = true;
    state.statuses[q] = 'answered';

    setPhase('reviewing');
    const review = await json(`/api/sessions/${state.sid}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q }),
    });
    renderFeedback(review);
    state.statuses[q] = review.verdict;

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

export async function startInterview(session) {
  Object.assign(state, {
    sid: session.sid,
    questions: session.questions,
    statuses: session.questions.map(() => 'new'),
    index: 0,
    settings: session.settings,
  });
  $('speak-feedback').checked = session.settings.speak_feedback;
  showScreen('interview');
  renderQuestion();
  await speakQuestion();
}

async function nextQuestion() {
  if (state.index + 1 >= state.questions.length) {
    await callbacks.onFinish(state.sid);
    return;
  }
  state.index += 1;
  renderQuestion();
  await speakQuestion();
}

async function skipQuestion() {
  showError('');
  try {
    await api(`/api/sessions/${state.sid}/skip`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q: state.index }),
    });
  } catch (error) {
    showError(error.message);
    return;
  }
  if (state.statuses[state.index] === 'new') state.statuses[state.index] = 'skipped';
  await nextQuestion();
}

export function initInterview({ onBack, onFinish }) {
  callbacks = { onBack, onFinish };
  $('mic-btn').addEventListener('click', toggleRecording);
  $('repeat-btn').addEventListener('click', speakQuestion);
  $('retry-btn').addEventListener('click', () => {
    $('transcript-card').hidden = true;
    $('feedback-card').hidden = true;
    setPhase('idle');
    startRecording();
  });
  $('next-btn').addEventListener('click', nextQuestion);
  $('back-btn').addEventListener('click', () => callbacks.onBack());
  $('skip-btn').addEventListener('click', skipQuestion);
  $('finish-btn').addEventListener('click', () => callbacks.onFinish(state.sid));
  $('toggle-text-btn').addEventListener('click', () => {
    state.textVisible = !state.textVisible;
    applyTextVisibility();
  });

  const isTypingTarget = (target) => ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
  document.addEventListener('keydown', (event) => {
    if (event.code !== 'Space' || isTypingTarget(event.target) || $('interview').hidden) return;
    event.preventDefault();
    if (!event.repeat) toggleRecording();
  });
  document.addEventListener('keyup', (event) => {
    if (event.code === 'Space' && !isTypingTarget(event.target)) event.preventDefault();
  });
}
