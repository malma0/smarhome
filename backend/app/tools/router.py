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
    "computer": ("open_application", "close_application", "search_web", "find_files", "list_directory",
                 "read_file", "write_file", "delete_file"),
    "pc": ("media", "desktop", "type_text", "screen", "power"),
    "home": ("get_home_status", "control_devices", "set_room_norm", "run_scenario"),
    "reminders": ("reminders",),
    "weather": ("get_weather",),
    "shopping": ("shopping_list",),
    "radio": ("radio",),
    "currency": ("exchange_rates",),
}

# Stems, matched inside words ("кухн" in "на кухне"), case- and ё-insensitive.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "home": (
        "свет", "ламп", "люстр", "темн", "ярк", "ярч", "приглуш", "тускл", "розетк", "кондиц", "отоплен", "обогрев", "вентиляц", "провет", "душн", "жарк",
        "холодн", "тепле", "теплее", "прохлад", "температур", "влажн", "co2", "углекисл", "комнат", "кухн",
        "спальн", "зал", "кабинет", "коридор", "прихож", "дом", "норм", "сценари", "ушел", "ухожу", "пошел",
        "пришел", "вернул", "спать", "спокойной", "ночи", "утро", "подъем", "проснул", "кран", "воду", "вода",
        "газ", "дым", "пожар", "протеч", "датчик",
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
    "weather": ("погод", "дожд", "снег", "прогноз", "надеть", "зонт", "ветер", "ветр", "на улице", "мороз"),
    "shopping": ("купи", "покуп", "магазин", "список", "кончил"),
    "radio": ("радио", "радиостанц", "играет", "эфир"),
    "currency": ("курс", "доллар", "евро", "юан", "валют", "тенге", "фунт стерлинг", "йен", "рубл"),
}


def group_of(tool_name: str) -> str | None:
    return next((group for group, names in GROUPS.items() if tool_name in names), None)


def select(message: str, previous: set[str] | None) -> set[str] | None:
    """The groups to send - or None: send every tool."""
    text = message.casefold().replace("ё", "е")
    found = {group for group, stems in KEYWORDS.items() if any(stem in text for stem in stems)}
    if found:
        return found
    return set(previous) if previous else None
