# MEMORY — журнал

## Статус проекта
**Этап 0 в работе:** каркас и три check-скрипта готовы; TTS и STT проверены на синтетическом аудио. Ждём от пользователя: запуск LM Studio сервера, свои записи голоса, подтверждение этапа 0.

## Открытые вопросы
- После этапа 1 напомнить пользователю про прогон `claude-automation-recommender` (решено отложить).
- После первой содержательной сессии предложить `fewer-permission-prompts`.
- Плагин `github` не подключается (ошибка «Authorization header is badly formatted»): токен настроен неверно. На пуш через git не влияет.
- Плагин `context7` установлен, нужен перезапуск сессии.
- Нарядный README собрать на этапе 6 (скилл `beautify-github-readme`), по фактам из кода.

## Решения по project-starter
| Пункт | Решение |
|---|---|
| Контекстные файлы | да (PLAN.md и CLAUDE.md остаются главными, не дублируются) |
| Git | да, remote https://github.com/konicaRu/sobes_voice, коммитим и пушим |
| Скиллы Карпатого | да (установлены) |
| Superpowers | да (приоритет: правила пользователя > Superpowers) |
| context7 | да, установлен для сверки документации (правило 11) |
| README | нарядный |
| Накопительные правила, Humanizer, конституция, карта архитектуры, ub-mode | нет |
| fewer-permission-prompts | позже |

## Лог сессий
### 2026-10-28 (этап 0)
- Создан `.venv` (Python 3.11.9), `requirements.txt` с закреплёнными версиями (torch 2.14.1+cpu), `config.yaml`, `glossary.yaml`, `pyrightconfig.json`.
- Скрипты `scripts/check_tts.py`, `check_stt.py`, `check_lmstudio.py`.
- Сверка документации: у Silero есть `v5_6_ru` (новее `v5_5_ru` из плана), в `config.yaml` поставлен он. Имя модели GigaAM: `gigaam-v3-e2e-rnnt`, VAD: `onnx_asr.load_vad("silero")` + `model.with_vad(vad)`.
- TTS (eugene, v5_6_ru): 8 с аудио за 1.15 с. STT (GigaAM + VAD) на синтетике: 8 с за 0.4 с. Скачано: Silero TTS 141 MB, GigaAM ~5 файлов (загрузка 2 мин).
- LM Studio не проверен: сервер был выключен.

### 2026-10-28 (старт проекта)
- Прочитаны PLAN.md, CLAUDE.md, project-starter.md; обсуждены скиллы и сервисы.
- Создан репозиторий, первый коммит и пуш в `main`; добавлен `.gitignore` (`data/`, `.venv/` и др.).
- Установлен `context7@claude-plugins-official`. Уже были включены `pyright-lsp`, `security-guidance`, `github`.
- Заведены MEMORY.md, ARCHITECTURE.md, заготовка README.md.
