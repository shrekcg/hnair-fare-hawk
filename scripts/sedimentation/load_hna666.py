"""解析 HNA666-flight-map 的 HTML 内嵌 JSON → 统一 Flight schema。

HTML 中数据为 `const flights=[...]`；每条记录示例：
{
  "airline": "祥鹏航空", "flight_number": "8L9501",
  "days": [1,2,3,4,5,6,7],
  "date_range": "2026/10/01-2026/10/24" | "2026/09/02-2026/09/02 & 2026/09/13-2026/09/27",
  "route": ["昆明/长水", "郑州/新郑"],
  "ticketable_segments": ["昆明/长水-郑州/新郑"],
  "time_details": {"昆明/长水-郑州/新郑": {"dep": "21:15", "dep_note": null, "arr": "23:55", "arr_note": null}},
  "aircraft_type": "B738", "aircraft_size": "中型",
  "is_morning": false, "is_early_departure": false, "is_late_arrival": true, "time_category": "evening"
}

产品归属由文件名决定：666fms.html → product="666"，2666fms.html → product="2666"，
66666.html → product="666/2666"（全量计划，不表示可兑档位，仅作全量底册）。
多航段记录（ticketable_segments 含多段）拆成多条 Flight，一条航段一条。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from models import Flight

FLIGHTS_RE = re.compile(r"const\s+flights\s*=\s*(\[.*?\]);", re.S)

# 文件名 → 产品标记
PRODUCT_BY_NAME = {
    "666fms.html": "666",
    "2666fms.html": "2666",
    "66666.html": "666/2666",
}

# 默认数据目录：本文件在 scripts/sedimentation/，参考资料在其上级上级
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "参考资料" / "HNA666-flight-map"


def _norm_date(s: str) -> str:
    """'2026/10/01' -> '2026-10-01'；'2026/9/2' -> '2026-09-02'。"""
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", s.strip())
    if not m:
        raise ValueError(f"无法解析日期: {s!r}")
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def parse_date_range(date_range: str) -> list[list[str]]:
    """'2026/10/01-2026/10/24 & 2026/09/02-2026/09/02' -> [["2026-10-01","2026-10-24"], ...]"""
    out = []
    for part in date_range.split("&"):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out.append([_norm_date(a), _norm_date(b)])
    return out


def _split_route_point(point: str) -> tuple[str, str]:
    """'昆明/长水' -> ('昆明', '长水')；无 / 时机场名=城市名。"""
    city, _, airport = point.partition("/")
    return city.strip(), (airport or city).strip()


def load_html(path: str | Path, product: str | None = None) -> list[Flight]:
    path = Path(path)
    if product is None:
        product = PRODUCT_BY_NAME.get(path.name)
        if product is None:
            product = "666/2666"
    html = path.read_text(encoding="utf-8", errors="replace")
    m = FLIGHTS_RE.search(html)
    if not m:
        raise ValueError(f"{path} 中未找到 const flights=[...]")
    records = json.loads(m.group(1))
    flights = []
    for rec in records:
        route = rec.get("route") or []
        if len(route) < 2:
            continue
        segments = rec.get("ticketable_segments") or [f"{route[0]}-{route[-1]}"]
        tdetails = rec.get("time_details") or {}
        # 每个可售票航段拆成一条 Flight（与 CSV 一行一航段对齐）
        for seg in segments:
            origin_pt, _, dest_pt = seg.partition("-")
            o_city, o_airport = _split_route_point(origin_pt)
            d_city, d_airport = _split_route_point(dest_pt)
            td = tdetails.get(seg) or {}
            fl = Flight(
                source="hna666",
                product=product,
                carrier=(rec.get("airline") or "").strip(),
                flight_no=(rec.get("flight_number") or "").strip(),
                origin_city=o_city,
                origin_airport=o_airport,
                origin_iata="",  # hna666 无 IATA，可用 CN271 对照表补（后续增强）
                dest_city=d_city,
                dest_airport=d_airport,
                dest_iata="",
                dep_time=(td.get("dep") or "").strip(),
                arr_time=(td.get("arr") or "").strip(),
                days=sorted(rec.get("days") or []),
                date_ranges=parse_date_range(rec.get("date_range") or ""),
                notes=None,
                raw={
                    "original_route": route,
                    "aircraft_type": rec.get("aircraft_type"),
                    "aircraft_size": rec.get("aircraft_size"),
                    "dep_note": td.get("dep_note"),
                    "arr_note": td.get("arr_note"),
                },
            )
            flights.append(fl)
    return flights


if __name__ == "__main__":
    from collections import Counter
    total = Counter()
    for name in PRODUCT_BY_NAME:
        flights = load_html(DEFAULT_DIR / name)
        total[name] = len(flights)
        print(f"{name}: {len(flights)} 条，样例 {flights[0].as_dict()}")
    print("total:", dict(total))