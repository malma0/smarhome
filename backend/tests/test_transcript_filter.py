from app.transcript_filter import is_hallucination


def test_the_one_seen_live():
    assert is_hallucination("Продолжение следует...")


def test_well_known_subtitle_credits():
    assert is_hallucination("Субтитры сделал DimaTorzok")
    assert is_hallucination("Редактор субтитров А.Семкин Корректор А.Егорова")
    assert is_hallucination("Спасибо за просмотр!")


def test_real_requests_are_kept():
    for text in (
        "Спасибо.",  # also a silence hallucination - but a real thing to say
        "Включи свет на кухне",
        "Расскажи, что было дальше, продолжение",
        "Открой YouTube и найди продолжение сериала",
        "",
    ):
        assert not is_hallucination(text), text
