"""全量数据查询 CLI：查某城市可飞（出发/到达）的 666/2666 航班。

用法：
    .venv/bin/python scripts/sedimentation/query.py 海口 [--product 666] [--date 2026-09-15]
                     [--direction depart|arrive|both] [--json out.json]

--json 输出「某城市全部可飞航线×日期」清单文件（第 5 阶段与实时监控联动用）。
规则：product 过滤取规范化数据的 product（并集）；--date 时校验落在 effective_dates
且当天班期 days 包含该日星期。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

NORMALIZED = Path(__file__).resolve().parents[2] / "data" / "sediment" / "flights_normalized.json"

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def day_to_weekday(d: date) -> int:
    """date -> 1(周一)..7(周日)"""
    return d.isoweekday()


def load_records(path: str | Path | None = None) -> list[dict]:
    payload = json.loads(Path(path or NORMALIZED).read_text(encoding="utf-8"))
    return payload["records"]


def matches_product(record: dict, product: str | None) -> bool:
    if not product or product == "all":
        return True
    return product in record["product"].split("/")


def matches_date(record: dict, d: str) -> bool:
    """d 落在 effective_dates 且班期含当天星期。"""
    wd = day_to_weekday(date.fromisoformat(d))
    in_range = any(a <= d <= b for a, b in record["effective_dates"])
    in_days = wd in (record["days"] or [])
    return in_range and in_days


def query(city: str, product: str | None = None, d: str | None = None,
          direction: str = "both") -> list[dict]:
    out = []
    for rec in load_records():
        o, de = rec["origin"]["city"], rec["dest"]["city"]
        if city not in (o, de):
            continue
        if direction == "depart" and o != city:
            continue
        if direction == "arrive" and de != city:
            continue
        if not matches_product(rec, product):
            continue
        if d and not matches_date(rec, d):
            continue
        out.append(rec)
    return out


def fmt_dates(ranges: list[list[str]]) -> str:
    if not ranges:
        return "?"
    parts = []
    for a, b in ranges:
        parts.append(f"{a[5:]}~{b[5:]}" if a != b else a[5:])
    return ",".join(parts)


def fmt_days(days: list[int]) -> str:
    if not days:
        return "?"
    return "".join(str(d) for d in days)


def print_table(city: str, results: list[dict], d: str | None) -> None:
    if not results:
        print(f"无匹配（城市={city}）")
        return
    print(f"城市 {city} 共 {len(results)} 条可飞记录" + (f"（{d} 当天）" if d else ""))
    print(f"{'方向':<4}{'航班号':<9}{'航司':<7}{'对端城市':<9}{'起飞':<6}{'降落':<6}{'班期':<8}{'日期':<26}{'档位':<9}{'备注'}")
    for rec in sorted(results, key=lambda r: (r["origin"]["city"] != city, r["dep_time"] or "")):
        o, de = rec["origin"]["city"], rec["dest"]["city"]
        direction = "出" if o == city else "入"
        peer = de if o == city else o
        print(f"{direction:<4}{rec['flight_no']:<9}{rec['carrier']:<8}{peer:<10}"
              f"{(rec['dep_time'] or '?'):<7}{(rec['arr_time'] or '?'):<7}"
              f"{fmt_days(rec['days']):<8}{fmt_dates(rec['effective_dates']):<26}{rec['product']:<10}"
              f"{(rec['notes'] or '')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="查询全量航班计划（666/2666）")
    ap.add_argument("city", help="城市名，如 海口")
    ap.add_argument("--product", choices=["666", "2666", "all"], default=None,
                    help="档位过滤（默认不过滤）")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，校验当天班期与有效日期")
    ap.add_argument("--direction", choices=["depart", "arrive", "both"], default="both")
    ap.add_argument("--json", default=None, help="输出 JSON 清单文件路径（联动用）")
    args = ap.parse_args(argv)

    results = query(args.city, args.product, args.date, args.direction)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "generated_at": ts,
            "query": vars(args),
            "count": len(results),
            "flights": results,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写出 {len(results)} 条清单: {out}")
    print_table(args.city, results, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))