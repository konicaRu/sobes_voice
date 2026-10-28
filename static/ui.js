// Общие помощники интерфейса: поиск элементов, ошибки, запросы к API, переключение экранов.

export const $ = (id) => document.getElementById(id);

export function showError(message) {
  const box = $('error');
  box.textContent = message;
  box.hidden = !message;
  if (message) box.scrollIntoView({ behavior: 'smooth', block: 'center' }); // ошибка не должна оставаться за кадром
}

// FastAPI отвечает так, когда такого адреса нет в запущенном коде: сервер не перезапущен после обновления
const STALE_SERVER_DETAILS = ['Method Not Allowed', 'Not Found'];
const STALE_SERVER_MESSAGE =
  'Сервер запущен на старой версии кода. Остановите его (Ctrl+C в окне PowerShell) и запустите заново, затем обновите страницу.';

export async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Ошибка сервера (${response.status})`;
    try {
      const body = await response.json();
      if (STALE_SERVER_DETAILS.includes(body.detail)) detail = STALE_SERVER_MESSAGE;
      else if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail)) detail = 'Некорректные данные. Проверьте введённые значения.';
    } catch { /* ответ не JSON */ }
    throw new Error(detail);
  }
  return response;
}

export const json = async (path, options) => (await api(path, options)).json();

export function jsonRequest(method, data) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) };
}

const SCREENS = ['home', 'prepare', 'interview', 'finish', 'history'];

export function showScreen(name) {
  SCREENS.forEach((id) => { $(id).hidden = id !== name; });
  showError('');
}

export const STATUS_LABELS = {
  ok: 'Хорошо',
  improve: 'Доработать',
  redo: 'Переделать',
  answered: 'Без разбора',
  skipped: 'Пропущен',
  new: 'Не начат',
};

export function formatDate(iso) {
  return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
