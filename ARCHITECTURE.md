# ARCHITECTURE

## Назначение
Локальный голосовой тренажёр собеседований. Подробная архитектура и этапы: [PLAN.md](PLAN.md). Правила работы: [CLAUDE.md](CLAUDE.md).

## Структура
Целевая структура описана в PLAN.md, раздел 3. Фактическое состояние (после этапа 2):

- `PLAN.md`: план работ и архитектура.
- `CLAUDE.md`: правила работы агента.
- `project-starter.md`: резервная копия общего стартового чек-листа.
- `MEMORY.md`: журнал сессий.
- `app/`: бэкенд FastAPI.
  - `main.py`: роуты и фабрика `create_app` (движки и хранилище внедряются, в тестах подменяются); потоковое чтение тела с лимитом.
  - `config.py`, `models.py` (Question, Interview, Attempt, Session, слоты документов).
  - `storage.py`: JSON и файлы в `data/interviews/` (собеседования, документы, вопросы, сессии, аудио); защита от выхода за папки.
  - `documents.py`: извлечение текста из md/txt/docx/pdf, безопасные имена файлов, лимиты (5 МБ, 50 МБ в распакованном docx, 300 страниц pdf).
  - `questions.py`: парсер списка вопросов (нумерованные и маркированные списки, заголовки, строки с «?», критерии) и склейка списков из нескольких файлов.
  - `stt.py` (GigaAM, нарезка по паузам, приведение к 16 кГц, выравнивание громкости), `tts.py` (Silero, кэш WAV на диске), `text_norm.py` (глоссарий, числа, транслитерация латиницы).
  - `llm.py` (клиент LM Studio, JSON-схема, повторы, лог `data/logs/llm.jsonl`), `review.py` + `prompts/review.md`.
- `static/`: страница без фреймворков, ES-модули. `index.html`, `style.css`, `main.js` (связка экранов), `ui.js` (общие помощники), `home.js` (список собеседований), `prepare.js` (документы по слотам и редактор вопросов), `interview.js` (голосовой цикл), `recorder-worklet.js` (запись через AudioWorklet, WAV 16 кГц mono).
- `tests/`: pytest, 149 тестов; STT, TTS и LLM замоканы.
- `.claude/settings.json`: PostToolUse-хук (после правки `.py` прогоняет pytest через `scripts/hooks/pytest_on_py_edit.py`, лог в `data/logs/hook.log`).
- `.claude/skills/git-save/`: скилл `/git-save` (тесты, pyright, обновление MEMORY/ARCHITECTURE/README, коммит, пуш); запускается только пользователем.
- `pyrightconfig.json`: настройки Pyright; корректно работает из CLI в корне проекта (в редакторе LSP-плагин корень выбирает сам, оттуда шум «не удаётся разрешить импорт»).
- `config.yaml`, `glossary.yaml`: настройки и глоссарий.
- `requirements.txt`: закреплённые зависимости (torch только CPU).
- `scripts/check_lmstudio.py`, `check_stt.py`, `check_tts.py`: проверка компонентов (этап 0); `scripts/hooks/`: скрипт хука.

## Данные на диске (`data/`, в .gitignore)
```
data/interviews/<id>/
  interview.json
  docs/<questions|vacancy|resume|cases>/<файлы>
  questions.json                     # утверждённый список
  sessions/<ГГГГММДД-ЧЧММСС>/
    session.json                     # вопросы-снимок, попытки, разборы
    audio/q01_a1.wav
data/cache/tts/, data/logs/, data/check/
```
`<id>` — латинский slug из названия (`t2-produktovyi-analitik`), id сессии: `<id>__<ГГГГММДД-ЧЧММСС>`.

## Команды (PowerShell, из корня проекта)
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\check_tts.py
.\.venv\Scripts\python.exe scripts\check_stt.py --record 10
.\.venv\Scripts\python.exe scripts\check_stt.py --wav data\check\tts.wav
.\.venv\Scripts\python.exe scripts\check_lmstudio.py
.\.venv\Scripts\python.exe -m pytest -q
pyright                                   # из корня проекта, должно быть 0 errors
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # затем http://localhost:8000
```

Модели грузятся при старте сервера (около 15 с). Сессии, попытки и аудио сохраняются на диск сразу, после перезапуска всё на месте.

## Отклонения от PLAN.md
- STT: вместо Silero VAD длинные ответы режутся по паузам на куски ~20 с (`chunk_seconds`). Причина: VAD терял речь на тихих записях.
- TTS: модель `v5_6_ru` (в плане `v5_5_ru`), темп через SSML (`tts.rate`).
- LLM: `qwen2.5-14b-instruct-1m` Q4_K_M, контекст 8192 (в плане Qwen3-14B).
- API (этап 2): файлы загружаются сырым телом запроса (`?slot=&filename=`), а не multipart; нечитаемый файл отклоняется до сохранения. `POST /api/sessions` этапа 1 убран, сессия создаётся через `POST /api/interviews/{id}/sessions` только при утверждённом списке. «Пропустить» и `/next` пока нет (навигация на клиенте).

## Changelog
### 2026-10-28 (этап 2)
- Собеседования, документы по слотам, парсер вопросов, редактор списка, хранение на диске (`storage.py`), экран «Подготовка» и список собеседований; `app.js` разбит на ES-модули. Заглушки `sample_questions.py` удалены. Тесты: 149.

### 2026-10-28 (автоматизация по рекомендациям)
- Хук pytest после правки `.py`, скилл `/git-save`, `pyright` из корня проекта: 0 ошибок (было 20 замечаний по типам).
- STT приводит любую допустимую частоту записи к 16 кГц (`resample`), `check_stt.py` использует тот же `SttEngine`. Тесты: 80.

### 2026-10-28 (security-ревью, второй проход)
- `/transcribe`: тело читается потоком с лимитом; `decode_wav` проверяет заголовок (только WAV, частота, длительность) до декодирования. Тесты: 71.

### 2026-10-28 (security-ревью этапа 1)
- Входные ограничения: частота записи 8–48 кГц, длительность ≤ `max_answer_seconds` + 30 с, размер тела `/transcribe`, текст `/api/tts` ≤ 2000 символов, числа > 15 цифр по цифрам. Тесты: 67.

### 2026-10-28 (этап 1)
- Добавлены бэкенд, страница с голосовым циклом и тесты. Латиница вне глоссария транслитерируется (Silero падал на `Z-тест`, `SRM`, `guardrail`); ошибка озвучки отдаётся как 500 «Не удалось озвучить текст.».

### 2026-10-28 (этап 0, проверки)
- `check_stt.py`: нарезка по паузам вместо VAD; `check_tts.py`: параметр `--rate`; `config.yaml`: `tts.rate`, `stt.chunk_seconds`; глоссарий пополнен.

### 2026-10-28 (этап 0)
- Добавлены каркас окружения, конфиги и три check-скрипта.
- TTS-модель в конфиге `v5_6_ru` вместо `v5_5_ru` из PLAN.md.

### 2026-10-28
- Инициализирован git, добавлены документы проекта и `.gitignore`.
