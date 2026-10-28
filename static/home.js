import { $, element, json, jsonRequest, showError, showScreen } from './ui.js';

let openInterview = () => {};

function renderList(interviews) {
  const list = $('interview-list');
  $('home-empty').hidden = interviews.length > 0;
  list.replaceChildren(...interviews.map((item) => {
    const row = element('div', 'interview-row');
    const info = element('div');
    info.append(element('strong', '', item.title));
    const docs = Object.values(item.documents).reduce((a, b) => a + b, 0);
    const questions = item.questions === null ? 'вопросы не утверждены' : `вопросов: ${item.questions}`;
    info.append(element('div', 'muted', `${new Date(item.created_at).toLocaleDateString('ru-RU')} · документов: ${docs} · ${questions}`));
    const open = element('button', 'primary', 'Открыть');
    open.addEventListener('click', () => openInterview(item.id));
    row.append(info, open);
    return row;
  }));
}

export async function showHome() {
  showScreen('home');
  try {
    renderList(await json('/api/interviews'));
  } catch (error) {
    showError(error.message);
  }
}

export function initHome({ onOpen }) {
  openInterview = onOpen;
  $('create-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const title = $('new-title').value.trim();
    if (!title) return;
    try {
      const created = await json('/api/interviews', jsonRequest('POST', { title }));
      $('new-title').value = '';
      openInterview(created.id);
    } catch (error) {
      showError(error.message);
    }
  });
}
