"""把查询观测固化进正式底表：以观测覆盖 flights_normalized.json 的可变事实字段。

流程：
1. 读 data/sediment/observations.json（查价时累积的实时观测）；
2. 对 flights_normalized.json 每条记录，若存在观测键则覆盖 dep_time/arr_time，
   并在 origin/dest 补 terminal 字段；
3. 覆盖前存档时点快照（data/sediment/snapshots/），追加版本记录。

说明：查询层（/api/flights/query）已经实时应用观测，本脚本用于把观测正式落进底表
文件，使底表本体也渐进更新（供导出/巡检使用）。sediment 重新 build 时会用上游
CSV/HNA 重建底表，观测层仍独立存在并继续覆盖展示。
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NORMALIZED = ROOT / "data" / "sediment" / "flights_normalized.json"
OBS_FILE = ROOT / "data" / "sediment" / "observations.json"
VERSIONS = ROOT / "data" / "sediment" / "versions.json"
SNAPSHOTS = ROOT / "data" / "sediment" / "snapshots"


def apply(force: bool = False) -> dict:
    payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    obs_payload = json.loads(OBS_FILE.read_text(encoding="utf-8"))
    obs = obs_payload.get("observations") or {}
    if not obs:
        print("观测库为空，无需固化。")
        return {"changed": 0, "count": len(payload["records"])}

    changed = 0
    for rec in payload["records"]:
        o = rec.get("origin") or {}
        de = rec.get("dest") or {}
        key = f"{rec.get('flight_no')}|{o.get('city')}|{de.get('city')}"
        ob = obs.get(key)
        if not ob:
            continue
        touched = False
        if ob.get("dep_time"):
            if rec.get("dep_time") != ob["dep_time"]:
                rec["dep_time"] = ob["dep_time"]
                touched = True
        if ob.get("arr_time"):
            if rec.get("arr_time") != ob["arr_time"]:
                rec["arr_time"] = ob["arr_time"]
                touched = True
        if ob.get("dep_terminal"):
            rec["origin"] = {**o, "terminal": ob["dep_terminal"]}
            touched = True
        if ob.get("arr_terminal"):
            rec["dest"] = {**de, "terminal": ob["arr_terminal"]}
            touched = True
        if touched:
            changed += 1

    if not changed:
        print("观测已全部生效，无变更。")
        return {"changed": 0, "count": len(payload["records"])}

    # 覆盖前存档
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snap = SNAPSHOTS / f"flights_normalized_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    shutil.copy2(NORMALIZED, snap)

    payload["note"] = (payload.get("note") or "") + "；查询观测固化: " + obs_payload.get("updated_at", "")
    NORMALIZED.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 版本记录
    vlist = []
    if VERSIONS.exists():
        vlist = json.loads(VERSIONS.read_text(encoding="utf-8")).get("versions", [])
    try:
        snap_rel = str(snap.relative_to(ROOT))
    except ValueError:
        # 快照目录可能被外部（如测试）指到 ROOT 之外，此时记录绝对路径
        snap_rel = str(snap)
    entry = {
        "version": len(vlist) + 1,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "apply_observations",
        "changed": changed,
        "count": len(payload["records"]),
        "product_dist": dict(Counter(r["product"] for r in payload["records"])),
        "snapshot": snap_rel,
    }
    vlist.append(entry)
    VERSIONS.write_text(json.dumps({"versions": vlist}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"changed": changed, "count": len(payload["records"]), "snapshot": snap_rel}


if __name__ == "__main__":
    result = apply()
    print("固化结果:", result)