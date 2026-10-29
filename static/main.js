import { initHistory, showHistory } from './history.js';
import { initHome, showHome } from './home.js';
import { initInterview, startInterview } from './interview.js';
import { initPrepare, openPrepare } from './prepare.js';
import { finishSession, initReport, openReport } from './report.js';
import { initSettings, openSettings } from './settings.js';
import { $, json, jsonRequest, showError } from './ui.js';

let currentInterviewId = null;

async function openInterviewPrepare(interviewId) {
  currentInterviewId = interviewId;
  await openPrepare(interviewId);
}

// mode: 'all' (весь утверждённый список) или 'weak' (только вопросы с вердиктом «доработать» или «переделать»)
async function startSession(interviewId, mode = 'all') {
  currentInterviewId = interviewId;
  try {
    const shuffle = $('shuffle-check').checked;
    await startInterview(await json(`/api/interviews/${interviewId}/sessions`, jsonRequest('POST', { mode, shuffle })));
  } catch (error) {
    showError(error.message);
  }
}

async function openHistory(interviewId) {
  currentInterviewId = interviewId;
  await showHistory(interviewId);
}

const LM_RECHECK_MS = 5000;

async function refreshStatus() {
  const status = $('status');
  try {
    const lm = (await json('/api/health')).lmstudio;
    status.className = `status ${lm.ok ? 'ok' : 'bad'}`;
    status.textContent = lm.ok ? `LM Studio: ${lm.model}` : 'LM Studio недоступен';
    const hint = $('lm-hint');
    hint.hidden = lm.ok;
    if (!lm.ok) {
      hint.textContent = `${lm.error} ${lm.hint}`;
      setTimeout(refreshStatus, LM_RECHECK_MS); // плашка сама пропадёт, когда LM Studio заработает
    }
  } catch {
    status.className = 'status bad';
    status.textContent = 'Сервер недоступен';
  }
}

initHome({ onOpen: openInterviewPrepare });
initSettings({ onOpen: openSettings, onBack: showHome });
initPrepare({
  onBack: showHome,
  onStart: (id) => startSession(id),
  onHistory: openHistory,
  onWeak: (id) => startSession(id, 'weak'),
});
initInterview({
  onBack: () => openInterviewPrepare(currentInterviewId),
  onFinish: finishSession,
});
initReport({
  onRestart: () => startSession(currentInterviewId),
  onWeak: () => startSession(currentInterviewId, 'weak'),
  onHistory: () => openHistory(currentInterviewId),
  onBack: () => openInterviewPrepare(currentInterviewId),
});
initHistory({
  onBack: () => openInterviewPrepare(currentInterviewId),
  onOpenReport: openReport,
});

// пока страница открыта, сервер знает, что он нужен; после закрытия вкладки он сам завершится
const ping = () => fetch('/api/ping', { method: 'POST' }).catch(() => {});
ping();
setInterval(ping, 15000);

showHome();
refreshStatus();
