"""产品归属冲突复核工具。

工作流：
1. `review.py export`  → 把待复核冲突导出为 data/sediment/review/conflicts.csv
2. 用户用表格软件（Numbers/Excel/WPS）打开，逐行在 decision 列填：
      666   只按 666 档可飞
      2666  只按 2666 档可飞
      both  两档都可飞（666/2666）
      ignore  忽略（后续不再提示）
3. `review.py apply`   → 读取用户填好的 CSV，写入 data/sediment/review_decisions.json
4. 重新 `build_normalized.py` → 已裁决的键按 decision 应用，product_conflict 清除
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

NORMALIZED = Path(__file__).resolve().parents[2] / "data" / "sediment" / "flights_normalized.json"
REVIEW_DIR = Path(__file__).resolve().parents[2] / "data" / "sediment" / "review"
DECISIONS = Path(__file__).resolve().parents[2] / "data" / "sediment" / "review_decisions.json"

VALID = {"666", "2666", "both", "ignore"}
COLUMNS = [
    "flight_no", "origin", "dest", "csv_products", "hna_products", "dep_time", "arr_time",
    "days", "csv_dates", "hna_dates", "notes", "current_product", "suggestion", "decision",
]


def _dep_in_666_window(dep: str) -> bool:
    """666 可兑时段 20:00–次日08:00。"""
    if not dep:
        return False
    hh, _, mm = dep.partition(":")
    try:
        m = int(hh) * 60 + int(mm)
    except ValueError:
        return False
    return m >= 20 * 60 or m < 8 * 60


def suggestion_for(dep: str, hna_products: list[str]) -> str:
    """按规则给出建议裁决：起飞时刻是否落在 666 时段。"""
    if _dep_in_666_window(dep):
        return "666 时段内 → 666 或 both（以 hna 标记为准）"
    return "非 666 时段（19-20/08-09点）→ 仅 2666"


def conflict_records() -> list[dict]:
    payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    return [r for r in payload["records"] if r.get("product_conflict")]


def export() -> int:
    conflicts = conflict_records()
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = REVIEW_DIR / "conflicts.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in sorted(conflicts, key=lambda x: (x["flight_no"], x["origin"]["city"], x["dest"]["city"])):
            def join_dates(ds):
                return " ".join(f"{a[5:]}~{b[5:]}" if a != b else a[5:] for a, b in ds)
            w.writerow([
                r["flight_no"], r["origin"]["city"], r["dest"]["city"],
                "/".join(r["csv_products"]), "/".join(r["hna_products"]),
                r["dep_time"] or "", r["arr_time"] or "",
                "".join(str(d) for d in r["days"]),
                join_dates(r["csv_dates"]), join_dates(r["hna_dates"]),
                r["notes"] or "", r["product"],
                suggestion_for(r["dep_time"] or "", r["hna_products"]),
                "",  # decision 待用户填
            ])
    print(f"已导出 {len(conflicts)} 条待复核冲突: {out}")
    print("请用表格软件打开，逐行在 decision 列填: 666 / 2666 / both / ignore，保存后运行 apply")
    return 0


def apply() -> int:
    in_file = REVIEW_DIR / "conflicts.csv"
    if not in_file.exists():
        print(f"未找到 {in_file}，请先运行 export")
        return 1
    decisions: dict[str, dict] = {}
    skipped = 0
    with open(in_file, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = f"{row['flight_no']}|{row['origin']}|{row['dest']}"
            decision = (row.get("decision") or "").strip()
            if not decision:
                continue
            if decision not in VALID:
                print(f"跳过非法 decision {decision!r}（{key}）；允许值: {sorted(VALID)}")
                skipped += 1
                continue
            decisions[key] = {"decision": decision, "note": (row.get("notes") or "")[:200]}
    if not decisions and skipped == 0:
        print("CSV 中没有任何 decision，请先填写后保存")
        return 1
    DECISIONS.write_text(
        json.dumps({
            "exported_at": date.today().isoformat(),
            "count": len(decisions),
            "decisions": decisions,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {len(decisions)} 条裁决: {DECISIONS}" + (f"（跳过非法 {skipped} 条）" if skipped else ""))
    print("下一步运行 build_normalized.py 使裁决生效")
    return 0


def load_decisions(path: str | Path | None = None) -> dict[str, str]:
    p = Path(path or DECISIONS)
    if not p.exists():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    return {k: v["decision"] for k, v in payload.get("decisions", {}).items()}


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "export":
        return export()
    if cmd == "apply":
        return apply()
    print(f"未知子命令 {cmd!r}；支持 export / apply")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())