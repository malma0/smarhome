from app.tts.text import text_for_speech


def test_the_actual_reply_that_prompted_this():
    reply = "Файл `hello.txt` уже создан со строкой **«привет мира»** и сохранён."
    assert text_for_speech(reply) == "Файл hello.txt уже создан со строкой «привет мира» и сохранён."


def test_markdown_links_keep_only_their_text():
    assert text_for_speech("Открыл [YouTube](https://youtube.com) для тебя.") == "Открыл YouTube для тебя."


def test_list_and_heading_markers_are_dropped_and_lines_joined():
    reply = "# Итог\n- свет включён\n- шторы закрыты\n1. готово"
    assert text_for_speech(reply) == "Итог свет включён шторы закрыты готово"


def test_single_char_emphasis_removed_but_math_and_identifiers_survive():
    assert text_for_speech("Это *очень* важно, 5*3 = 15, файл hello_world.txt") == (
        "Это очень важно, 5*3 = 15, файл hello_world.txt"
    )


def test_emoji_removed():
    assert text_for_speech("Готово! 😄👍 Приятного просмотра ✨") == "Готово! Приятного просмотра"


def test_plain_text_unchanged():
    text = "Включаю свет на кухне."
    assert text_for_speech(text) == text
