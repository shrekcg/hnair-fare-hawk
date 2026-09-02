"""解析 sxfroute/data/airport.csv → 统一 Flight schema。

CSV 19 列（表头见 参考资料/sxfroute/data/airport.csv）：
航班号,出港城市,到港城市,出发时刻,班期,产品,航空公司,起飞城市所属省/市,降落城市所属省/市,
起飞城市机场名称,降落城市机场名称,起飞机场IATA代码,降落机场IATA代码,降落时刻,备注,有效开始日期,
有效结束日期,指定日期,来源行

班期为数字位图字符串（如 "246" = 周二/四/六，"13457" = 周一/三/四/五/日）。
产品取值：666/2666（双档）或 2666。
"""
from __future__ import annotations

import csv
from pathlib import Path

from models import Flight


def parse_week(week_str: str) -> list[int]:
    """'246' -> [2, 4, 6]；空/异常 -> []；输出按星期升序。"""
    out = set()
    for ch in week_str:
        if ch.isdigit():
            d = int(ch)
            if 1 <= d <= 7:
                out.add(d)
    return sorted(out)


def parse_date_range(from_str: str, to_str: str) -> list[list[str]]:
    """CSV 有效开始/结束日期（可能为空）→ [["yyyy-mm-dd", "yyyy-mm-dd"]]"""
    if not from_str or not to_str:
        return []
    return [[from_str, to_str]]


def load_csv(path: str | Path) -> list[Flight]:
    path = Path(path)
    flights = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            flight_no = (row.get("航班号") or "").strip()
            if not flight_no:
                continue
            product = (row.get("产品") or "").strip()
            if product not in ("666", "2666", "666/2666"):
                product = "666/2666"  # 容错：未知值按双档处理，报告里会统计
            fl = Flight(
                source="sxfroute",
                product=product,
                carrier=(row.get("航空公司") or "").strip(),
                flight_no=flight_no,
                origin_city=(row.get("出港城市") or "").strip(),
                origin_airport=(row.get("起飞城市机场名称") or "").strip(),
                origin_iata=(row.get("起飞机场IATA代码") or "").strip(),
                dest_city=(row.get("到港城市") or "").strip(),
                dest_airport=(row.get("降落城市机场名称") or "").strip(),
                dest_iata=(row.get("降落机场IATA代码") or "").strip(),
                dep_time=(row.get("出发时刻") or "").strip(),
                arr_time=(row.get("降落时刻") or "").strip(),
                days=parse_week(row.get("班期") or ""),
                date_ranges=parse_date_range(
                    (row.get("有效开始日期") or "").strip(),
                    (row.get("有效结束日期") or "").strip(),
                ),
                notes=(row.get("备注") or "").strip() or None,
                raw={"source_line": (row.get("来源行") or "").strip(),
                     "specified_dates": (row.get("指定日期") or "").strip() or None},
            )
            flights.append(fl)
    return flights


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "../../参考资料/sxfroute/data/airport.csv"
    flights = load_csv(src)
    from collections import Counter
    print(f"sxfroute 解析 {len(flights)} 条")
    print("产品分布:", dict(Counter(f.product for f in flights)))
    print("样例:", flights[0].as_dict())