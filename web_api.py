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
    code_to_city_label,
    delete_task,
    ensure_data_files,
    load_config,
    read_last_log_lines,
    read_price_history,
    save_curl,
    save_feishu,
    save_monitor_window,
    save_proxy,
    save_send_keys,
    set_status,
    set_task_enabled,
)
from backend.notifier import send_test_alert, test_feishu  # noqa: E402
from city_codes import city_options, resolve_code  # noqa: E402

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


def _ticket_summary(curl: str) -> Dict[str, Any]:
    if not curl:
        return {"configured": False, "url": "", "length": 0}
    # 只展示域名+路径，绝不暴露 query 里的 token/签名等敏感参数
    start = curl.find("http")
    url = curl[start:].split()[0] if start >= 0 else ""
    if url:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        url = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return {"configured": True, "url": url, "length": len(curl)}


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
        "plus_ticket": _ticket_summary(plus_curl),
        "normal_ticket": _ticket_summary(normal_curl),
        "polling": config.get("polling", DEFAULT_CONFIG.get("polling", {})),
        # 飞书渠道：AppID 掩码展示（前4后4），AppSecret 永不回传，receiver 不敏感可展示
        "feishu": {
            "configured": bool(feishu_app_id and str(feishu.get("app_secret", "") or "").strip()),  # noqa: E501
            "app_id": _mask_key(feishu_app_id),
            "has_secret": bool(str(feishu.get("app_secret", "") or "").strip()),
            "receiver": feishu_receiver,
        },
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
        "tasks": [
            {
                "id": t.get("id", ""),
                "date": t.get("date", ""),
                "date_end": t.get("date_end") or t.get("date", ""),
                "from_code": t.get("from_code", ""),
                "from_city": code_to_city_label(str(t.get("from_code", ""))),
                "to_code": t.get("to_code", ""),
                "to_city": code_to_city_label(str(t.get("to_code", ""))),
                "target_price": t.get("target_price", 199),
                "fare_type": t.get("fare_type", "normal"),
                "enabled": bool(t.get("enabled", True)),
            }
            for t in tasks
        ],
        "history": [
            {
                "ts": r.get("ts", ""),
                "date": r.get("date", ""),
                "from": code_to_city_label(str(r.get("from", ""))),
                "to": code_to_city_label(str(r.get("to", ""))),
                "fare_type": r.get("fare_type", "normal"),
                "flight": r.get("flight", ""),
                "price": r.get("price", ""),
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
        try:
            if path == "/api/state":
                _send_json(self, 200, _build_state())
            elif path == "/api/city/options":
                _send_json(self, 200, {"options": city_options()})
            elif path == "/api/city/resolve":
                q = _get_qs(parsed.query, "q")
                code, tips = resolve_code(q)
                _send_json(self, 200, {"code": code, "tips": tips[:8]})
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
            elif path == "/api/tasks/enabled":
                task_id = str(data.get("id", "")).strip()
                enabled = bool(data.get("enabled", True))
                set_task_enabled(task_id, enabled)
                _send_json(self, 200, {"ok": True})
            elif path == "/api/tasks/delete":
                task_id = str(data.get("id", "")).strip()
                delete_task(task_id)
                _send_json(self, 200, {"ok": True})
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
            elif path == "/api/feishu":
                self._post_feishu(data)
            elif path == "/api/feishu/test":
                config = load_config()
                feishu = config.get("feishu") or {}
                app_id = str(data.get("app_id", "")).strip() or str(feishu.get("app_id", "") or "").strip()
                app_secret = str(data.get("app_secret", "")).strip() or str(feishu.get("app_secret", "") or "").strip()
                receiver = str(data.get("receiver", "")).strip() or str(feishu.get("receiver", "") or "").strip()
                if not (app_id and app_secret and receiver):
                    _send_json(self, 400, {"ok": False, "error": "飞书配置不完整，请先填写 App ID / App Secret / 接收人。"})
                    return
                ok, err = test_feishu(app_id, app_secret, receiver)
                if ok:
                    _send_json(self, 200, {"ok": True, "success": True, "message": "测试消息已发送，请到飞书查看。"})
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
        if fare_type == "plus" and not str(config.get("plus_curl", "") or "").strip():
            _send_json(self, 400, {"ok": False, "error": "你选择了 PLUS专享，但尚未配置 PLUS 票据，请先到「票据管理」粘贴抓包 cURL。"})
            return

        add_task(
            task_date=task_date,
            task_date_end=task_date_end,
            from_code=from_code,
            to_code=to_code,
            target_price=target_price,
            fare_type=fare_type,
        )
        label = "PLUS专享" if fare_type == "plus" else "普通票价"
        date_label = task_date if not task_date_end else f"{task_date} ~ {task_date_end}"
        _send_json(
            self,
            200,
            {
                "ok": True,
                "message": f"任务已添加：{date_label} | {code_to_city_label(from_code)} -> {code_to_city_label(to_code)} | {label} | <= {target_price}",
            },
        )

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


def _get_qs(query: str, key: str, default: str = "") -> str:
    for part in query.split("&"):
        if part.startswith(key + "="):
            from urllib.parse import unquote

            return unquote(part[len(key) + 1 :])
    return default


def _parse_time(value: str, fallback: str):
    from datetime import datetime as _dt

    try:
        return _dt.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        return _dt.strptime(fallback, "%H:%M").time()


def main() -> None:
    ensure_data_files()
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