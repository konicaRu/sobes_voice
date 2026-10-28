"""Итоги сессии: статусы вопросов, отчёт, история и выбор слабых вопросов.

Цифры и статусы считает код, модель пишет только качественный итог (общая оценка, слабые зоны, рекомендации).
"""
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel, field_validator

from app.models import Question, Session

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "session_summary.md"
REPORT_TEMPERATURE = 0.2
MAX_WEAK_AREAS = 3
MAX_RECOMMENDATIONS = 4
MAX_ISSUES_IN_PROMPT = 2
EXPECTED_REPORT_CHARS = 900  # типичный ответ модели с JSON-обвязкой, для расчёта процентов


def report_percent(written_chars: int) -> int:
    return min(95, 10 + int(85 * written_chars / EXPECTED_REPORT_CHARS))

STATUS_LABELS = {
    "ok": "Хорошо",
    "improve": "Доработать",
    "redo": "Переделать",
    "answered": "Без разбора",
    "skipped": "Пропущен",
    "new": "Не начат",
}
REVIEWED = ("ok", "improve", "redo")
WEAK = ("improve", "redo")


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _last_attempt(session: Session, index: int):
    attempts = [a for a in session.attempts if a.q == index]
    return attempts[-1] if attempts else None


def question_status(session: Session, index: int) -> str:
    last = _last_attempt(session, index)
    if last is not None:
        verdict = (last.review or {}).get("verdict")
        return verdict if verdict in REVIEWED else "answered"
    return "skipped" if index in session.skipped else "new"


def session_rows(session: Session) -> list[dict]:
    rows = []
    for index, question in enumerate(session.questions):
        status = question_status(session, index)
        last = _last_attempt(session, index)
        score = (last.review or {}).get("score") if last is not None and status in REVIEWED else None
        rows.append({
            "index": index, "text": question.text, "category": question.category, "status": status,
            "score": score, "attempts": sum(1 for a in session.attempts if a.q == index),
        })
    return rows


def session_stats(rows: list[dict]) -> dict:
    counts = {status: sum(1 for r in rows if r["status"] == status) for status in STATUS_LABELS}
    scores = [r["score"] for r in rows if r["score"] is not None]
    return {
        "total": len(rows),
        "answered": sum(counts[s] for s in (*REVIEWED, "answered")),
        "skipped": counts["skipped"],
        "average": round(sum(scores) / len(scores), 1) if scores else None,
        "counts": counts,
    }


def weak_questions(sessions: list[Session], approved: list[Question]) -> list[Question]:
    """Вопросы утверждённого списка, на которые в последний раз ответили с вердиктом «доработать» или «переделать»."""
    latest: dict[str, str] = {}
    for session in sorted(sessions, key=lambda s: s.started_at):
        for index, question in enumerate(session.questions):
            status = question_status(session, index)
            if status in REVIEWED:
                latest[_key(question.text)] = status
    return [q for q in approved if latest.get(_key(q.text)) in WEAK]


def build_history(sessions: list[Session]) -> dict:
    """Список сессий со средней оценкой и матрица «вопрос × сессия» для динамики."""
    active = [s for s in sorted(sessions, key=lambda s: s.started_at) if s.attempts or s.skipped]
    infos, per_session = [], []
    for session in active:
        rows = session_rows(session)
        stats = session_stats(rows)
        infos.append({
            "sid": session.sid, "started_at": session.started_at, "finished_at": session.finished_at,
            "total": stats["total"], "answered": stats["answered"], "skipped": stats["skipped"], "average": stats["average"],
        })
        per_session.append({_key(r["text"]): r for r in rows})

    order: dict[str, str] = {}
    for rows_by_key in per_session:
        for key, row in rows_by_key.items():
            order.setdefault(key, row["text"])
    matrix = [
        {
            "text": text,
            "cells": [
                None if key not in rows_by_key else {"status": rows_by_key[key]["status"], "score": rows_by_key[key]["score"]}
                for rows_by_key in per_session
            ],
        }
        for key, text in order.items()
    ]
    return {"sessions": infos, "matrix": matrix}


class ReportDraft(BaseModel):
    summary: str
    weak_areas: list[str]
    recommendations: list[str]

    @field_validator("summary")
    @classmethod
    def strip_summary(cls, value: str) -> str:
        return value.strip()

    @field_validator("weak_areas", "recommendations", mode="before")
    @classmethod
    def clean_items(cls, value):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else value

    @field_validator("weak_areas")
    @classmethod
    def trim_weak_areas(cls, value: list[str]) -> list[str]:
        return value[:MAX_WEAK_AREAS]

    @field_validator("recommendations")
    @classmethod
    def trim_recommendations(cls, value: list[str]) -> list[str]:
        return value[:MAX_RECOMMENDATIONS]


def build_report_messages(rows: list[dict], session: Session) -> list[dict]:
    lines = []
    for row in rows:
        line = f"{row['index'] + 1}. [{row['category']}] {row['text']} — {STATUS_LABELS[row['status']]}"
        if row["score"] is not None:
            line += f", оценка {row['score']}/5"
        last = _last_attempt(session, row["index"])
        issues = ((last.review or {}).get("issues") or [])[:MAX_ISSUES_IN_PROMPT] if last is not None else []
        if issues:
            line += "; замечания: " + "; ".join(issues)
        lines.append(line)
    return [
        {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
        {"role": "user", "content": "Результаты тренировочного собеседования:\n" + "\n".join(lines)},
    ]


LATIN_TERM = re.compile(r"[A-Za-z]{3,}")
COMMON_TERMS = {"star"}  # общие термины, которые уместны в рекомендациях без упоминания во входных данных
SUMMARY_FALLBACK = "Подробности смотрите в таблице результатов."


def _terms(text: str) -> set[str]:
    return {term.lower() for term in LATIN_TERM.findall(text)}


def _ground(draft: ReportDraft, allowed: set[str]) -> ReportDraft:
    """Убирает пункты и предложения с латинскими терминами, которых нет во входных данных (модель их выдумывает)."""

    def grounded(text: str) -> bool:
        return _terms(text) <= allowed

    sentences = [s for s in re.split(r"(?<=[.!?])\s+", draft.summary) if grounded(s)]
    return ReportDraft(
        summary=" ".join(sentences) or SUMMARY_FALLBACK,
        weak_areas=[t for t in draft.weak_areas if grounded(t)],
        recommendations=[t for t in draft.recommendations if grounded(t)],
    )


def generate_report(
    llm,
    session: Session,
    title: str,
    on_progress: Callable[[int], None] | None = None,
    known_terms: Iterable[str] = (),
) -> dict:
    rows = session_rows(session)
    stats = session_stats(rows)
    if stats["answered"] == 0:
        raise ValueError("Нет ни одного ответа: ответьте хотя бы на один вопрос.")
    messages = build_report_messages(rows, session)
    draft = llm.chat_json(messages, ReportDraft, temperature=REPORT_TEMPERATURE, on_progress=on_progress)
    draft = _ground(draft, _terms(messages[1]["content"]) | _terms(" ".join(known_terms)) | COMMON_TERMS)
    weak = sorted((r for r in rows if r["status"] in WEAK), key=lambda r: r["score"] if r["score"] is not None else 0)
    skipped = [r for r in rows if r["status"] == "skipped"]
    return {
        "title": title, "stats": stats, "rows": rows,
        "summary": draft.summary, "weak_areas": draft.weak_areas, "recommendations": draft.recommendations,
        "repeat": [r["text"] for r in (*weak, *skipped)],
    }


def render_markdown(title: str, data: dict, finished_at: str) -> str:
    stats = data["stats"]
    lines = [f"# Отчёт: {title}", "", f"Дата: {finished_at[:16].replace('T', ' ')}", "", "## Итог", ""]
    if stats["average"] is not None:
        lines.append(f"Средняя оценка: {stats['average']} из 5")
    lines += [f"Отвечено вопросов: {stats['answered']} из {stats['total']}, пропущено: {stats['skipped']}", "", data["summary"], ""]
    lines += ["## Результаты по вопросам", "", "| № | Вопрос | Статус | Оценка |", "|---|---|---|---|"]
    for row in data["rows"]:
        score = row["score"] if row["score"] is not None else "–"
        lines.append(f"| {row['index'] + 1} | {row['text'].replace('|', '/')} | {STATUS_LABELS[row['status']]} | {score} |")
    for heading, items, empty in (
        ("Слабые зоны", data["weak_areas"], "Не выявлено."),
        ("Повторить", data["repeat"], "Всё в порядке, повторять нечего."),
        ("Рекомендации", data["recommendations"], "Без рекомендаций."),
    ):
        lines += ["", f"## {heading}", ""]
        lines += [f"- {item}" for item in items] or [empty]
    return "\n".join(lines) + "\n"
