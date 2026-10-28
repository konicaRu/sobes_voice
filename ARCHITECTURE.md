# ARCHITECTURE

## Назначение
Локальный голосовой тренажёр собеседований. Подробная архитектура и этапы: [PLAN.md](PLAN.md). Правила работы: [CLAUDE.md](CLAUDE.md).

## Структура
Пока в репозитории только документы. Целевая структура описана в PLAN.md, раздел 3.

- `PLAN.md`: план работ и архитектура.
- `CLAUDE.md`: правила работы агента.
- `project-starter.md`: резервная копия общего стартового чек-листа.
- `MEMORY.md`: журнал сессий.

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
```

## Changelog
### 2026-10-28 (этап 0)
- Добавлены каркас окружения, конфиги и три check-скрипта.
- TTS-модель в конфиге `v5_6_ru` вместо `v5_5_ru` из PLAN.md.

### 2026-10-28
- Инициализирован git, добавлены документы проекта и `.gitignore`.
