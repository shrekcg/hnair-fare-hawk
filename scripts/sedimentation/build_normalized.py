"""把两份数据源合并成一份规范化全量数据资产。

输出：data/sediment/flights_normalized.json

合并策略（可追溯、不臆断）：
- 每条记录以 (航班号, 出港城市, 到港城市) 为键，两源各自的产品/日期/时刻分别保留；
- product = csv ∪ hna（并集，宁多勿漏）；两源判定不一致时 product_conflict=True，供人工复核；
- effective_dates：HNA666 的 date_range 是航班实际执飞日期（更精确），优先采用；
  仅 CSV 有或无 HNA666 日期时，用 CSV 有效日期按备注修正后的区间（如 "9.28始"/"10.8止"/"仅9.9"）。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from models import Flight, canonical_key
from load_sxfroute import load_csv
from load_hna666 import load_html, DEFAULT_DIR as HNA666_DIR
from city_align import AirportIndex
from review import load_decisions, DECISIONS

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "sediment"
CSV_DEFAULT = Path(__file__).resolve().parents[2] / "参考资料" / "sxfroute" / "data" / "airport.csv"
HNA666_FILES = ["666fms.html", "2666fms.html", "66666.html"]

# ---------- CSV 备注解析（"仅9.9" / "9.28始" / "10.8止" / "9.28~10.9" / "仅9.2 9.3"） ----------

_MD = re.compile(r"^(\d{1,2})\.(\d{1,2})$")


def _md_to_date(month: int, day: int, season_year: int) -> str:
    """'9.28' -> '2026-09-28'；'10.8' -> '2026-10-08'。跨年（1 月初）视为次年。"""
    y = season_year
    if month <= 2:  # 1-2 月日期属次年航季
        y += 1
    return f"{y}-{month:02d}-{day:02d}"


def _parse_md_token(tok: str, season_year: int) -> tuple[int, int] | None:
    m = _MD.match(tok)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_csv_notes(note: str | None, season_year: int = 2026) -> list[list[str]] | None:
    """备注 → 修正后的日期区间列表；无法解析/无备注返回 None（表示沿用全季）。

    支持：
    - "仅9.9" / "仅9.2 9.3"                 → 仅这些日期
    - "9.28始" / "10.8止"                   → 单侧修正（由调用方结合全季区间）
    - "9.28~10.9"                           → 区间
    返回 [["2026-09-28","2026-10-09"], ...]
    """
    if not note:
        return None
    toks = note.split()
    ranges: list[list[str]] = []
    # 仅<日期列表>
    if toks[0].startswith("仅") or _MD.match(toks[0]):
        all_dates = []
        for tok in toks:
            tok = tok[1:] if tok.startswith("仅") else tok
            md = _parse_md_token(tok, season_year)
            if md:
                d = _md_to_date(*md, season_year)
                all_dates.append(d)
        if all_dates:
            ranges = [[d, d] for d in all_dates]
        return ranges or None
    # 单区间："X始" / "X止" / "X~Y"
    for tok in toks:
        if tok.endswith("始"):
            md = _parse_md_token(tok[:-1], season_year)
            if md:
                d = _md_to_date(*md, season_year)
                ranges.append([d, "9999-12-31"])
        elif tok.endswith("止"):
            md = _parse_md_token(tok[:-1], season_year)
            if md:
                d = _md_to_date(*md, season_year)
                ranges.append(["0000-01-01", d])
        elif "~" in tok:
            a, _, b = tok.partition("~")
            ma, mb = _parse_md_token(a, season_year), _parse_md_token(b, season_year)
            if ma and mb:
                ranges.append([_md_to_date(*ma, season_year), _md_to_date(*mb, season_year)])
    return ranges or None


def intersect_ranges(base: list[list[str]], patches: list[list[str]]) -> list[list[str]]:
    """把修正区间（含 0000/9999 哨兵）按 base 全季区间截断取交集。"""
    out = []
    for r in patches:
        a, b = r
        for ba, bb in base:
            lo = max(a, ba)
            hi = min(b, bb)
            if lo <= hi:
                out.append([lo, hi])
    # 合并重叠
    out.sort()
    merged = []
    for r in out:
        if merged and r[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(r)
    return merged


def is_date_in_ranges(d: str, ranges: list[list[str]]) -> bool:
    return any(a <= d <= b for a, b in ranges)


# ---------- 合并构建 ----------

def product_label(products: set[str]) -> str:
    if products == {"666", "2666"}:
        return "666/2666"
    return "/".join(sorted(products)) if products else ""


DECISION_PRODUCT = {"666": "666", "2666": "2666", "both": "666/2666"}


def build(csv_flights: list[Flight], hna_files: dict[str, list[Flight]],
          decisions: dict[str, str] | None = None, airports: AirportIndex | None = None) -> list[dict]:
    decisions = decisions or {}
    airports = airports or AirportIndex()
    csv_group: dict[tuple, list[Flight]] = {}
    for fl in csv_flights:
        csv_group.setdefault(canonical_key(fl), []).append(fl)
    hna_666: dict[tuple, list[Flight]] = {}
    hna_2666: dict[tuple, list[Flight]] = {}
    for name, product in (("666fms.html", "666"), ("2666fms.html", "2666")):
        for fl in hna_files.get(name, []):
            (hna_666 if product == "666" else hna_2666).setdefault(canonical_key(fl), []).append(fl)

    keys = set(csv_group) | set(hna_666) | set(hna_2666)
    records = []
    for key in sorted(keys):
        fl_no, o_city, d_city = key
        csv_recs = csv_group.get(key, [])
        hna_recs = hna_666.get(key, []) + hna_2666.get(key, [])

        csv_products = set()
        for fl in csv_recs:
            csv_products |= set(fl.product.split("/"))
        hna_products = set()
        if key in hna_666:
            hna_products.add("666")
        if key in hna_2666:
            hna_products.add("2666")

        merged_products = csv_products | hna_products
        # 只有两源都收录该键，档位判定不同才算冲突；纯单源键由 source 字段表达
        conflict = csv_products != hna_products if hna_recs and csv_recs else False

        # 已人工裁决的冲突键：按裁决覆盖，并从待复核中清除
        review = ""
        dkey = f"{fl_no}|{o_city}|{d_city}"
        if conflict and dkey in decisions:
            merged_products = set(DECISION_PRODUCT[decisions[dkey]].split("/"))
            conflict = False
            review = decisions[dkey]

        # 时刻/班期：CSV 优先，HNA666 兜底
        def pick_time(recs):
            for fl in recs:
                if fl.dep_time and fl.arr_time:
                    return fl.dep_time, fl.arr_time
            return None, None
        csv_dep, csv_arr = pick_time(csv_recs)
        hna_dep, hna_arr = pick_time(hna_recs)
        dep, arr = csv_dep or hna_dep, csv_arr or hna_arr

        days_set = set()
        for fl in csv_recs + hna_recs:
            days_set.update(fl.days)
        days = sorted(days_set)
        csv_days = sorted({d for fl in csv_recs for d in fl.days})
        hna_days = sorted({d for fl in hna_recs for d in fl.days})

        # 日期
        season_base = []
        for fl in csv_recs:
            season_base.extend([list(r) for r in fl.date_ranges])
        csv_dates: list[list[str]] = []
        if season_base:
            patches = []
            for fl in csv_recs:
                if fl.notes:
                    p = parse_csv_notes(fl.notes)
                    if p:
                        patches.extend(p)
            csv_dates = intersect_ranges(season_base, patches) if patches else season_base
        hna_dates = sorted({tuple(r) for fl in hna_recs for r in fl.date_ranges})
        hna_dates = [list(r) for r in hna_dates]

        effective_dates = hna_dates if hna_dates else csv_dates

        ref = csv_recs[0] if csv_recs else (hna_recs[0] if hna_recs else None)

        # IATA 对齐：CSV 侧已带；HNA666 侧用 CN271 补（同城多机场按机场名匹配）
        def resolve_iata(side_ref, city):
            if side_ref and side_ref.source == "csv" and side_ref.origin_iata and side_ref.dest_iata:
                return side_ref.origin_iata, side_ref.dest_iata
            if not side_ref:
                return "", ""
            if side_ref.origin_city == city:
                iata = side_ref.origin_iata or airports.lookup(city, side_ref.origin_airport)[0]
                return iata, side_ref.dest_iata
            iata = side_ref.dest_iata or airports.lookup(city, side_ref.dest_airport)[0]
            return side_ref.origin_iata, iata

        o_iata, _ = resolve_iata(ref, o_city)
        _, d_iata = resolve_iata(ref, d_city)

        records.append({
            "flight_no": fl_no,
            "carrier": ref.carrier if ref else "",
            "origin": {"city": o_city,
                       "airport": ref.origin_airport if ref else "",
                       "iata": o_iata},
            "dest": {"city": d_city,
                     "airport": ref.dest_airport if ref else "",
                     "iata": d_iata},
            "dep_time": dep,
            "arr_time": arr,
            "days": days,
            "days_csv": csv_days,
            "days_hna": hna_days,
            "csv_dates": csv_dates,
            "hna_dates": hna_dates,
            "effective_dates": effective_dates,
            "csv_products": sorted(csv_products),
            "hna_products": sorted(hna_products),
            "product": product_label(merged_products),
            "product_conflict": conflict,
            "review_decision": review or None,
            "notes": next((fl.notes for fl in csv_recs if fl.notes), None),
            "source": "both" if csv_recs and hna_recs else ("csv" if csv_recs else "hna"),
            "raw_csv_lines": sorted({fl.raw.get("source_line", "") for fl in csv_recs if fl.raw.get("source_line")}),
        })
    return records


def main() -> int:
    csv_flights = load_csv(CSV_DEFAULT)
    hna_files = {name: load_html(HNA666_DIR / name) for name in HNA666_FILES}
    records = build(csv_flights, hna_files)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "flights_normalized.json"
    payload = {
        "generated_at": date.today().isoformat(),
        "count": len(records),
        "note": "product=并集(宁多勿漏)，effective_dates=HNA666实际执飞优先/CSV备注修正兜底；product_conflict=True 需人工复核",
        "records": records,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"规范化快照已写入: {out}（{len(records)} 条，{out.stat().st_size/1024:.0f} KB）")
    print("产品分布:", dict(Counter(r["product"] for r in records)))
    print("产品冲突(待复核):", sum(1 for r in records if r["product_conflict"]))
    print("来源分布:", dict(Counter(r["source"] for r in records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())