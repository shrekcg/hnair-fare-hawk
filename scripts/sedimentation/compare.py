"""两源对比校验：sxfroute CSV vs HNA666-flight-map 三档 HTML。

维度：
1. 存在性（键 = 航班号 + 出港城市 + 到港城市）
2. 起降时刻一致性
3. 班期（days）一致性
4. 有效日期范围差异（含备注限定日期，报告为信息级差异）
5. 产品归属差异（CSV 产品字段 vs HNA666 出现在哪个档位文件）

输出：控制台摘要 + `data/sediment/reports/compare_<ts>.json` 全量差异明细。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from models import Flight, canonical_key
from load_sxfroute import load_csv
from load_hna666 import load_html, DEFAULT_DIR as HNA666_DIR

REPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "sediment" / "reports"

CSV_DEFAULT = Path(__file__).resolve().parents[2] / "参考资料" / "sxfroute" / "data" / "airport.csv"
HNA666_FILES = ["666fms.html", "2666fms.html", "66666.html"]


def product_set(product: str) -> set[str]:
    """'666/2666' -> {'666','2666'}；'2666' -> {'2666'}"""
    return {p.strip() for p in product.split("/") if p.strip()}


def group_by_key(flights: list[Flight]) -> dict[tuple, list[Flight]]:
    groups: dict[tuple, list[Flight]] = {}
    for fl in flights:
        groups.setdefault(canonical_key(fl), []).append(fl)
    return groups


def compare_sets(
    csv_items: set, hna_items: set
) -> tuple[list, list, list]:
    """返回 (一致, csv 独有, hna 独有) —— 通用比较两个记录指纹集合。"""
    same = sorted(csv_items & hna_items)
    only_csv = sorted(csv_items - hna_items)
    only_hna = sorted(hna_items - csv_items)
    return same, only_csv, only_hna


def flight_fingerprints(flights: list[Flight]) -> set[str]:
    """(dep,arr,days) 指纹集合，用于时刻/班期一致性。缺时刻记录加大前缀。"""
    out = set()
    for fl in flights:
        if not fl.dep_time or not fl.arr_time:
            out.add(f"MISSING_TIME|{','.join(map(str, fl.days))}")
        else:
            out.add(f"{fl.dep_time}|{fl.arr_time}|{','.join(map(str, fl.days))}")
    return out


def compare(csv_flights: list[Flight], hna_files: dict[str, list[Flight]]) -> dict:
    csv_group = group_by_key(csv_flights)
    hna_666 = group_by_key(hna_files.get("666fms.html", []))
    hna_2666 = group_by_key(hna_files.get("2666fms.html", []))
    hna_all = group_by_key(hna_files.get("66666.html", []))

    # 存在性：CSV vs HNA666 可兑档位（666fms ∪ 2666fms）
    hna_tier_keys = set(hna_666) | set(hna_2666)
    hna_all_keys = set(hna_all)
    csv_keys = set(csv_group)

    only_csv = sorted(csv_keys - hna_tier_keys)
    only_hna_tier = sorted(hna_tier_keys - csv_keys)
    only_hna_all = sorted(hna_all_keys - (csv_keys | hna_tier_keys))
    common_keys = sorted(csv_keys & hna_tier_keys)

    # 逐键一致性
    time_mismatch: list[dict] = []
    week_mismatch: list[dict] = []
    missing_hna_time_keys: list[tuple] = []
    date_diff: list[dict] = []
    product_diff: list[dict] = []
    product_consistent = 0
    matched_keys = 0

    for key in common_keys:
        csv_recs = csv_group[key]
        hna_recs = hna_666.get(key, []) + hna_2666.get(key, [])
        if not hna_recs:
            continue
        matched_keys += 1

        hna_no_time = any(not fl.dep_time or not fl.arr_time for fl in hna_recs)
        csv_fp = flight_fingerprints(csv_recs)
        hna_fp = flight_fingerprints(hna_recs)

        # HNA666 侧缺时刻（多为经停中间段无 time_details）单独归类，不算指纹不一致
        if hna_no_time:
            missing_hna_time_keys.append(key)
            hna_fp = {fp for fp in hna_fp if not fp.startswith("MISSING_TIME")}

        # 时刻/班期逐记录比对（同 key 可能多版本）
        fp_same, fp_only_csv, fp_only_hna = compare_sets(csv_fp, hna_fp)
        if fp_only_csv or fp_only_hna:
            time_mismatch.append({
                "key": key,
                "csv_only": sorted(fp_only_csv),
                "hna_only": sorted(fp_only_hna),
            })

        # 日期范围差异：不判对错，只记录两源各自的日期区间
        csv_dates = sorted({tuple(r) for fl in csv_recs for r in fl.date_ranges})
        hna_dates = sorted({tuple(r) for fl in hna_recs for r in fl.date_ranges})
        if csv_dates != hna_dates:
            date_diff.append({
                "key": key,
                "csv": [list(r) for r in csv_dates],
                "hna": [list(r) for r in hna_dates],
                "csv_notes": [fl.notes for fl in csv_recs if fl.notes],
            })

        # 产品归属：CSV 档位 vs HNA666 出现档位
        csv_products = set()
        for fl in csv_recs:
            csv_products |= product_set(fl.product)
        hna_products = set()
        if key in hna_666:
            hna_products.add("666")
        if key in hna_2666:
            hna_products.add("2666")
        if csv_products == hna_products:
            product_consistent += 1
        else:
            product_diff.append({
                "key": key,
                "csv": sorted(csv_products),
                "hna": sorted(hna_products),
            })

    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "csv_count": len(csv_flights),
            "hna666_counts": {k: len(v) for k, v in hna_files.items()},
        },
        "key_cross": {
            "csv_total": len(csv_keys),
            "hna_tier_total": len(hna_tier_keys),
            "hna_all_total": len(hna_all_keys),
            "common_keys": len(common_keys),
            "keys_only_csv": only_csv,
            "keys_only_hna_tier": only_hna_tier,
            "keys_only_hna_all": only_hna_all,
        },
        "consistent": {
            "fingerprint_consistent_keys": matched_keys - len(time_mismatch),
            "product_consistent_keys": product_consistent,
        },
        "missing_hna_time_keys": missing_hna_time_keys,
        "missing_hna_time_count": len(missing_hna_time_keys),
        "time_mismatch_count": len(time_mismatch),
        "time_mismatches": time_mismatch,
        "week_mismatch_count": len(week_mismatch),
        "week_mismatches": week_mismatch,
        "date_diff_count": len(date_diff),
        "date_diffs": date_diff,
        "product_diff_count": len(product_diff),
        "product_diffs": product_diff,
    }
    return report


def print_summary(rep: dict) -> None:
    kc = rep["key_cross"]
    print("=" * 60)
    print("两源对比摘要")
    print("=" * 60)
    print(f"CSV 总键     : {kc['csv_total']}")
    print(f"HNA666 可兑键: {kc['hna_tier_total']}（666fms ∪ 2666fms）")
    print(f"HNA666 全量键: {kc['hna_all_total']}（66666.html）")
    print(f"共同键       : {kc['common_keys']}")
    print(f"仅 CSV 有    : {len(kc['keys_only_csv'])}")
    print(f"仅 HNA666 可兑有: {len(kc['keys_only_hna_tier'])}")
    print(f"仅 HNA666 全量有: {len(kc['keys_only_hna_all'])}（多为非可兑时段白班航班）")
    print("-" * 60)
    print(f"指纹(时刻+班期)一致键: {rep['consistent']['fingerprint_consistent_keys']} / 共同键")
    print(f"指纹不一致键    : {rep['time_mismatch_count']}")
    print(f"HNA666 缺时刻键 : {rep['missing_hna_time_count']}（多为经停中间段无 time_details）")
    print(f"产品归属一致键  : {rep['consistent']['product_consistent_keys']}")
    print(f"产品归属差异键  : {rep['product_diff_count']}")
    print(f"日期范围差异键  : {rep['date_diff_count']}（含格式差异与真实屏蔽差异）")
    print("-" * 60)
    for name, key in [("仅 CSV 有", "keys_only_csv"), ("仅 HNA666 可兑有", "keys_only_hna_tier"), ("仅 HNA666 全量有", "keys_only_hna_all")]:
        items = kc[key]
        if items:
            print(f"{name} 前 10 条:")
            for k in items[:10]:
                print("   ", k)
    for name, cnt, items in [
        ("指纹不一致", rep["time_mismatch_count"], rep["time_mismatches"]),
        ("产品归属差异", rep["product_diff_count"], rep["product_diffs"]),
        ("日期范围差异", rep["date_diff_count"], rep["date_diffs"]),
    ]:
        if items:
            print(f"{name} 前 10 条:")
            for it in items[:10]:
                print("   ", json.dumps(it, ensure_ascii=False))


def main() -> int:
    csv_path = sys.argv[1] if len(sys.argv) > 1 else str(CSV_DEFAULT)
    hna_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else HNA666_DIR
    csv_flights = load_csv(csv_path)
    hna_files = {name: load_html(hna_dir / name) for name in HNA666_FILES}
    rep = compare(csv_flights, hna_files)
    print_summary(rep)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = REPORT_DIR / f"compare_{ts}.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = REPORT_DIR / "latest_compare.json"
    latest.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n全量差异明细已写入: {out}")
    print(f"最新基线已更新: {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())