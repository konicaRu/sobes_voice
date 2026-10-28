import { initHome, showHome } from './home.js';
import { initInterview, startInterview } from './interview.js';
import { initPrepare, openPrepare } from './prepare.js';
import { $, json, showError } from './ui.js';

let currentInterviewId = null;

async function openInterviewPrepare(interviewId) {
  currentInterviewId = interviewId;
  await openPrepare(interviewId);
}

async function startSession(interviewId) {
  currentInterviewId = interviewId;
  try {
    await startInterview(await json(`/api/interviews/${interviewId}/sessions`, { method: 'POST' }));
  } catch (error) {
    showError(error.message);
  }
}

async function refreshStatus() {
  const status = $('status');
  try {
    const lm = (await json('/api/health')).lmstudio;
    status.className = `status ${lm.ok ? 'ok' : 'bad'}`;
    status.textContent = lm.ok ? `LM Studio: ${lm.model}` : 'LM Studio недоступен';
    if (!lm.ok) showError(lm.error);
  } catch {
    status.className = 'status bad';
    status.textContent = 'Сервер недоступен';
  }
}

initHome({ onOpen: openInterviewPrepare });
initPrepare({ onBack: showHome, onStart: startSession });
initInterview({
  onBack: () => openInterviewPrepare(currentInterviewId),
  onRestart: () => startSession(currentInterviewId),
});

showHome();
refreshStatus();
