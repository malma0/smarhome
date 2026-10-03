"""Which tools a request needs - so the model isn't sent all of them.

Every tool's description and schema goes into every request, and with 19
tools that was ~2000 of a request's ~2500 tokens. Groq's free tier counts
tokens (8 000 a minute, 200 000 a day per model), so "включи свет на кухне"
paid for the descriptions of the screen, files, weather... Now a request
carries only the groups its words point to - "свет", "кухн" -> the house.

Deliberately simple: word stems, no model of its own. When nothing matches
("да", "выключи", "а в спальне?") the previous turn's groups are sent - that
is what a follow-up is about; with no previous turn either, everything is
sent, as before. So the worst case of a missed word is the old cost, never a
missing tool.
"""

GROUPS: dict[str, tuple[str, ...]] = {
    "computer": ("open_application", "close_application", "search_web", "web_answer", "find_files",
                 "list_directory", "read_file", "write_file", "delete_file"),
    "pc": ("media", "desktop", "type_text", "screen", "power"),
    "home": ("get_home_status", "control_devices", "set_room_norm", "run_scenario", "home_history", "house_schedule"),
    "reminders": ("reminders",),
    "weather": ("get_weather",),
    "shopping": ("shopping_list",),
    "radio": ("radio",),
    "currency": ("exchange_rates",),
    "web": ("web_answer",),
    "booking": ("booking",),
    "memory": ("remember",),
    "day": ("my_day",),
}

# Stems, matched inside words ("кухн" in "на кухне"), case- and ё-insensitive.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "home": (
        "свет", "ламп", "люстр", "темн", "ярк", "ярч", "приглуш", "тускл", "розетк", "кондиц", "отоплен", "обогрев", "вентиляц", "провет", "душн", "жарк",
        "холодн", "тепле", "теплее", "прохлад", "температур", "влажн", "co2", "углекисл", "комнат", "кухн",
        "спальн", "зал", "кабинет", "коридор", "прихож", "дом", "норм", "сценари", "ушел", "ухожу", "пошел",
        "пришел", "вернул", "спать", "спокойной", "ночи", "утро", "подъем", "проснул", "кран", "воду", "вода",
        "газ", "дым", "пожар", "протеч", "датчик", "штор", "жалюз", "занавес", "увлажн", "охран", "окн", "двер",
        "движен", "электричеств", "энерги", "расписан", "закат", "по будням",
    ),
    "pc": (
        "громк", "звук", "тише", "погромч", "потиш", "пауз", "трек", "песн", "музык", "следующ", "предыдущ",
        "скриншот", "снимок", "экран", "заблокир", "блокир", "сверни", "свернуть", "окна", "перезагр",
        "выключение", "выключи компьютер", "выключи ноутбук", "напиши в", "напечатай", "продиктую",
        "заряд", "батаре", "процессор", "памят", "диск", "компьютер", "ноутбук", "комп",
    ),
    "computer": (
        "открой", "закрой", "запусти", "программ", "приложен", "найди", "поищи", "где мой", "где моя", "файл",
        "папк", "документ", "браузер", "интернет", "гугл", "яндекс", "ютуб", "youtube", "сайт", "википед",
        "карт", "steam", "стим", "discord", "дискорд", "блокнот", "калькулятор", "прочитай", "запиши",
        "удали", "создай", "игр",
    ),
    "reminders": ("таймер", "напомни", "напоминан", "будильник", "разбуди", "засеки"),
    "day": ("у меня сегодня", "у меня завтра", "у меня на сегодня", "у меня на завтра", "какие планы",
            "планы на", "что сегодня", "что на сегодня", "что на завтра", "мой день", "что у нас сегодня"),
    "memory": ("запомн", "помнишь", "забуд", "обо мне", "про меня", "знаешь о", "что ты знаешь"),
    "weather": ("погод", "дожд", "снег", "прогноз", "надеть", "зонт", "ветер", "ветр", "на улице", "мороз"),
    "shopping": ("купи", "покуп", "магазин", "список", "кончил"),
    "radio": ("радио", "радиостанц", "играет", "эфир"),
    "currency": ("курс", "доллар", "евро", "юан", "валют", "тенге", "фунт стерлинг", "йен", "рубл"),
    # Things only the internet knows now - "что нового", "кто выиграл", "сколько стоит айфон".
    "web": ("новост", "узнай", "в интернете", "сколько стоит", "почем", "цена", "цены", "выиграл", "матч",
            "биткоин", "крипт", "что случилось", "что произошло"),
    "booking": ("запиши меня", "записаться", "запишись", "онлайн-запис", "онлайн запис", "бронир", "парикмахер",
                "барбер", "салон", "маникюр", "стоматолог", "к мастеру"),
}


# What the own home model (backend/training) was not trained on - such
# requests go to the main model until it is retrained with them.
# v5 learned curtains, humidifiers, the guard, windows, movement, history and
# schedules; the front door it never saw asked about.
BEYOND_HOME_MODEL = ("двер",)


def beyond_home_model(message: str) -> bool:
    text = message.casefold().replace("ё", "е")
    return any(stem in text for stem in BEYOND_HOME_MODEL)


def group_of(tool_name: str) -> str | None:
    return next((group for group, names in GROUPS.items() if tool_name in names), None)


# Verbs, not things: "закрой шторы" is the house, "закрой браузер" the computer.
# Live, "открой шторы в зале" went to the cloud model as computer + house,
# and it asked to confirm instead of opening them.
GENERIC_VERBS = {"computer": ("открой", "закрой", "запусти", "найди", "поищи", "прочитай", "запиши", "создай", "удали")}


def select(message: str, previous: set[str] | None) -> set[str] | None:
    """The groups to send - or None: send every tool."""
    text = message.casefold().replace("ё", "е")
    hits = {group: {stem for stem in stems if stem in text} for group, stems in KEYWORDS.items()}
    found = {group for group, matched in hits.items() if matched}
    only_verbs = {group for group in found if hits[group] <= set(GENERIC_VERBS.get(group, ()))}
    if found - only_verbs:
        found -= only_verbs
    if found:
        return found
    return set(previous) if previous else None
