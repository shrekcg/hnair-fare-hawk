"""第三方航班数据校正层：以海航随心飞底表为基线，用第三方平台的航班计划数据修正基础信息。

设计（2026-09-03）：
- 底表 flights_normalized.json 的航线集合/班期/可飞日期/档位来自海航随心飞基线
  （HNA666 + sxfroute 两源），这些不能由第三方替换；
- 第三方平台（聚合数据/去哪儿/高德等航班 API 或手工整理）只校正「基础事实」：
  起飞/到达时刻、航站楼、经停信息；
- 实时查价接口只服务查价（顺带观测），不做表更新；表更新走本层 + 固化脚本，
  低频手动执行，避免触发风控。
- 通用数据格式：第三方适配器（或手工整理）产出 corrections 数组，本层负责
  校验 → 匹配底表 → diff，供 scripts/sedimentation/sync_third_party.py 应用。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_TERMINAL_RE = re.compile(r"^[A-Za-z0-9]{1,4}$")


def validate_time(value: Any) -> str:
    """校验并规范化 HH:MM（容忍 H:MM）。非法返回空串（视为无该字段）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    m = _TIME_RE.match(text)
    if not m:
        return ""
    h, mi = text.split(":")
    if not (0 <= int(h) <= 23 and 0 <= int(mi) <= 59):
        return ""
    return f"{int(h):02d}:{mi}"


def validate_terminal(value: Any) -> str:
    """校验并规范化航站楼（T1/T2/T3/T4/A/B…，字母数字 1-4 位）。"""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    # 容忍 "T3 航站楼" 之类的补充写法，只取词首 token
    first = text.split()[0] if text else ""
    if not _TERMINAL_RE.match(first):
        return ""
    return first


def normalize_stops(value: Any) -> Optional[List[Dict[str, Any]]]:
    """规范化经停数组；空/非法返回 None（表示该校正不携带经停信息）。

    合法元素：{"city": "杭州", "airport": "萧山", "terminal": "T3",
               "arrive": "09:55", "depart": "12:50", "stay": "2h55m"}
    city 必填；其余可选。arrive/depart 若提供需为 HH:MM。
    """
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    out: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        city = str(item.get("city") or "").strip()
        if not city:
            return None
        norm: Dict[str, Any] = {"city": city}
        airport = str(item.get("airport") or "").strip()
        if airport:
            norm["airport"] = airport
        terminal = validate_terminal(item.get("terminal"))
        if terminal:
            norm["terminal"] = terminal
        arrive = validate_time(item.get("arrive"))
        if arrive:
            norm["arrive"] = arrive
        depart = validate_time(item.get("depart"))
        if depart:
            norm["depart"] = depart
        stay = str(item.get("stay") or "").strip()
        if stay:
            norm["stay"] = stay
        out.append(norm)
    return out or None


def normalize_correction(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把一条第三方校正数据规范化为标准格式；非法返回 None。

    标准格式：
    {
      "flight_no": "Y87531",          # 必填
      "origin_iata": "SZX",           # 必填（底表 origin.iata）
      "dest_iata": "CGQ",             # 必填（底表 dest.iata）
      "dep_time": "07:40",            # 可选，HH:MM
      "arr_time": "15:50",            # 可选，HH:MM
      "dep_terminal": "T3",           # 可选
      "arr_terminal": "T2",           # 可选
      "stops": [{"city": "杭州", ...}],  # 可选；有经停给数组，直飞给 [] 或省略
      "source": "juhe",               # 数据来源标识
      "observed_at": "2026-09-03",    # 数据观察日期 YYYY-MM-DD
    }
    """
    if not isinstance(raw, dict):
        return None
    flight_no = str(raw.get("flight_no") or "").strip().upper()
    origin_iata = str(raw.get("origin_iata") or "").strip().upper()
    dest_iata = str(raw.get("dest_iata") or "").strip().upper()
    if not (flight_no and origin_iata and dest_iata):
        return None
    out: Dict[str, Any] = {
        "flight_no": flight_no,
        "origin_iata": origin_iata,
        "dest_iata": dest_iata,
        "source": str(raw.get("source") or "third_party").strip(),
        "observed_at": str(raw.get("observed_at") or "").strip(),
    }
    dep = validate_time(raw.get("dep_time"))
    if dep:
        out["dep_time"] = dep
    arr = validate_time(raw.get("arr_time"))
    if arr:
        out["arr_time"] = arr
    dep_terminal = validate_terminal(raw.get("dep_terminal"))
    if dep_terminal:
        out["dep_terminal"] = dep_terminal
    arr_terminal = validate_terminal(raw.get("arr_terminal"))
    if arr_terminal:
        out["arr_terminal"] = arr_terminal
    stops = normalize_stops(raw.get("stops"))
    if stops is not None:
        out["stops"] = stops
    return out


def load_corrections(path) -> List[Dict[str, Any]]:
    """读取第三方校正文件。支持两种结构：
    - {"corrections": [...]}
    - 顶层为数组 [...]
    逐条 normalize，非法条目跳过。
    """
    import json

    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = payload.get("corrections") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for raw in items:
        norm = normalize_correction(raw)
        if norm:
            out.append(norm)
    return out


def correction_key(c: Dict[str, Any]) -> str:
    return f"{c.get('flight_no', '')}|{c.get('origin_iata', '')}|{c.get('dest_iata', '')}"


def record_key(record: Dict[str, Any]) -> str:
    origin = record.get("origin") or {}
    dest = record.get("dest") or {}
    return (
        f"{str(record.get('flight_no', '')).strip().upper()}"
        f"|{str(origin.get('iata', '')).strip().upper()}"
        f"|{str(dest.get('iata', '')).strip().upper()}"
    )


def diff_fields(record: Dict[str, Any], correction: Dict[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    """比对底表记录与校正数据，返回不一致字段 {字段: (底表值, 校正值)}。

    只比较校正携带的字段；底表缺失或不同都算差异。
    """
    diffs: Dict[str, Tuple[Any, Any]] = {}
    if "dep_time" in correction:
        cur = str(record.get("dep_time") or "").strip()
        if cur != correction["dep_time"]:
            diffs["dep_time"] = (cur or None, correction["dep_time"])
    if "arr_time" in correction:
        cur = str(record.get("arr_time") or "").strip()
        if cur != correction["arr_time"]:
            diffs["arr_time"] = (cur or None, correction["arr_time"])
    if "dep_terminal" in correction:
        cur = (record.get("origin") or {}).get("terminal")
        cur = str(cur or "").strip().upper()
        if cur != correction["dep_terminal"]:
            diffs["dep_terminal"] = (cur or None, correction["dep_terminal"])
    if "arr_terminal" in correction:
        cur = (record.get("dest") or {}).get("terminal")
        cur = str(cur or "").strip().upper()
        if cur != correction["arr_terminal"]:
            diffs["arr_terminal"] = (cur or None, correction["arr_terminal"])
    if "stops" in correction:
        # 底表目前不落 stops 字段；stops 差异统一记为有校正
        diffs["stops"] = (None, correction["stops"])
    return diffs


def apply_to_record(record: Dict[str, Any], correction: Dict[str, Any]) -> Dict[str, Any]:
    """把校正应用到底表记录（返回新 dict，不改入参）。只覆盖校正携带的字段。"""
    out = dict(record)
    if "dep_time" in correction:
        out["dep_time"] = correction["dep_time"]
    if "arr_time" in correction:
        out["arr_time"] = correction["arr_time"]
    if "dep_terminal" in correction:
        origin = dict(out.get("origin") or {})
        origin["terminal"] = correction["dep_terminal"]
        out["origin"] = origin
    if "arr_terminal" in correction:
        dest = dict(out.get("dest") or {})
        dest["terminal"] = correction["arr_terminal"]
        out["dest"] = dest
    if "stops" in correction:
        out["stops"] = correction["stops"] if correction["stops"] else []
        out["_stops_source"] = correction.get("source", "third_party")
    return out