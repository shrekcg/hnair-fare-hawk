"""用第三方航班数据校正底表基础信息（起降时刻/航站楼/经停）。

原则（2026-09-03）：
- 航线集合/班期/可飞日期/档位以海航随心飞底表为基线，第三方数据不覆盖这些；
- 第三方校正只更新 dep_time/arr_time/terminal/stops 等基础事实；
- 默认仅预览 diff（不写文件）；加 --apply 才正式合入（应用前快照 + versions.json 记录）；
- 实时查价接口不承担表更新职责（防风控）；本脚本低频手动执行。

输入文件格式（data/sediment/third_party/*.json，或 --file 指定）：
{"corrections": [{"flight_no": "Y87531", "origin_iata": "SZX", "dest_iata": "CGQ",
  "dep_time": "07:40", "arr_time": "15:50", "dep_terminal": "T3", "arr_terminal": "T2",
  "stops": [{"city": "杭州", "airport": "萧山", "terminal": "T3",
             "arrive": "09:55", "depart": "12:50", "stay": "2h55m"}],
  "source": "juhe", "observed_at": "2026-09-03"}]}

用法：
  python scripts/sedimentation/sync_third_party.py --preview            # 只打印差异建议
  python scripts/sedimentation/sync_third_party.py --apply --file xxx.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.third_party import (  # noqa: E402
    apply_to_record,
    correction_key,
    diff_fields,
    load_corrections,
    record_key,
)
from backend.sediment import load_records  # noqa: E402

NORMALIZED = ROOT / "data" / "sediment" / "flights_normalized.json"
THIRD_PARTY_DIR = ROOT / "data" / "sediment" / "third_party"
VERSIONS = ROOT / "data" / "sediment" / "versions.json"
SNAPSHOTS = ROOT / "data" / "sediment" / "snapshots"


def _match_records(records, corrections):
    """返回 (matched_idx_by_correction, unmatched_keys)。"""
    by_key: Dict[str, List[int]] = {}
    for idx, rec in enumerate(records):
        by_key.setdefault(record_key(rec), []).append(idx)
    matched = []
    unmatched = []
    for c in corrections:
        idxs = by_key.get(correction_key(c), [])
        if idxs:
            matched.append((c, idxs))
        else:
            unmatched.append(c)
    return matched, unmatched


def _fmt_diff(field: str, old, new) -> str:
    old_s = "—" if old is None else str(old)
    new_s = "—" if new is None else str(new)
    return f"{field}: {old_s} → {new_s}"


def run(preview: bool, corrections, records, note: str = "") -> dict:
    matched, unmatched = _match_records(records, corrections)
    print(f"校正条目: {len(corrections)} 条；匹配底表: {len(matched)} 条；无匹配: {len(unmatched)} 条")
    for c in unmatched:
        print(f"  [未匹配] {c.get('flight_no')} {c.get('origin_iata')}→{c.get('dest_iata')} 跳过")

    changed = 0
    summary: Dict[str, str] = {}
    for c, idxs in matched:
        # 同键多条底表记录（方向一致的特殊情况）统一应用
        for idx in idxs:
            rec = records[idx]
            diffs = diff_fields(rec, c)
            key = record_key(rec)
            if not diffs:
                continue
            changed += 1
            label = f"{rec.get('flight_no')} {c.get('origin_iata')}→{c.get('dest_iata')}"
            print(f"  [差异] {label}（{c.get('source')} {c.get('observed_at')}）")
            for field, (old, new) in diffs.items():
                print(f"    - {_fmt_diff(field, old, new)}")
            summary[key] = f"{c.get('source')} {c.get('observed_at')}"
            if not preview:
                records[idx] = apply_to_record(rec, c)

    if preview:
        print(f"\n预览结束：{changed} 条有差异将更新。加 --apply 可正式合入。")
        return {"preview": True, "changed": changed}

    if not changed:
        print("校正数据与底表一致，无需更新。")
        return {"changed": 0}

    # 覆盖前存档
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snap = SNAPSHOTS / f"flights_normalized_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    shutil.copy2(NORMALIZED, snap)

    payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    payload["records"] = records
    payload["note"] = (payload.get("note") or "") + f"；第三方校正: {note or corrections[0].get('source', 'third_party')} 共{changed}条"
    NORMALIZED.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 版本记录
    vlist = []
    if VERSIONS.exists():
        vlist = json.loads(VERSIONS.read_text(encoding="utf-8")).get("versions", [])
    try:
        snap_rel = str(snap.relative_to(ROOT))
    except ValueError:
        snap_rel = str(snap)
    entry = {
        "version": len(vlist) + 1,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "sync_third_party",
        "changed": changed,
        "count": len(payload["records"]),
        "product_dist": dict(Counter(r["product"] for r in payload["records"])),
        "snapshot": snap_rel,
        "corrections": len(corrections),
    }
    vlist.append(entry)
    VERSIONS.write_text(json.dumps({"versions": vlist}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已合入 {changed} 条更新；底表随附快照与版本记录。")
    return {"changed": changed, "snapshot": snap_rel}


def main() -> None:
    parser = argparse.ArgumentParser(description="第三方航班数据校正底表基础信息")
    parser.add_argument("--file", help="校正 JSON 文件路径（默认扫描 data/sediment/third_party/ 下所有 .json）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preview", action="store_true", default=True, help="只预览 diff，不写文件（默认）")
    group.add_argument("--apply", action="store_true", help="正式合入底表（自动快照 + 版本记录）")
    args = parser.parse_args()

    if args.file:
        files = [Path(args.file)]
    else:
        THIRD_PARTY_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(THIRD_PARTY_DIR.glob("*.json"))
    if not files:
        print(f"未找到校正文件。请放入 {THIRD_PARTY_DIR}/ 或 --file 指定。")
        return

    all_corrections = []
    for f in files:
        batch = load_corrections(f)
        print(f"读取 {f.name}: {len(batch)} 条有效校正")
        all_corrections.extend(batch)
    if not all_corrections:
        print("没有有效校正条目（格式参考 --help 说明）。")
        return

    records = load_records(force=True)
    result = run(
        preview=not args.apply,
        corrections=all_corrections,
        records=records,
        note=", ".join(p.name for p in files),
    )
    print("结果:", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()