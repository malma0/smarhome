"""Russian for the rooms: where something is ("на кухне", "в спальне") - shared by
what Jarvis says (the panel's alarms) and the training answers."""

LOCATIVE = {
    "Кухня": "на кухне", "Спальня": "в спальне", "Зал": "в зале", "Гостиная": "в гостиной", "Кабинет": "в кабинете",
    "Коридор": "в коридоре", "Прихожая": "в прихожей", "Детская": "в детской", "Ванная": "в ванной",
    "Балкон": "на балконе", "Гостевая": "в гостевой", "Столовая": "в столовой",
}


def locative(room: str) -> str:
    """'Кухня' -> 'на кухне'; a room it doesn't know -> 'в комнате «…»'."""
    return LOCATIVE.get(room, f"в комнате «{room}»")
