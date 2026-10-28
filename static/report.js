import { $, element, json, showError, showScreen, STATUS_LABELS } from './ui.js';

let timer = null;

function stopPolling() {
  clearInterval(timer);
  timer = null;
}

function fillList(id, items, empty) {
  $(id).replaceChildren(...(items.length ? items : [empty]).map((text) => element('li', '', text)));
}

function stat(value, label) {
  const block = element('div', 'stat');
  block.append(element('strong', '', value), element('span', '', label));
  return block;
}

function renderReport(data) {
  $('report-loading').hidden = true;
  $('report-content').hidden = false;
  const { stats } = data;
  $('report-stats').replaceChildren(
    stat(stats.average === null ? '–' : `${stats.average} / 5`, 'средняя оценка'),
    stat(`${stats.answered} из ${stats.total}`, 'вопросов отвечено'),
    stat(String(stats.skipped), 'пропущено'),
  );
  $('report-summary').textContent = data.summary;

  const header = element('tr');
  ['№', 'Вопрос', 'Статус', 'Оценка'].forEach((title) => header.append(element('th', '', title)));
  const rows = data.rows.map((row) => {
    const tr = element('tr');
    const badge = element('span', `badge ${row.status}`, STATUS_LABELS[row.status]);
    const status = element('td');
    status.append(badge);
    tr.append(
      element('td', '', String(row.index + 1)),
      element('td', '', row.text),
      status,
      element('td', '', row.score === null ? '–' : String(row.score)),
    );
    return tr;
  });
  $('report-table').replaceChildren(header, ...rows);

  fillList('report-weak', data.weak_areas, 'Не выявлено.');
  fillList('report-repeat', data.repeat, 'Всё в порядке, повторять нечего.');
  fillList('report-reco', data.recommendations, 'Без рекомендаций.');
}

function applyStatus(status) {
  const progress = $('report-progress');
  if (status.state === 'running') {
    progress.hidden = false;
    progress.value = status.percent;
    $('report-status').textContent = `${status.message} ${status.percent}% · ${status.elapsed} с`;
    return;
  }
  stopPolling();
  if (status.state === 'done') {
    renderReport(status.report);
  } else if (status.state === 'error') {
    progress.hidden = true;
    $('report-status').textContent = 'Не удалось собрать отчёт.';
    showError(status.message);
  }
}

async function poll(sid) {
  try {
    applyStatus(await json(`/api/sessions/${sid}/finish/status`));
  } catch (error) {
    stopPolling();
    showError(error.message);
  }
}

// Завершает сессию и показывает отчёт. false, если завершить нельзя (например, нет ни одного ответа).
export async function finishSession(sid) {
  showError('');
  stopPolling();
  let status;
  try {
    status = await json(`/api/sessions/${sid}/finish`, { method: 'POST' });
  } catch (error) {
    showError(error.message);
    return false;
  }
  showScreen('finish');
  $('report-content').hidden = true;
  $('report-loading').hidden = false;
  applyStatus(status);
  if (status.state === 'running') timer = setInterval(() => poll(sid), 700);
  return true;
}

// Открывает уже готовый отчёт прошлой сессии (из истории).
export async function openReport(sid) {
  showError('');
  stopPolling();
  try {
    const result = await json(`/api/sessions/${sid}/report`);
    if (!result.report) {
      showError('Для этой сессии отчёта ещё нет. Он собирается кнопкой «Завершить и получить отчёт».');
      return;
    }
    showScreen('finish');
    renderReport(result.report);
  } catch (error) {
    showError(error.message);
  }
}

export function initReport({ onRestart, onWeak, onHistory, onBack }) {
  $('restart-btn').addEventListener('click', onRestart);
  $('weak-btn').addEventListener('click', onWeak);
  $('finish-history-btn').addEventListener('click', onHistory);
  $('finish-back-btn').addEventListener('click', onBack);
}
