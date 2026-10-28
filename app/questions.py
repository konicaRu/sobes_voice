"""Разбор списка вопросов из текста (md/txt/docx/pdf после extract_text).

Что считается вопросом:
- если в документе три и больше заголовков с «?» (конспект: вопросы заголовками, вокруг пояснения),
  вопросы только эти заголовки, а списки и абзацы вокруг не трогаем;
- иначе пункт списка (нумерованного или маркированного), заголовок с «?»,
  строка с «?», если в документе нет списков;
- если ничего не нашлось, заголовки `##` и глубже.
Блоки кода и строки таблиц игнорируются, номер в начале заголовка («1.», «2а)») убирается.

Критерии (what_to_check): строка «Критерии: a; b», строки после «Критерии:» без текста, а вне режима
заголовков ещё вложенные пункты под вопросом и маркированные пункты под нумерованным вопросом.
"""
import re
from dataclasses import dataclass, field

from app.models import MAX_CRITERIA, MAX_CRITERION_CHARS, MAX_QUESTION_CHARS, Question

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^(\s*)(\d+[.)]|[-*•–—])\s+(.+)$")
LABEL = re.compile(
    r"^\s*(?:(?:\d+[.)]|[-*•–—])\s+)?[*_]*"
    r"(?:критери\w+(?:\s+оценки)?|что\s+проверя\w+|проверя\w+|ожидаем\w+\s+(?:пункт\w*|ответ\w*|тезис\w*))"
    r"[*_]*\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
WHOLE_LINE_BOLD = re.compile(r"^(\s*)\*\*(.+?)\*\*\s*$")
CODE_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
HEADING_NUMBER = re.compile(r"^\d+[а-яa-z]?[.)]\s+", re.IGNORECASE)
MIN_QUESTION_CHARS = 3
MIN_QUESTION_HEADINGS = 3


@dataclass
class _Candidate:
    text: str
    kind: str  # num | bullet | heading | plain
    indent: int
    criteria: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return text.replace("**", "").strip(" *_\t")


def _heading_question(title: str) -> str:
    """Убирает номер в начале и подзаголовок после последнего «?»."""
    title = HEADING_NUMBER.sub("", title)
    if "?" in title and not title.endswith("?"):
        title = title[: title.rindex("?") + 1]
    return title


def parse_questions(text: str, category: str = "general") -> list[Question]:
    text = CODE_FENCE.sub("", text.replace("\r\n", "\n"))
    lines = [WHOLE_LINE_BOLD.sub(r"\1\2", line) for line in text.expandtabs(4).split("\n")]
    has_list = any(LIST_ITEM.match(line) and not LABEL.match(line) for line in lines)
    question_headings = sum(1 for line in lines if (h := HEADING.match(line)) and "?" in h[2])
    heading_mode = question_headings >= MIN_QUESTION_HEADINGS

    candidates: list[_Candidate] = []
    deep_headings: list[str] = []
    current: _Candidate | None = None
    criteria_mode = False

    for line in lines:
        if not line.strip() or line.lstrip().startswith("|"):
            continue

        label = LABEL.match(line)
        if label:
            if current is not None:
                items = [_clean(item) for item in label[1].split(";") if _clean(item)]
                current.criteria += items
                criteria_mode = not items
            continue

        heading = HEADING.match(line)
        if heading:
            criteria_mode = False
            title = _heading_question(_clean(heading[2]))
            if len(heading[1]) >= 2:
                deep_headings.append(title)
            if "?" in title:
                current = _Candidate(title, "heading", -1)
                candidates.append(current)
            else:
                current = None
            continue

        item = LIST_ITEM.match(line)
        if item:
            indent, kind = len(item[1]), "num" if item[2][0].isdigit() else "bullet"
            body = _clean(item[3])
            if heading_mode:  # пункты вокруг заголовков-вопросов — пояснения; берём только после «Критерии:»
                if criteria_mode and current is not None:
                    current.criteria.append(body)
                continue
            is_child = current is not None and (
                indent > current.indent or (current.kind == "num" and kind == "bullet" and indent >= current.indent)
            )
            if is_child and current is not None:
                current.criteria.append(body)
            else:
                criteria_mode = False
                current = _Candidate(body, kind, indent)
                candidates.append(current)
            continue

        if criteria_mode and current is not None:
            current.criteria.append(_clean(line))
        elif not heading_mode and not has_list and "?" in line:
            current = _Candidate(_clean(line), "plain", len(line) - len(line.lstrip()))
            candidates.append(current)

    if not candidates:
        candidates = [_Candidate(title, "heading", -1) for title in deep_headings]

    return _to_questions(candidates, category)


def merge_questions(groups: list[list[Question]]) -> list[Question]:
    """Склеивает списки из нескольких файлов: убирает повторы, перенумеровывает id."""
    merged: list[Question] = []
    seen: set[str] = set()
    for question in (q for group in groups for q in group):
        key = question.text.lower()
        if key not in seen:
            seen.add(key)
            merged.append(question.model_copy(update={"id": f"q{len(merged) + 1:02d}"}))
    return merged


def _to_questions(candidates: list[_Candidate], category: str) -> list[Question]:
    questions: list[Question] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = candidate.text[:MAX_QUESTION_CHARS].strip()
        key = text.lower()
        if len(text) < MIN_QUESTION_CHARS or key in seen:
            continue
        seen.add(key)
        criteria = [c[:MAX_CRITERION_CHARS] for c in candidate.criteria if c][:MAX_CRITERIA]
        questions.append(Question(id=f"q{len(questions) + 1:02d}", text=text, category=category, what_to_check=criteria))
    return questions
