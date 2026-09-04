"""轻量 Web API 服务（替代 Streamlit 前端入口，仅供本机回环访问）。

职责：
- 提供 REST API 给 React 前端（读/写 config.json、tasks.json、runtime_state.json、
  price_history.jsonl、run_log.txt），复用 app.py 中经过验证的读写逻辑与文件锁。
- 生产模式下同时托管 web/dist 静态资源。

运行：.venv/bin/python web_api.py  （绑定 127.0.0.1:8501）
daemon.py 完全不受影响：它只读同一批 JSON 文件，配置保存后下一轮自动生效。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# 复用现有 app.py 的读写与业务逻辑（不执行 Streamlit UI）
from app import (  # noqa: E402
    DEFAULT_CONFIG,
    LOG_PATH,
    add_task,
    add_tasks_batch,
    clear_notify_history,
    clear_price_history,
    clear_run_log,
    code_to_city_label,
    delete_task,
    delete_tasks,
    ensure_data_files,
    expand_task_dates,
    load_config,
    read_last_log_lines,
    read_notify_history,
    read_price_history,
    save_curl,
    save_feishu,
    save_monitor_window,
    save_notify_channels,
    save_price_query,
    save_proxy,
    save_polling,
    save_send_keys,
    set_status,
    set_task_enabled,
    set_tasks_enabled,
    update_tasks,
)
from backend.notifier import fake_price_hit_payload, send_test_alert, test_feishu  # noqa: E402
from backend.fetcher import TokenExpiredError, fetch_price_status  # noqa: E402
import backend.sediment as sediment  # noqa: E402
import backend.observations as observations  # noqa: E402
import backend.feishu_ws as feishu_ws  # noqa: E402
from backend.channels import CHANNEL_LABELS, build_confirm_card, send_channel  # noqa: E402
from city_codes import city_options, code_to_city_only, code_to_airport_name, resolve_code  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("WEB_API_PORT", "8501"))
DIST_DIR = os.path.join(BASE_DIR, "web", "dist")

MIME_MAP = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def _mask_key(value: str) -> str:
    """SendKey 掩码：SCT****abcd。"""
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "****"
    return value[:3] + "****" + value[-4:]


def _ticket_summary(curl: str, expected_path: str = "") -> Dict[str, Any]:
    if not curl:
        return {
            "configured": False,
            "url": "",
            "path": "",
            "length": 0,
            "endpoint_match": False,
            "endpoint_note": "",
        }
    # 只展示域名+路径，绝不暴露 query 里的 token/签名等敏感参数
    start = curl.find("http")
    url = curl[start:].split()[0] if start >= 0 else ""
    path = ""
    if url:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        url = f"{parts.scheme}://{parts.netloc}{parts.path}"
        path = parts.path
    endpoint_match = bool(expected_path and expected_path in path)
    return {
        "configured": True,
        "url": url,
        "path": path,
        "length": len(curl),
        "endpoint_match": endpoint_match,
        "endpoint_note": _endpoint_note(path, expected_path),
    }


def _endpoint_note(path: str, expected_path: str) -> str:
    """票据端点提示：PLUS 运行时会改写为 ffl 端点，普通票价使用 airLowFareSearch。"""
    if not path:
        return "未识别到请求路径"
    if "ffl" in path:
        return "PLUS 专享端点（ffl/airLowFareSearch）"
    if "airLowFareSearch" in path:
        return "低价查询端点（airLowFareSearch）"
    if "airCtLowFareSearch" in path:
        return "请求为 airCtLowFareSearch，运行时将改写为 ffl/airLowFareSearch（PLUS 专享）"
    return f"请求路径：{path}"


def _mask_url(url: str) -> str:
    """URL 脱敏：保留协议+主机，query 里的 token 类参数掩码（如钉钉 webhook）。"""
    url = str(url or "").strip()
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        if not parts.query:
            return url
        qs = []
        for kv in parts.query.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                qs.append(f"{k}={_mask_key(v)}")
            else:
                qs.append(_mask_key(kv))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(qs), ""))
    except ValueError:
        return _mask_key(url)


def _notify_channels_view(config: Dict[str, Any]) -> Dict[str, Any]:
    """多通道通知配置的脱敏视图：各渠道 enabled + 凭证掩码，飞书不可用配置不重复返回。"""
    nc = config.get("notify_channels") or {}
    if not isinstance(nc, dict):
        nc = {}
    wecom = nc.get("wecom") or {}
    dingtalk = nc.get("dingtalk") or {}
    bark = nc.get("bark") or {}
    ntfy = nc.get("ntfy") or {}
    view = {
        "urgent_enabled": bool(nc.get("urgent_enabled", True)),
        "wecom": {
            "enabled": bool(wecom.get("enabled", False)),
            "corp_id": _mask_key(wecom.get("corp_id", "")),
            "agent_id": _mask_key(wecom.get("agent_id", "")),
            "secret": _mask_key(wecom.get("secret", "")),
            "user_id": _mask_key(wecom.get("user_id", "")),
        },
        "dingtalk": {
            "enabled": bool(dingtalk.get("enabled", False)),
            "webhook": _mask_url(dingtalk.get("webhook", "")),
            "secret": _mask_key(dingtalk.get("secret", "")),
        },
        "bark": {
            "enabled": bool(bark.get("enabled", False)),
            "device_key": _mask_key(bark.get("device_key", "")),
            "server": str(bark.get("server", "") or "https://api.day.app").strip(),
        },
        "ntfy": {
            "enabled": bool(ntfy.get("enabled", False)),
            "topic": _mask_key(ntfy.get("topic", "")),
            "server": str(ntfy.get("server", "") or "https://ntfy.sh").strip(),
        },
        # 飞书长连接（事件订阅）状态：未配置/连接中/已连接/重连/失败
        "feishu_ws": feishu_ws.web_status(),
    }
    return view


def _sanitize_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """返回给前端的配置：凭证类字段脱敏，绝不回传完整 SendKey/cURL/AppSecret。"""
    send_keys = [str(k) for k in config.get("send_keys", []) if str(k).strip()]
    plus_curl = str(config.get("plus_curl", "") or "").strip()
    normal_curl = str(config.get("normal_curl", "") or "").strip()
    feishu = config.get("feishu") or {}
    feishu_app_id = str(feishu.get("app_id", "") or "").strip()
    feishu_receiver = str(feishu.get("receiver", "") or "").strip()

    return {
        "status": config.get("status", "stopped"),
        "send_keys": [_mask_key(k) for k in send_keys],
        "send_keys_count": len(send_keys),
        "monitor_window": {
            "start": config.get("monitor_window", {}).get("start", "07:00"),
            "end": config.get("monitor_window", {}).get("end", "23:00"),
        },
        "proxy": config.get("proxy", ""),
        "sign_refresh": bool(config.get("sign_refresh", False)),
        # derived_from 表示该档票据未抓包，运行时会自动从另一档派生（共用凭证，改写端点+specialZone）
        "plus_ticket": {
            **_ticket_summary(plus_curl, "ffl/airLowFareSearch"),
            "derived_from": "normal" if not plus_curl and normal_curl else "",
        },
        "normal_ticket": {
            **_ticket_summary(normal_curl, "airLowFareSearch"),
            "derived_from": "plus" if not normal_curl and plus_curl else "",
        },
        "polling": config.get("polling", DEFAULT_CONFIG.get("polling", {})),
        # 实时查价开关与最小间隔（防风控）：前端据此展示状态与禁用提示
        "price_query": {
            "enabled": bool(config.get("price_query", {}).get("enabled", True)),
            "min_interval": int(config.get("price_query", {}).get("min_interval", 8) or 8),
        },
        # 飞书渠道：AppID 完整回传（仅 hover 展示用，非机密；AppSecret 永不回传，receiver 不敏感可展示）
        "feishu": {
            "configured": bool(feishu_app_id and str(feishu.get("app_secret", "") or "").strip()),  # noqa: E501
            "app_id": feishu_app_id,
            "has_secret": bool(str(feishu.get("app_secret", "") or "").strip()),
            "receiver": feishu_receiver,
            "enabled": bool(feishu.get("enabled", True)),
            "urgent_enabled": bool(feishu.get("urgent_enabled", True)),
        },
        # 微信公众号（Server酱「方糖」）：开关状态
        "wechat": {"enabled": bool((config.get("wechat") or {}).get("enabled", True))},
        # 多通道通知（企业微信/钉钉/Bark/ntfy + 全局加急开关 + 飞书长连接状态）
        "notify_channels": _notify_channels_view(config),
    }


def _build_state() -> Dict[str, Any]:
    from app import TASKS_PATH, read_last_log_lines as _read_logs

    config = load_config()
    with open(TASKS_PATH, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    tasks_data = json.loads(raw) if raw else {"tasks": []}
    tasks = tasks_data.get("tasks", [])

    history = read_price_history(50)
    logs = _read_logs(LOG_PATH, line_count=200)

    # 统计（从历史里聚合）
    hit_count = sum(1 for r in history if int(r.get("price") or 0) <= int(
        _task_target(tasks, r.get("task_id", ""), r.get("date", ""),
                     r.get("from", ""), r.get("to", ""), r.get("fare_type", ""))
    )) if history else 0

    return {
        "config": _sanitize_config(config),
        "tasks": _build_task_rows(tasks),
        # 多通道通知历史（成功/失败都记录，最多 50 条）
        "notification_history": read_notify_history(50),
        # 飞书加急卡片确认回执（用户在卡片上点「确认已处理/忽略」后的记录）
        "card_confirmations": feishu_ws.read_confirmations(),
        "history": [
            {
                "ts": r.get("ts", ""),
                "date": r.get("date", ""),
                "from": code_to_city_label(str(r.get("from", ""))),
                "to": code_to_city_label(str(r.get("to", ""))),
                "fare_type": r.get("fare_type", "normal"),
                "flight": r.get("flight", ""),
                "price": r.get("price", ""),
                # 来源任务 id（前端据此在任务列表里匹配余票/舱位监控条件）
                "task_id": r.get("task_id", ""),
                # 档位（会员专享产品列表；普通可购价为空数组）
                "tiers": r.get("tiers") or [],
                # 起降时刻与余票（旧记录可能缺失，前端需防空）
                "dep_time": r.get("dep_time", ""),
                "arr_time": r.get("arr_time", ""),
                "seats": r.get("seats"),
            }
            for r in history
        ],
        "stats": {
            "task_count": len(tasks),
            "enabled_count": sum(1 for t in tasks if t.get("enabled", True)),
            "hit_count_24h": hit_count,
            "history_count": len(history),
        },
        "logs": logs,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _task_group_key(t: Dict[str, Any]) -> str:
    """任务分组键（与 app.add_tasks_batch 一致的穷举语义）：

    - 出发|到达|航班号|起飞|到达：不同航班/时刻各自成行；
    - 无航班号的旧式任务按航线分组（向后兼容）。
    """
    fc = str(t.get("from_code", "") or "").strip().upper()
    tc = str(t.get("to_code", "") or "").strip().upper()
    return "|".join([
        fc,
        tc,
        str(t.get("flight_no", "") or "").strip().upper(),
        str(t.get("dep_time", "") or "").strip(),
        str(t.get("arr_time", "") or "").strip(),
    ])


def _build_task_rows(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """任务列表按「出发|到达|航班号|起降时刻」分组合并展示。

    - 同一航班（同航班号且起降时刻相同）的多个监控日期合并成一条（dates 并集、档位并集）；
    - 一天内不同时间的航班各自独立成行（穷举，不按航线合并）；
    - 快照字段（代表航班/时刻/经停/档位）来自该分组最近一条带快照的任务，
      可被观测库中更新的实时事实（起降时刻/航站楼/经停）覆盖；
    - ids 为分组下所有底层任务 id，批量启停/删除以此为单位。
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for t in tasks:
        fc = str(t.get("from_code", "") or "").strip().upper()
        tc = str(t.get("to_code", "") or "").strip().upper()
        if not fc or not tc:
            continue
        grouped.setdefault(_task_group_key(t), []).append(t)

    airports = (sediment.meta().get("airports") or {})
    obs_data = (observations.load().get("observations") or {})
    rows: List[Dict[str, Any]] = []
    for group in grouped.values():
        from_code = str(group[0]["from_code"]).upper()
        to_code = str(group[0]["to_code"]).upper()
        dates = sorted({d for t in group for d in expand_task_dates(t)})
        products = sorted({p for t in group for p in str(t.get("product", "") or "").split("/") if p})
        snap = next((t for t in reversed(group) if t.get("flight_no")), group[0])
        flight_no = str(snap.get("flight_no", "") or "").strip()
        dep_time = str(snap.get("dep_time", "") or "").strip()
        arr_time = str(snap.get("arr_time", "") or "").strip()
        stop = snap.get("stop") if isinstance(snap.get("stop"), dict) else None
        from_city = code_to_city_only(from_code)
        to_city = code_to_city_only(to_code)
        # 观测覆盖：查价观测到的实时事实（时刻/航站楼/经停）优先于快照
        from_terminal = ""
        to_terminal = ""
        if flight_no:
            obs = obs_data.get(observations.obs_key(flight_no, from_city, to_city))
            if obs:
                if obs.get("dep_time"):
                    dep_time = obs["dep_time"]
                if obs.get("arr_time"):
                    arr_time = obs["arr_time"]
                if isinstance(obs.get("stop"), dict):
                    stop = obs["stop"]
                from_terminal = str(obs.get("dep_terminal") or "")
                to_terminal = str(obs.get("arr_terminal") or "")
        # 旧任务无档位快照时，从底表补一条代表航线的档位（便于展示）
        if not products:
            recs = sediment.query(from_city=from_city, to_city=to_city, limit=1)
            if recs:
                products = [p for p in str(recs[0].get("product", "") or "").split("/") if p]
        rows.append({
            "id": group[0].get("id", ""),
            "ids": [t.get("id", "") for t in group],
            "from_code": from_code,
            "to_code": to_code,
            "from_city": from_city,
            "to_city": to_city,
            "from_airport": (airports.get(from_code) or {}).get("airport", "") or code_to_airport_name(from_code),
            "to_airport": (airports.get(to_code) or {}).get("airport", "") or code_to_airport_name(to_code),
            "from_terminal": from_terminal,
            "to_terminal": to_terminal,
            "dates": dates,
            "date": dates[0] if dates else "",
            "date_end": dates[-1] if dates else "",
            "product": "/".join(products),
            "flight_no": flight_no,
            "dep_time": dep_time,
            "arr_time": arr_time,
            "stop": stop,
            "target_price": 199,
            "fare_type": "plus",
            # 档位条件（多选白名单）：组内并集；旧任务只有单值 tier 时并入
            "tiers": sorted({
                str(x).strip() for t in group
                for x in (t.get("tiers") if isinstance(t.get("tiers"), (list, tuple)) else ())
                if str(x).strip() in ("666", "2666", "66666")
            }.union({
                str(t.get("tier", "")).strip() for t in group
                if str(t.get("tier", "") or "").strip() in ("666", "2666", "66666")
            }), key=lambda x: int(x)),
            # 余票/舱位监控条件：取组内最严（min_seats 最大），舱位白名单取并集
            "min_seats": max(int(t.get("min_seats", 1) or 1) for t in group),
            "cabins": sorted({str(c).strip().upper() for t in group for c in (t.get("cabins") or []) if str(c).strip()}),
            "enabled": any(bool(t.get("enabled", True)) for t in group),
        })
    return rows


def _task_target(
    tasks: List[Dict[str, Any]], task_id: str, date: str, frm: str, to: str, fare_type: str
) -> int:
    """历史记录里没有 target_price 时，为统计强行找一个匹配任务的目标价。"""
    for t in tasks:
        if (
            t.get("id") == task_id
            or (
                t.get("date") == date
                and t.get("from_code", "").upper() == frm.upper()
                and t.get("to_code", "").upper() == to.upper()
            )
        ) and (not fare_type or t.get("fare_type") == fare_type):
            return int(t.get("target_price", 199))
    return 199


def _send_json(handler: BaseHTTPRequestHandler, code: int, obj: Any) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _serve_static(handler: BaseHTTPRequestHandler, path: str) -> None:
    """托管 web/dist 下的静态文件。"""
    if not os.path.isdir(DIST_DIR):
        body = "API OK - 前端未构建（web/dist 不存在）".encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return

    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    full = os.path.normpath(os.path.join(DIST_DIR, rel))
    if not full.startswith(DIST_DIR):
        handler.send_error(403)
        return
    if not os.path.isfile(full):
        # SPA 回退：未知路径一律给 index.html（前端路由处理）
        full = os.path.join(DIST_DIR, "index.html")
    ext = os.path.splitext(full)[1].lower()
    ctype = MIME_MAP.get(ext, "application/octet-stream")
    with open(full, "rb") as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "HNAControl/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # 静默访问日志；错误仍可通过 _log_error 查看
        pass

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parsed.query
        try:
            if path == "/api/state":
                _send_json(self, 200, _build_state())
            elif path == "/api/city/options":
                _send_json(self, 200, {"options": city_options()})
            elif path == "/api/city/resolve":
                q = _get_qs(parsed.query, "q")
                code, tips = resolve_code(q)
                _send_json(self, 200, {"code": code, "tips": tips[:8]})
            elif path == "/api/flights/meta":
                _send_json(self, 200, {"ok": True, **sediment.meta()})
            elif path == "/api/flights/options":
                _send_json(self, 200, {
                    "ok": True,
                    **sediment.options(
                        from_city=_get_qs(qs, "from"),
                        to_city=_get_qs(qs, "to"),
                        product=_get_qs(qs, "product") or None,
                        date_str=_get_qs(qs, "date"),
                        date_start=_get_qs(qs, "date_start"),
                        date_end=_get_qs(qs, "date_end"),
                    ),
                })
            elif path == "/api/flights/query":
                qs = parsed.query
                records = sediment.query(
                    city=_get_qs(qs, "city"),
                    from_city=_get_qs(qs, "from"),
                    to_city=_get_qs(qs, "to"),
                    product=_get_qs(qs, "product") or None,
                    date_str=_get_qs(qs, "date"),
                    date_start=_get_qs(qs, "date_start"),
                    date_end=_get_qs(qs, "date_end"),
                    flight_no=_get_qs(qs, "flight_no"),
                    direction=_get_qs(qs, "direction") or "both",
                    limit=int(_get_qs(qs, "limit") or 300),
                )
                # 查询观测覆盖：最近一次查价观测到的实时事实优先于底表静态字段
                records = [observations.apply_to_record(r) for r in records]
                _send_json(self, 200, {
                    "ok": True,
                    "count": len(records),
                    "records": records,
                    "meta": {"generated_at": sediment.meta()["generated_at"]},
                })
            elif path == "/api/flights/prices":
                self._flights_prices(parsed.query)
            elif path == "/api/ticket/raw":
                fare_type = _get_qs(parsed.query, "fare_type", "plus")
                config = load_config()
                key = "plus_curl" if fare_type == "plus" else "normal_curl"
                _send_json(self, 200, {"fare_type": fare_type, "raw": config.get(key, "")})
            elif path.startswith("/api/"):
                _send_json(self, 404, {"ok": False, "error": "not found"})
            else:
                _serve_static(self, path)
        except Exception as exc:  # noqa: BLE001
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    def _flights_prices(self, qs: str) -> None:
        """航线低价查询：normal=原价（日历价），plus=优惠价（PLUS 会员价档，通常 199）。

        仅当出发/到达都能解析为唯一三字码且指定日期时可用；逐档查询海航低价接口，
        返回按航班号映射的价格 dict。票据未配置或验签失败会给出状态与提示，不影响其他档位。
        """
        try:
            datetime.strptime(_get_qs(qs, "date"), "%Y-%m-%d")
        except ValueError:
            _send_json(self, 400, {"ok": False, "error": "查价必须指定日期（YYYY-MM-DD）。"})
            return
        from_code, from_tips = resolve_code(_get_qs(qs, "from"))
        to_code, to_tips = resolve_code(_get_qs(qs, "to"))
        if not from_code or not to_code:
            _send_json(self, 400, {"ok": False, "error": "出发/到达需能解析为唯一三字码。", "tips": (from_tips or to_tips)[:8]})
            return
        date_str = _get_qs(qs, "date")
        config = load_config()
        # 防风控：总开关关闭时不再请求海航接口（前端仍可查底表航班信息）
        pq = config.get("price_query") or {}
        if not pq.get("enabled", True):
            _send_json(self, 200, {
                "ok": True,
                "disabled": True,
                "query": {"from": from_code, "to": to_code, "date": date_str},
                "prices": {
                    "normal": {"status": "disabled", "prices": {}, "note": "实时查价已关闭（防风控），可在「监控管理」重新开启。"},
                    "plus": {"status": "disabled", "prices": {}, "note": "实时查价已关闭（防风控），可在「监控管理」重新开启。"},
                },
                "stops": {},
                "times": {},
                "seats": {"normal": {}, "plus": {}},
            })
            return
        min_interval = max(2, int(pq.get("min_interval") or 8))
        wait = _check_price_throttle(min_interval)
        if wait > 0:
            _send_json(self, 429, {
                "ok": False,
                "code": "throttled",
                "retry_after": int(wait) + 1,
                "error": f"实时查价过于频繁，请 {int(wait) + 1} 秒后再试（防风控）。",
            })
            return
        prices: Dict[str, Any] = {}
        stops_map: Dict[str, Any] = {}  # 经停/中转信息（来自查询响应自身，不额外请求）
        times_map: Dict[str, Any] = {}  # 实时起降时刻/航站楼（覆盖底表静态 CSV 时刻，仅查价后才有）
        seats_map: Dict[str, Dict[str, Any]] = {"normal": {}, "plus": {}}  # 实时余票/舱位（按档位）
        for fare_type in ("normal", "plus"):
            curl_key = "plus_curl" if fare_type == "plus" else "normal_curl"
            other_key = "normal_curl" if fare_type == "plus" else "plus_curl"
            has_own = bool(str(config.get(curl_key, "") or "").strip())
            has_other = bool(str(config.get(other_key, "") or "").strip())
            if not has_own and not has_other:
                prices[fare_type] = {
                    "status": "no_ticket",
                    "prices": {},
                    "note": "plus" if fare_type == "plus" else "normal",
                }
                continue
            try:
                status, fares = fetch_price_status(from_code, to_code, date_str, fare_type=fare_type)
            except TokenExpiredError:
                prices[fare_type] = {"status": "token_expired", "prices": {}, "note": "票据失效或验签失败，请在「票据管理」重新抓包。"}
                continue
            except Exception as exc:  # noqa: BLE001
                prices[fare_type] = {"status": "error", "prices": {}, "note": str(exc)}
                continue
            pmap: Dict[str, int] = {}
            for f in fares:
                fl = str(f.get("flight") or "").strip()
                pr = f.get("price")
                if fl and pr is not None:
                    pmap[fl] = int(pr)
                # 实时余票/舱位：按档位保留每个航班的余量与舱位明细
                if fl and f.get("seats") is not None:
                    seats_map[fare_type][fl] = {
                        "seats": int(f.get("seats") or 0),
                        "cabins": f.get("cabins") or [],
                    }
                stop = f.get("stop")
                if fl and isinstance(stop, dict):
                    item = dict(stop)
                    item["via"] = [code_to_city_only(v) for v in (stop.get("via") or [])]
                    details = []
                    for d in (stop.get("stops_detail") or []):
                        dd = dict(d)
                        code = str(dd.get("code") or "").strip()
                        if code:
                            dd["city"] = dd.get("city") or code_to_city_only(code)
                            dd["airport"] = code_to_airport_name(code)
                        details.append(dd)
                    item["stops_detail"] = details
                    stops_map[fl] = item
                times = f.get("times")
                if fl and isinstance(times, dict) and fl not in times_map:
                    times_map[fl] = times
            prices[fare_type] = {"status": status, "prices": pmap}
            # 查询观测回写：把本次查价观测到的实时事实（时刻/航站楼/经停）记入观测库，
            # 后续 /api/flights/query 会用它覆盖底表静态 CSV/HNA 字段，底表据此渐进更新。
            if fares:
                try:
                    observations.record_fares(
                        fares,
                        code_to_city_only(from_code),
                        code_to_city_only(to_code),
                        observed_at=date_str,
                    )
                except Exception:  # noqa: BLE001 观测失败不影响主流程
                    pass
        _send_json(self, 200, {
            "ok": True,
            "query": {"from": from_code, "to": to_code, "date": date_str},
            "prices": prices,
            "stops": stops_map,
            "times": times_map,
            "seats": seats_map,
        })

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            data = _read_body(self)
            if path == "/api/status":
                status = str(data.get("status", "")).strip()
                if status not in ("running", "stopped"):
                    _send_json(self, 400, {"ok": False, "error": "status 必须是 running/stopped"})
                    return
                set_status(status)
                _send_json(self, 200, {"ok": True, "status": status})
            elif path == "/api/tasks":
                self._post_task(data)
            elif path == "/api/tasks/batch":
                self._post_tasks_batch(data)
            elif path == "/api/tasks/enabled":
                ids = _task_ids(data)
                if not ids:
                    _send_json(self, 400, {"ok": False, "error": "缺少任务 id。"})
                    return
                set_tasks_enabled(ids, bool(data.get("enabled", True)))
                _send_json(self, 200, {"ok": True})
            elif path == "/api/tasks/delete":
                ids = _task_ids(data)
                if not ids:
                    _send_json(self, 400, {"ok": False, "error": "缺少任务 id。"})
                    return
                delete_tasks(ids)
                _send_json(self, 200, {"ok": True})
            elif path == "/api/tasks/update":
                self._post_tasks_update(data)
            elif path == "/api/send_keys":
                raw = str(data.get("raw", ""))
                keys = [k.strip() for k in raw.splitlines() if k.strip()]
                if not keys:
                    _send_json(self, 200, {"ok": True, "skipped": True, "message": "输入为空，SendKey 保持不变。"})
                    return
                save_send_keys(raw)
                _send_json(self, 200, {"ok": True, "skipped": False})
            elif path == "/api/test_alert":
                keys = [str(k).strip() for k in data.get("keys", []) if str(k).strip()]
                if not keys:
                    # 前端未传时使用已保存的 SendKey（避免用未保存的文本来测试）
                    config = load_config()
                    keys = [str(k).strip() for k in config.get("send_keys", []) if str(k).strip()]
                if not keys:
                    _send_json(self, 400, {"ok": False, "error": "没有可用的 SendKey，请先保存"})
                    return
                results = send_test_alert(keys)
                ok = sum(1 for v in results.values() if v)
                _send_json(self, 200, {"ok": True, "success": ok, "total": len(results), "results": results})
            elif path == "/api/monitor_window":
                start = str(data.get("start", "07:00"))
                end = str(data.get("end", "23:00"))
                save_monitor_window(_parse_time(start, "07:00"), _parse_time(end, "23:00"))
                _send_json(self, 200, {"ok": True})
            elif path == "/api/proxy":
                save_proxy(str(data.get("proxy", "")))
                _send_json(self, 200, {"ok": True})
            elif path == "/api/sign_refresh":
                config = load_config()
                config["sign_refresh"] = bool(data.get("enabled", False))
                from app import _write_json, CONFIG_PATH  # noqa: PLC0415
                _write_json(CONFIG_PATH, config)
                _send_json(self, 200, {"ok": True, "sign_refresh": bool(data.get("enabled", False))})
            elif path == "/api/price_query":
                enabled = bool(data.get("enabled", True))
                min_interval = int(data.get("min_interval") or 8)
                save_price_query(enabled, min_interval)
                _send_json(self, 200, {"ok": True, "enabled": enabled, "min_interval": min_interval})
            elif path == "/api/polling":
                save_polling(
                    day_min_sec=data.get("day_min_sec"),
                    day_max_sec=data.get("day_max_sec"),
                    night_min_sec=data.get("night_min_sec"),
                    night_max_sec=data.get("night_max_sec"),
                )
                poll = load_config().get("polling", {})
                _send_json(self, 200, {"ok": True, "polling": poll})
            elif path == "/api/feishu":
                self._post_feishu(data)
            elif path == "/api/notify/save":
                save_notify_channels(data)
                _send_json(self, 200, {"ok": True, "message": "通知渠道配置已保存，下一轮监控自动生效。"})
            elif path == "/api/notify/test":
                self._post_notify_test(data)
            elif path == "/api/feishu/callback":
                # 飞书事件订阅回调（长连接模式为 ws，这里兼容 webhook/challenge 验证与卡片回调）
                _send_json(self, 200, feishu_ws.process_card_action_payload(data))
            elif path == "/api/log/clear":
                archived = clear_run_log()
                _send_json(self, 200, {"ok": True, "archived_bytes": archived, "message": "运行日志已清除，旧日志已归档为 run_log.txt.bak（本地保留）。"})
            elif path == "/api/notify_history/clear":
                archived = clear_notify_history()
                _send_json(self, 200, {"ok": True, "archived_bytes": archived, "message": "通知历史已清除，旧记录已归档为 notification_history.jsonl.bak（本地保留）。"})
            elif path == "/api/price_history/clear":
                archived = clear_price_history()
                _send_json(self, 200, {"ok": True, "archived_bytes": archived, "message": "最近低价命中记录已清除，旧记录已归档为 price_history.jsonl.bak（本地保留）。"})
            elif path == "/api/feishu/test":
                config = load_config()
                feishu = config.get("feishu") or {}
                app_id = str(data.get("app_id", "")).strip() or str(feishu.get("app_id", "") or "").strip()
                app_secret = str(data.get("app_secret", "")).strip() or str(feishu.get("app_secret", "") or "").strip()
                receiver = str(data.get("receiver", "")).strip() or str(feishu.get("receiver", "") or "").strip()
                if not (app_id and app_secret and receiver):
                    _send_json(self, 400, {"ok": False, "error": "飞书配置不完整，请先填写 App ID / App Secret / 接收人。"})
                    return
                # 默认发交互确认卡片（带回执，可确认/忽略），内容模拟真实低价命中提醒
                test_title, test_content = fake_price_hit_payload()
                test_card = build_confirm_card(
                    test_title, test_content, callback_key="test_alert",
                    action_value=str(int(time.time() * 1000)),
                )
                # 跟随「当前已配置的加急形态」：默认加急开 → 加急卡片；关 → 普通卡片
                urgent = bool((config.get("feishu") or {}).get("urgent_enabled", True))
                ok, err, message_id = test_feishu(app_id, app_secret, receiver, card=test_card, urgent=urgent)
                if ok and message_id:
                    # 登记测试卡片原文：用户点「确认已处理/忽略」时回调能更新卡片并记录回执
                    feishu_ws.record_sent_card(message_id, test_card)
                if ok:
                    _send_json(self, 200, {"ok": True, "success": True, "message": "测试卡片已发送，请到飞书点击按钮验证确认回执。"})
                else:
                    _send_json(self, 200, {"ok": True, "success": False, "error": err})
            elif path == "/api/ticket":
                fare_type = "plus" if str(data.get("fare_type", "plus")) == "plus" else "normal"
                ok, msg = save_curl(str(data.get("raw", "")), fare_type)
                _send_json(self, 200, {"ok": ok, "error": None if ok else msg})
            else:
                _send_json(self, 404, {"ok": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    def _post_task(self, data: Dict[str, Any]) -> None:
        task_date = str(data.get("date", "")).strip()
        task_date_end = str(data.get("date_end", "")).strip()
        from_city = str(data.get("from_city", "")).strip()
        to_city = str(data.get("to_city", "")).strip()
        target_price = int(data.get("target_price") or 199)
        fare_type = "plus" if str(data.get("fare_type", "normal")) == "plus" else "normal"
        # PLUS 专享监控固定 199 元会员价档位，不适用自定义阈值；仅普通票价任务使用目标价。
        if fare_type == "plus":
            target_price = 199
        # 余票/舱位监控条件：min_seats（至少 N 张，默认 1）+ cabins 舱位白名单（可选）
        try:
            min_seats = max(1, int(data.get("min_seats") or 1))
        except (TypeError, ValueError):
            min_seats = 1
        cabin_raw = data.get("cabins")

        if not task_date:
            _send_json(self, 400, {"ok": False, "error": "请先选择日期。"})
            return
        if task_date_end and task_date_end < task_date:
            _send_json(self, 400, {"ok": False, "error": "结束日期不能早于开始日期。"})
            return
        if not from_city:
            _send_json(self, 400, {"ok": False, "error": "请先填写出发地。"})
            return
        if not to_city:
            _send_json(self, 400, {"ok": False, "error": "请先填写到达地。"})
            return
        from_code, from_tips = resolve_code(from_city)
        to_code, to_tips = resolve_code(to_city)
        if not from_code:
            _send_json(self, 400, {"ok": False, "error": "出发地无法唯一匹配，请换个写法或直接输入三字码。", "tips": from_tips[:8]})
            return
        if not to_code:
            _send_json(self, 400, {"ok": False, "error": "到达地无法唯一匹配，请换个写法或直接输入三字码。", "tips": to_tips[:8]})
            return
        config = load_config()
        has_plus = bool(str(config.get("plus_curl", "") or "").strip())
        has_normal = bool(str(config.get("normal_curl", "") or "").strip())
        if fare_type == "plus" and not (has_plus or has_normal):
            _send_json(self, 400, {"ok": False, "error": "你选择了 PLUS专享，但尚未配置任何票据（PLUS 或普通），请先到「监控管理」粘贴抓包 cURL。"})
            return

        add_task(
            task_date=task_date,
            task_date_end=task_date_end,
            from_code=from_code,
            to_code=to_code,
            target_price=target_price,
            fare_type=fare_type,
            min_seats=min_seats,
            cabins=cabin_raw,
        )
        label = "PLUS专享" if fare_type == "plus" else "普通票价"
        seat_label = f"余票≥{min_seats}张" if min_seats > 1 else ""
        cabin_list = []
        if isinstance(cabin_raw, (list, tuple)):
            cabin_list = [str(c).strip().upper() for c in cabin_raw if str(c).strip()]
        elif isinstance(cabin_raw, str):
            cabin_list = [c.strip().upper() for c in cabin_raw.split(",") if c.strip()]
        if cabin_list:
            seat_label = (seat_label + " " if seat_label else "") + f"舱位({'、'.join(sorted(set(cabin_list)))})"
        date_label = task_date if not task_date_end else f"{task_date} ~ {task_date_end}"
        _send_json(
            self,
            200,
            {
                "ok": True,
                "message": f"任务已添加：{date_label} | {code_to_city_label(from_code)} -> {code_to_city_label(to_code)} | {label} | <= {target_price}" + (f" | {seat_label}" if seat_label else ""),
            },
        )

    def _post_tasks_batch(self, data: Dict[str, Any]) -> None:
        """批量创建监控任务（从「航线查询」勾选 -> 弹窗二次确认）。

        body.items 每项：{from_code, to_code, dates: [YYYY-MM-DD...], product,
        flight_no, dep_time, arr_time, stop}。穷举语义：不同航班号/起降时刻各自创建
        独立监控任务；仅同航班号且起降时刻相同时合并日期（dates/档位并集）。
        """
        items = data.get("items")
        if not isinstance(items, list) or not items:
            _send_json(self, 400, {"ok": False, "error": "缺少 items（至少一条）。"})
            return
        ok_items: List[Dict[str, Any]] = []
        errors: List[str] = []
        for it in items:
            if not isinstance(it, dict):
                errors.append("无效条目")
                continue
            from_code, _ = resolve_code(str(it.get("from_code", "")).strip())
            to_code, _ = resolve_code(str(it.get("to_code", "")).strip())
            if not from_code or not to_code:
                errors.append(f"航线无法解析：{it.get('from_code')} -> {it.get('to_code')}")
                continue
            dates = [str(d).strip() for d in (it.get("dates") or []) if str(d).strip()]
            if not dates:
                errors.append(f"缺少监控日期：{from_code} -> {to_code}")
                continue
            item = {
                "from_code": from_code,
                "to_code": to_code,
                "dates": dates,
                "product": str(it.get("product", "") or "").strip(),
                "tier": str(it.get("tier", "") or "").strip(),
                # 档位条件多选白名单（空=不限）；旧客户端只发单值 tier，由 app 层兼容
                "tiers": [str(x).strip() for x in (it.get("tiers") or []) if str(x).strip()],
                "flight_no": str(it.get("flight_no", "") or "").strip(),
                "dep_time": str(it.get("dep_time", "") or "").strip(),
                "arr_time": str(it.get("arr_time", "") or "").strip(),
            }
            # 余票/舱位监控条件（弹窗内统一设置）
            if "min_seats" in it:
                try:
                    item["min_seats"] = max(1, int(it.get("min_seats") or 1))
                except (TypeError, ValueError):
                    pass
            if "cabins" in it:
                item["cabins"] = it.get("cabins")
            stop = it.get("stop")
            if isinstance(stop, dict):
                item["stop"] = stop
            ok_items.append(item)

        if not ok_items:
            _send_json(self, 400, {"ok": False, "error": "没有可创建的任务。", "errors": errors[:8]})
            return
        result = add_tasks_batch(ok_items)
        parts = [f"已创建 {result['created']} 个监控任务"]
        if result["merged"]:
            parts.append(f"并入已有任务 {result['merged']} 个（同航班号且起降时刻相同的日期已并入）")
        _send_json(self, 200, {"ok": True, **result, "message": "；".join(parts)})

    def _post_tasks_update(self, data: Dict[str, Any]) -> None:
        """更新监控任务（编辑弹窗）：按 id/ids 一次性更新组内全部任务。"""
        ids = _task_ids(data)
        if not ids:
            _send_json(self, 400, {"ok": False, "error": "缺少任务 id。"})
            return
        from_code = str(data.get("from_code", "") or "").strip()
        to_code = str(data.get("to_code", "") or "").strip()
        if from_code:
            code, tips = resolve_code(from_code)
            if not code:
                _send_json(self, 400, {"ok": False, "error": "出发地无法唯一匹配，请换个写法或直接输入三字码。", "tips": tips[:8]})
                return
            data["from_code"] = code
        if to_code:
            code, tips = resolve_code(to_code)
            if not code:
                _send_json(self, 400, {"ok": False, "error": "到达地无法唯一匹配，请换个写法或直接输入三字码。", "tips": tips[:8]})
                return
            data["to_code"] = code
        dates = data.get("dates")
        if isinstance(dates, list) and not dates:
            _send_json(self, 400, {"ok": False, "error": "至少保留一个监控日期。"})
            return
        touched = update_tasks(ids, data)
        _send_json(self, 200, {"ok": True, "touched": touched, "message": f"已更新 {touched} 个监控任务"})

    def _post_feishu(self, data: Dict[str, Any]) -> None:
        """保存飞书通知渠道配置。app_secret 为空串表示不修改（永不回传）。"""
        app_id = str(data.get("app_id", "")).strip()
        app_secret = str(data.get("app_secret", "")).strip()
        receiver = str(data.get("receiver", "")).strip()
        save_feishu(app_id=app_id, app_secret=app_secret, receiver=receiver)
        _send_json(
            self,
            200,
            {
                "ok": True,
                "message": "飞书配置已保存" + ("（App Secret 未修改）" if not app_secret else ""),
            },
        )

    def _post_notify_test(self, data: Dict[str, Any]) -> None:
        """发送指定渠道的测试消息：模拟一条真实低价命中提醒（假数据已注明，跟随当前加急形态）。

        飞书用可点按确认的交互卡片（callback_key=test_alert）；其他渠道按各自形态。
        """
        channel = str(data.get("channel", "")).strip()
        if channel not in CHANNEL_LABELS:
            _send_json(self, 400, {"ok": False, "error": f"未知渠道：{channel or '(空)'}，可选 {', '.join(CHANNEL_LABELS)}"})
            return
        config = load_config()
        # 与 daemon 相同的通知配置结构：notify_channels + 旧 feishu 配置
        notify_cfg = dict(config.get("notify_channels") or {})
        notify_cfg.setdefault("urgent_enabled", True)
        notify_cfg["feishu"] = config.get("feishu") or {}
        title, content = fake_price_hit_payload()
        card = None
        if channel == "feishu":
            card = build_confirm_card(title, content, callback_key="test_alert", action_value=str(int(time.time() * 1000)))
        ok, error, message_id = send_channel(notify_cfg, channel, "important", title, content, card=card)
        if ok and message_id and card:
            # 登记卡片原文，用户点「确认已处理」时回调能更新卡片并记录回执
            feishu_ws.record_sent_card(message_id, card)
        _send_json(self, 200, {
            "ok": True,
            "success": ok,
            "channel": channel,
            "channel_label": CHANNEL_LABELS[channel],
            "message": "测试消息已发送" if ok else f"发送失败：{error}",
            "error": error or None,
            "message_id": message_id,
        })

    def _post_feishu_callback(self, data: Dict[str, Any]) -> None:
        """飞书卡片回调入口：处理确认/忽略，返回卡片更新后的 toast 提示。"""
        result = feishu_ws.process_card_action_payload(data)
        _send_json(self, 200, result)


def _get_qs(query: str, key: str, default: str = "") -> str:
    for part in query.split("&"):
        if part.startswith(key + "="):
            from urllib.parse import unquote

            return unquote(part[len(key) + 1 :])
    return default


def _task_ids(data: Dict[str, Any]) -> List[str]:
    """兼容任务操作请求中的 id 与 ids（分组行批量操作传 ids 数组）。"""
    ids = data.get("ids")
    if isinstance(ids, list) and ids:
        return [str(i).strip() for i in ids if str(i).strip()]
    tid = str(data.get("id", "")).strip()
    return [tid] if tid else []


def _parse_time(value: str, fallback: str):
    from datetime import datetime as _dt

    try:
        return _dt.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        return _dt.strptime(fallback, "%H:%M").time()


# ---- 实时查价节流（防风控） ----
# 只作用于 web 入口；daemon 直连 fetcher，自带任务间错峰与轮询间隔。
_price_throttle_lock = threading.Lock()
_last_price_query_ts = 0.0  # time.monotonic() 基准


def _check_price_throttle(min_interval: int) -> float:
    """全局查价最小间隔：不足间隔返回剩余秒数（>0 表示应拒绝），否则放行并记录本次。"""
    global _last_price_query_ts
    now = time.monotonic()
    with _price_throttle_lock:
        if _last_price_query_ts > 0 and now - _last_price_query_ts < min_interval:
            return round(min_interval - (now - _last_price_query_ts), 1)
        _last_price_query_ts = now
        return 0.0


def main() -> None:
    ensure_data_files()
    # 飞书配置完整则启动长连接事件订阅（卡片回调闭环依赖它）
    try:
        feishu_ws.start_if_configured()
    except Exception as exc:  # noqa: BLE001 长连接失败不影响控制台本体
        print(f"[web_api] 飞书长连接启动失败：{exc}", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[web_api] 控制台 API 已启动: http://{HOST}:{PORT}", flush=True)
    if os.path.isdir(DIST_DIR):
        print(f"[web_api] 托管前端: {DIST_DIR}", flush=True)
    else:
        print("[web_api] 提示: web/dist 不存在，请先构建前端 (cd web && pnpm build)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()