# ARCHITECTURE

## Назначение
Локальный голосовой тренажёр собеседований. Подробная архитектура и этапы: [PLAN.md](PLAN.md). Правила работы: [CLAUDE.md](CLAUDE.md).

## Структура
Пока в репозитории только документы. Целевая структура описана в PLAN.md, раздел 3.

- `PLAN.md`: план работ и архитектура.
- `CLAUDE.md`: правила работы агента.
- `project-starter.md`: резервная копия общего стартового чек-листа.
- `MEMORY.md`: журнал сессий.

- `app/`: бэкенд FastAPI (этап 1). `main.py` (роуты, фабрика `create_app` с внедрением движков), `config.py`, `stt.py` (GigaAM, нарезка по паузам, выравнивание громкости), `tts.py` (Silero, кэш WAV на диске), `text_norm.py` (глоссарий, числа, транслитерация латиницы), `llm.py` (клиент LM Studio, JSON-схема, повторы, лог `data/logs/llm.jsonl`), `review.py` + `prompts/review.md`, `models.py`, `sample_questions.py` (3 вопроса-заглушки).
- `static/`: `index.html`, `app.js`, `recorder-worklet.js`, `style.css` (запись через AudioWorklet, WAV 16 кГц mono в браузере).
- `tests/`: pytest, 80 тестов; STT, TTS и LLM замоканы.
- `.claude/settings.json`: PostToolUse-хук (после правки `.py` прогоняет pytest через `scripts/hooks/pytest_on_py_edit.py`, лог в `data/logs/hook.log`).
- `.claude/skills/git-save/`: скилл `/git-save` (тесты, pyright, обновление MEMORY/ARCHITECTURE/README, коммит, пуш); запускается только пользователем.
- `pyrightconfig.json`: настройки Pyright; корректно работает из CLI в корне проекта (в редакторе LSP-плагин корень выбирает сам, оттуда шум «не удаётся разрешить импорт»).
- `config.yaml`, `glossary.yaml`: настройки и глоссарий.
- `requirements.txt`: закреплённые зависимости (torch только CPU).
- `scripts/check_lmstudio.py`, `check_stt.py`, `check_tts.py`: проверка компонентов (этап 0).
- `pyrightconfig.json`: Pyright видит `.venv`.

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

Сессии на этапе 1 хранятся только в памяти сервера; модели грузятся при старте (около 15 с).

## Отклонения от PLAN.md
- STT: вместо Silero VAD длинные ответы режутся по паузам на куски ~20 с (`chunk_seconds`). Причина: VAD терял речь на тихих записях.
- TTS: модель `v5_6_ru` (в плане `v5_5_ru`), темп через SSML (`tts.rate`).
- LLM: `qwen2.5-14b-instruct-1m` Q4_K_M, контекст 8192 (в плане Qwen3-14B).

## Changelog
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
