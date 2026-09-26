"""The teacher: a big model that only writes words - how a person would say
a command, and how Jarvis answers a result. It never decides the tool
calls (see training/intents.py).

Groq's free tier, on the fallback models so Jarvis keeps gpt-oss-120b's daily
budget; a model that runs out of its day is set aside for the rest of the
run. Every answer is cached on disk by its request, so an interrupted or
repeated build doesn't pay twice.
"""

import hashlib
import json
import time
from pathlib import Path

import httpx

from app.config import settings

CACHE_DIR = Path(__file__).parent / "cache"
# Not gpt-oss-120b: its daily budget is what Jarvis itself runs on. Not
# qwen3.8-27b either: tried as a teacher, it wrote four reorderings of one
# phrase, copied words from the task ("Холодно в гостиной, жалуюсь"), got the
# meaning wrong ("вруби" for "cooler") and skipped whole batches.
TEACHER_MODELS = ["openai/gpt-oss-20b"]

PHRASING_PROMPT = """Ты собираешь обучающие данные для голосового ассистента умного дома по имени Джарвис.
Для каждого пункта ниже напиши {k} РАЗНЫХ фраз, которыми живой человек сказал бы это Джарвису голосом.

Правила:
- Разговорно и по-разному: короткие и длинные, вежливые и грубоватые, разные глаголы и обороты,
  просторечия («вруби», «погаси», «вырубай», «сделай потеплее», «чё-то душно»).
- Имя «Джарвис» не пиши - его отрезает распознавание, фразы без него.
- Это распознанная речь: слова целиком, без сокращений («выкл», «вент», «°» - нельзя),
  числа только ЦИФРАМИ и ровно те, что в пункте (22, 22,5, 40%, 900 - не «девятьсот»),
  грамотный русский язык.
- Назови ВСЕ перечисленные комнаты - и не добавляй других комнат, устройств и действий.
- Комнаты в естественном падеже («на кухне», «в спальне», «в зале»).
- Если сказано «без числа» - не называй чисел. Если «без просьбы» - только жалоба, без команды.
- Не копируй служебные слова из описания: «жалуется», «спрашивает», «человек», «комната:».
- Фразы должны отличаться словами, а не только порядком слов.

Пункты:
{items}

Верни только JSON: {{"items": [{{"id": <id>, "phrases": ["...", ...]}}, ...]}}"""

REPLY_PROMPT = """Ты - Джарвис, голосовой ассистент умного дома. Для каждого случая ниже напиши, что Джарвис
ответит ВСЛУХ после того, как дом выполнил (или не выполнил) команду.

Правила:
- 1-2 коротких предложения, как говорят вслух. Без markdown, списков, эмодзи, английских слов.
- О себе - в женском роде: «включила», «сделала», «поставила».
- Только то, что есть в результатах дома. Ничего не выдумывай.
- Ошибка - скажи просто, что не вышло и почему (например, «в коридоре нет розетки»).
- Если результат просит подтверждения (confirmed=true) - коротко спроси «да/нет» (например, «Открыть газ? Подтверди.»).
- Вопрос о доме - ответь по данным (температура, что включено, где жарче).
- Не предлагай сделать то, чего дом не умеет, и не советуй проветрить.

Случаи:
{items}

Верни только JSON: {{"items": [{{"id": <id>, "reply": "..."}}, ...]}}"""


class Teacher:
    def __init__(self, models: list[str] | None = None, log=print):
        self.models = list(models or TEACHER_MODELS)
        self.log = log
        self.client = httpx.Client(timeout=120, headers={"Authorization": f"Bearer {settings.groq_api_key}"})
        CACHE_DIR.mkdir(exist_ok=True)

    def ask_json(self, prompt: str) -> dict:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
        cached = CACHE_DIR / f"{key}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        while self.models:
            model = self.models[0]
            body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.9,
                    "response_format": {"type": "json_object"}}
            if model.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "low"
            else:
                body["reasoning_format"] = "hidden"
            response = self.client.post(f"{settings.groq_base_url}/chat/completions", json=body)
            if response.status_code == 429:
                wait = float(response.headers.get("retry-after", "30"))
                if wait > 120:  # the day's budget - set this model aside for the run
                    self.log(f"  {model}: дневной лимит, дальше без неё")
                    self.models.pop(0)
                    continue
                time.sleep(wait + 1)
                continue
            if response.status_code != 200:
                self.log(f"  {model}: {response.status_code} {response.text[:150]}")
                time.sleep(3)
                continue
            try:
                data = json.loads(response.json()["choices"][0]["message"]["content"])
            except (KeyError, ValueError, TypeError):
                self.log(f"  {model}: ответ не JSON, повторяю")
                continue
            cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        raise RuntimeError("Every teacher model is out of its daily limit - continue tomorrow (the cache keeps progress).")

    def phrasings(self, items: list[tuple[int, str]], k: int) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        # gpt-oss-20b now and then answers 1 item of 12; what it skipped is asked again on its own.
        for _ in range(3):
            missing = [(i, meaning) for i, meaning in items if not out.get(i)]
            if not missing:
                break
            lines = "\n".join(f"{i}. {meaning}" for i, meaning in missing)
            data = self.ask_json(PHRASING_PROMPT.format(k=k, items=lines))
            for item in data.get("items", []):
                try:
                    out[int(item["id"])] = [str(p).strip() for p in item.get("phrases", []) if str(p).strip()]
                except (KeyError, ValueError, TypeError):
                    continue
        return out

    def replies(self, items: list[tuple[int, str, str]]) -> dict[int, str]:
        """items: (id, what the person said, the house's results as JSON)."""
        lines = "\n\n".join(f"id {i}\nЧеловек: {said}\nРезультаты дома: {results}" for i, said, results in items)
        data = self.ask_json(REPLY_PROMPT.format(items=lines))
        out = {}
        for item in data.get("items", []):
            try:
                out[int(item["id"])] = str(item["reply"]).strip()
            except (KeyError, ValueError, TypeError):
                continue
        return out
