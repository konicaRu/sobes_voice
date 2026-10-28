// Общие помощники интерфейса: поиск элементов, ошибки, запросы к API, переключение экранов.

export const $ = (id) => document.getElementById(id);

export function showError(message) {
  const box = $('error');
  box.textContent = message;
  box.hidden = !message;
}

export async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Ошибка сервера (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') detail = body.detail;
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

const SCREENS = ['home', 'prepare', 'interview', 'finish'];

export function showScreen(name) {
  SCREENS.forEach((id) => { $(id).hidden = id !== name; });
  showError('');
}

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
