import { $, api, element, json, jsonRequest, showError, showScreen } from './ui.js';

const SPEAKER_LABELS = { aidar: 'Айдар (муж.)', baya: 'Бая (жен.)', kseniya: 'Ксения (жен.)', xenia: 'Ксения-2 (жен.)', eugene: 'Евгений (муж.)' };
const RATE_LABELS = { 'x-slow': 'Очень медленно', slow: 'Медленно', medium: 'Средне', fast: 'Быстро', 'x-fast': 'Очень быстро' };
const AUTO_MODEL = 'auto';

function fill(select, values, labels, current) {
  select.replaceChildren(...values.map((value) => {
    const option = element('option', '', labels[value] || value);
    option.value = value;
    return option;
  }));
  select.value = current;
}

function render(data) {
  const { values, choices } = data;
  fill($('set-speaker'), choices.speakers, SPEAKER_LABELS, values.speaker);
  fill($('set-rate'), choices.rates, RATE_LABELS, values.rate);
  const models = [AUTO_MODEL, ...choices.models.filter((m) => m !== AUTO_MODEL)];
  if (!models.includes(values.model)) models.push(values.model); // сохранённой модели может не быть в LM Studio сейчас
  fill($('set-model'), models, { [AUTO_MODEL]: 'Первая загруженная (auto)' }, values.model);
  $('set-speak-feedback').checked = values.speak_feedback;
  $('set-no-think').checked = values.disable_thinking;
  $('set-stt').textContent = `Движок: ${data.stt.engine}, модель: ${data.stt.model}. Работает на CPU. Смена движка пока не поддерживается.`;
}

function collect() {
  return {
    speaker: $('set-speaker').value,
    rate: $('set-rate').value,
    speak_feedback: $('set-speak-feedback').checked,
    model: $('set-model').value,
    disable_thinking: $('set-no-think').checked,
  };
}

async function save() {
  const data = await json('/api/settings', jsonRequest('PUT', collect()));
  render(data);
  return data;
}

export async function openSettings() {
  showScreen('settings');
  $('settings-status').textContent = '';
  try {
    render(await json('/api/settings'));
  } catch (error) {
    showError(error.message);
  }
}

async function testVoice() {
  const button = $('set-test-voice-btn');
  button.disabled = true;
  try {
    await save();
    const response = await api('/api/tts', jsonRequest('POST', { text: 'Расскажите о проекте, которым вы больше всего гордитесь.' }));
    const audio = new Audio(URL.createObjectURL(await response.blob()));
    await audio.play();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
}

export function initSettings({ onOpen, onBack }) {
  $('settings-open-btn').addEventListener('click', onOpen);
  $('settings-back-btn').addEventListener('click', onBack);
  $('set-test-voice-btn').addEventListener('click', testVoice);
  $('settings-save-btn').addEventListener('click', async () => {
    try {
      await save();
      $('settings-status').textContent = 'Сохранено';
      showError('');
    } catch (error) {
      showError(error.message);
    }
  });
}
