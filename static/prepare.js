import { $, api, element, json, jsonRequest, showError, showScreen } from './ui.js';

const SLOTS = {
  questions: { label: 'Вопросы', hint: 'Файл со списком вопросов' },
  vacancy: { label: 'Вакансия', hint: 'Описание позиции' },
  resume: { label: 'Резюме', hint: 'Ваше резюме' },
  cases: { label: 'Кейсы', hint: 'Описания ваших кейсов' },
};
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const CATEGORY_LABELS = {
  experience: 'Опыт и кейсы',
  technical: 'Технические',
  product_case: 'Продуктовые кейсы',
  behavioral: 'Поведенческие',
  motivation: 'Мотивация',
};

let callbacks = { onBack: () => {}, onStart: (_id) => {}, onHistory: (_id) => {}, onWeak: (_id) => {} };
// ctx.draft: редактируемый список; ctx.approvedCount: сколько вопросов утверждено на сервере
let ctx = null;

// ---------- документы ----------

function renderSlots() {
  $('slots').replaceChildren(...Object.entries(SLOTS).map(([slot, meta]) => {
    const card = element('div', 'slot');
    card.append(element('strong', '', meta.label), element('div', 'muted', meta.hint));
    const files = ctx.documents[slot];
    card.append(...files.map((name) => {
      const row = element('div', 'file-row');
      const remove = element('button', 'icon', '✕');
      remove.title = 'Удалить файл';
      remove.addEventListener('click', () => removeFile(slot, name));
      row.append(element('span', '', name), remove);
      return row;
    }));
    const input = element('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.md,.txt,.docx,.pdf';
    input.addEventListener('change', () => uploadFiles(slot, [...input.files]));
    card.append(input, pasteBlock(slot));
    return card;
  }));
  $('parse-btn').disabled = ctx.documents.questions.length === 0;
  updateProfileButton();
}

// ---------- профиль кандидата ----------

let profileTimer = null;

function updateProfileButton() {
  const hasSources = ctx.documents.resume.length + ctx.documents.cases.length > 0;
  $('profile-btn').disabled = ctx.profileRunning || !hasSources;
  updateGenerateButton();
}

export function stopProfilePolling() {
  clearInterval(profileTimer);
  profileTimer = null;
}

function applyProfileStatus(status) {
  ctx.profileRunning = status.state === 'running';
  $('profile-progress').hidden = !ctx.profileRunning;
  $('profile-progress').value = status.percent;
  $('profile-btn').textContent = ctx.profileRunning ? 'Собираю профиль…' : 'Собрать профиль из резюме и кейсов';
  if (ctx.profileRunning) {
    $('profile-status').textContent = `${status.message} ${status.percent}% · ${status.elapsed} с`;
  } else {
    stopProfilePolling();
    if (status.state === 'done') {
      $('profile-text').value = status.profile;
      $('profile-status').textContent = `Профиль собран и сохранён за ${status.elapsed} с. Проверьте факты и цифры.`;
    } else if (status.state === 'error') {
      $('profile-status').textContent = '';
      showError(status.message);
    }
  }
  updateProfileButton();
}

async function pollProfile(interviewId) {
  if ($('prepare').hidden || ctx.id !== interviewId) {
    stopProfilePolling();
    return;
  }
  try {
    applyProfileStatus(await json(`/api/interviews/${interviewId}/profile/status`));
  } catch (error) {
    stopProfilePolling();
    showError(error.message);
  }
}

function startProfilePolling() {
  stopProfilePolling();
  const interviewId = ctx.id;
  profileTimer = setInterval(() => pollProfile(interviewId), 700);
}

async function buildProfile() {
  showError('');
  try {
    applyProfileStatus(await json(`/api/interviews/${ctx.id}/profile`, { method: 'POST' }));
    if (ctx.profileRunning) startProfilePolling();
  } catch (error) {
    showError(error.message);
  }
}

async function saveProfile() {
  showError('');
  try {
    const result = await json(`/api/interviews/${ctx.id}/profile`, jsonRequest('PUT', { profile: $('profile-text').value }));
    $('profile-text').value = result.profile;
    $('profile-status').textContent = 'Сохранено.';
  } catch (error) {
    showError(error.message);
  }
}

// Вставка текста вместо загрузки файла: сохраняется как md-документ слота. Черновик не теряется при перерисовке.
function pasteBlock(slot) {
  const details = element('details', 'paste');
  details.open = Boolean(ctx.paste[slot]?.open);
  details.append(element('summary', '', 'Вставить текст'));
  const text = element('textarea');
  text.rows = 5;
  text.placeholder = 'Вставьте текст сюда (скопированный из документа, письма или заметок)';
  text.value = ctx.paste[slot]?.text || '';
  text.addEventListener('input', () => { ctx.paste[slot] = { open: true, text: text.value }; });
  details.addEventListener('toggle', () => { ctx.paste[slot] = { text: ctx.paste[slot]?.text || '', open: details.open }; });
  const add = element('button', '', 'Добавить текст');
  add.addEventListener('click', () => addText(slot, text.value));
  details.append(text, add);
  return details;
}

async function addText(slot, text) {
  showError('');
  const body = text.trim();
  if (!body) {
    showError('Вставьте текст перед добавлением.');
    return;
  }
  const blob = new Blob([body], { type: 'text/markdown' });
  if (blob.size > MAX_UPLOAD_BYTES) {
    showError('Текст слишком большой (максимум 5 МБ).');
    return;
  }
  const stamp = new Date().toISOString().slice(0, 19).replace('T', ' ').replace(/:/g, '-');
  const filename = `Текст ${SLOTS[slot].label.toLowerCase()} ${stamp}.md`;
  try {
    await api(`/api/interviews/${ctx.id}/documents?slot=${slot}&filename=${encodeURIComponent(filename)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: blob,
    });
  } catch (error) {
    showError(error.message);
    return;
  }
  ctx.paste[slot] = { open: false, text: '' };
  await refreshDocuments();
}

async function refreshDocuments() {
  ctx.documents = (await json(`/api/interviews/${ctx.id}`)).documents;
  renderSlots();
}

async function uploadFiles(slot, files) {
  showError('');
  for (const file of files) {
    if (file.size > MAX_UPLOAD_BYTES) {
      showError(`${file.name}: файл слишком большой (максимум 5 МБ).`);
      continue;
    }
    try {
      await api(`/api/interviews/${ctx.id}/documents?slot=${slot}&filename=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      });
    } catch (error) {
      showError(`${file.name}: ${error.message}`);
    }
  }
  await refreshDocuments();
}

async function removeFile(slot, name) {
  try {
    await api(`/api/interviews/${ctx.id}/documents/${slot}/${encodeURIComponent(name)}`, { method: 'DELETE' });
    await refreshDocuments();
  } catch (error) {
    showError(error.message);
  }
}

// ---------- редактор вопросов ----------

const toDraft = (question) => ({
  text: question.text,
  category: question.category || 'general',
  criteria: question.what_to_check.join('\n'),
  reference: question.reference_answer || '',
});

// ---------- генерация вопросов ----------

let generateTimer = null;

function updateGenerateButton() {
  const hasSources = ctx.documents.vacancy.length > 0 || $('profile-text').value.trim() !== '';
  $('generate-btn').disabled = ctx.generating || !hasSources;
}

function stopGenerationPolling() {
  clearInterval(generateTimer);
  generateTimer = null;
}

function generationCounts() {
  return Object.fromEntries(
    Object.keys(CATEGORY_LABELS).map((key) => [key, Math.max(0, Math.min(10, Number($(`count-${key}`).value) || 0))]),
  );
}

function showGenerationPanel(visible) {
  $('generate-toggle').checked = visible;
  $('generate-panel').hidden = !visible;
}

function applyGenerationStatus(status) {
  ctx.generating = status.state === 'running';
  if (ctx.generating) showGenerationPanel(true);
  $('generate-progress').hidden = !ctx.generating;
  $('generate-progress').value = status.percent;
  $('generate-btn').textContent = ctx.generating ? 'Генерирую…' : 'Сгенерировать';
  if (ctx.generating) {
    $('generate-status').textContent = `${status.message} ${status.percent}% · ${status.elapsed} с`;
  } else {
    stopGenerationPolling();
    if (status.state === 'done') {
      ctx.draft.push(...status.questions.map(toDraft));
      markDirty();
      renderEditor();
      $('generate-status').textContent = status.questions.length
        ? `Добавлено вопросов: ${status.questions.length} (за ${status.elapsed} с). Проверьте список и утвердите его.`
        : 'Новых вопросов не получилось (все повторяли уже имеющиеся). Попробуйте ещё раз.';
    } else if (status.state === 'error') {
      $('generate-status').textContent = '';
      showError(status.message);
    }
  }
  updateGenerateButton();
}

async function pollGeneration(interviewId) {
  if ($('prepare').hidden || ctx.id !== interviewId) {
    stopGenerationPolling();
    return;
  }
  try {
    applyGenerationStatus(await json(`/api/interviews/${interviewId}/questions/generate/status`));
  } catch (error) {
    stopGenerationPolling();
    showError(error.message);
  }
}

function startGenerationPolling() {
  stopGenerationPolling();
  const interviewId = ctx.id;
  generateTimer = setInterval(() => pollGeneration(interviewId), 700);
}

async function generateQuestions() {
  showError('');
  try {
    const profile = $('profile-text').value;
    if (profile.trim()) await json(`/api/interviews/${ctx.id}/profile`, jsonRequest('PUT', { profile }));
    const existing = ctx.draft.map((item) => item.text.trim()).filter(Boolean);
    applyGenerationStatus(
      await json(`/api/interviews/${ctx.id}/questions/generate`, jsonRequest('POST', { counts: generationCounts(), existing })),
    );
    if (ctx.generating) startGenerationPolling();
  } catch (error) {
    showError(error.message);
  }
}

function updateStatus() {
  const status = $('approve-status');
  if (ctx.dirty) status.textContent = 'Есть неутверждённые изменения';
  else if (ctx.approvedCount > 0) status.textContent = `Список утверждён: ${ctx.approvedCount} вопросов`;
  else status.textContent = 'Список ещё не утверждён';
  $('start-btn').disabled = ctx.dirty || ctx.approvedCount === 0;
}

function markDirty() {
  ctx.dirty = true;
  updateStatus();
}

function move(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= ctx.draft.length) return;
  [ctx.draft[index], ctx.draft[target]] = [ctx.draft[target], ctx.draft[index]];
  markDirty();
  renderEditor();
}

function renderEditor() {
  $('questions-empty').hidden = ctx.draft.length > 0;
  $('questions-editor').replaceChildren(...ctx.draft.map((item, index) => {
    const row = element('div', 'q-row');
    const head = element('div', 'q-head');
    const title = element('strong', '', `№ ${index + 1}`);
    if (CATEGORY_LABELS[item.category]) title.append(element('span', 'q-tag', `· ${CATEGORY_LABELS[item.category]}`));
    head.append(title);
    for (const [label, title, action] of [
      ['↑', 'Выше', () => move(index, -1)],
      ['↓', 'Ниже', () => move(index, 1)],
      ['✕', 'Удалить вопрос', () => { ctx.draft.splice(index, 1); markDirty(); renderEditor(); }],
    ]) {
      const button = element('button', 'icon', label);
      button.title = title;
      button.addEventListener('click', action);
      head.append(button);
    }
    const text = element('textarea', 'q-text');
    text.rows = 2;
    text.placeholder = 'Текст вопроса';
    text.value = item.text;
    text.addEventListener('input', () => { item.text = text.value; markDirty(); });
    const criteria = element('textarea', 'q-criteria');
    criteria.rows = 2;
    criteria.placeholder = 'Что проверять (по одному пункту в строке, можно оставить пустым)';
    criteria.value = item.criteria;
    criteria.addEventListener('input', () => { item.criteria = criteria.value; markDirty(); });
    const reference = element('textarea', 'q-criteria');
    reference.rows = 2;
    reference.placeholder = 'Эталонный ответ (необязательно): как вы сами хотите отвечать. Ревьюер сверяет ответ с ним';
    reference.value = item.reference;
    reference.addEventListener('input', () => { item.reference = reference.value; markDirty(); });
    row.append(head, text, criteria, reference);
    return row;
  }));
  updateStatus();
}

async function parseQuestions() {
  showError('');
  if (ctx.draft.length > 0 && !window.confirm('Заменить текущий список вопросами из файла?')) return;
  try {
    const result = await json(`/api/interviews/${ctx.id}/questions/parse`, { method: 'POST' });
    ctx.draft = result.questions.map(toDraft);
    markDirty();
    renderEditor();
  } catch (error) {
    showError(error.message);
  }
}

async function approveQuestions() {
  showError('');
  const questions = ctx.draft
    .filter((item) => item.text.trim())
    .map((item) => ({
      text: item.text.trim(),
      category: item.category,
      what_to_check: item.criteria.split('\n').map((line) => line.trim()).filter(Boolean),
      reference_answer: item.reference.trim(),
    }));
  try {
    const saved = await json(`/api/interviews/${ctx.id}/questions`, jsonRequest('PUT', { questions }));
    ctx.draft = saved.questions.map(toDraft);
    ctx.approvedCount = saved.questions.length;
    ctx.dirty = false;
    renderEditor();
  } catch (error) {
    showError(error.message);
  }
}

// ---------- вход на экран ----------

export async function openPrepare(interviewId) {
  stopProfilePolling();
  stopGenerationPolling();
  showScreen('prepare');
  try {
    const details = await json(`/api/interviews/${interviewId}`);
    ctx = {
      id: interviewId,
      documents: details.documents,
      draft: (details.questions || []).map(toDraft),
      approvedCount: details.questions ? details.questions.length : 0,
      dirty: false,
      paste: {},
      profileRunning: false,
      generating: false,
    };
    $('prepare-title').textContent = details.interview.title;
    $('profile-text').value = details.profile || '';
    $('profile-status').textContent = '';
    $('profile-progress').hidden = true;
    showGenerationPanel(false); // генерация необязательна: по умолчанию вопросы берутся из файла
    $('generate-progress').hidden = true;
    $('generate-status').textContent = '';
    renderSlots();
    renderEditor();
    // сборка могла быть запущена до перезагрузки страницы: подхватываем ход
    const status = await json(`/api/interviews/${interviewId}/profile/status`);
    if (status.state === 'running') {
      applyProfileStatus(status);
      startProfilePolling();
    }
    const generation = await json(`/api/interviews/${interviewId}/questions/generate/status`);
    if (generation.state === 'running') {
      applyGenerationStatus(generation);
      startGenerationPolling();
    }
  } catch (error) {
    showError(error.message);
  }
}

export function initPrepare({ onBack, onStart, onHistory, onWeak }) {
  callbacks = { onBack, onStart, onHistory, onWeak };
  $('history-open-btn').addEventListener('click', () => callbacks.onHistory(ctx.id));
  $('weak-start-btn').addEventListener('click', () => callbacks.onWeak(ctx.id));
  $('back-home-btn').addEventListener('click', () => callbacks.onBack());
  $('profile-btn').addEventListener('click', buildProfile);
  $('profile-save-btn').addEventListener('click', saveProfile);
  $('generate-btn').addEventListener('click', generateQuestions);
  $('generate-toggle').addEventListener('change', () => showGenerationPanel($('generate-toggle').checked));
  $('profile-text').addEventListener('input', updateGenerateButton);
  $('parse-btn').addEventListener('click', parseQuestions);
  $('add-question-btn').addEventListener('click', () => {
    ctx.draft.push({ text: '', category: 'general', criteria: '', reference: '' });
    markDirty();
    renderEditor();
  });
  $('approve-btn').addEventListener('click', approveQuestions);
  $('start-btn').addEventListener('click', () => callbacks.onStart(ctx.id));
}
