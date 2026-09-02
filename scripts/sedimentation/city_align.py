"""机场对齐：用 HNA666 附带的 CN271 对照表把城市/机场名映射到 IATA。

表结构（参考资料/HNA666-flight-map/CN271_cityairport_name_IATA_ICAO_coords.csv）：
    简名(城市/机场名), 全名, IATA, ICAO, 坐标
    昆明/长水, 昆明长水国际机场, KMG, ZPPP, ...
    北京/首都, 北京首都国际机场, PEK, ZBAA, ...
"""
from __future__ import annotations

import csv
from pathlib import Path

DEFAULT_CN271 = Path(__file__).resolve().parents[2] / "参考资料" / "HNA666-flight-map" \
    / "CN271_cityairport_name_IATA_ICAO_coords.csv"


class AirportIndex:
    def __init__(self, path: str | Path = DEFAULT_CN271):
        path = Path(path)
        self.by_city_airport: dict[tuple[str, str], str] = {}   # (城市, 机场简名) -> IATA
        self.by_full_name: dict[str, str] = {}                  # 全名 -> IATA
        self.airports_by_city: dict[str, list[str]] = {}        # 城市 -> [机场简名...]
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                short = (row.get("简名(城市/机场名)") or "").strip()
                full = (row.get("全名") or "").strip()
                iata = (row.get("IATA") or "").strip()
                if not short or not iata:
                    continue
                city, _, airport = short.partition("/")
                city, airport = city.strip(), (airport or city).strip()
                self.by_city_airport[(city, airport)] = iata
                self.by_full_name[full] = iata
                self.airports_by_city.setdefault(city, []).append(airport)

    def lookup(self, city: str, airport: str = "") -> tuple[str, bool]:
        """按 (城市, 机场名) 查 IATA。

        返回 (iata, ambiguous)：ambiguous=True 表示同城市多机场但机场名无法唯一确定。
        """
        city = (city or "").strip()
        airport = (airport or "").strip()
        if not city:
            return "", False
        # 1) 精确匹配 (城市, 机场名)
        if (city, airport) in self.by_city_airport:
            return self.by_city_airport[(city, airport)], False
        # 2) 机场名直接命中某机场（机场名可能带"国际机场"后缀等变体）
        if airport:
            for (c, a), iata in self.by_city_airport.items():
                if c == city and (a in airport or airport in a):
                    return iata, False
        # 3) 城市只有一个机场：直接给
        airports = self.airports_by_city.get(city, [])
        if len(airports) == 1:
            return self.by_city_airport[(city, airports[0])], False
        if len(airports) > 1:
            return "", True
        return "", False


if __name__ == "__main__":
    idx = AirportIndex()
    print("样例:")
    for c, a in [("昆明", "长水"), ("北京", "首都"), ("北京", "大兴"), ("北京", "首都国际机场")]:
        print(f"  {c}/{a} -> {idx.lookup(c, a)}")
    print("同城多机场城市:", {c for c, a in idx.airports_by_city.items() if len(a) > 1})