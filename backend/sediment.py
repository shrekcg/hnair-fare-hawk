"""666/2666 随心飞底表只读查询模块。

数据源：data/sediment/flights_normalized.json（2026 秋航季，由 scripts/sedimentation/update.py
重建，重建前存时点快照，版本见 data/sediment/versions.json）。

设计：
- 只读，启动后懒加载一次（约 1.6MB），之后常驻内存；
- 查询维度：出发/到达城市、产品档位（666/2666）、日期（班期 + 有效日期区间）、航班号；
- 判断规则：product 必须按 "/" 切分后判成员（"666" in "2666" 是坑）；日期需落在
  effective_dates 任一段且 days 含当天星期（周一=1 … 周日=7，isoweekday）。
"""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SEDIMENT_PATH = Path(__file__).resolve().parents[1] / "data" / "sediment" / "flights_normalized.json"

_lock = threading.Lock()
_cache: Optional[List[Dict[str, Any]]] = None


def load_records(path: str | Path | None = None, force: bool = False) -> List[Dict[str, Any]]:
    """加载底表 records，首次调用后缓存。数据文件缺失时返回空列表（不影响其他功能）。"""
    global _cache
    if _cache is None or force:
        with _lock:
            if _cache is None or force:
                try:
                    payload = json.loads(Path(path or SEDIMENT_PATH).read_text(encoding="utf-8"))
                    _cache = list(payload.get("records", []))
                except (OSError, ValueError):
                    _cache = []
    return _cache


def day_to_weekday(d: date) -> int:
    """date -> 1(周一)..7(周日)。"""
    return d.isoweekday()


def matches_product(record: Dict[str, Any], product: str | None) -> bool:
    """product 过滤：按 "/" 切分后判成员，避免 "666" in "2666" 的误判。"""
    if not product or product == "all":
        return True
    return str(product) in str(record.get("product", "")).split("/")


def matches_date(record: Dict[str, Any], date_str: str) -> bool:
    """日期过滤：落在 effective_dates 任一段，且当天班期 days 含该日星期（1=周一）。"""
    if not date_str:
        return True
    try:
        wd = day_to_weekday(date.fromisoformat(date_str))
    except ValueError:
        return False
    in_range = any(a <= date_str <= b for a, b in (record.get("effective_dates") or []))
    in_days = wd in (record.get("days") or [])
    return in_range and in_days


def matches_date_in_range(record: Dict[str, Any], date_start: str = "", date_end: str = "") -> bool:
    """日期区间过滤：区间与 effective_dates 任一段有交集，且交集内至少存在一个班期日。

    用于「可飞日期选择区间」：只要区间内有任意一天可飞即命中（不要求每天都可飞）。
    """
    if not date_start and not date_end:
        return True
    try:
        d0 = date.fromisoformat(date_start) if date_start else None
        d1 = date.fromisoformat(date_end) if date_end else None
    except ValueError:
        return False
    if d0 and d1 and d0 > d1:
        return False
    days = set(record.get("days") or [])
    if not days:
        return False
    for a, b in (record.get("effective_dates") or []):
        try:
            seg_start = date.fromisoformat(a)
            seg_end = date.fromisoformat(b)
        except ValueError:
            continue
        lo = max(d0, seg_start) if d0 else seg_start
        hi = min(d1, seg_end) if d1 else seg_end
        if lo > hi:
            continue
        span = (hi - lo).days
        if span >= 7:
            # 交集覆盖至少一整周：只要班期非空必有可飞日
            return True
        cur = lo
        while cur <= hi:
            if cur.isoweekday() in days:
                return True
            cur += timedelta(days=1)
    return False


def _airport_matches(airport: Dict[str, Any], target: str) -> bool:
    """机场匹配：支持城市名 / 机场名 / IATA 三字码。"""
    t = str(target or "").strip().upper()
    if not t:
        return True
    iata = str(airport.get("iata", "") or "").upper()
    city = str(airport.get("city", "") or "").strip()
    ap = str(airport.get("airport", "") or "").strip()
    norm = lambda s: s.replace("机场", "").replace(" ", "")
    if t in (iata, city.upper(), norm(ap).upper(), norm(city).upper(), norm(city + ap).upper()):
        return True
    return norm(city) in norm(t) or norm(t) in norm(city)


def query(
    city: str = "",
    from_city: str = "",
    to_city: str = "",
    product: str | None = None,
    date_str: str = "",
    date_start: str = "",
    date_end: str = "",
    flight_no: str = "",
    direction: str = "both",
    limit: int = 300,
) -> List[Dict[str, Any]]:
    """多维度查询底表。

    - city：任一端城市（出发或到达），兼容验收锚点口径（海口 any 方向）；
    - from_city / to_city：分别限定出发/到达端；
    - product：666 / 2666 / all / 空（不限）；
    - date_str：YYYY-MM-DD，校验有效日期区间 + 班期（单日）；
    - date_start / date_end：YYYY-MM-DD 区间，区间内任意一天可飞即命中（二选一与单日互斥）；
    - flight_no：航班号模糊匹配（子串，大小写不敏感）；
    - direction：depart（仅出发）/ arrive（仅到达）/ both（默认，配合 city 使用）。
    """
    records = load_records()
    out: List[Dict[str, Any]] = []
    for rec in records:
        o = rec.get("origin") or {}
        de = rec.get("dest") or {}
        o_city, de_city = o.get("city", ""), de.get("city", "")
        if from_city and not _airport_matches(o, from_city):
            continue
        if to_city and not _airport_matches(de, to_city):
            continue
        if city:
            if city not in (o_city, de_city):
                continue
            if direction == "depart" and o_city != city:
                continue
            if direction == "arrive" and de_city != city:
                continue
        if not matches_product(rec, product):
            continue
        if date_str:
            if not matches_date(rec, date_str):
                continue
        elif date_start or date_end:
            if not matches_date_in_range(rec, date_start, date_end):
                continue
        if flight_no:
            fn = str(rec.get("flight_no", "")).upper()
            if str(flight_no).upper() not in fn:
                continue
        out.append(rec)
        if limit and len(out) >= limit:
            break
    return out


def options(
    city: str = "",
    from_city: str = "",
    to_city: str = "",
    product: str | None = None,
    date_str: str = "",
    date_start: str = "",
    date_end: str = "",
    direction: str = "both",
) -> Dict[str, List[str]]:
    """基于数据的出发/到达联动选项（与 query 同一套过滤口径）。

    返回 {from_options, to_options}：当前筛选条件下「可搜到的航线」两端城市列表，
    格式「城市（IATA1/IATA2）·机场1/机场2」（机场名去「机场」后缀），
    城市/三字码/机场名均可模糊搜索。供前端下拉组件联动筛选。
    """
    records = query(
        city=city,
        from_city=from_city,
        to_city=to_city,
        product=product,
        date_str=date_str,
        date_start=date_start,
        date_end=date_end,
        direction=direction,
        limit=100000,
    )
    origins: Dict[str, set] = {}
    dests: Dict[str, set] = {}
    airport_by_iata: Dict[str, str] = {}
    for rec in records:
        o = rec.get("origin") or {}
        de = rec.get("dest") or {}
        if o.get("iata") and o.get("city"):
            origins.setdefault(str(o["city"]).strip(), set()).add(str(o["iata"]).strip())
            if o.get("airport"):
                airport_by_iata.setdefault(str(o["iata"]).strip(), str(o["airport"]).strip())
        if de.get("iata") and de.get("city"):
            dests.setdefault(str(de["city"]).strip(), set()).add(str(de["iata"]).strip())
            if de.get("airport"):
                airport_by_iata.setdefault(str(de["iata"]).strip(), str(de["airport"]).strip())

    def _label(c: str, ias: set) -> str:
        """城市（IATA1/IATA2）·机场1/机场2，机场名去「机场」后缀，供前端模糊搜索。"""
        names = []
        for iata in sorted(ias):
            name = airport_by_iata.get(iata, "")
            if name:
                names.append(name.replace("机场", ""))
        suffix = f"·{'/'.join(names)}" if names else ""
        return f"{c}（{'/'.join(sorted(ias))}）{suffix}"

    def fmt(m: Dict[str, set]) -> List[str]:
        return sorted(_label(c, ias) for c, ias in m.items())

    return {"from_options": fmt(origins), "to_options": fmt(dests)}


def meta() -> Dict[str, Any]:
    """元信息：版本、总数、档位/来源分布、城市与机场列表（供前端筛选器）。"""
    records = load_records()
    cities: Dict[str, set] = {}
    airports: Dict[str, Dict[str, str]] = {}
    product_dist: Dict[str, int] = {}
    source_dist: Dict[str, int] = {}
    for rec in records:
        for key in ("origin", "dest"):
            a = rec.get(key) or {}
            iata = str(a.get("iata", "")).strip()
            city = str(a.get("city", "")).strip()
            if iata:
                airports.setdefault(iata, {"city": city, "airport": str(a.get("airport", "")).strip()})
                cities.setdefault(city, set()).add(iata)
        product_dist[rec.get("product", "")] = product_dist.get(rec.get("product", ""), 0) + 1
        source_dist[rec.get("source", "")] = source_dist.get(rec.get("source", ""), 0) + 1

    version: Any = None
    try:
        versions = json.loads((SEDIMENT_PATH.parent / "versions.json").read_text(encoding="utf-8"))
        vlist = versions.get("versions", [])
        if vlist:
            version = vlist[-1]
    except (OSError, ValueError):
        pass

    city_list = sorted({f"{c}（{'/'.join(sorted(ias))}）" for c, ias in cities.items()})
    return {
        "count": len(records),
        "version": version,
        "city_options": city_list,
        "airports": airports,
        "product_dist": product_dist,
        "source_dist": source_dist,
        "generated_at": f"{date.today().isoformat()}",
    }