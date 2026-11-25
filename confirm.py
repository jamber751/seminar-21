"""Согласие абонента на прочитанную запись.

Ясные ответы решают слова (clinic.by_words). Неясные размечает узкий классификатор:
одна реплика, четыре метки, без инструментов. Записывает только book и только на yes.
"""

import json
import os

from loguru import logger
from openai import AsyncOpenAI

import clinic

SYSTEM = (
    "Ты проверяешь один ответ абонента на прочтение записи к врачу. "
    "label: yes, если абонент согласен записаться ровно так, как прочитано; "
    "no, если отказывается; change, если просит другое время, день или врача; "
    "unclear, если ответ непонятен или не про запись."
)
LABELS = ["yes", "no", "change", "unclear"]
SCHEMA = {"type": "json_schema", "json_schema": {
    "name": "confirmation", "strict": True,
    "schema": {"type": "object", "additionalProperties": False, "required": ["label"],
               "properties": {"label": {"type": "string", "enum": LABELS}}}}}


async def classify(reply: str) -> str:
    """Метка одной реплики: та же модель на Groq, ответ только из LABELS."""
    client = AsyncOpenAI(base_url="https://api.groq.com/openai/v1",
                         api_key=os.getenv("GROQ_API_KEY"))
    r = await client.chat.completions.create(
        model="openai/gpt-oss-20b", reasoning_effort="low", response_format=SCHEMA,
        timeout=5,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user",
                   "content": f"Бот прочитал: «{clinic.CALL['read_back']}»\n"
                              f"Абонент ответил: «{reply}»"}],
    )
    return json.loads(r.choices[0].message.content)["label"]


def time_heard(messages: list[dict]) -> bool:
    """Прозвучало ли время после hold.

    В историю попадают только доигранные фразы: если абонент перебил прочтение
    раньше, чем прозвучало время, времени в истории нет.
    """
    holds = [i for i, m in enumerate(messages) if m.get("role") == "assistant"
             and any(t["function"]["name"] == "hold" for t in m.get("tool_calls") or [])]
    if not holds:
        return False
    spoken = " ".join(m["content"] for m in messages[holds[-1]:]
                      if m.get("role") == "assistant" and isinstance(m.get("content"), str))
    time = clinic.slot_info(clinic.CALL["held"])["time"].replace(":", "")
    return time in clinic.digits_of(spoken)


async def decide(heard: list[str], messages: list[dict]) -> dict:
    """Согласие на прочтение: сначала слова, неясное размечает классификатор."""
    if not clinic.CALL["held"] or len(heard) != clinic.CALL["held_at"] + 1:
        return {"label": "unclear", "by": "не сразу после прочтения"}
    reply = heard[-1]
    label = clinic.by_words(reply, clinic.slot_info(clinic.CALL["held"])["time"])
    if label in ("no", "change"):  # отказ и другую просьбу понимаем и без прочтения
        return {"label": label, "by": "слова"}
    if not time_heard(messages):  # согласие только на услышанное время
        return {"label": "unclear", "by": "время не дослушано"}
    if label:
        return {"label": label, "by": "слова"}
    try:
        return {"label": await classify(reply), "by": "классификатор"}
    except Exception as e:  # сеть, лимит, непонятный ответ модели: записи нет
        logger.warning(f"классификатор не ответил: {e}")
        return {"label": "unclear", "by": "ошибка классификатора"}
