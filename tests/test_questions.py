from app.models import Question
from app.questions import merge_questions, parse_questions


def texts(questions):
    return [q.text for q in questions]


def test_numbered_list_with_inline_and_nested_criteria():
    text = """# Вопросы для собеседования

1. Расскажите, как вы проверяете результаты A/B-теста?
   - Критерии: SRM; размер выборки; p-value
2. Что такое retention?
   Критерии:
   - формула
   - когорты
3. Расскажите о себе.
"""
    questions = parse_questions(text)
    assert texts(questions) == [
        "Расскажите, как вы проверяете результаты A/B-теста?",
        "Что такое retention?",
        "Расскажите о себе.",
    ]
    assert questions[0].what_to_check == ["SRM", "размер выборки", "p-value"]
    assert questions[1].what_to_check == ["формула", "когорты"]
    assert questions[2].what_to_check == []


def test_bullet_list_with_criteria_line():
    text = """- Что такое SQL JOIN?
  Критерии: типы join; разница inner и left
- Как считать конверсию?
"""
    questions = parse_questions(text)
    assert texts(questions) == ["Что такое SQL JOIN?", "Как считать конверсию?"]
    assert questions[0].what_to_check == ["типы join", "разница inner и left"]


def test_headings_with_question_marks():
    text = """## Что такое p-value?
Критерии: интерпретация; ошибки

## Как проверить SRM?
"""
    questions = parse_questions(text)
    assert texts(questions) == ["Что такое p-value?", "Как проверить SRM?"]
    assert questions[0].what_to_check == ["интерпретация", "ошибки"]


def test_plain_lines_with_question_marks_only():
    text = """Расскажите о проекте с A/B-тестом?
Как вы выбираете метрики для продукта?

Это просто заметка без вопроса.
"""
    assert texts(parse_questions(text)) == [
        "Расскажите о проекте с A/B-тестом?",
        "Как вы выбираете метрики для продукта?",
    ]


def test_section_headings_are_ignored_when_lists_present():
    text = """## SQL
1. Чем WHERE отличается от HAVING?
2. Что такое оконные функции?

## Статистика
1. Что такое доверительный интервал?
"""
    assert texts(parse_questions(text)) == [
        "Чем WHERE отличается от HAVING?",
        "Что такое оконные функции?",
        "Что такое доверительный интервал?",
    ]


def test_text_extracted_from_docx_with_nested_bullets():
    text = "# Вопросы\n- Что такое p-value?\n  - вероятность\n  - не вероятность гипотезы\n- Что такое MDE?"
    questions = parse_questions(text)
    assert texts(questions) == ["Что такое p-value?", "Что такое MDE?"]
    assert questions[0].what_to_check == ["вероятность", "не вероятность гипотезы"]


def test_bullets_under_numbered_question_are_criteria():
    text = "1. Что такое p-value?\n- вероятность получить данные\n2. Что такое MDE?"
    questions = parse_questions(text)
    assert texts(questions) == ["Что такое p-value?", "Что такое MDE?"]
    assert questions[0].what_to_check == ["вероятность получить данные"]


def test_bold_markers_are_removed():
    questions = parse_questions("**1. Что такое CUPED?**\n2. **Что такое SRM?**")
    assert texts(questions) == ["Что такое CUPED?", "Что такое SRM?"]


def test_headings_only_document_uses_headings_as_questions():
    questions = parse_questions("# План\n## Расскажите о себе\n## Опишите свой проект")
    assert texts(questions) == ["Расскажите о себе", "Опишите свой проект"]


def test_duplicates_are_dropped_and_ids_are_sequential():
    questions = parse_questions("1. Что такое SRM?\n2. что такое srm?\n3. Что такое MDE?")
    assert [q.id for q in questions] == ["q01", "q02"]
    assert [q.category for q in questions] == ["general", "general"]


def test_no_questions_gives_empty_list():
    assert parse_questions("") == []
    assert parse_questions("Просто текст без списков и вопросов.") == []


def test_windows_line_endings_and_tabs():
    questions = parse_questions("1. Что такое SRM?\r\n\t- проверка пропорций\r\n2. Что такое MDE?\r\n")
    assert texts(questions) == ["Что такое SRM?", "Что такое MDE?"]
    assert questions[0].what_to_check == ["проверка пропорций"]


STUDY_NOTES = """# Подготовка к собеседованию

Что внутри:
- термины простыми словами
- схема RAG

## Термины
1. Токен — кусочек текста.
2. TTFT — время до первого токена.

```
6. БИЗНЕС   что это даёт
5. ПОЛЬЗОВАТЕЛИ   довольны ли люди
```

| Метрика | Что показывает? |
|---|---|
| TTFT | задержку |

# Часть 2. Вопросы

### 1. Как понять, что офис приносит пользу?

**Коротко:**
> Смотрю на три уровня.

- первый пункт пояснения
- второй пункт пояснения

### 2. Масштабировать ли пилот на всю компанию?
Критерии: контрольная группа; стоимость масштаба

### 2а. Как мерить RAG целиком? Лестница метрик

Критерии:
- инфраструктура
- поиск

### 3. Почему AI и почему к нам?

## Твои вопросы директору
1. **«Как вы показываете пользу правлению?»** Самый сильный вопрос.
"""


def test_many_question_headings_make_headings_the_only_questions():
    questions = parse_questions(STUDY_NOTES)
    assert texts(questions) == [
        "Как понять, что офис приносит пользу?",
        "Масштабировать ли пилот на всю компанию?",
        "Как мерить RAG целиком?",
        "Почему AI и почему к нам?",
    ]


def test_heading_mode_keeps_only_explicit_criteria():
    questions = parse_questions(STUDY_NOTES)
    assert questions[0].what_to_check == []  # пояснительные пункты под заголовком не критерии
    assert questions[1].what_to_check == ["контрольная группа", "стоимость масштаба"]
    assert questions[2].what_to_check == ["инфраструктура", "поиск"]


def test_fenced_code_blocks_and_table_rows_are_not_questions():
    text = "1. Что такое SRM?\n```\n2. Это схема в блоке кода\n```\n| колонка | вопрос? |\n3. Что такое MDE?"
    assert texts(parse_questions(text)) == ["Что такое SRM?", "Что такое MDE?"]


def test_heading_number_prefix_is_removed_in_list_mode_too():
    questions = parse_questions("## 1. Что такое SRM?\n## 2а) Что такое MDE?")
    assert texts(questions) == ["Что такое SRM?", "Что такое MDE?"]


def test_merge_questions_dedupes_across_files_and_renumbers():
    first = [Question(id="q01", text="Что такое SRM?"), Question(id="q02", text="Что такое MDE?")]
    second = [Question(id="q01", text="что такое srm?"), Question(id="q02", text="Что такое CUPED?")]
    merged = merge_questions([first, second])
    assert [(q.id, q.text) for q in merged] == [("q01", "Что такое SRM?"), ("q02", "Что такое MDE?"), ("q03", "Что такое CUPED?")]


def test_overlong_text_and_criteria_are_truncated_to_model_limits():
    questions = parse_questions("1. " + "а" * 2000 + "\n" + "\n".join(f"   - пункт {i}" for i in range(40)))
    assert len(questions[0].text) == 1000
    assert len(questions[0].what_to_check) == 20
