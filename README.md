# Interview Trainer

Локальный голосовой тренажёр собеседований на русском языке. Вопросы озвучиваются, ответ записывается с микрофона, распознаётся и разбирается локальной LLM. Облачные API не используются.

> Статус: в разработке. Готов этап 1: голосовой цикл с тремя тестовыми вопросами (запись, расшифровка, фидбек голосом). Дальше: документы и список вопросов (этап 2). Нарядная версия README со схемами и объяснением решений появится на этапе 6.

## Как это работает
Вопрос озвучивается (Silero TTS), ответ записывается в браузере и распознаётся (GigaAM, CPU), затем Qwen в LM Studio разбирает ответ и даёт фидбек текстом и голосом.

## Стек
Python 3.11, FastAPI, onnx-asr (GigaAM v3), Silero TTS, LM Studio (Qwen), чистый HTML/JS/CSS.

## Быстрый старт (Windows, PowerShell)
1. Запустите LM Studio, загрузите Qwen (Q4_K_M, контекст 8192) и включите Local Server на порту 1234.
2. Один раз: `py -3.11 -m venv .venv`, затем `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Запуск: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, затем откройте `http://localhost:8000`.
4. Тесты: `.\.venv\Scripts\python.exe -m pytest -q`; типы: `pyright`.

## Документы
- [PLAN.md](PLAN.md): план и этапы
- [CLAUDE.md](CLAUDE.md): правила работы
- [ARCHITECTURE.md](ARCHITECTURE.md): устройство проекта
- [MEMORY.md](MEMORY.md): журнал
