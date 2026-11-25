"""Отчёт по журналу звонка: что сказали, через сколько зазвучал ответ.

uv run report.py              последний звонок из runs/
uv run report.py --detail     ещё и раскладка паузы по частям
uv run report.py runs/call_2026-11-18_223000.jsonl
"""

import json
import statistics
import sys
from pathlib import Path

RUNS = Path(__file__).parent / "runs"


def load(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def turns_of(rows: list[dict]) -> list[dict]:
    turns = [{"name": "Приветствие", "heard": [], "said": [], "tools": []}]
    speech_end, speaking = None, False
    for r in rows:
        ev, cur = r["ev"], turns[-1]
        if ev in ("bot_start", "bot_stop"):
            speaking = ev == "bot_start"
        if ev == "user_stop":
            speech_end = r["t"] - r.get("stop_secs", 0.0)
        elif ev == "heard":
            if cur["said"] or cur["name"] == "Приветствие":
                cur = {"name": f"Ход {len(turns)}", "heard": [], "said": [],
                       "tools": []}
                turns.append(cur)
            cur["heard"].append(r["text"])
            cur["speech_end"] = speech_end
        elif ev == "typed":
            cur = {"name": f"Ход {len(turns)} (текстом)", "heard": [r["text"]],
                   "said": [], "tools": [], "speech_end": r["t"]}
            turns.append(cur)
        elif ev == "tool":
            args = json.dumps(r["args"], ensure_ascii=False)
            cur["tools"].append(f"{r['name']}({args})")
        elif ev == "tool_result":
            result = json.dumps(r["result"], ensure_ascii=False)
            cur["tools"].append(f"  результат: {result[:110]}")
        elif ev == "said":
            cur["said"].append(r["text"])
        elif ev == "bot_start" and "first" not in cur:
            cur["first_at"] = r["t"]
            if cur.get("speech_end") is not None:
                cur["first"] = round(r["t"] - cur["speech_end"], 2)
        elif ev == "interrupted" and speaking:
            cur["cut"] = True
        elif ev == "bot_stop" and "first_at" in cur and not cur.get("cut"):
            if cur.get("speech_end") is not None:
                cur["full"] = round(r["t"] - cur["speech_end"], 2)
        elif ev == "breakdown":
            cur.setdefault("breakdown", r["lines"])
    return turns


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv
    path = Path(args[0]) if args else max(RUNS.glob("call_*.jsonl"))
    rows = load(path)
    print(f"Звонок {path.name}, {rows[-1]['t']:.0f} с")
    turns = turns_of(rows)
    for t in turns:
        print(f"\n{t['name']}")
        if t["heard"]:
            print(f"  абонент: {' '.join(t['heard'])}")
        for tool in t["tools"]:
            if tool.startswith("  результат"):
                print(f"  {tool}")
            else:
                print(f"  инструмент: {tool}")
        if t["said"]:
            print(f"  бот: {' '.join(t['said'])}")
        if "first" in t:
            if t.get("cut"):
                end = "перебит абонентом"
            elif t.get("full") is None:
                end = "звонок закончился раньше ответа"
            else:
                end = f"ответ целиком {t['full']} с"
            print(f"  первый звук {t['first']} с, {end}")
        if detail and t.get("breakdown"):
            for line in t["breakdown"]:
                print(f"    {line}")
    firsts = sorted(t["first"] for t in turns if "first" in t)
    tokens = [(r["t"], r["total"]) for r in rows if r["ev"] == "tokens"]
    print()
    if firsts:
        print(f"Первый звук: медиана {statistics.median(firsts):.2f} с "
              f"(от {firsts[0]:.2f} до {firsts[-1]:.2f}), ходов {len(firsts)}")
    if tokens:
        peak = max(
            sum(n for t2, n in tokens if t1 - 60 < t2 <= t1) for t1, _ in tokens
        )
        print(f"Токены: {sum(n for _, n in tokens)} за звонок, "
              f"больше всего за 60 с: {peak}")


if __name__ == "__main__":
    main()
