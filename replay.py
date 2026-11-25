"""Проверка голосом без микрофона: фразы из bank/ идут в работающий бот.

uv run bot.py                                 первый терминал
uv run replay.py                              второй терминал, все сценарии
uv run replay.py scenarios/call.yaml --reps 3
uv run report.py                              разбор последнего звонка
"""

import argparse
import asyncio
from pathlib import Path

from pipecat.evals.scenario import EvalScenarioFile
from pipecat.evals.session import EvalSession, EvalSessionParams

URL = "ws://localhost:7860/ws"
HERE = Path(__file__).parent


async def play(path: Path, reps: int):
    for scenario in EvalScenarioFile.load(path).scenarios:
        scenario.bot_audio = True
        for rep in range(1, reps + 1):
            session = EvalSession.from_scenario(
                scenario, URL, params=EvalSessionParams(default_timeout_ms=40000)
            )
            result = await session.run()
            print(f"\n{scenario.name} #{rep}")
            speaking = False
            for e in result.events_seen:
                kind, at = e["type"], e["at"]
                if kind in ("bot_started_speaking", "bot_stopped_speaking"):
                    speaking = kind == "bot_started_speaking"
                elif kind == "user_transcription":
                    print(f"  {at:6.1f} с  абонент: {e['transcript']}")
                elif kind == "tts_response":
                    print(f"  {at:6.1f} с  бот: {e['text']}")
                elif kind == "bot_interrupted" and speaking:
                    print(f"  {at:6.1f} с  абонент перебил бота")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scenarios", nargs="*", type=Path)
    p.add_argument("--reps", type=int, default=1)
    args = p.parse_args()
    files = args.scenarios or sorted((HERE / "scenarios").glob("*.yaml"))
    for path in files:
        asyncio.run(play(path, args.reps))
    print("Подробности каждого звонка: uv run report.py")


if __name__ == "__main__":
    main()
