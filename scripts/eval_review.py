"""Оценка качества разбора ответов (этап 3).

Прогоняет эталонные пары «вопрос + ответ + ожидаемый вердикт» через ревьюера (нужен запущенный LM Studio)
и печатает таблицу: совпадение вердиктов, оценки, время. Для хороших ответов показывает замечания,
чтобы глазами проверить, нет ли выдуманных придирок. Результаты сохраняются в data/eval/.

Примеры:
  .\\.venv\\Scripts\\python.exe scripts\\eval_review.py
  .\\.venv\\Scripts\\python.exe scripts\\eval_review.py --prompt app\\prompts\\review.md --runs 3
  .\\.venv\\Scripts\\python.exe scripts\\eval_review.py --model qwen2.5-14b-instruct-1m
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import review  # noqa: E402
from app.config import load_config, load_glossary  # noqa: E402
from app.llm import LlmError, build_llm  # noqa: E402
from app.models import Question  # noqa: E402

LEVELS = {"redo": 0, "improve": 1, "ok": 2}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=ROOT / "scripts" / "eval" / "review_cases.json")
    parser.add_argument("--prompt", type=Path, help="файл промпта ревьюера (по умолчанию app/prompts/review.md)")
    parser.add_argument("--model", help="имя модели в LM Studio (по умолчанию из config.yaml)")
    parser.add_argument("--runs", type=int, default=1, help="сколько раз прогнать каждый случай (для проверки стабильности)")
    parser.add_argument("--only", nargs="+", metavar="ID", help="прогнать только эти случаи")
    args = parser.parse_args()

    config, glossary = load_config(), load_glossary()
    if args.model:
        config.lmstudio.model = args.model
    if args.prompt:
        review.PROMPT_PATH = args.prompt.resolve()
    llm = build_llm(config)
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.only:
        data["cases"] = [case for case in data["cases"] if case["id"] in args.only]
    terms = [*glossary.tts, *glossary.stt_fix.values()]

    try:
        model = llm.get_model()
    except LlmError as exc:
        print(f"ОШИБКА: {exc}")
        return 1
    print(f"Модель: {model}; промпт: {review.PROMPT_PATH.name}; случаев: {len(data['cases'])} x {args.runs}\n")

    rows = []
    for case in data["cases"]:
        question = Question(id=case["id"], **case["question"])
        for run in range(args.runs):
            started = time.perf_counter()
            result = review.review_answer(
                llm, question, case["answer"], case["seconds"], profile=data["profile"], vacancy=data["vacancy"], terms=terms
            )
            rows.append({
                "id": case["id"], "run": run + 1, "expected": case["expected"], "got": result.verdict, "score": result.score,
                "acceptable": case.get("acceptable", [case["expected"]]),
                "seconds": round(time.perf_counter() - started, 1), "issues": result.issues, "strengths": result.strengths,
                "how_to_fix": result.how_to_fix, "spoken_summary": result.spoken_summary, "note": case["note"],
                "coverage": [point.model_dump() for point in result.coverage],
            })
            row = rows[-1]
            mark = "OK " if row["expected"] == row["got"] else ("ок~" if row["got"] in row["acceptable"] else "ERR")
            print(f"{mark} {row['id']:<20} ждали {row['expected']:<8} получили {row['got']:<8} оценка {row['score']} {row['seconds']:>5} с")

    matches = sum(r["expected"] == r["got"] for r in rows)
    lenient = sum(r["got"] in r["acceptable"] for r in rows)
    adjacent = sum(abs(LEVELS[r["expected"]] - LEVELS[r["got"]]) == 1 for r in rows)
    opposite = sum(abs(LEVELS[r["expected"]] - LEVELS[r["got"]]) == 2 for r in rows)
    average = sum(r["seconds"] for r in rows) / len(rows)
    print(f"\nСовпало вердиктов строго: {matches} из {len(rows)}; с учётом допустимых для пограничных: {lenient} из {len(rows)}")
    print(f"На ступень мимо: {adjacent}; противоположные: {opposite}; среднее время разбора: {average:.1f} с")

    if args.runs > 1:
        unstable = [cid for cid in {r["id"] for r in rows} if len({r["got"] for r in rows if r["id"] == cid}) > 1]
        print(f"Нестабильные случаи (вердикт менялся между прогонами): {sorted(unstable) or 'нет'}")

    out_dir = ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"review-{stamp}.json").write_text(
        json.dumps({"model": model, "prompt": review.PROMPT_PATH.name, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [f"# Оценка разбора ответов {stamp}", f"Модель {model}, промпт {review.PROMPT_PATH.name}", f"Совпало строго {matches} из {len(rows)}, с допустимыми {lenient}, среднее время {average:.1f} с", ""]
    for r in rows:
        lines += [
            f"## {r['id']} (ждали {r['expected']}, получили {r['got']}, оценка {r['score']}, {r['seconds']} с): {r['note']}",
            "Пункты: " + "; ".join(f"{c['point']} → «{c['quote']}»" for c in r["coverage"]),
            "Сильные стороны: " + "; ".join(r["strengths"]),
            "Замечания: " + "; ".join(r["issues"]),
            "Как исправить: " + "; ".join(r["how_to_fix"]),
            "Озвучка: " + r["spoken_summary"],
            "",
        ]
    (out_dir / "last.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Подробности: {out_dir / 'last.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
