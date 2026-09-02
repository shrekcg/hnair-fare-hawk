"""数据沉淀统一模型：把两份数据源（sxfroute CSV / HNA666 HTML）归一化成同一 schema。

字段约定：
- days: 1=周一 ... 7=周日，列表
- date_ranges: 规范 ISO 日期区间列表 [["2026-09-01", "2026-10-24"], ...]
- product: "666" | "2666" | "666/2666"  —— 表示该记录在哪个档位下可兑
- notes: 原文备注（如 "仅9.9"、"9.28始"），无则 None
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class Flight:
    source: str                 # "sxfroute" | "hna666"
    product: str                # "666" | "2666" | "666/2666"
    carrier: str                # 航司（中文，如"祥鹏航空"）
    flight_no: str              # 航班号，如 "8L9501"
    origin_city: str            # 城市名（如 "昆明"，hna666 路由里 / 前的部分）
    origin_airport: str         # 机场名（如 "昆明长水国际机场"，无则城市名）
    origin_iata: str            # IATA 三字码，无则 ""
    dest_city: str
    dest_airport: str
    dest_iata: str
    dep_time: str               # "HH:MM"
    arr_time: str               # "HH:MM"
    days: list[int]             # 1=周一 ... 7=周日
    date_ranges: list[list[str]]  # [["2026-09-01", "2026-10-24"], ...]
    notes: Optional[str] = None
    # hna666 特有字段（原样保留，不参与跨源一致性的硬比对）
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def write_jsonl(flights: list[Flight], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for fl in flights:
            f.write(json.dumps(fl.as_dict(), ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> list[Flight]:
    flights = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            flights.append(Flight(**json.loads(line)))
    return flights


def canonical_key(fl: Flight) -> tuple:
    """跨源对齐主键：航班号 + 起降城市（城市级对齐，避免机场名书写差异）。"""
    return (fl.flight_no, fl.origin_city, fl.dest_city)