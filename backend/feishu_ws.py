"""飞书长连接事件接收（WebSocket 长连接模式）。

- 事件订阅使用长连接而非 Webhook：POST /callback/ws/endpoint 换取 wss 地址，
  建立 WebSocket 全双工通道后由开放平台主动推送事件（无需公网地址/内网穿透）；
- 协议（protobuf 帧、ping/pong、断线重连、消息排重）由官方 lark-oapi SDK 的
  ws.Client 承载，本模块只做业务：接收 card.action.trigger → 更新卡片状态 +
  把回执写入 runtime_state.json；
- 状态机（供控制台展示）：未配置 / 连接中 / 已连接 / 重连 / 失败（含原因）。

注意：
- 长连接模式仅支持企业自建应用；
- 接收到事件后需在 3 秒内处理完，否则开放平台会超时重推；
- 同一应用最多 50 个连接，集群模式（随机一个客户端收到）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

import portalocker
import requests

from backend.channels import build_confirm_resolved_card, update_feishu_card

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "runtime_state.json")

# 状态机取值：not_configured / connecting / connected / reconnecting / failed / stopped
_status: Dict[str, Any] = {
    "state": "not_configured",
    "message": "未配置飞书，或关闭了长连接接收",
    "endpoint_ok": False,
    "endpoint_error": "",
    "last_event_ts": 0,
    "event_count": 0,
    "last_error": "",
    "started_at": "",
}
_status_lock = threading.Lock()

# 单例服务：web_api 进程内只允许一个接收线程
_thread: Optional[threading.Thread] = None
_client_ref: Any = None
_event_loop_ref: Any = None


# ==================== 状态 ====================


def _set_status(**kw: Any) -> None:
    with _status_lock:
        _status.update(kw)


def status() -> Dict[str, Any]:
    """当前长连接状态（供 _sanitize_config / _build_state 展示）。"""
    with _status_lock:
        st = dict(_status)
    # 派生状态：SDK 初次连接成功不回调 on_reconnected，用 WebSocket 连接对象是否存在判断
    if st["state"] in ("connecting", "reconnecting") and _client_ref is not None:
        try:
            if getattr(_client_ref, "_conn", None) is not None:
                st = dict(st)
                st["state"] = "connected"
                st["message"] = "已连接（长连接接收事件中）"
                st["last_error"] = ""
        except Exception:  # noqa: BLE001
            pass
    return st


def _state_label(state: str) -> str:
    return {
        "not_configured": "未配置",
        "connecting": "连接中",
        "connected": "已连接",
        "reconnecting": "重连中",
        "failed": "失败",
        "stopped": "已停止",
    }.get(state, state)


def web_status() -> Dict[str, Any]:
    """面向前端的状态视图。"""
    st = status()
    return {
        "state": st["state"],
        "label": _state_label(st["state"]),
        "message": st["message"],
        "endpoint_ok": st["endpoint_ok"],
        "endpoint_error": st["endpoint_error"],
        "last_event_ts": st["last_event_ts"] or "",
        "event_count": st["event_count"],
        "last_error": st["last_error"],
    }


# ==================== 读配置 / 写状态文件 ====================


def _load_feishu_cfg() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f) or {}
    except (OSError, ValueError):
        config = {}
    feishu = config.get("feishu") or {}
    if not isinstance(feishu, dict):
        feishu = {}
    return {
        "app_id": str(feishu.get("app_id", "") or "").strip(),
        "app_secret": str(feishu.get("app_secret", "") or "").strip(),
        "receiver": str(feishu.get("receiver", "") or "").strip(),
    }


def is_configured() -> bool:
    cfg = _load_feishu_cfg()
    return bool(cfg["app_id"] and cfg["app_secret"] and cfg["receiver"])


def _read_state(state_path: Optional[str] = None) -> Dict[str, Any]:
    path = state_path or STATE_PATH
    try:
        with portalocker.Lock(path, mode="r+", timeout=5, encoding="utf-8") as f:
            f.seek(0)
            text = f.read().strip()
            if not text:
                return {}
            return json.loads(text)
    except Exception:
        return {}


def _write_state(data: Dict[str, Any], state_path: Optional[str] = None) -> None:
    path = state_path or STATE_PATH
    try:
        with portalocker.Lock(path, mode="w", timeout=5, encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
    except Exception:
        pass


MAX_CARDS = 100      # 卡片原文最多保留条数（按发送顺序淘汰最旧）
MAX_CONFIRMS = 200   # 回执记录最多保留条数


def record_sent_card(message_id: str, card: Dict[str, Any], state_path: Optional[str] = None) -> None:
    """发送交互卡片后把原文存入 runtime_state，供回调时替换为“已确认/已忽略”状态。"""
    if not message_id:
        return
    path = state_path or STATE_PATH
    try:
        data = _read_state(path)
        cards = data.setdefault("cards", {})
        if not isinstance(cards, dict):
            cards = {}
            data["cards"] = cards
        cards[message_id] = {"card": card, "ts": datetime.now().isoformat(timespec="seconds")}
        # 淘汰最旧（按插入顺序）
        while len(cards) > MAX_CARDS:
            cards.pop(next(iter(cards)), None)
        _write_state(data, path)
    except Exception:
        pass


def record_confirmation(entry: Dict[str, Any], state_path: Optional[str] = None) -> None:
    """把卡片回执追加进 runtime_state.card_confirmations。"""
    path = state_path or STATE_PATH
    try:
        data = _read_state(path)
        confirms = data.setdefault("card_confirmations", [])
        if not isinstance(confirms, list):
            confirms = []
            data["card_confirmations"] = confirms
        confirms.append(entry)
        del confirms[:-MAX_CONFIRMS]
        _write_state(data, path)
    except Exception:
        pass


def read_confirmations(state_path: Optional[str] = None) -> list:
    path = state_path or STATE_PATH
    try:
        data = _read_state(path)
        confirms = data.get("card_confirmations")
        return list(confirms) if isinstance(confirms, list) else []
    except Exception:
        return []


# ==================== endpoint 申请（也用于状态诊断） ====================


def get_ws_endpoint(app_id: str, app_secret: str) -> Optional[str]:
    """向开放平台申请长连接 wss 地址。

    返回 URL；失败抛出带 code/msg 的 RuntimeError。
    """
    resp = requests.post(
        "https://open.feishu.cn/callback/ws/endpoint",
        headers={"Content-Type": "application/json", "locale": "zh", "User-Agent": "hna-monitor/1.0"},
        json={"AppID": app_id, "AppSecret": app_secret},
        timeout=10,
    )
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"endpoint 接口返回异常（HTTP {resp.status_code}），请确认应用已开通「事件订阅 - 长连接模式」。")
    if data.get("code") != 0:
        raise RuntimeError(f"申请长连接失败 code={data.get('code')}: {data.get('msg', '')}（请确认应用已添加事件订阅并发布版本）")
    url = (data.get("data") or {}).get("URL") or ""
    if not url:
        raise RuntimeError("endpoint 接口未返回 URL")
    return url


# ==================== 卡片回调处理 ====================


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def process_card_action(
    operator_open_id: str,
    message_id: str,
    action_value: Dict[str, Any],
    token: str,
    state_path: Optional[str] = None,
) -> Dict[str, Any]:
    """处理一次 card.action.trigger：更新卡片状态 + 记录回执。

    返回给开放平台的响应（toast 等）。
    """
    path = state_path or STATE_PATH
    cfg = _load_feishu_cfg()
    action_value = action_value or {}
    callback_key = str(action_value.get("callback_key", "") or "")
    action = str(action_value.get("action", "") or "")
    confirm = str(action_value.get("confirm", "0") or "0")
    confirmed = confirm == "1"

    # 1. 取出卡片原文，替换按钮为状态文本并回写
    update_ok, update_error = False, ""
    data = _read_state()
    cards = data.get("cards") if isinstance(data.get("cards"), dict) else {}
    origin = cards.get(message_id)
    if origin and isinstance(origin.get("card"), dict) and cfg["app_id"] and cfg["app_secret"]:
        new_card = build_confirm_resolved_card(origin["card"], confirmed)
        try:
            update_ok, update_error = update_feishu_card(cfg["app_id"], cfg["app_secret"], message_id, new_card)
        except Exception as exc:  # noqa: BLE001 网络/接口异常不影响回执落盘
            update_error = f"更新卡片异常：{exc}"

    # 2. 记录回执（成功/失败都记录，供前端「通知」页展示）
    entry = {
        "ts": _now_iso(),
        "message_id": message_id,
        "open_id": operator_open_id,
        "callback_key": callback_key,
        "action": action,
        "confirm": confirm,
        "confirmed": confirmed,
        "card_updated": update_ok,
        "error": (update_error or "")[:200],
        "token": token,
    }
    record_confirmation(entry, path)

    # 3. 更新长连接状态里的最近事件时间（即使卡片未找到也算收到事件）
    _set_status(last_event_ts=_now_iso())
    with _status_lock:
        _status["event_count"] = int(_status.get("event_count", 0)) + 1

    if not update_ok:
        return {"toast": {"type": "info", "content": "已记录反馈（卡片更新失败）"}}
    return {"toast": {"type": "success", "content": "已确认 ✅" if confirmed else "已忽略"}}


# ==================== 长连接服务线程 ====================

# 兼容 HTTP 回调入口（/api/feishu/callback）传入的原始 v2 事件体
def process_card_action_payload(payload: Dict[str, Any], state_path: Optional[str] = None) -> Dict[str, Any]:
    """从 v2 事件体（Webhook/长连接原始 JSON）提取字段并处理卡片回调。

    若请求是 URL 验证（challenge），原样返回 challenge。
    """
    if isinstance(payload.get("challenge"), str) and "token" in payload:
        return {"challenge": payload["challenge"]}
    event = payload.get("event") or {}
    operator = event.get("operator") or {}
    context = event.get("context") or {}
    action = event.get("action") or {}
    value = action.get("value")
    if not isinstance(value, dict):
        value = {}
    operator_open_id = str((operator.get("open_id") or operator.get("user_id") or "") or "")
    message_id = str(context.get("open_message_id") or "")
    token = str(event.get("token") or "")
    if not message_id:
        return {"toast": {"type": "info", "content": "缺少消息 id，无法处理"}}
    return process_card_action(operator_open_id, message_id, value, token, state_path=state_path)


def _ws_worker(app_id: str, app_secret: str, state_path: str) -> None:
    """后台线程：建立/维持长连接。lark-oapi 在本线程内导入，保证其全局事件循环绑定当前线程。"""
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _set_status(state="connecting", message="正在申请长连接地址并建立 WebSocket…", endpoint_ok=False, endpoint_error="", last_error="")
    try:
        url = get_ws_endpoint(app_id, app_secret)
        _set_status(endpoint_ok=True, endpoint_error="")
    except Exception as exc:  # noqa: BLE001
        _set_status(state="failed", message="申请长连接地址失败", endpoint_ok=False, endpoint_error=str(exc), last_error=str(exc))
        return

    # 事件分发：只关心卡片按钮回调（card.action.trigger）
    import lark_oapi  # noqa: PLC0415,F401 先加载顶层包，避免 api 模块星号导入遮蔽 event 属性
    from lark_oapi.core.enum import LogLevel  # noqa: PLC0415
    from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger  # noqa: PLC0415
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler  # noqa: PLC0415
    from lark_oapi.ws.client import Client as WsClient  # noqa: PLC0415

    def on_card_action(data: P2CardActionTrigger) -> Any:
        try:
            event = data.event
            operator = event.operator
            context = event.context
            action = event.action
            value = action.value if isinstance(action.value, dict) else {}
            open_id = str((operator.open_id if operator else "") or "")
            message_id = str(context.open_message_id if context else "")
            token = str(event.token if event else "")
            return process_card_action(open_id, message_id, value, token, state_path=state_path)
        except Exception as exc:  # noqa: BLE001 回调内异常会触发开放平台重推，兜底不抛
            return {"toast": {"type": "info", "content": "处理失败，请稍后重试"}}

    dispatcher = EventDispatcherHandler.builder("", "").register_p2_card_action_trigger(on_card_action).build()

    client = WsClient(
        app_id,
        app_secret,
        event_handler=dispatcher,
        log_level=LogLevel.ERROR,
        auto_reconnect=True,
    )
    client.on_reconnecting = lambda: _set_status(state="reconnecting", message="连接断开，正在重连…", last_error="连接断开")
    client.on_reconnected = lambda: _set_status(state="connected", message="已连接（长连接接收事件中）", last_error="")

    global _client_ref, _event_loop_ref
    _client_ref = client
    _event_loop_ref = loop
    _set_status(state="connecting", message="正在建立 WebSocket 连接…", started_at=_now_iso())
    try:
        client.start()  # 阻塞直到连接失败后重连/或连接被关闭
    except Exception as exc:  # noqa: BLE001
        _set_status(state="failed", message="长连接异常退出", last_error=str(exc))
    finally:
        _set_status(state="stopped", message="长连接已停止（服务重启后自动重新连接）")


def start_if_configured(state_path: Optional[str] = None) -> Dict[str, Any]:
    """web_api 启动时调用：飞书配置完整则拉起长连接接收线程。"""
    global _thread
    if not is_configured():
        _set_status(state="not_configured", message="未配置飞书（App ID / App Secret / 接收人），长连接未启动")
        return status()
    if _thread is not None and _thread.is_alive():
        return status()
    cfg = _load_feishu_cfg()
    _set_status(state="connecting", message="正在申请长连接地址并建立 WebSocket…", started_at=_now_iso())
    _thread = threading.Thread(
        target=_ws_worker,
        args=(cfg["app_id"], cfg["app_secret"], state_path or STATE_PATH),
        name="feishu-ws",
        daemon=True,
    )
    _thread.start()
    return status()


def stop() -> None:
    """停止长连接（尽力而为）：关闭 WebSocket 并让线程退出，进程退出时也会自然回收。"""
    global _client_ref
    client = _client_ref
    _client_ref = None
    if client is None:
        return
    try:
        if getattr(client, "_auto_reconnect", True):
            client._auto_reconnect = False
        conn = getattr(client, "_conn", None)
        loop = _event_loop_ref
        if conn is not None and loop is not None:
            import asyncio  # noqa: PLC0415

            fut = asyncio.run_coroutine_threadsafe(conn.close(), loop)
            try:
                fut.result(timeout=3)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    _set_status(state="stopped", message="长连接已停止")