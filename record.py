"""Запись своего банка фраз с микрофона: bank/<ключ>.wav, 16 кГц, моно.

uv run record.py              фразы, у которых ещё нет файла
uv run record.py --redo pick  одну фразу заново
"""

import argparse
import json
import queue
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

BANK = Path(__file__).parent / "bank"
RATE = 16000


def record_once() -> np.ndarray:
    chunks: queue.Queue = queue.Queue()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        callback=lambda data, *_: chunks.put(data.copy())):
        input("   идёт запись, Enter: стоп ")
    parts = [chunks.get() for _ in range(chunks.qsize())]
    return np.concatenate(parts)[:, 0] if parts else np.zeros(0, dtype=np.int16)


def save(path: Path, x: np.ndarray):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(x.tobytes())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--redo", nargs="*", default=[])
    args = p.parse_args()
    phrases = json.loads((BANK / "phrases.json").read_text(encoding="utf-8"))
    print(f"Микрофон: {sd.query_devices(kind='input')['name']}")
    print("Темп обычного телефонного разговора, данные только вымышленные.\n")
    for i, ph in enumerate(phrases["phrases"], 1):
        path = BANK / f"{ph['key']}.wav"
        if path.exists() and ph["key"] not in args.redo:
            continue
        while True:
            print(f"[{i}/{len(phrases['phrases'])}] {ph['key']}: «{ph['text']}»")
            input("   Enter: начало записи ")
            x = record_once()
            peak = 20 * np.log10(max(1, int(np.abs(x).max(initial=1))) / 32768)
            print(f"   {len(x) / RATE:.1f} с, пик {peak:.0f} дБ")
            if len(x) < RATE // 2 or peak < -40:
                print("   слишком коротко или тихо, ещё раз")
                continue
            answer = input("   Enter: сохранить, p: прослушать, r: заново > ")
            if answer.strip().lower() == "p":
                sd.play(x, RATE)
                sd.wait()
                answer = input("   Enter: сохранить, r: заново > ")
            if answer.strip().lower() != "r":
                save(path, x)
                break
    done = len(list(BANK.glob("*.wav")))
    print(f"\nВ банке {done} из {len(phrases['phrases'])} фраз.")


if __name__ == "__main__":
    main()
