"""海航低价抓取模块。"""

from __future__ import annotations

import os
import time
import hmac
import hashlib
import re
import shlex
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlsplit

import requests
from dotenv import load_dotenv

# 自动加载项目根目录下的 .env，方便分享给他人时按模板填值。
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class TokenExpiredError(Exception):
    """用于表示 token/sign/cookie 等鉴权信息失效。"""


# ==================== 硬编码区（按需替换）====================
# 注意：这里保留完整参数结构，但默认值均为占位符，避免泄露敏感信息。
REQUEST_URL = "https://app.hnair.com/ticket/lfs/airLowFareSearch"
REQUEST_URL_PLUS = "https://app.hnair.com/ticket/lfs/ffl/airLowFareSearch"
REQUEST_QUERY = {
    "token": "REPLACE_WITH_TOKEN",
}

REQUEST_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "appver": "10.12.5",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "cookie": "REPLACE_WITH_COOKIE",
    "ekingcode": "REPLACE_WITH_EKINGCODE",
    "hna-app": "APP",
    "hna-channel": "HTML5",
    "origin": "https://m.hnair.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://m.hnair.com/",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36",
}

REQUEST_BODY_TEMPLATE: Dict[str, Any] = {
    "common": {
        "sname": "MacIntel",
        "sver": "5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36",
        "schannel": "HTML5",
        "caller": "HTML5",
        "slang": "zh-CN",
        "did": "REPLACE_WITH_DID",
        "stime": 0,
        "szone": -480,
        "aname": "com.hnair.spa.web.standard",
        "aver": "10.12.5",
        "akey": "9E4BBDDEC6C8416EA380E418161A7CD3",
        "abuild": "63862",
        "atarget": "standard",
        "slat": "slat",
        "slng": "slng",
        "gtcid": "defualt_web_gtcid",
        "riskToken": "REPLACE_WITH_RISK_TOKEN",
        "captchaToken": "",
        "blackBox": "REPLACE_WITH_BLACKBOX",
        "validateToken": "",
        "sens": "REPLACE_WITH_SENS",
    },
    "data": {
        "originDestinations": [
            {
                "departureDate": "2026-03-28",
                "destination": "URC",
                "origin": "SZX",
                "destinationType": "1",
                "originType": "1",
            }
        ],
        "passenger": "ADT:1,CNN:1,INF:1",
        "_referer": "/book/query/start",
    },
}
# ===========================================================

# 海航 H5 标准环境签名参数（从官方前端静态资源提取）。
SIGN_HARD_CODE = "21047C596EAD45209346AE29F0350491"
SIGN_CERTIFICATE_HASH = "6093941774D84495A5D15D8F909CAA1E"
AUTO_CREDENTIAL_FILE = BASE_DIR / "credentials_auto.json"
CREDENTIAL_CACHE_FILE = BASE_DIR / "credentials_cache.json"
_LAST_REMOTE_FETCH_TS = 0.0
_REMOTE_CREDENTIAL_CACHE: Dict[str, str] = {}


def _is_placeholder(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return ("REPLACE_WITH_" in text) or ("YOUR_" in text)


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_auto_cfg_from_config() -> Dict[str, Any]:
    """从 config.json 读取自动凭证配置（由前端页面维护）。"""
    cfg = _read_json_file(BASE_DIR / "config.json")
    if not isinstance(cfg, dict):
        return {}
    auto_cfg = cfg.get("credential_auto", {})
    if not isinstance(auto_cfg, dict):
        return {}
    return auto_cfg


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    try:
        import json

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # 缓存写失败不影响主流程。
        pass


def _first_non_empty(data: Dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _normalize_credential_dict(raw: Dict[str, Any]) -> Dict[str, str]:
    """
    统一凭证字段命名，兼容以下格式：
    - token/hnairSign/cookie...
    - HNA_TOKEN/HNA_HNAIR_SIGN/HNA_COOKIE...
    - data.token 这样的二层结构（若上层包含 data）。
    """
    source = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(source, dict):
        return {}

    return {
        "token": _first_non_empty(source, ["token", "HNA_TOKEN"]),
        "hnairSign": _first_non_empty(source, ["hnairSign", "HNA_HNAIR_SIGN"]),
        "cookie": _first_non_empty(source, ["cookie", "HNA_COOKIE"]),
        "ekingcode": _first_non_empty(source, ["ekingcode", "HNA_EKINGCODE"]),
        "did": _first_non_empty(source, ["did", "HNA_DID"]),
        "riskToken": _first_non_empty(source, ["riskToken", "HNA_RISK_TOKEN"]),
        "blackBox": _first_non_empty(source, ["blackBox", "HNA_BLACKBOX"]),
        "sens": _first_non_empty(source, ["sens", "HNA_SENS"]),
    }


def _extract_credentials_from_curl(curl_cmd: str) -> Dict[str, str]:
    """从 curl 命令中提取凭证信息。"""
    if not curl_cmd:
        return {}
    try:
        parsed = parse_curl_command(curl_cmd)
        headers = parsed.get("headers", {})
        url = parsed.get("url", "")
        
        result: Dict[str, str] = {}
        
        # 从 URL 参数提取 token
        if url:
            parsed_url = urlsplit(url)
            query_params = dict(parse_qsl(parsed_url.query))
            if "token" in query_params:
                result["token"] = query_params["token"]
            if "hnairSign" in query_params:
                result["hnairSign"] = query_params["hnairSign"]
        
        # 从 headers 提取
        if "cookie" in headers:
            result["cookie"] = headers["cookie"]
        if "ekingcode" in headers:
            result["ekingcode"] = headers["ekingcode"]
        
        # 从 payload 提取
        data_str = parsed.get("data")
        if data_str:
            try:
                payload = json.loads(data_str)
                if isinstance(payload, dict):
                    common = payload.get("common", {})
                    if isinstance(common, dict):
                        if "did" in common:
                            result["did"] = str(common["did"])
                        if "riskToken" in common:
                            result["riskToken"] = str(common["riskToken"])
                        if "blackBox" in common:
                            result["blackBox"] = str(common["blackBox"])
                        if "sens" in common:
                            result["sens"] = str(common["sens"])
            except json.JSONDecodeError:
                pass
        
        return result
    except Exception:
        return {}


def _load_auto_credentials_from_file() -> Dict[str, str]:
    """
    从本地自动凭证文件读取（适合"有人持续更新文件"的场景）。
    默认读取项目根目录 credentials_auto.json，也可通过环境变量覆盖路径。
    """
    auto_cfg = _load_auto_cfg_from_config()
    enabled = bool(auto_cfg.get("enabled", False))
    configured = ""
    if enabled:
        configured = str(auto_cfg.get("file_path", "")).strip()
    if not configured:
        configured = os.getenv("HNA_CREDENTIALS_FILE", "").strip()
    path = Path(configured) if configured else AUTO_CREDENTIAL_FILE
    # 未启用自动更新且未显式配置环境变量时，不主动读取外部文件。
    if not enabled and not os.getenv("HNA_CREDENTIALS_FILE", "").strip():
        return {}
    raw = _read_json_file(path)
    return _normalize_credential_dict(raw)


def _load_credentials_from_config_curl() -> Dict[str, str]:
    """从 config.json 的 plus_curl 字段提取凭证。"""
    cfg = _read_json_file(BASE_DIR / "config.json")
    if not isinstance(cfg, dict):
        return {}
    
    # 优先使用 plus_curl，其次使用 normal_curl
    curl_cmd = cfg.get("plus_curl", "").strip()
    if not curl_cmd:
        curl_cmd = cfg.get("normal_curl", "").strip()
    
    return _extract_credentials_from_curl(curl_cmd)


def _fetch_auto_credentials_from_url() -> Dict[str, str]:
    """
    从远端接口自动拉取凭证（适合"有人提供凭证刷新服务"的场景）。
    默认关闭，需在 config.json 里开启并配置 url。
    """
    auto_cfg = _load_auto_cfg_from_config()
    enabled = bool(auto_cfg.get("enabled", False))
    url = ""
    if enabled:
        url = str(auto_cfg.get("url", "")).strip()
    if not url:
        url = os.getenv("HNA_CREDENTIALS_URL", "").strip()
    if not url:
        return {}

    global _LAST_REMOTE_FETCH_TS, _REMOTE_CREDENTIAL_CACHE

    now = time.time()
    # 同一进程内 60 秒内不重复请求。
    if now - _LAST_REMOTE_FETCH_TS < 60 and _REMOTE_CREDENTIAL_CACHE:
        return dict(_REMOTE_CREDENTIAL_CACHE)

    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return {}
        raw = resp.json()
        if not isinstance(raw, dict):
            return {}
        creds = _normalize_credential_dict(raw)
        _REMOTE_CREDENTIAL_CACHE = dict(creds)
        _LAST_REMOTE_FETCH_TS = now
        return creds
    except Exception:
        return {}


def _load_credentials() -> Dict[str, str]:
    """
    按优先级合并凭证：
    1. 环境变量（HNA_TOKEN / HNA_COOKIE / HNA_EKINGCODE / HNA_DID / HNA_RISK_TOKEN / HNA_BLACKBOX / HNA_SENS）
    2. .env 文件（项目根目录）
    3. 远端接口（若启用）
    4. config.json 的 plus_curl（若配置）
    5. 本地自动凭证文件（若启用）
    6. 硬编码占位符（兜底，用于提示未配置）
    """
    # 环境变量（最高优先级）
    env_creds = {
        "token": os.getenv("HNA_TOKEN", "").strip(),
        "hnairSign": os.getenv("HNA_HNAIR_SIGN", "").strip(),
        "cookie": os.getenv("HNA_COOKIE", "").strip(),
        "ekingcode": os.getenv("HNA_EKINGCODE", "").strip(),
        "did": os.getenv("HNA_DID", "").strip(),
        "riskToken": os.getenv("HNA_RISK_TOKEN", "").strip(),
        "blackBox": os.getenv("HNA_BLACKBOX", "").strip(),
        "sens": os.getenv("HNA_SENS", "").strip(),
    }

    # .env 文件（若环境变量为空，尝试从 .env 读取）
    dotenv_creds = {
        "token": os.getenv("HNA_TOKEN", "").strip(),
        "hnairSign": os.getenv("HNA_HNAIR_SIGN", "").strip(),
        "cookie": os.getenv("HNA_COOKIE", "").strip(),
        "ekingcode": os.getenv("HNA_EKINGCODE", "").strip(),
        "did": os.getenv("HNA_DID", "").strip(),
        "riskToken": os.getenv("HNA_RISK_TOKEN", "").strip(),
        "blackBox": os.getenv("HNA_BLACKBOX", "").strip(),
        "sens": os.getenv("HNA_SENS", "").strip(),
    }

    # 远端接口（若启用）
    remote_creds = _fetch_auto_credentials_from_url()

    # 从 config.json 的 plus_curl 提取凭证
    config_curl_creds = _load_credentials_from_config_curl()

    # 本地文件（若启用）
    file_creds = _load_auto_credentials_from_file()

    def _pick(*candidates: Dict[str, str]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        keys = ["token", "hnairSign", "cookie", "ekingcode", "did", "riskToken", "blackBox", "sens"]
        for key in keys:
            for cand in candidates:
                val = cand.get(key, "").strip()
                if val:
                    result[key] = val
                    break
            else:
                result[key] = ""
        return result

    # 优先级：环境变量 > .env > 远端 > config_curl > 文件 > 硬编码
    merged = _pick(env_creds, dotenv_creds, remote_creds, config_curl_creds, file_creds)

    # 若某项仍为空，使用硬编码占位符（保持向后兼容，便于报错提示）
    defaults = {
        "token": REQUEST_QUERY.get("token", ""),
        "hnairSign": "",
        "cookie": REQUEST_HEADERS.get("cookie", ""),
        "ekingcode": REQUEST_HEADERS.get("ekingcode", ""),
        "did": REQUEST_BODY_TEMPLATE.get("common", {}).get("did", ""),
        "riskToken": REQUEST_BODY_TEMPLATE.get("common", {}).get("riskToken", ""),
        "blackBox": REQUEST_BODY_TEMPLATE.get("common", {}).get("blackBox", ""),
        "sens": REQUEST_BODY_TEMPLATE.get("common", {}).get("sens", ""),
    }

    for key, default_val in defaults.items():
        if not merged.get(key):
            merged[key] = default_val

    return merged


def _safe_int(value: Any) -> int | None:
    """安全地将值转为 int，失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value == int(value):
            return int(value)
        return None
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _sign_refresh_enabled() -> bool:
    """是否开启 stime 刷新 + 官方算法重签（config.sign_refresh，默认关闭）。"""
    config = _read_json_file(BASE_DIR / "config.json")
    if not isinstance(config, dict):
        return False
    return bool(config.get("sign_refresh", False))


def _make_hnair_sign(
    headers: Dict[str, str],
    query: Dict[str, str],
    payload: Dict[str, Any],
) -> str:
    """
    海航 H5 官方签名算法（2026-09-02 从 app.ff7f308e1a.js 逆向确认）。

    message = concat(
        headers 中 key 以 "hna" 开头的值（key 字典序）,
        query 的所有值（key 字典序，不含 hnairSign 本身）,
        payload 中 common∪data 合并后所有标量值的值（key 字典序）,
        certificateHash,
    )
    sign = HMAC-SHA1(message, key=hardCode).hexdigest().upper()
    已用真实抓包请求验证与线上 hnairSign 完全一致。
    """
    hna_vals = [str(headers[k]) for k in sorted(headers) if str(k).startswith("hna")]
    q_vals = [str(query[k]) for k in sorted(query) if k != "hnairSign"]

    merged: Dict[str, Any] = {}
    common = payload.get("common")
    if isinstance(common, dict):
        merged.update(common)
    data = payload.get("data")
    if isinstance(data, dict):
        merged.update(data)
    d_vals = [
        str(merged[k])
        for k in sorted(merged)
        if isinstance(merged[k], (int, float, str, bool))
    ]

    message = "".join(hna_vals) + "".join(q_vals) + "".join(d_vals) + SIGN_CERTIFICATE_HASH
    return hmac.new(
        SIGN_HARD_CODE.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest().upper()


def _extract_itineraries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从响应体提取航班列表。"""
    # 优先尝试标准嵌套结构
    air_itineraries = (
        data.get("data", {}).get("airItineraries")
        or data.get("airItineraries")
        or data.get("data", {}).get("itineraries")
        or data.get("itineraries")
    )
    if isinstance(air_itineraries, list):
        return air_itineraries

    origin_destinations = data.get("data", {}).get("originDestinations")
    if isinstance(origin_destinations, list) and origin_destinations:
        first_destination = origin_destinations[0]
        if isinstance(first_destination, dict):
            air_itineraries = first_destination.get("airItineraries")
            if isinstance(air_itineraries, list):
                return air_itineraries

    # 兜底：若顶层就是列表，直接返回
    if isinstance(data, list):
        return data

    return []


def _format_flight_code(segment: Dict[str, Any]) -> str:
    """从 segment 提取航班号（如 HU1234）。"""
    airline = str(
        segment.get("airline")
        or segment.get("marketingAirlineCode")
        or segment.get("operatingAirlineCode")
        or ""
    ).strip()
    flight_number = str(segment.get("flightNumber") or segment.get("flightNo") or "").strip()
    if airline and flight_number:
        return f"{airline}{flight_number}"
    return str(segment.get("flightCode", "")).strip() or "UNKNOWN"


def _hhmm(value: Any) -> str:
    """把经停起降时间规整为 HH:mm；接口有时给完整 datetime，有时只有 HH:mm。"""
    s = str(value or "").strip()
    if not s:
        return ""
    if " " in s:
        s = s.split(" ")[-1]
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def _collect_stop_details(segments: Any) -> List[Dict[str, Any]]:
    """收集所有经停/中转点的详细信息（机场三字码、航站楼、起降时间、停留时长）。

    两种来源：
    - 单航段的 stopInfoCitys[]（同机经停，如 Y87531 深圳-长春 经停杭州）：
      自带 airPortTerm / arrivalTime / departrueTime / stopoverTime；
    - 多航段切分：中间点由 segments[i].arrivalAirportCode 与相邻段的
      arrivalTerminal/departureTerminal、arrivalTime/departureTime 推断。
    单航段只有 stopCitys 中文名时以中文名兜底（code 为空）。
    """
    details: List[Dict[str, Any]] = []
    # 1) stopInfoCitys（最完整）
    for s in segments:
        if not isinstance(s, dict):
            continue
        info = s.get("stopInfoCitys")
        if not isinstance(info, list):
            continue
        for item in info:
            if not isinstance(item, dict):
                continue
            code = str(item.get("airportCode") or "").strip().upper()
            if not code:
                continue
            details.append({
                "code": code,
                "city": str(item.get("airportName") or "").strip(),
                "terminal": str(item.get("airPortTerm") or "").strip(),
                "arrive": _hhmm(item.get("arrivalTime")),
                "depart": _hhmm(item.get("departrueTime")),
                "stay": str(item.get("stopoverTime") or "").strip(),
            })
    # 2) 多航段段间推断（同机经停/中转的共同中间点）
    for i in range(len(segments) - 1):
        s0, s1 = segments[i], segments[i + 1]
        if not (isinstance(s0, dict) and isinstance(s1, dict)):
            continue
        code = str(s0.get("arrivalAirportCode") or "").strip().upper()
        if not code or any(d.get("code") == code for d in details):
            continue
        term0 = str(s0.get("arrivalTerminal") or "").strip()
        term1 = str(s1.get("departureTerminal") or "").strip()
        terminal = term0 if term0 and term0 == term1 else "/".join(filter(None, [term0, term1]))
        details.append({
            "code": code,
            "city": "",
            "terminal": terminal,
            "arrive": _hhmm(s0.get("arrivalTime")),
            "depart": _hhmm(s1.get("departureTime")),
            "stay": "",
        })
    # 3) stopCitys 中文兜底（无三字码）
    for s in segments:
        if not isinstance(s, dict):
            continue
        stop_citys = s.get("stopCitys")
        if isinstance(stop_citys, str):
            stop_citys = [stop_citys]
        if not isinstance(stop_citys, list):
            continue
        for c in stop_citys:
            c = str(c or "").strip()
            if not c:
                continue
            if any(d.get("city") == c for d in details) or any(d.get("code") == c.upper() for d in details):
                continue
            details.append({"code": "", "city": c, "terminal": "", "arrive": "", "depart": "", "stay": ""})
    return details


def _segment_stop_info(segments: Any) -> Optional[Dict[str, Any]]:
    """从航段列表识别经停/中转信息（来自查询响应自身的 flightSegments，不额外请求）。

    返回 {"kind": "direct"|"stopover"|"transfer", "legs": N, "stops": M, "via": [...],
          "stops_detail": [...]}；无法判断（无航段）时返回 None。
    stops_detail 仅在有经停/中转点时附带，每项含
    code（三字码，可能为空）/city/terminal/arrive/depart/stay。
    - direct   ：单航段直飞
    - stopover ：同机经停（多航段同航班号，或单航段带 stopCitys/stopInfoCitys，
                 如 Y8 7531 深圳-长春 经停杭州）
    - transfer ：多航段且航班号不同（中转）
    via 为中间经停/中转机场三字码（不含起点与终点）。
    """
    if not isinstance(segments, list) or not segments:
        return None
    legs = len(segments)
    codes = [_format_flight_code(s) for s in segments if isinstance(s, dict)]
    non_empty = [c for c in codes if c]
    details = _collect_stop_details(segments)
    extra_stops = len(details)
    via: List[str] = [d.get("code") or d.get("city") for d in details if d.get("code") or d.get("city")]
    if legs == 1:
        if extra_stops:
            kind = "stopover"
            stops = extra_stops
        else:
            kind = "direct"
            stops = 0
    elif len(set(non_empty)) <= 1:
        # 同机经停；航段信息不全时保守按经停处理
        kind = "stopover"
        stops = extra_stops
    else:
        kind = "transfer"
        stops = extra_stops
    if not via:
        via = [
            str(s.get("arrivalAirportCode") or "").strip().upper()
            for s in segments[:-1]
            if isinstance(s, dict) and str(s.get("arrivalAirportCode") or "").strip()
        ]
    result: Dict[str, Any] = {"kind": kind, "legs": legs, "stops": stops, "via": via}
    if details:
        result["stops_detail"] = details
    return result


def _is_price_available(itinerary: Dict[str, Any]) -> bool:
    """
    检查航班票价是否真实可购买。
    
    检查以下条件：
    1. 库存状态 - 确保不是售罄状态
    2. 运营状态 - 确保航班正常运营
    3. 票价选项 - 确保有可购买的票价选项
    """
    # 检查航班状态
    flight_status = str(itinerary.get("flightStatus", "")).upper()
    if flight_status in ("SOLD_OUT", "CLOSED", "CANCELLED", "NO_SEAT"):
        return False
    
    # 检查是否有票价选项
    options = itinerary.get("airItineraryPrices")
    if not isinstance(options, list) or len(options) == 0:
        # 没有票价选项，可能是展示价格但无实际库存
        return False
    
    # 检查是否有至少一个可购买的选项
    has_available_option = False
    for option in options:
        if not isinstance(option, dict):
            continue
        # 检查选项状态
        option_status = str(option.get("status", "")).upper()
        if option_status in ("SOLD_OUT", "UNAVAILABLE", "CLOSED"):
            continue
        # 检查是否有价格
        option_price = _extract_price_from_option(option)
        if option_price is not None and option_price > 0:
            has_available_option = True
            break
    
    return has_available_option


def _extract_itinerary_price(itinerary: Dict[str, Any]) -> int | None:
    """提取航班票价，并验证是否可购买。"""
    # 首先验证票价是否真实可购买
    if not _is_price_available(itinerary):
        return None
    
    # 只使用"票面价"字段，不直接使用含税总价字段。
    for key in ("minLowPrice", "memberPrice", "plusPrice", "lowPrice", "lowPriceC"):
        price = _safe_int(itinerary.get(key))
        if price is not None:
            return price

    # 若接口只给了"含税总价 + 税费"，则回推票面价（总价-税费）。
    total_with_tax = _safe_int(
        itinerary.get("minLowPriceWithTax")
        or itinerary.get("memberPriceWithTax")
        or itinerary.get("plusPriceWithTax")
    )
    tax_price = _safe_int(itinerary.get("taxPrice"))
    if total_with_tax is not None and tax_price is not None:
        base_price = total_with_tax - tax_price
        if base_price >= 0:
            return base_price
    return None


# 「2666海航PLUS会员专享」等会员专享产品命名：数字即档位（666/2666/66666…）
_PLUS_TIER_RE = re.compile(r"(\d{3,6})\s*海航\s*PLUS\s*会员专享", re.IGNORECASE)


def _extract_tiers(itinerary: Dict[str, Any]) -> List[int]:
    """从 airItineraryPrices 解析该航班含哪些「PLUS 会员专享」产品档位。

    档位来源：fareFamilyName 命名中的数字（如「2666海航PLUS会员专享」→ 2666）。
    带 purchaseUserTags（会员身份门槛）但命名识别不到数字时，保守归入最低档 666，
    避免把带门槛价误判成普通可购价（漏报方向）。
    返回升序档位列表；无会员专享产品返回 []。
    """
    options = itinerary.get("airItineraryPrices")
    if not isinstance(options, list):
        return []
    tiers: set[int] = set()
    for opt in options:
        if not isinstance(opt, dict):
            continue
        name = str(opt.get("fareFamilyName") or "").strip()
        m = _PLUS_TIER_RE.search(name)
        if m:
            tiers.add(int(m.group(1)))
            continue
        if opt.get("purchaseUserTags"):
            tiers.add(666)
    return sorted(tiers)


def _extract_seat_info(itinerary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从航班响应提取余票与舱位信息（PLUS 专享档）。

    响应结构（2026-09-03 实测）：
      airItineraries[].airItineraryPrices[].flightBookingClasses[]
        ├─ bookingClass       "B"/"C"/"Z"/"R" 等
        ├─ inventoryQuantity  剩余张数；充足时为 10（封顶）
        └─ inventoryStatus    "A"=充足(10张+)；数字=精确剩余张数(1/2/4…)

    同一 bookingClass 可能跨多个价格产品（666/2666 PLUS 专享）重复出现，
    按舱位去重取最大余量（同一物理库存的不同产品视图）；seats=各舱位余量之和。
    返回 {"seats": N, "cabins": [{"cabin","qty","status"}, ...]}；解析不到返回 None。
    """
    options = itinerary.get("airItineraryPrices")
    if not isinstance(options, list) or not options:
        return None

    cabins: Dict[str, int] = {}
    for opt in options:
        if not isinstance(opt, dict):
            continue
        classes = opt.get("flightBookingClasses")
        if not isinstance(classes, list):
            continue
        for b in classes:
            if not isinstance(b, dict):
                continue
            code = str(b.get("bookingClass") or "").strip()
            if not code:
                continue
            qty = _safe_int(b.get("inventoryQuantity"))
            if qty is None:
                qty = 0
            if qty < 0:
                qty = 0
            status = str(b.get("inventoryStatus") or "").strip().upper()
            # "A"=充足，官方仅返回 10 封顶；精确数字时按字面值
            if status in ("A", "AVAILABLE"):
                qty = max(qty, 10)
            cabins[code] = max(cabins.get(code, 0), qty)

    if not cabins:
        return None

    cabin_list = [
        {
            "cabin": c,
            "qty": q,
            "status": "A" if q >= 10 else str(q),
        }
        for c, q in sorted(cabins.items())
    ]
    return {"seats": sum(cabins.values()), "cabins": cabin_list}


def _extract_price_from_option(option: Dict[str, Any]) -> int | None:
    """从 airItineraryPrices 子项中提取票面价，辅助选择可下单项。"""
    for key in ("memberPrice", "price", "adultPrice", "basePrice", "lowPrice", "minLowPrice"):
        price = _safe_int(option.get(key))
        if price is not None:
            return price

    traveler_prices = option.get("travelerPrices")
    if isinstance(traveler_prices, list):
        for traveler in traveler_prices:
            if not isinstance(traveler, dict):
                continue
            for key in ("ticketPrice", "baseFare", "basePrice", "price", "adultPrice", "totalTaxExclude"):
                price = _safe_int(traveler.get(key))
                if price is not None:
                    return price
    return None


def _is_auth_failure(status_code: int, data: Dict[str, Any]) -> tuple[bool, str]:
    """
    根据响应状态码和 body 判断是否鉴权失败。
    返回 (是否失败, 提示信息)。
    """
    if status_code in (401, 403):
        return True, f"HTTP {status_code}"

    if not isinstance(data, dict):
        return False, ""

    # 海航常见错误码结构
    error_code = str(
        data.get("code")
        or data.get("errorCode")
        or data.get("error_code")
        or data.get("status")
        or ""
    ).strip()

    error_msg = str(
        data.get("message")
        or data.get("errorMessage")
        or data.get("error_message")
        or data.get("msg")
        or ""
    ).strip()

    # 常见鉴权失败关键词
    auth_failure_codes = {
        "401",
        "403",
        "1001",
        "1002",
        "1003",
        "TOKEN_EXPIRED",
        "TOKEN_INVALID",
        "SIGN_INVALID",
        "AUTH_FAILED",
        "UNAUTHORIZED",
        "FORBIDDEN",
    }

    auth_failure_keywords = [
        "token",
        "sign",
        "验签",
        "auth",
        "unauthorized",
        "forbidden",
        "expire",
        "invalid",
        "鉴权",
        "登录",
        "失效",
    ]

    if error_code.upper() in auth_failure_codes:
        return True, f"code={error_code}, msg={error_msg}"

    lower_msg = error_msg.lower()
    for kw in auth_failure_keywords:
        if kw in lower_msg:
            return True, f"code={error_code}, msg={error_msg}"

    return False, ""


def _load_captured_request_profile(fare_type: str) -> Dict[str, Any]:
    """读取本地保存的完整浏览器请求，作为当前票价类型的请求模板。"""
    config = _read_json_file(BASE_DIR / "config.json")
    if not isinstance(config, dict):
        return {}

    config_key = "plus_curl" if fare_type == "plus" else "normal_curl"
    raw_command = str(config.get(config_key, "")).strip()
    if not raw_command:
        return {}

    parsed = parse_curl_command(raw_command)
    raw_url = str(parsed.get("url", "")).strip()
    raw_payload = parsed.get("data")
    if not raw_url or not raw_payload:
        return {}

    try:
        url_parts = urlsplit(raw_url)
        payload = json.loads(str(raw_payload))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

    headers = parsed.get("headers", {})
    if not isinstance(headers, dict) or not isinstance(payload, dict):
        return {}

    return {
        "url": f"{url_parts.scheme}://{url_parts.netloc}{url_parts.path}",
        "query": dict(parse_qsl(url_parts.query)),
        "headers": dict(headers),
        "payload": payload,
        "preserve_captured_sign": True,
    }


def _build_request_profile(
    from_code: str,
    to_code: str,
    date: str,
    fare_type: str = "normal",
    sign_refresh: bool | None = None,
) -> Dict[str, Any]:
    """
    构建请求 profile（query/headers/payload）。
    自动从环境变量/文件/远端接口加载凭证。

    sign_refresh：
    - None  按 config.sign_refresh 决定是否刷新 stime 并重签；
    - False 强制不刷新（用于验签失败时回退原始抓包签名）；
    - True  强制刷新。
    """
    captured_profile = _load_captured_request_profile(fare_type)
    if not captured_profile:
        # 票据派生：本类型未抓包时，复用另一档票据的凭证，仅改写端点与 specialZone。
        # 普通档与 PLUS 档共用同一套 cookie/token/签名，差异只有端点与 specialZone 字段。
        other_type = "plus" if fare_type == "normal" else "normal"
        captured_profile = _load_captured_request_profile(other_type)
        if captured_profile:
            captured_profile["derived_from"] = other_type

    if captured_profile:
        payload = deepcopy(captured_profile["payload"])
        data = payload.setdefault("data", {})
        origin_destinations = data.setdefault("originDestinations", [{}])
        if origin_destinations and isinstance(origin_destinations[0], dict):
            origin_destinations[0]["origin"] = from_code.upper()
            origin_destinations[0]["destination"] = to_code.upper()
            origin_destinations[0]["departureDate"] = date
        if fare_type == "plus":
            data["specialZone"] = "ffl"
            # PLUS 会员专享通道固定使用 ffl/airLowFareSearch 端点。
            # 抓包抓到的 airCtLowFareSearch 是普通低价接口，对 PLUS 会返回 0903“无航班”。
            captured_profile["url"] = REQUEST_URL_PLUS
        else:
            # 普通档永远走 airLowFareSearch 端点，且不带 specialZone。
            # 派生自 PLUS 票据时也要清掉 specialZone，否则返回的是会员档价格。
            data.pop("specialZone", None)
            captured_profile["url"] = REQUEST_URL

        captured_profile["payload"] = payload

        # 优化项 7：stime 刷新 + 官方算法重签（默认关闭）。
        # 开启后每次请求时间戳新鲜，消除“永恒时间戳”风控特征；
        # 若服务器不认新签名，fetch_price_status 会自动回退原始抓包签名。
        if sign_refresh is not False and (sign_refresh is True or _sign_refresh_enabled()):
            common = payload.setdefault("common", {})
            if not isinstance(common, dict):
                common = payload["common"] = {}
            common["stime"] = int(time.time() * 1000)
            captured_profile["query"]["hnairSign"] = _make_hnair_sign(
                headers=captured_profile["headers"],
                query=captured_profile["query"],
                payload=payload,
            )

        return captured_profile

    creds = _load_credentials()

    query = dict(REQUEST_QUERY)
    query["token"] = creds.get("token", query.get("token", ""))

    headers = dict(REQUEST_HEADERS)
    headers["cookie"] = creds.get("cookie", headers.get("cookie", ""))
    headers["ekingcode"] = creds.get("ekingcode", headers.get("ekingcode", ""))

    payload = deepcopy(REQUEST_BODY_TEMPLATE)
    common = payload.setdefault("common", {})
    common["did"] = creds.get("did", common.get("did", ""))
    common["riskToken"] = creds.get("riskToken", common.get("riskToken", ""))
    common["blackBox"] = creds.get("blackBox", common.get("blackBox", ""))
    common["sens"] = creds.get("sens", common.get("sens", ""))
    common["stime"] = int(time.time() * 1000)

    data = payload.setdefault("data", {})
    origin_destinations = data.setdefault("originDestinations", [{}])
    if origin_destinations and isinstance(origin_destinations[0], dict):
        origin_destinations[0]["origin"] = from_code.upper()
        origin_destinations[0]["destination"] = to_code.upper()
        origin_destinations[0]["departureDate"] = date

    # 静态模板仅作为未导入完整请求时的兼容兜底。
    if fare_type == "plus":
        data["specialZone"] = "ffl"
    else:
        data.pop("specialZone", None)

    # 根据 fare_type 选择不同的 API 端点
    request_url = REQUEST_URL_PLUS if fare_type == "plus" else REQUEST_URL

    return {
        "url": request_url,
        "query": query,
        "headers": headers,
        "payload": payload,
    }


# ==================== cURL 解析工具（用于快速导入浏览器请求）====================

def parse_curl_command(curl_cmd: str) -> Dict[str, Any]:
    """
    解析 curl 命令字符串，提取 method/headers/data/url。
    支持 -H 'Key: Value' 和 --data-raw '{json}' 等常见写法。
    """
    result: Dict[str, Any] = {"method": "GET", "url": "", "headers": {}, "data": None}
    if not curl_cmd or not isinstance(curl_cmd, str):
        return result

    # 简单分词（保留引号内内容）
    tokens = shlex.split(curl_cmd.strip())
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-X", "--request"):
            i += 1
            if i < len(tokens):
                result["method"] = tokens[i].upper()
        elif tok == "--url":
            i += 1
            if i < len(tokens):
                result["url"] = tokens[i]
        elif tok in ("-H", "--header"):
            i += 1
            if i < len(tokens):
                header_line = tokens[i]
                if ":" in header_line:
                    key, val = header_line.split(":", 1)
                    result["headers"][key.strip().lower()] = val.strip()
        elif tok in ("-b", "--cookie"):
            i += 1
            if i < len(tokens):
                result["headers"]["cookie"] = tokens[i]
        elif tok in ("-d", "--data", "--data-raw", "--data-binary"):
            i += 1
            if i < len(tokens):
                result["data"] = tokens[i]
                result["method"] = "POST"
        elif tok.startswith("http://") or tok.startswith("https://"):
            result["url"] = tok
        i += 1

    return result


# ==================== 对外接口 ====================


def _load_proxy() -> str:
    """从 config.json 读取可选 HTTP(S) 代理地址；未配置返回空串。"""
    try:
        cfg = _read_json(BASE_DIR / "config.json")
    except Exception:
        return ""
    if not isinstance(cfg, dict):
        return ""
    return str(cfg.get("proxy", "")).strip()


def _request_proxies(proxy: str) -> Dict[str, str] | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _request_price(profile: Dict[str, Any]) -> "tuple[str, List[Dict[str, Any]]]":
    """
    用给定 profile 执行一次抓价请求并解析。

    返回 (status, fares)：
    - "ok"      ：正常返回，fares 为可购航班列表；
    - "network" ：网络不通 / 网关错误 / 429 限流 / 非 200；
    - "parse"   ：响应不是预期 JSON 结构；
    - "empty"   ：200 且解析成功，但没有任何可购航班。

    鉴权失败（401/403/验签错误/占位符）一律抛 TokenExpiredError。
    """
    query = dict(profile.get("query", {}))
    headers = dict(profile.get("headers", {}))
    payload = dict(profile.get("payload", {}))
    request_url = str(profile.get("url", REQUEST_URL))

    # 完整浏览器请求保留其有效签名；静态兜底模板才尝试动态生成。
    if not profile.get("preserve_captured_sign"):
        query["hnairSign"] = _make_hnair_sign(headers=headers, query=query, payload=payload)

    # 若仍是占位符，直接按"接口凭证无效"处理，触发阻断告警。
    if _is_placeholder(query.get("token", "")) or _is_placeholder(headers.get("cookie", "")):
        raise TokenExpiredError("检测到 token/sign/cookie 仍为占位符，尚未配置真实凭证")

    proxies = _request_proxies(_load_proxy())

    try:
        response = requests.post(
            request_url,
            params=query,
            headers=headers,
            json=payload,
            timeout=20,
            proxies=proxies,
        )
    except requests.RequestException:
        return "network", []

    if response.status_code in (401, 403):
        raise TokenExpiredError(f"HTTP {response.status_code}: token/sign/cookie 失效或被风控")

    # 高频限流或网关错误多为临时问题，不判定为凭证过期，直接跳过本次。
    if response.status_code in (429, 500, 502, 503, 504):
        return "network", []

    if response.status_code != 200:
        return "network", []

    try:
        data = response.json()
    except ValueError as exc:
        # 返回体不是 JSON，通常意味着接口结构改变或网关返回异常页。
        raise TokenExpiredError(f"接口返回格式异常（非 JSON）：{exc}") from exc

    if not isinstance(data, dict):
        return "parse", []

    auth_failed, auth_hint = _is_auth_failure(response.status_code, data)
    if auth_failed:
        raise TokenExpiredError(f"接口鉴权失败：{auth_hint}" if auth_hint else "接口鉴权失败")

    itineraries = _extract_itineraries(data)
    if not itineraries:
        return "empty", []

    results: List[Dict[str, Any]] = []

    for itinerary in itineraries:
        if not isinstance(itinerary, dict):
            continue

        price = _extract_itinerary_price(itinerary)
        if price is None:
            continue

        segments = (
            itinerary.get("flightSegments")
            or itinerary.get("segments")
            or itinerary.get("segmentList")
        )
        segment0: Dict[str, Any] = {}
        if isinstance(segments, list) and segments and isinstance(segments[0], dict):
            segment0 = segments[0]
        flight_no = _format_flight_code(segment0)

        result: Dict[str, Any] = {
            "flight": str(flight_no),
            "price": price,
        }
        stop_info = _segment_stop_info(segments)
        if stop_info is not None:
            result["stop"] = stop_info

        # PLUS 余票与舱位（199 元档各舱位余量），供热票监控/实时余票展示
        seat_info = _extract_seat_info(itinerary)
        if seat_info is not None:
            result["seats"] = seat_info.get("seats", 0)
            result["cabins"] = seat_info.get("cabins", [])

        # 会员专享产品档位（666/2666/66666…）：daemon 按任务的档位条件过滤命中
        tiers = _extract_tiers(itinerary)
        if tiers:
            result["tiers"] = tiers

        # 实时起降时刻与航站楼（供前端覆盖底表静态 CSV 时刻，如 Y87531 CSV 误抓 08:50）
        if isinstance(segments, list) and segments:
            s_first = segments[0] if isinstance(segments[0], dict) else {}
            s_last = segments[-1] if isinstance(segments[-1], dict) else {}
            dep_time = _hhmm(s_first.get("departureTime"))
            arr_time = _hhmm(s_last.get("arrivalTime"))
            if dep_time or arr_time:
                result["times"] = {
                    "dep": dep_time,
                    "arr": arr_time,
                    "dep_terminal": str(s_first.get("departureTerminal") or "").strip(),
                    "arr_terminal": str(s_last.get("arrivalTerminal") or "").strip(),
                }

        results.append(result)

    return "ok", results


def fetch_price_status(
    from_code: str,
    to_code: str,
    date: str,
    fare_type: str = "normal",
) -> "tuple[str, List[Dict[str, Any]]]":
    """
    带状态分类的抓价接口。

    返回 (status, fares)：见 _request_price。

    开启 config.sign_refresh 后：先以刷新 stime + 重签的 profile 请求，
    若服务器返回验签错误，自动用未刷新的原始抓包签名重发一次（保证抓取效果不劣化）。
    """
    profile = _build_request_profile(
        from_code=from_code,
        to_code=to_code,
        date=date,
        fare_type=fare_type,
    )
    if not profile:
        return "network", []

    refresh_enabled = _sign_refresh_enabled() and bool(profile.get("preserve_captured_sign"))
    fallback_profile = None
    if refresh_enabled:
        fallback_profile = _build_request_profile(
            from_code=from_code,
            to_code=to_code,
            date=date,
            fare_type=fare_type,
            sign_refresh=False,
        )

    try:
        return _request_price(profile)
    except TokenExpiredError:
        # 重签被服务器拒绝时，回退到原始抓包签名重发，保持与旧行为一致。
        if fallback_profile is not None:
            return _request_price(fallback_profile)
        raise


def real_fetch_price(from_code: str, to_code: str, date: str, fare_type: str = "normal") -> List[Dict[str, Any]]:
    """
    抓取价格列表（兼容旧接口，字段只增不减）。

    返回格式：
    [{"flight": "HU1234", "price": 1360, "stop": {"kind": "direct"/"stopover"/"transfer", "legs": N, "stops": N-1, "via": [中间机场三字码...]}}, ...]

    异常规则：
    - 凭证失效/验签失败/结构异常：抛 TokenExpiredError
    - 其他错误：返回 []
    """
    status, fares = fetch_price_status(
        from_code=from_code,
        to_code=to_code,
        date=date,
        fare_type=fare_type,
    )
    if status != "ok":
        return []
    return fares

