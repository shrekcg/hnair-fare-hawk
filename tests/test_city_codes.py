from city_codes import code_to_city_label, resolve_code


def test_known_city_still_resolves_to_airport_code() -> None:
    assert resolve_code("沈阳") == ("SHE", [])


def test_unknown_but_valid_iata_code_is_accepted() -> None:
    # ZZZ 不在基础表也不在底表，三字码直接放行，标签原样返回
    assert resolve_code("zzz") == ("ZZZ", [])
    assert code_to_city_label("ZZZ") == "ZZZ"


def test_iata_code_merged_from_sediment_shows_label() -> None:
    # 底表收录的机场（LZY 林芝）三字码能显示中文标签
    assert resolve_code("lzy") == ("LZY", [])
    assert "林芝" in code_to_city_label("LZY")


def test_non_ascii_three_character_text_is_not_treated_as_iata_code() -> None:
    code, _ = resolve_code("不存在")
    assert code is None


def test_multi_airport_city_prefers_sediment_main_airport() -> None:
    # 多机场城市输入纯城市名时，优先返回底表航班数最多的机场
    assert resolve_code("成都") == ("TFU", [])
    assert resolve_code("上海") == ("PVG", [])
    assert resolve_code("北京") == ("PEK", [])


def test_multi_airport_explicit_airport_still_works() -> None:
    # 明确指定机场名/三字码不受主机场策略影响
    assert resolve_code("成都双流") == ("CTU", [])
    assert resolve_code("SHA") == ("SHA", [])
