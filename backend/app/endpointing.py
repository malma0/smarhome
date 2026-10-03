"""How long a silence ends a phrase, by what was said so far.

One fixed pause for every phrase is a compromise: 0.6 s cut the resident off at a
natural pause ("включи... свет"), 0.9 s makes every command wait almost a second
after the last word. The local recognizer (Vosk) has the words while the phrase is
being spoken, so the pause can follow them:

- the phrase sounds finished ("...на кухне", "стоп", "22 градуса") - a short pause;
- it hangs on a word that can't end a sentence ("и", "на", "потом", "включи", the
  name alone) - a long one, the rest is coming;
- anything else - the usual PHRASE_END_SILENCE.

The words are the small Vosk model's - rough, but the last word is all this needs.
"""

from __future__ import annotations

# can't end a command: a conjunction, a preposition, a verb still waiting for its object, the name
HANGING = {
    "и", "а", "но", "или", "да", "на", "в", "во", "с", "со", "к", "ко", "по", "у", "за", "из", "от", "до", "о", "об",
    "про", "для", "при", "без", "через", "над", "под", "потом", "затем", "ещё", "еще", "чтобы", "что", "как", "где",
    "когда", "если", "это", "мне", "ну", "вот", "так", "эм", "э", "ээ", "пожалуйста",
    "включи", "выключи", "поставь", "сделай", "открой", "закрой", "найди", "напомни", "добавь", "убавь", "прибавь",
    "скажи", "какая", "какой", "какие", "сколько", "запусти", "переключи", "держи",
    "джарвис", "джервис",
}

# a command ends here as a rule: a room, a stop word, a unit after a number
FINISHED = {
    "кухне", "зале", "спальне", "кабинете", "коридоре", "прихожей", "гостиной", "детской", "ванной", "балконе",
    "столовой", "везде", "стоп", "хватит", "пауза", "отмена", "спасибо", "дальше", "громче", "тише", "погромче",
    "потише", "процентов", "процента", "процент", "градусов", "градуса", "градус",
}


def end_silence_for(text: str, normal: float, short: float, long: float) -> float:
    """Seconds of silence that end the phrase heard so far."""
    words = text.casefold().replace("ё", "е").split()
    if not words:
        return normal
    last = words[-1]
    if last in HANGING:
        return long
    if last in FINISHED:
        return short
    return normal
