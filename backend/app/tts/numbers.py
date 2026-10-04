"""Numbers in a reply, said in Russian words before the voice reads them.

The cloned voice (Voicebox, Chatterbox) can't read digits in Russian: "Сейчас
20 часов 42 минут" came out garbled. So the spoken text gets words instead -
"двадцать часов сорок две минуты" - with the noun after a number agreeing
with it (1 минута, 2 минуты, 5 минут), the number feminine before a feminine
noun (одна, две), and a few written forms said aloud: 20:42, 21,5, 50%, -3.

Only the count forms the model gets wrong are fixed: a genitive plural after
a number that needs another form ("42 минут" -> "минуты"). Other cases are
left as written - "до 21 градуса" stays "градуса".
"""

import re

_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
         "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят",
         "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот",
             "девятьсот"]
_FEMININE = {"один": "одна", "два": "две"}

# Nouns said after a number: (one, two-four, five+), and whether they're feminine.
_NOUNS = {
    "час": (("час", "часа", "часов"), False),
    "минут": (("минута", "минуты", "минут"), True),
    "секунд": (("секунда", "секунды", "секунд"), True),
    "градус": (("градус", "градуса", "градусов"), False),
    "процент": (("процент", "процента", "процентов"), False),
    "день": (("день", "дня", "дней"), False),
    "недел": (("неделя", "недели", "недель"), True),
    "раз": (("раз", "раза", "раз"), False),
}
_THOUSANDS = (("тысяча", "тысячи", "тысяч"), True)
_MILLIONS = (("миллион", "миллиона", "миллионов"), False)


def plural_form(n: int, forms: tuple[str, str, str]) -> str:
    """The noun form that goes with n: 1 минута, 2 минуты, 5 минут, 21 минута, 11 минут."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def _below_thousand(n: int, feminine: bool) -> list[str]:
    words = [_HUNDREDS[n // 100]] if n >= 100 else []
    n %= 100
    if n >= 20:
        words.append(_TENS[n // 10])
        n %= 10
        if n:
            words.append(_ONES[n])
    elif n:
        words.append(_ONES[n])
    if feminine and words and words[-1] in _FEMININE:
        words[-1] = _FEMININE[words[-1]]
    return words


def number_words(n: int, feminine: bool = False) -> str:
    """0 <= n < 10**9 in words, nominative: 2026 -> "две тысячи двадцать шесть"."""
    if n == 0:
        return _ONES[0]
    if n < 0:
        return "минус " + number_words(-n, feminine)
    words = []
    for scale, (forms, fem) in ((10**6, _MILLIONS), (1000, _THOUSANDS)):
        if n >= scale:
            count = n // scale
            words += _below_thousand(count, fem) + [plural_form(count, forms)]
            n %= scale
    words += _below_thousand(n, feminine)
    return " ".join(w for w in words if w)


def _noun_after(word: str):
    """(forms, feminine, is_genitive_plural) if word is one of _NOUNS in any form."""
    low = word.lower()
    for stem, (forms, feminine) in _NOUNS.items():
        if low in forms or (low.startswith(stem) and len(low) - len(stem) <= 2):
            return forms, feminine, low == forms[2] and forms[2] != forms[0]
    return None


def _match_case(word: str, like: str) -> str:
    return word.capitalize() if like[:1].isupper() else word


_TIME_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?%")
_NUMBER_RE = re.compile(r"(?<![\w.,])(-?)(\d{1,9})(?:[.,](\d{1,2}))?(?![\d])(\s+([А-Яа-яЁё]+))?")


def _time(m: re.Match) -> str:
    hours, minutes = int(m.group(1)), int(m.group(2))
    said = f"{hours} {plural_form(hours, _NOUNS['час'][0])}"
    return said + (f" {minutes} {plural_form(minutes, _NOUNS['минут'][0])}" if minutes else " ровно")


def _number(m: re.Match) -> str:
    sign, whole, fraction, tail, noun = m.groups()
    n = int(whole)
    info = _noun_after(noun) if noun else None
    if fraction:  # 21,5 градуса - "двадцать один и пять"; the noun stays as written
        said = number_words(n) + " и " + number_words(int(fraction))
        return ("минус " if sign else "") + said + (tail or "")
    if info is None:
        return ("минус " if sign else "") + number_words(n) + (tail or "")
    forms, feminine, genitive_plural = info
    if genitive_plural:  # "42 минут" -> "сорок две минуты": the model's usual slip
        noun = _match_case(plural_form(n, forms), noun)
    return ("минус " if sign else "") + number_words(n, feminine) + " " + noun


def numbers_to_words(text: str) -> str:
    text = _TIME_RE.sub(_time, text)
    text = _PERCENT_RE.sub(lambda m: f"{m.group(1)} процентов", text)
    return _NUMBER_RE.sub(_number, text)  # "50 процентов" from above agrees here too
