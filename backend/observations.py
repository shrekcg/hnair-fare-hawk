"""查询观测覆盖层：查价成功后记录实时航班事实，查询时优先于底表静态字段。

设计（2026-09-03）：
- 底表 flights_normalized.json 来自 GitHub 二手数据（sxfroute CSV + HNA666 HTML），
  时刻可能过期/误抓（如 Y87531 CSV 08:50 vs 官方 07:40）。海航实时查价接口
  返回的 flightSegments 才是权威事实（起降时刻/航站楼/经停）。
- 每次 /api/flights/prices 查价成功后，把 fares 的实时字段写入本文件；
  sediment.query 返回记录时，若能匹配到观测键则用观测覆盖 dep_time/arr_time，
  并在 origin/dest 附 terminal 字段。
- 价格/班期/可飞日期绝不回写（每日变化且查询响应不含）；只覆盖结构化事实。
- 与 sediment 重建解耦：build_normalized.py 不动本文件，观测独立累积；
  如需固化进正式底表，可运行 scripts/sedimentation/apply_observations.py。
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

OBS_PATH = Path(__file__).resolve().parents[1] / "data" / "sediment" / "observations.json"

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None


def obs_key(flight_no: str, origin_city: str, dest_city: str) -> str:
    return f"{flight_no}|{origin_city}|{dest_city}"


def load(force: bool = False) -> Dict[str, Any]:
    with _lock:
        return _load_locked(force)


def _load_locked(force: bool = False) -> Dict[str, Any]:
    global _cache
    if _cache is None or force:
        try:
            payload = json.loads(OBS_PATH.read_text(encoding="utf-8"))
            _cache = {
                "observations": payload.get("observations") or {},
                "updated_at": payload.get("updated_at", ""),
            }
        except (OSError, ValueError):
            _cache = {"observations": {}, "updated_at": ""}
    return _cache


def _save_locked(data: Dict[str, Any]) -> None:
    OBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    OBS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def record(
    flight_no: str,
    origin_city: str,
    dest_city: str,
    dep_time: str = "",
    arr_time: str = "",
    dep_terminal: str = "",
    arr_terminal: str = "",
    stop: Optional[Dict[str, Any]] = None,
    observed_at: str = "",
) -> bool:
    """写入一条观测。同键同日只更新一次；返回是否真正写入。"""
    if not flight_no or not origin_city or not dest_city:
        return False
    key = obs_key(flight_no, origin_city, dest_city)
    today = observed_at or date.today().isoformat()
    changed = False
    with _lock:
        data = _load_locked(force=True)
        obs = data["observations"]
        cur = obs.get(key)
        # 同日已观测过且字段未变化时不重复写
        if cur and cur.get("observed_at") == today:
            same = (
                cur.get("dep_time") == dep_time
                and cur.get("arr_time") == arr_time
                and cur.get("dep_terminal") == dep_terminal
                and cur.get("arr_terminal") == arr_terminal
                and cur.get("stop") == stop
            )
            if same:
                return False
        obs[key] = {
            "flight_no": flight_no,
            "origin": origin_city,
            "dest": dest_city,
            "dep_time": dep_time,
            "arr_time": arr_time,
            "dep_terminal": dep_terminal,
            "arr_terminal": arr_terminal,
            "stop": stop,
            "observed_at": today,
            "source": "price_api",
        }
        data["updated_at"] = f"{today} {date.today().strftime('%H:%M:%S')}"
        _save_locked(data)
        changed = True
    return changed


def record_fares(fares: List[Dict[str, Any]], origin_city: str, dest_city: str, observed_at: str = "") -> int:
    """批量记录一次查价响应中的实时事实（按航班号去重，只取首条 times/stop）。"""
    written = 0
    seen: set[str] = set()
    for fare in fares:
        flight_no = str(fare.get("flight") or "").strip()
        if not flight_no or flight_no in seen:
            continue
        seen.add(flight_no)
        times = fare.get("times") or {}
        stop = fare.get("stop")
        if record(
            flight_no,
            origin_city,
            dest_city,
            dep_time=str(times.get("dep") or "").strip(),
            arr_time=str(times.get("arr") or "").strip(),
            dep_terminal=str(times.get("dep_terminal") or "").strip(),
            arr_terminal=str(times.get("arr_terminal") or "").strip(),
            stop=stop if isinstance(stop, dict) else None,
            observed_at=observed_at,
        ):
            written += 1
    return written


def apply_to_record(record_: Dict[str, Any]) -> Dict[str, Any]:
    """用观测覆盖单条底表记录的可变事实字段（时刻/航站楼）。返回新 dict，不修改入参。"""
    out = dict(record_)
    o = out.get("origin") or {}
    de = out.get("dest") or {}
    key = obs_key(
        str(out.get("flight_no", "")).strip(),
        str(o.get("city", "")).strip(),
        str(de.get("city", "")).strip(),
    )
    obs = (load().get("observations") or {}).get(key)
    if not obs:
        return out
    out = dict(out)
    if obs.get("dep_time"):
        out["dep_time"] = obs["dep_time"]
        out["_obs_time"] = True
    if obs.get("arr_time"):
        out["arr_time"] = obs["arr_time"]
    if obs.get("dep_terminal"):
        out["origin"] = {**o, "terminal": obs["dep_terminal"]}
    if obs.get("arr_terminal"):
        out["dest"] = {**de, "terminal": obs["arr_terminal"]}
    if obs.get("observed_at"):
        out["_obs_at"] = obs["observed_at"]
    return out