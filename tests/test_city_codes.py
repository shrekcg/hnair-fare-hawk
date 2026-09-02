from city_codes import code_to_city_label, resolve_code


def test_known_city_still_resolves_to_airport_code() -> None:
    assert resolve_code("沈阳") == ("SHE", [])


def test_unknown_but_valid_iata_code_is_accepted() -> None:
    assert resolve_code("lzy") == ("LZY", [])
    assert code_to_city_label("LZY") == "LZY"


def test_non_ascii_three_character_text_is_not_treated_as_iata_code() -> None:
    code, _ = resolve_code("不存在")
    assert code is None
