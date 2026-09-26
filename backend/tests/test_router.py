from app.tools import router


def test_words_pick_the_groups():
    assert router.select("Джарвис, включи свет на кухне", None) == {"home"}
    assert router.select("сделай погромче", None) == {"pc"}
    assert router.select("поставь таймер на 10 минут", None) == {"reminders"}
    assert router.select("какая погода завтра?", None) == {"weather"}
    assert router.select("найди мой отчёт за сентябрь", None) == {"computer"}
    assert router.select("открой ютуб и сделай потише", None) == {"computer", "pc"}
    assert router.select("я ухожу", None) == {"home"}


def test_a_follow_up_is_about_the_previous_turn():
    assert router.select("да", {"home"}) == {"home"}
    assert router.select("а ещё на двадцать процентов", {"pc"}) == {"pc"}


def test_nothing_to_go_on_means_every_tool():
    assert router.select("сколько будет семнадцать на четыре", None) is None


def test_every_tool_jarvis_has_belongs_to_a_group(tmp_path):
    """A new tool left out of router.GROUPS would never be sent when groups are picked."""
    from app import reminders
    from app.db import connect
    from app.domains import computer, file_search, files, home, system, weather
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for domain in (computer, files, file_search, system, home):
        domain.register(registry)
    reminders.register(registry, reminders.ReminderStore(connect(str(tmp_path / "j.db"))))
    weather.register(registry, "Омск")
    missing = [d.name for d in registry.definitions() if router.group_of(d.name) is None]
    assert missing == []
