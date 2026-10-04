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
    assert text_for_speech("Это *очень* важно, 5*3 = 15, файл hello_world2.txt") == (
        "Это очень важно, пять*три = пятнадцать, файл hello_world2.txt"
    )


def test_numbers_are_said_in_words_agreeing_with_their_noun():
    # the reply the cloned voice couldn't read - and the model's own "42 минут"
    assert text_for_speech("Сейчас 20 часов 42 минут.") == "Сейчас двадцать часов сорок две минуты."
    assert text_for_speech("Сейчас 20:42.") == "Сейчас двадцать часов сорок две минуты."
    assert text_for_speech("Встреча в 1:00.") == "Встреча в один час ровно."
    assert text_for_speech("Свет на 50%, в спальне 21 градус, на улице -3 градуса.") == (
        "Свет на пятьдесят процентов, в спальне двадцать один градус, на улице минус три градуса."
    )
    assert text_for_speech("Через 3 дней, 2 недель, 2026 год.") == "Через три дня, две недели, две тысячи двадцать шесть год."


def test_a_number_keeps_a_noun_form_that_isnt_the_counting_one():
    assert text_for_speech("Держу до 21 градуса, 21,5 градуса.") == (
        "Держу до двадцать один градуса, двадцать один и пять градуса."
    )
    assert text_for_speech("С 5-7 вечера, модель v6.") == "С пять-семь вечера, модель v6."


def test_emoji_removed():
    assert text_for_speech("Готово! 😄👍 Приятного просмотра ✨") == "Готово! Приятного просмотра"


def test_plain_text_unchanged():
    text = "Включаю свет на кухне."
    assert text_for_speech(text) == text
