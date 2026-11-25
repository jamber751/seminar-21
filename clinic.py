"""Учебная регистратура: пациенты, врачи и свободное время. Все данные вымышлены."""

import re
from datetime import date, timedelta

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

TODAY = date(2026, 11, 18)

PATIENTS = {
    "900515300127": "Ахметов Ерлан",
    "850321400563": "Иванова Ольга",
    "781117450032": "Сейткалиева Гульнара",
    "010202600772": "Нурланова Айгерим",
}
DOCTORS = {
    "терапевт": "Жумабаева А. К.",
    "лор": "Ким В. С.",
    "офтальмолог": "Оспанов Д. Е.",
}
TIMES = ["09:00", "10:30", "14:00", "15:30", "16:45"]
TAKEN = {("терапевт", 1, "10:30"), ("терапевт", 1, "16:45"), ("лор", 1, "09:00")}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]

BOOKINGS: list[dict] = []
SMS: list[str] = []
CALL = {"patient": None, "held": None, "held_at": 0, "read_back": "", "unclear": 0, "misses": 0}
# Ясное согласие и ясный отказ решают слова всей реплики. «Да нет, не надо»
# и «Да, но давайте в 14» содержат «да», и это отказ и другая просьба.
# Остальные ответы размечает классификатор в confirm.py.
YES = {"да", "верно", "правильно", "подтверждаю", "записывайте", "окей", "ок", "ok",
       "okay", "хорошо", "ага", "угу", "конечно", "давайте", "пойдёт", "пойдет",
       "подходит", "отлично", "иә", "ия", "жарайды"}
FILLER = {"ну", "всё", "все", "пожалуйста", "спасибо", "меня", "так", "вот", "тогда",
          "можно", "на", "в", "это"}
NO = {"нет", "не", "неа", "жоқ", "но", "а", "или", "лучше", "другое", "другой"}


def new_call():
    CALL.update(patient=None, held=None, held_at=0, read_back="", unclear=0, misses=0)


def miss(result: dict) -> dict:
    """Неудачи подряд: после второй предлагаем текст, после третьей оператора."""
    CALL["misses"] += 1
    if CALL["misses"] >= 2:
        result["offer_text"] = ("предложи ввести ИИН текстом в окне чата на этой "
                                "странице или прислать свободное время в SMS")
    if CALL["misses"] >= 3:
        result["offer_operator"] = "третья неудача подряд: предложи оператора"
    return result


def iin_valid(digits: str) -> bool:
    """Контрольная цифра ИИН: веса 1..11, при остатке 10 веса 3..11, 1, 2."""
    d = [int(c) for c in digits]
    for weights in (range(1, 12), [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]):
        check = sum(x * w for x, w in zip(d, weights)) % 11
        if check != 10:
            return check == d[11]
    return False


def by_digit(digits: str) -> str:
    """«900515300127» синтез читает как миллиарды, а «9 0 0 5...» по цифрам."""
    return " ".join(digits)


def digits_of(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def find_patient(iin: str, heard: list[str]) -> dict:
    """Цифры ИИН сверяем с услышанным: модель может их потерять или выдумать."""
    # ИИН группами с паузами приходит кусками, до шести реплик: сверяем с восемью
    digits, said = digits_of(iin), digits_of(" ".join(heard[-8:]))
    last = digits_of(heard[-1]) if heard else ""
    if len(last) == 12:  # в последней реплике ровно 12 цифр: берём услышанное
        digits = last
    if not digits or digits not in said:
        result = {"found": False, "reason": "этих цифр абонент не называл"}
        # цифры звучали, но модель передала другие: это тоже неудача голоса
        return miss(result) if said else result
    if len(digits) != 12:
        return miss({"found": False,
                     "reason": f"услышано цифр: {len(digits)}, нужно 12"})
    if not iin_valid(digits):
        return miss({"found": False, "reason": "контрольная цифра не сходится",
                     "heard": by_digit(digits)})
    name = PATIENTS.get(digits)
    if not name:
        return miss({"found": False,
                     "reason": "пациент не прикреплён к поликлинике"})
    CALL.update(patient=digits, misses=0)
    return {"found": True, "patient_id": digits, "name": name,
            "iin_spoken": by_digit(digits)}


def find_slots(specialty: str, day: str) -> dict:
    spec = specialty.strip().lower()
    if spec not in DOCTORS:
        return {"slots": [], "reason": f"нет специальности {specialty}"}
    offset = {"сегодня": 0, "завтра": 1, "послезавтра": 2}.get(day.lower(), 1)
    d = TODAY + timedelta(days=offset)
    slots = [
        {"slot_id": f"{spec[:3]}-{d.isoformat()}-{t.replace(':', '')}", "time": t}
        for t in TIMES
        if (spec, offset, t) not in TAKEN
    ]
    return {"doctor": DOCTORS[spec], "date": f"{d.day} {MONTHS[d.month - 1]}",
            "slots": slots}


def slot_info(slot_id: str) -> dict | None:
    """«тер-2026-11-19-1530» превращается во врача, дату и время."""
    spec = next((s for s in DOCTORS if slot_id.startswith(s[:3] + "-")), None)
    if not spec or len(slot_id) != 19:
        return None
    d, hhmm = date.fromisoformat(slot_id[4:14]), slot_id[15:]
    return {"doctor": DOCTORS[spec], "date": f"{d.day} {MONTHS[d.month - 1]}",
            "time": f"{hhmm[:2]}:{hhmm[2:]}"}


def by_words(reply: str, time: str) -> str | None:
    """Ясный ответ на прочтение: "yes", "no", "change" или None, если неясно.

    Цифры допустимы, только если это прочитанное время.
    """
    words = re.findall(r"[a-zа-яёәіңғүұқөһ]+", reply.lower())
    said = digits_of(reply)
    if said and said != time.replace(":", ""):
        return "change"
    if any(w in NO for w in words):
        return "no"
    if any(w in YES for w in words) and all(w in YES | FILLER for w in words):
        return "yes"
    return None


def hold(slot_id: str, heard: list[str]) -> dict:
    """Запомнить выбранное время. Записи ещё нет: её делает book после «да»."""
    if not CALL["patient"]:
        return {"ok": False, "reason": "сначала найди пациента по ИИН"}
    info = slot_info(slot_id)
    if not info:
        return {"ok": False, "reason": f"нет времени {slot_id}"}
    read_back = f"{info['doctor']}, {info['date']}, {info['time']}. Записываю?"
    CALL.update(held=slot_id, held_at=len(heard), read_back=read_back)
    return {"ok": True, "read_back": read_back}


def book(heard: list[str], decision: dict | None = None, **_ignored) -> dict:
    """Записать на выбранное время, если сразу после прочтения абонент согласился.

    Аргументы от модели не нужны: время и пациента код берёт из hold, а решение
    о согласии (метку и кто её поставил) приносит confirm.decide.
    """
    slot, patient = CALL["held"], CALL["patient"]
    if not slot:
        return {"ok": False, "reason": "время не выбрано: сначала hold и прочитай"}
    last = heard[-1] if heard else ""
    label = (decision or {}).get("label", "unclear")
    if len(heard) == CALL["held_at"]:  # book в том же ходе, что hold: ответа ещё нет
        return {"ok": False, "decision": decision, "reason": "абонент ещё не ответил "
                "на прочтение: прочитай read_back и дождись ответа, hold не повторяй"}
    if len(heard) != CALL["held_at"] + 1 or label != "yes":
        CALL["unclear"] += 1
        if label == "change":
            reason = (f"абонент просит другое: «{last}». Вызови hold с новым "
                      "временем или врачом и прочитай read_back")
        elif label == "no":
            reason = f"абонент отказался: «{last}». Спроси, что изменить"
        elif (decision or {}).get("by") == "время не дослушано":
            reason = (f"абонент ответил, не дослушав время: «{last}». Снова вызови "
                      "hold и прочитай read_back до конца")
        else:
            reason = (f"нет ясного согласия на прочтение: «{last}». Снова вызови "
                      "hold и прочитай read_back")
        result = {"ok": False, "decision": decision, "reason": reason}
        if CALL["unclear"] >= 3:
            result["offer_operator"] = "третий раз без подтверждения: предложи оператора"
        return result
    for b in BOOKINGS:
        if b["patient_id"] == patient and b["slot_id"] == slot:
            return {"ok": True, "booking_id": b["booking_id"], "repeat": True}
        if b["patient_id"] == patient:
            return {"ok": False, "reason": f"уже есть запись {b['booking_id']}"}
    booking_id = f"B{len(BOOKINGS) + 1:03d}"
    BOOKINGS.append({"booking_id": booking_id, "slot_id": slot,
                     "patient_id": patient})
    CALL["held"] = None
    return {"ok": True, "decision": decision, "booking_id": booking_id}


def cancel(booking_id: str) -> dict:
    found = [b for b in BOOKINGS if b["booking_id"] == booking_id]
    for b in found:
        BOOKINGS.remove(b)
    return {"ok": bool(found)}


def send_sms(text: str) -> dict:
    """Учебная SMS: не уходит никуда, только в список и журнал звонка."""
    SMS.append(text)
    return {"ok": True}


def to_operator(reason: str) -> dict:
    return {"ok": True,
            "say": "Соединяю с оператором. При угрозе жизни звоните 103."}


SYSTEM = (
    "Ты голосовой администратор учебной поликлиники номер 7, данные вымышлены. "
    "Сегодня 18 ноября 2026. Говоришь по телефону: одно или два коротких "
    "предложения, без списков, markdown и эмодзи. Порядок: узнай ИИН и найди "
    "пациента, затем специальность и день, найди свободное время, назови до трёх "
    "вариантов. Выбранное время передай в hold и прочитай read_back, после «да» "
    "вызови book. Если описывают симптомы, вызови to_operator. "
    "Если ИИН не найден, назови причину и попроси продиктовать по одной цифре. "
    "Если в ответе есть offer_text, предложи это. Любые цифры ИИН, даже неполные, "
    "сразу передавай в find_patient: полноту проверяет код. "
    "Цифры ИИН произноси только так, как в полях heard и iin_spoken."
)

TOOLS = [
    FunctionSchema(
        name="find_patient",
        description="Найти пациента по ИИН из 12 цифр.",
        properties={"iin": {"type": "string", "description": "12 цифр"}},
        required=["iin"],
    ),
    FunctionSchema(
        name="find_slots",
        description="Свободное время врача на день.",
        properties={
            "specialty": {"type": "string", "enum": list(DOCTORS)},
            "day": {"type": "string", "enum": ["сегодня", "завтра", "послезавтра"]},
        },
        required=["specialty", "day"],
    ),
    FunctionSchema(
        name="hold",
        description="Выбранное время: вернёт фразу, которую надо прочитать.",
        properties={"slot_id": {"type": "string"}},
        required=["slot_id"],
    ),
    FunctionSchema(
        name="book",
        description="Записать на выбранное время после «да» пациента.",
        properties={},
        required=[],
    ),
    FunctionSchema(
        name="cancel",
        description="Отменить запись.",
        properties={"booking_id": {"type": "string"}},
        required=["booking_id"],
    ),
    FunctionSchema(
        name="send_sms",
        description="Отправить пациенту SMS, например свободное время текстом.",
        properties={"text": {"type": "string"}},
        required=["text"],
    ),
    FunctionSchema(
        name="to_operator",
        description="Соединить с оператором: симптомы, жалобы, вопрос вне записи.",
        properties={"reason": {"type": "string"}},
        required=["reason"],
    ),
]
HANDLERS = {"find_patient": find_patient, "find_slots": find_slots, "hold": hold,
            "book": book, "cancel": cancel, "to_operator": to_operator,
            "send_sms": send_sms}
NEEDS_HEARD = {"find_patient", "hold", "book"}
