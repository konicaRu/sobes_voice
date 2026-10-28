import { $, element, formatDate, json, showError, showScreen } from './ui.js';

let openReport = () => {};

function renderSessions(sessions) {
  $('history-empty').hidden = sessions.length > 0;
  const header = element('tr');
  ['Дата', 'Отвечено', 'Пропущено', 'Средняя оценка', 'Отчёт'].forEach((title) => header.append(element('th', '', title)));
  const rows = sessions.map((session) => {
    const tr = element('tr');
    const report = element('td');
    if (session.finished_at) {
      const button = element('button', 'link', 'Открыть');
      button.addEventListener('click', () => openReport(session.sid));
      report.append(button);
    } else {
      report.textContent = 'не завершена';
    }
    tr.append(
      element('td', '', formatDate(session.started_at)),
      element('td', '', `${session.answered} из ${session.total}`),
      element('td', '', String(session.skipped)),
      element('td', '', session.average === null ? '–' : `${session.average} / 5`),
      report,
    );
    return tr;
  });
  $('history-sessions').replaceChildren(...(sessions.length ? [header, ...rows] : []));
}

function renderMatrix(history) {
  if (!history.sessions.length) {
    $('history-matrix').replaceChildren();
    return;
  }
  const table = element('table', 'matrix');
  const header = element('tr');
  header.append(element('th', '', 'Вопрос'));
  history.sessions.forEach((session) => header.append(element('th', '', formatDate(session.started_at))));
  table.append(header);
  history.matrix.forEach((row) => {
    const tr = element('tr');
    tr.append(element('td', 'q', row.text));
    row.cells.forEach((cell) => {
      const td = element('td', cell ? cell.status : '', cell ? (cell.score === null ? '–' : String(cell.score)) : '');
      tr.append(td);
    });
    table.append(tr);
  });
  $('history-matrix').replaceChildren(table);
}

export async function showHistory(interviewId) {
  showScreen('history');
  try {
    const history = await json(`/api/interviews/${interviewId}/history`);
    renderSessions(history.sessions);
    renderMatrix(history);
  } catch (error) {
    showError(error.message);
  }
}

export function initHistory({ onBack, onOpenReport }) {
  openReport = onOpenReport;
  $('history-back-btn').addEventListener('click', onBack);
}
