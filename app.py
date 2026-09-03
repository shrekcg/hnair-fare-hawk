from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Dict, List

import portalocker
import streamlit as st
from backend.fetcher import parse_curl_command
from backend.notifier import send_test_alert
from city_codes import city_options, code_to_city_label, resolve_code

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TASKS_PATH = BASE_DIR / "tasks.json"
LOG_PATH = BASE_DIR / "run_log.txt"
STATE_PATH = BASE_DIR / "runtime_state.json"
HISTORY_PATH = BASE_DIR / "price_history.jsonl"

DEFAULT_CONFIG = {
    "status": "stopped",
    "send_keys": [],
    "polling": {
        "day_min_sec": 90,
        "day_max_sec": 240,
        "night_min_sec": 300,
        "night_max_sec": 600,
    },
    "runtime": {"check_status_interval_sec": 3},
    "monitor_window": {"start": "07:00", "end": "23:00"},
    "credential_auto": {
        "enabled": False,
        "file_path": "",
        "url": "",
        "auth": "",
        "refresh_sec": 120,
    },
    "normal_curl": "",
    "plus_curl": "",
    "proxy": "",
    "sign_refresh": False,
    # 实时查价（海航接口）：enabled 为总开关（被风控时可随时关掉，关后 web 查价与 daemon 均跳过）；
    # min_interval 为全局查询最小间隔（秒），防手动连点触发风控。
    "price_query": {"enabled": True, "min_interval": 8},
    # 通知渠道：飞书（企业自建应用机器人）。app_secret 仅在保存时写入，任何接口不回传。
    "feishu": {"app_id": "", "app_secret": "", "receiver": ""},
    # 多通道通知：urgent_enabled=全局加急开关（默认开）；各渠道凭证为空即未启用
    "notify_channels": {
        "urgent_enabled": True,
        "wecom": {"enabled": False, "corp_id": "", "agent_id": "", "secret": "", "user_id": ""},
        "dingtalk": {"enabled": False, "webhook": "", "secret": ""},
        "bark": {"enabled": False, "device_key": "", "server": "https://api.day.app"},
        "ntfy": {"enabled": False, "topic": "", "server": "https://ntfy.sh"},
    },
}
DEFAULT_TASKS = {"tasks": []}
DEFAULT_STATE = {"price_alerts": {}, "token_alert": {"last_ts": 0}}
NOTIFY_HISTORY_PATH = BASE_DIR / "notification_history.jsonl"


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _write_json(path, default)
        return dict(default)

    with portalocker.Lock(str(path), mode="r+", timeout=5, encoding="utf-8") as f:
        f.seek(0)
        content = f.read().strip()
        if not content:
            f.seek(0)
            f.truncate()
            json.dump(default, f, ensure_ascii=False, indent=2)
            f.flush()
            return dict(default)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            f.seek(0)
            f.truncate()
            json.dump(default, f, ensure_ascii=False, indent=2)
            f.flush()
            return dict(default)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(path), mode="w", timeout=5, encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()


def ensure_data_files() -> None:
    """初始化缺失或空文件，保证前后端双进程通信有稳定落盘结构。"""
    for path, default in [
        (CONFIG_PATH, DEFAULT_CONFIG),
        (TASKS_PATH, DEFAULT_TASKS),
        (STATE_PATH, DEFAULT_STATE),
    ]:
        if not path.exists() or path.stat().st_size == 0:
            _write_json(path, default)

    if not LOG_PATH.exists():
        LOG_PATH.touch()


def read_last_log_lines(path: Path, line_count: int = 20) -> List[str]:
    if not path.exists():
        return []
    with portalocker.Lock(str(path), mode="r", timeout=5, encoding="utf-8") as f:
        lines = f.readlines()
    return [line.rstrip("\n") for line in lines[-line_count:]]


def save_send_keys(raw: str) -> None:
    config = load_config()
    send_keys = [line.strip() for line in raw.splitlines() if line.strip()]
    config["send_keys"] = send_keys
    _write_json(CONFIG_PATH, config)


def _norm_cabins(cabins: Any) -> List[str]:
    """归一化舱位白名单：接受列表或逗号分隔字符串，统一大写、去空、去重。"""
    if isinstance(cabins, str):
        raw_list = [c.strip() for c in cabins.split(",")]
    elif isinstance(cabins, (list, tuple)):
        raw_list = [str(c).strip() for c in cabins]
    else:
        raw_list = []
    out: List[str] = []
    for c in raw_list:
        if c:
            c = c.upper()
            if c not in out:
                out.append(c)
    return out


def add_task(
    task_date: str,
    from_code: str,
    to_code: str,
    target_price: int,
    fare_type: str = "normal",
    task_date_end: str = "",
    min_seats: int = 1,
    cabins: Optional[List[str]] = None,
) -> None:
    """新增监控任务。task_date_end 为空表示只监控单日。

    min_seats：余票达到 N 张才触发（默认 1，即任何有票）；
    cabins：舱位白名单（如 ["B","C"]），命中任一舱位余量之和才算数；空列表=不限舱位。
    """
    # PLUS 会员专享：监控的即是 199 元会员价档位（有票即命中），不适用自定义阈值；
    # 只有普通票价任务才使用用户设置的目标价。
    if fare_type == "plus":
        target_price = 199
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS)
    task_list = tasks.get("tasks", [])
    task_list.append(
        {
            "id": str(uuid.uuid4()),
            "date": task_date,
            "date_end": task_date_end or task_date,
            "from_code": from_code.strip().upper(),
            "to_code": to_code.strip().upper(),
            "target_price": int(target_price),
            "fare_type": "plus" if fare_type == "plus" else "normal",
            "enabled": True,
            "min_seats": max(1, int(min_seats or 1)),
            "cabins": _norm_cabins(cabins),
        }
    )
    tasks["tasks"] = task_list
    _write_json(TASKS_PATH, tasks)


def delete_task(task_id: str) -> None:
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS)
    task_list = tasks.get("tasks", [])
    tasks["tasks"] = [task for task in task_list if task.get("id") != task_id]
    _write_json(TASKS_PATH, tasks)


def delete_tasks(task_ids: List[str]) -> int:
    """按 id 列表删除任务（兼容分组行一次删除多条）。返回删除条数。"""
    ids = {str(i).strip() for i in task_ids if str(i).strip()}
    if not ids:
        return 0
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS)
    task_list = tasks.get("tasks", [])
    keep = [task for task in task_list if str(task.get("id", "")).strip() not in ids]
    removed = len(task_list) - len(keep)
    tasks["tasks"] = keep
    _write_json(TASKS_PATH, tasks)
    return removed


def set_task_enabled(task_id: str, enabled: bool) -> None:
    """启用或停用单个任务（daemon 会过滤 enabled=False 的任务）。"""
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS)
    for task in tasks.get("tasks", []):
        if task.get("id") == task_id:
            task["enabled"] = bool(enabled)
    _write_json(TASKS_PATH, tasks)


def set_tasks_enabled(task_ids: List[str], enabled: bool) -> int:
    """按 id 列表批量启停任务（兼容分组行一次切换多条）。返回受影响条数。"""
    ids = {str(i).strip() for i in task_ids if str(i).strip()}
    if not ids:
        return 0
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS)
    touched = 0
    for task in tasks.get("tasks", []):
        if str(task.get("id", "")).strip() in ids:
            task["enabled"] = bool(enabled)
            touched += 1
    _write_json(TASKS_PATH, tasks)
    return touched


def expand_task_dates(task: Dict[str, Any]) -> List[str]:
    """展开任务监控日期集合（去重排序）。

    - 新模型：task.dates 为日期列表（同一航线多个监控日期合并）；直接展开；
    - 旧模型：无 dates 时按 date ~ date_end 逐日展开（与 daemon.expand_dates 同口径）。
    """
    dates = task.get("dates")
    if isinstance(dates, list) and dates:
        out: List[str] = []
        for d in dates:
            s = str(d).strip()
            try:
                datetime.strptime(s, "%Y-%m-%d")
                out.append(s)
            except ValueError:
                continue
        if out:
            return sorted(set(out))
    start = str(task.get("date", "") or "").strip()
    end = str(task.get("date_end", "") or "").strip() or start
    if not start:
        return []
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return [start] if start else []
    if d1 < d0:
        d0, d1 = d1, d0
    out = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _task_key(from_code: str, to_code: str, flight_no: Any, dep_time: Any, arr_time: Any) -> str:
    """任务分组键：出发|到达|航班号|起飞|到达。

    - 不同航班号/起降时刻各自成任务（穷举，不按航线合并）；
    - 无航班号的旧式条目退化为按航线合并（向后兼容旧添加入口）。
    """
    return "|".join([
        from_code,
        to_code,
        str(flight_no or "").strip().upper(),
        str(dep_time or "").strip(),
        str(arr_time or "").strip(),
    ])


def add_tasks_batch(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从「航线查询」批量创建监控任务（当前只监控 199 元优惠价档）。

    规则：
    - 「一个航班 = 一条任务」：按 出发|到达|航班号|起飞|到达 分组，把一天内不同时间起飞的
      航班全部穷举成独立监控任务（不按航线合并，避免不同时刻的航班被并成一条导致漏提醒）；
    - 同一分组（同航班号且起降时刻相同）的多个可飞日期并入同一条任务；
    - 合并/新建统一为 fare_type=plus、target_price=199（普通票价档暂不监控）；
    - 快照字段（代表航班/时刻/经停/档位）用于列表展示，daemon 会按 task.flight_no 过滤航班，
      只提醒任务对应的那个航班，避免同航线多个任务重复提醒。

    返回 {"created": 新建条数, "merged": 合并条数}。
    """
    def _norm_dates(dates: Any) -> List[str]:
        out: List[str] = []
        for d in (dates or []):
            s = str(d).strip()
            try:
                datetime.strptime(s, "%Y-%m-%d")
                out.append(s)
            except ValueError:
                continue
        return sorted(set(out))

    created = 0
    merged = 0
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS)
    existing = tasks.get("tasks", [])
    idx: Dict[str, int] = {}
    for i, t in enumerate(existing):
        fc, tc = str(t.get("from_code", "")).strip().upper(), str(t.get("to_code", "")).strip().upper()
        if fc and tc:
            idx.setdefault(_task_key(fc, tc, t.get("flight_no"), t.get("dep_time"), t.get("arr_time")), i)

    for item in items or []:
        from_code = str(item.get("from_code", "")).strip().upper()
        to_code = str(item.get("to_code", "")).strip().upper()
        dates = _norm_dates(item.get("dates"))
        if not from_code or not to_code or not dates:
            continue
        product = str(item.get("product", "") or "").strip()
        # 余票/舱位监控条件：min_seats（至少 N 张）+ cabins 舱位白名单
        min_seats = item.get("min_seats")
        cabins = item.get("cabins")
        has_seat_cfg = min_seats is not None or cabins is not None
        norm_min = max(1, int(min_seats or 1)) if min_seats is not None else 1
        norm_cabins = _norm_cabins(cabins) if cabins is not None else []
        snapshot = {
            "flight_no": str(item.get("flight_no", "") or "").strip(),
            "dep_time": str(item.get("dep_time", "") or "").strip(),
            "arr_time": str(item.get("arr_time", "") or "").strip(),
        }
        stop = item.get("stop")
        if isinstance(stop, dict) and (stop.get("kind") or stop.get("stops_detail")):
            snapshot["stop"] = stop

        # 分组键含航班号+起降时刻：一天内不同时间的航班各自成为独立监控任务
        key = _task_key(from_code, to_code, snapshot["flight_no"], snapshot["dep_time"], snapshot["arr_time"])

        if key in idx:
            t = existing[idx[key]]
            merged_dates = _norm_dates(expand_task_dates(t) + dates)
            t["dates"] = merged_dates
            t["date"] = merged_dates[0]
            t["date_end"] = merged_dates[-1]
            # 当前明确只监控 199 元优惠价档：合并时统一收敛（旧普通票价任务不再保留自定义阈值语义）
            t["fare_type"] = "plus"
            t["target_price"] = 199
            old_p = {p for p in str(t.get("product", "") or "").split("/") if p}
            new_p = {p for p in product.split("/") if p}
            t["product"] = "/".join(sorted(old_p | new_p))
            for k in ("flight_no", "dep_time", "arr_time"):
                if not t.get(k) and snapshot.get(k):
                    t[k] = snapshot[k]
            if "stop" not in t and "stop" in snapshot:
                t["stop"] = snapshot["stop"]
            if has_seat_cfg:
                # 同批转的余票/舱位条件一致：显式指定时覆盖（更严格的监控条件生效）
                t["min_seats"] = norm_min
                t["cabins"] = norm_cabins
            merged += 1
        else:
            task = {
                "id": str(uuid.uuid4()),
                "dates": dates,
                "date": dates[0],
                "date_end": dates[-1],
                "from_code": from_code,
                "to_code": to_code,
                "target_price": 199,
                "fare_type": "plus",
                "enabled": True,
                "product": product,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            for k in ("flight_no", "dep_time", "arr_time"):
                if snapshot.get(k):
                    task[k] = snapshot[k]
            if "stop" in snapshot:
                task["stop"] = snapshot["stop"]
            if has_seat_cfg:
                task["min_seats"] = norm_min
                task["cabins"] = norm_cabins
            existing.append(task)
            idx[key] = len(existing) - 1
            created += 1

    tasks["tasks"] = existing
    _write_json(TASKS_PATH, tasks)
    return {"created": created, "merged": merged}


def save_proxy(proxy: str) -> None:
    config = load_config()
    config["proxy"] = str(proxy or "").strip()
    _write_json(CONFIG_PATH, config)


def save_price_query(enabled: bool, min_interval: int) -> None:
    """保存实时查价开关与最小间隔。min_interval 钳制在 2~120 秒。"""
    config = load_config()
    pq = config.setdefault("price_query", dict(DEFAULT_CONFIG["price_query"]))
    if not isinstance(pq, dict):
        pq = dict(DEFAULT_CONFIG["price_query"])
        config["price_query"] = pq
    pq["enabled"] = bool(enabled)
    try:
        interval = max(2, min(120, int(min_interval)))
    except (TypeError, ValueError):
        interval = DEFAULT_CONFIG["price_query"]["min_interval"]
    pq["min_interval"] = interval
    _write_json(CONFIG_PATH, config)


def save_feishu(app_id: str = "", app_secret: str = "", receiver: str = "") -> None:
    """保存飞书通知渠道配置。

    规则：
    - app_id / receiver 传空串表示清空；
    - app_secret 传空串表示“不修改”（secret 从不回传前端，空=未改动）。
    """
    config = load_config()
    feishu = config.setdefault("feishu", dict(DEFAULT_CONFIG["feishu"]))
    if not isinstance(feishu, dict):
        feishu = dict(DEFAULT_CONFIG["feishu"])
        config["feishu"] = feishu
    feishu["app_id"] = str(app_id or "").strip() if app_id is not None else feishu.get("app_id", "")
    feishu["receiver"] = str(receiver or "").strip() if receiver is not None else feishu.get("receiver", "")
    if app_secret:
        feishu["app_secret"] = str(app_secret).strip()
    _write_json(CONFIG_PATH, config)


def save_notify_channels(raw: Dict[str, Any]) -> None:
    """保存多通道通知配置（企业微信/钉钉/Bark/ntfy + 全局加急开关）。

    凭证字段（secret/device_key/topic 等）空串表示“不修改”，避免前端脱敏值覆盖真实凭证。
    """
    config = load_config()
    nc = config.setdefault("notify_channels", dict(DEFAULT_CONFIG["notify_channels"]))
    if not isinstance(nc, dict):
        nc = dict(DEFAULT_CONFIG["notify_channels"])
        config["notify_channels"] = nc

    # 加急开关已从界面移除：重要/阻断告警默认加急，保存时固定开启
    nc["urgent_enabled"] = True

    field_rules = {
        "wecom": ("corp_id", "agent_id", "user_id", "secret"),
        "dingtalk": ("webhook", "secret"),
        "bark": ("device_key", "server"),
        "ntfy": ("topic", "server"),
    }
    for name, fields in field_rules.items():
        ch = nc.setdefault(name, dict(DEFAULT_CONFIG["notify_channels"].get(name, {})))
        if not isinstance(ch, dict):
            ch = dict(DEFAULT_CONFIG["notify_channels"].get(name, {}))
            nc[name] = ch
        incoming = raw.get(name)
        if not isinstance(incoming, dict):
            continue
        if "enabled" in incoming:
            ch["enabled"] = bool(incoming.get("enabled", False))
        for f in fields:
            v = incoming.get(f)
            if v is None:
                continue
            val = str(v).strip()
            if f in ("secret", "device_key", "topic"):
                # secret 类凭证：空串=不修改；其他字段空串=清空
                if val:
                    ch[f] = val
            else:
                ch[f] = val
    _write_json(CONFIG_PATH, config)


def read_notify_history(limit: int = 50) -> List[Dict[str, Any]]:
    """读取最近的通知历史（成功/失败都记录）。"""
    if not NOTIFY_HISTORY_PATH.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(NOTIFY_HISTORY_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def save_curl(raw: str, fare_type: str) -> tuple[bool, str]:
    """校验并保存抓包 cURL 到 config.json（fare_type: plus / normal）。

    返回 (是否成功, 提示信息)。保存后 daemon 下一轮自动生效，无需重启。
    """
    text = str(raw or "").strip()
    key = "plus_curl" if fare_type == "plus" else "normal_curl"
    if not text:
        return False, "内容为空，未保存。"

    parsed = parse_curl_command(text)
    url = str(parsed.get("url", "")).strip()
    if not url:
        return False, "未能从内容中识别到请求地址。请用浏览器开发者工具 Copy as cURL 复制完整命令。"
    if "hnair.com" not in url:
        return False, f"识别到的地址不是海航接口（{url[:80]}），请确认抓的是官网查询请求。"
    if not parsed.get("data"):
        return False, "内容中没有识别到请求体（--data-raw）。请确认复制的是完整的 POST 查询请求。"

    config = load_config()
    config[key] = text
    _write_json(CONFIG_PATH, config)
    return True, f"已保存到 {key}（{len(text)} 字符），daemon 下一轮自动生效。"


def read_price_history(line_count: int = 50) -> List[Dict[str, Any]]:
    """读取最近 N 条价格历史记录（JSONL）。"""
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").strip().splitlines()
    records: List[Dict[str, Any]] = []
    for line in lines[-line_count:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _archive_and_reset(path: Path) -> int:
    """将数据文件轮转为备份（保留旧档），然后重建空文件继续写入，返回归档字节数。

    用于页面的“清除”操作：界面展示清零，真正的历史仍保留在本地备份文件中。
    """
    if not path.exists():
        path.touch()
        return 0
    with portalocker.Lock(str(path), mode="a+", timeout=5, encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return 0
        f.flush()
        backup = str(path) + ".bak"
        try:
            os.replace(str(path), backup)
        except OSError:
            return 0
        path.touch()
        return size


def clear_price_history() -> int:
    """清除价格历史（页面显示清零，旧数据归档为 price_history.jsonl.bak）。"""
    return _archive_and_reset(HISTORY_PATH)


def clear_run_log() -> int:
    """清除运行日志（页面显示清零，旧日志归档为 run_log.txt.bak）。"""
    return _archive_and_reset(LOG_PATH)


def set_status(status: str) -> None:
    config = load_config()
    config["status"] = status
    _write_json(CONFIG_PATH, config)


def load_config() -> Dict[str, Any]:
    """读取配置并补齐默认字段。"""
    config = _read_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not isinstance(config, dict):
        return dict(DEFAULT_CONFIG)

    config.setdefault("status", DEFAULT_CONFIG["status"])
    config.setdefault("send_keys", [])
    config.setdefault("polling", dict(DEFAULT_CONFIG["polling"]))
    config.setdefault("runtime", dict(DEFAULT_CONFIG["runtime"]))
    config.setdefault("monitor_window", dict(DEFAULT_CONFIG["monitor_window"]))
    config.setdefault("credential_auto", dict(DEFAULT_CONFIG["credential_auto"]))
    config.setdefault("normal_curl", DEFAULT_CONFIG["normal_curl"])
    config.setdefault("plus_curl", DEFAULT_CONFIG["plus_curl"])
    config.setdefault("proxy", DEFAULT_CONFIG["proxy"])
    config.setdefault("sign_refresh", DEFAULT_CONFIG["sign_refresh"])
    config.setdefault("price_query", dict(DEFAULT_CONFIG["price_query"]))
    if not isinstance(config["price_query"], dict):
        config["price_query"] = dict(DEFAULT_CONFIG["price_query"])
    config["price_query"].setdefault("enabled", DEFAULT_CONFIG["price_query"]["enabled"])
    config["price_query"].setdefault("min_interval", DEFAULT_CONFIG["price_query"]["min_interval"])

    config.setdefault("feishu", dict(DEFAULT_CONFIG["feishu"]))

    if not isinstance(config["feishu"], dict):
        config["feishu"] = dict(DEFAULT_CONFIG["feishu"])
    else:
        for k, v in DEFAULT_CONFIG["feishu"].items():
            config["feishu"].setdefault(k, v)

    if not isinstance(config["monitor_window"], dict):
        config["monitor_window"] = dict(DEFAULT_CONFIG["monitor_window"])
    else:
        for k, v in DEFAULT_CONFIG["monitor_window"].items():
            config["monitor_window"].setdefault(k, v)

    if not isinstance(config["credential_auto"], dict):
        config["credential_auto"] = dict(DEFAULT_CONFIG["credential_auto"])
    else:
        for k, v in DEFAULT_CONFIG["credential_auto"].items():
            config["credential_auto"].setdefault(k, v)
    return config


def _parse_hhmm(value: str, fallback: str) -> dt_time:
    """将 HH:MM 字符串解析为 time 对象，异常时回退默认值。"""
    raw = str(value or "").strip()
    for candidate in (raw, fallback):
        try:
            return datetime.strptime(candidate, "%H:%M").time()
        except ValueError:
            continue
    return dt_time(hour=7, minute=0)


def save_monitor_window(start_at: dt_time, end_at: dt_time) -> None:
    """保存每日监控时段设置。"""
    config = load_config()
    config["monitor_window"] = {
        "start": start_at.strftime("%H:%M"),
        "end": end_at.strftime("%H:%M"),
    }
    _write_json(CONFIG_PATH, config)


def inject_calendar_cn_locale() -> None:
    """
    将 Streamlit 原生日历弹层中的英文月/星期替换为中文。
    说明：这是前端文本替换策略，随 Streamlit/组件升级可能需微调。
    """
    st.markdown(
        """
<script>
(function() {
  const monthMap = {
    'January':'1月','February':'2月','March':'3月','April':'4月','May':'5月','June':'6月',
    'July':'7月','August':'8月','September':'9月','October':'10月','November':'11月','December':'12月'
  };
  const weekMap = { 'Mo':'一','Tu':'二','We':'三','Th':'四','Fr':'五','Sa':'六','Su':'日' };

  function replaceText(root) {
    if (!root) return;
    const nodes = root.querySelectorAll('div,span,button');
    nodes.forEach((el) => {
      if (!el || el.children.length > 0) return;
      const raw = (el.textContent || '').trim();
      if (!raw) return;
      if (monthMap[raw]) {
        el.textContent = monthMap[raw];
        return;
      }
      if (weekMap[raw]) {
        el.textContent = weekMap[raw];
      }
    });
  }

  function localizeCalendar() {
    document.querySelectorAll('[data-baseweb="calendar"], div[role="dialog"]').forEach((root) => replaceText(root));
  }

  localizeCalendar();
  setInterval(localizeCalendar, 400);
})();
</script>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="海航低价监控", page_icon="✈️", layout="wide")
    ensure_data_files()

    config = load_config()
    tasks = _read_json(TASKS_PATH, DEFAULT_TASKS).get("tasks", [])

    st.title("海南航空机票低价监控系统")
    st.caption("双进程解耦模式：本页面仅负责配置与展示，不直接执行抓价循环。")

    with st.sidebar:
        st.header("微信推送配置（Server酱SendKey）")
        with st.expander("微信绑定教程（小白版）", expanded=False):
            st.markdown(
                "\n".join(
                    [
                        "1. 打开浏览器访问 `https://sct.ftqq.com/login`。",
                        "2. 用微信扫码登录，进入页面后复制 `SendKey`。",
                        "3. 回到本页面，把 `SendKey` 粘贴到下方输入框（多个微信号就一行一个）。",
                        "4. 先点“保存 SendKey”，再点“发送测试消息”，微信收到测试即绑定成功。",
                    ]
                )
            )
        raw_send_keys = st.text_area(
            "微信推送配置（Server酱SendKey，每行一个）",
            value="\n".join(config.get("send_keys", [])),
            height=140,
            help="保存后 daemon 会在下一轮读取到新的 send_keys。",
        )
        if st.button("保存 SendKey"):
            save_send_keys(raw_send_keys)
            st.success("SendKey 已保存。")

        if st.button("发送测试消息"):
            test_keys = [line.strip() for line in raw_send_keys.splitlines() if line.strip()]
            if not test_keys:
                st.error("请先填写至少 1 个 SendKey。")
            else:
                results = send_test_alert(test_keys)
                success_count = sum(1 for ok in results.values() if ok)
                if success_count > 0:
                    st.success(f"测试消息发送完成：成功 {success_count} / 总计 {len(results)}。")
                else:
                    st.error("测试消息发送失败，请检查 SendKey 是否正确、网络是否可用。")

        st.divider()
        st.subheader("每日监控时段")
        window_cfg = config.get("monitor_window", DEFAULT_CONFIG["monitor_window"])
        start_time = _parse_hhmm(
            str(window_cfg.get("start", DEFAULT_CONFIG["monitor_window"]["start"])),
            DEFAULT_CONFIG["monitor_window"]["start"],
        )
        end_time = _parse_hhmm(
            str(window_cfg.get("end", DEFAULT_CONFIG["monitor_window"]["end"])),
            DEFAULT_CONFIG["monitor_window"]["end"],
        )
        c_start, c_end = st.columns(2)
        start_input = c_start.time_input("开始时间", value=start_time, step=60)
        end_input = c_end.time_input("结束时间", value=end_time, step=60)
        st.caption("说明：daemon 仅在该时段内抓价；若跨天请设置为例如 22:00 到 07:00。")
        if st.button("保存监控时段"):
            save_monitor_window(start_input, end_input)
            st.success(
                f"监控时段已保存：{start_input.strftime('%H:%M')} - {end_input.strftime('%H:%M')}"
            )
            st.rerun()

        st.divider()
        with st.expander("票据管理（抓包请求 cURL）", expanded=False):
            st.caption("PLUS 与普通票据互相独立。保存后 daemon 下一轮自动生效，无需重启。")
            plus_ok = bool(str(config.get("plus_curl", "")).strip())
            normal_ok = bool(str(config.get("normal_curl", "")).strip())
            st.caption(
                "当前状态："
                + ("PLUS专享 ✅ 已配置" if plus_ok else "PLUS专享 ❌ 未配置")
                + " ｜ "
                + ("普通票价 ✅ 已配置" if normal_ok else "普通票价 ❌ 未配置")
            )
            st.markdown("**PLUS 专享票据**（对应任务页的「PLUS专享」票价）")
            plus_raw = st.text_area(
                "PLUS 票据 cURL",
                value=str(config.get("plus_curl", "")),
                height=120,
                key="curl_plus",
                label_visibility="collapsed",
            )
            if st.button("保存 PLUS 票据", key="save_plus_btn"):
                ok, msg = save_curl(plus_raw, "plus")
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            st.markdown("**普通票价票据**（对应任务页的「普通票价」票价）")
            normal_raw = st.text_area(
                "普通票据 cURL",
                value=str(config.get("normal_curl", "")),
                height=120,
                key="curl_normal",
                label_visibility="collapsed",
            )
            if st.button("保存普通票据", key="save_normal_btn"):
                ok, msg = save_curl(normal_raw, "normal")
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            st.caption(
                "抓票方法：官网查询页按 F12 → Network → 筛选 airLowFareSearch → "
                "右键请求 → Copy → Copy as cURL (bash)。"
                "PLUS 通道抓 ffl/airLowFareSearch，普通票价抓 airLowFareSearch，两份要各自抓。"
                "若粘贴后报“无法识别”，请改用 Copy as cURL (cmd) 格式再试。"
            )

        st.divider()
        with st.expander("高级设置", expanded=False):
            proxy_value = st.text_input(
                "HTTP(S) 代理（可选）",
                value=str(config.get("proxy", "")),
                placeholder="例如 http://127.0.0.1:7890",
                help="所有海航抓价请求都会走该代理；留空表示直连。",
            )
            if st.button("保存代理"):
                save_proxy(proxy_value)
                st.success("代理设置已保存。")
            st.caption(
                "安全提示：远端自动凭证拉取（credential_auto.url）默认关闭，"
                "仅在完全信任的配置下启用，避免把凭证发送到恶意地址。"
            )

        st.divider()
        st.metric("当前状态", config.get("status", "stopped"))

    st.subheader("添加监控任务")
    # 不使用 form，避免输入框回车时触发“添加任务”。
    col1, col2, col3 = st.columns(3)
    task_date = col1.date_input("日期（点击日历选择）", value=None, format="YYYY-MM-DD", key="task_date_native")
    from_city = col2.text_input("出发地（城市/机场/三字码）", value="", placeholder="例如：深圳、深圳宝安、SZX")
    to_city = col3.text_input("到达地（城市/机场/三字码）", value="", placeholder="例如：乌鲁木齐、URC")
    inject_calendar_cn_locale()

    st.caption("可输入示例：深圳、深圳宝安、SZX、乌鲁木齐、URC。")
    st.caption("常用机场： " + " | ".join(city_options()[:10]))
    c_price, c_type = st.columns([2, 1])
    target_price = c_price.number_input("提醒阈值(元)", min_value=1, value=199, step=1)
    fare_type_label = c_type.selectbox("票价类型", options=["普通票价", "PLUS专享"], index=0)
    fare_type = "plus" if fare_type_label == "PLUS专享" else "normal"

    if st.button("添加任务", key="add_task_btn"):
        from_code, from_tips = resolve_code(from_city)
        to_code, to_tips = resolve_code(to_city)

        if task_date is None:
            st.error("请先选择日期。")
        elif not from_city.strip():
            st.error("请先填写出发地。")
        elif not to_city.strip():
            st.error("请先填写到达地。")
        elif not from_code:
            st.error("出发地无法唯一匹配，请换个写法或直接输入三字码。")
            if from_tips:
                st.info("出发地候选：" + " | ".join(from_tips[:8]))
        elif not to_code:
            st.error("到达地无法唯一匹配，请换个写法或直接输入三字码。")
            if to_tips:
                st.info("到达地候选：" + " | ".join(to_tips[:8]))
        elif fare_type == "plus" and not str(config.get("plus_curl", "")).strip():
            st.error("你选择了 PLUS专享，但尚未配置本人本地查询请求。请按极简上手指南完成配置。")
        else:
            add_task(
                task_date=task_date.strftime("%Y-%m-%d"),
                from_code=from_code,
                to_code=to_code,
                target_price=int(target_price),
                fare_type=fare_type,
            )
            st.success(
                f"任务已添加：{task_date.strftime('%Y-%m-%d')} | "
                f"{code_to_city_label(from_code)} -> {code_to_city_label(to_code)} | "
                f"{fare_type_label} | <= {int(target_price)}"
            )
            st.rerun()

    st.subheader("任务列表")
    if tasks:
        display_tasks = []
        for idx, task in enumerate(tasks, start=1):
            row = {
                "序号": idx,
                "日期": task.get("date", ""),
                "出发地": code_to_city_label(str(task.get("from_code", ""))),
                "到达地": code_to_city_label(str(task.get("to_code", ""))),
                "票价类型": "PLUS专享" if str(task.get("fare_type", "normal")) == "plus" else "普通票价",
                "价格阈值≤": task.get("target_price", 199),
                "启用": task.get("enabled", True),
            }
            display_tasks.append(row)
        st.dataframe(display_tasks, width=800, hide_index=True)
    else:
        st.info("暂无任务，请先添加。")

    if tasks:
        selected_index = st.number_input("选择要操作的任务序号", min_value=1, max_value=len(tasks), value=1, step=1)
        selected_task = tasks[selected_index - 1]
        state_label = "启用" if selected_task.get("enabled", True) else "停用"
        st.caption(
            "当前选中："
            + f"第 {selected_index} 条 | {selected_task.get('date', '')} | "
            + f"{code_to_city_label(str(selected_task.get('from_code', '')))} -> "
            + f"{code_to_city_label(str(selected_task.get('to_code', '')))} | "
            + ("PLUS专享" if str(selected_task.get("fare_type", "normal")) == "plus" else "普通票价")
            + f" | <= {selected_task.get('target_price', 199)}"
            + f" | 当前{state_label}"
        )
        c_ena, c_dis, c_del = st.columns(3)
        if c_ena.button("启用该任务"):
            set_task_enabled(selected_task["id"], True)
            st.success("任务已启用。")
            st.rerun()
        if c_dis.button("停用该任务"):
            set_task_enabled(selected_task["id"], False)
            st.warning("任务已停用（daemon 不再查询）。")
            st.rerun()
        if c_del.button("删除选中任务"):
            delete_task(selected_task["id"])
            st.success("任务已删除。")
            st.rerun()

    st.subheader("监控控制")
    c1, c2 = st.columns(2)
    if c1.button("启动监控"):
        set_status("running")
        st.success("已写入 running 状态。")
        st.rerun()
    if c2.button("停止监控"):
        set_status("stopped")
        st.warning("已写入 stopped 状态。")
        st.rerun()

    st.subheader("价格历史（最近 50 条）")
    records = read_price_history(50)
    if records:
        display_rows = []
        for r in records:
            display_rows.append(
                {
                    "时间": r.get("ts", ""),
                    "日期": r.get("date", ""),
                    "航线": f"{code_to_city_label(str(r.get('from', '')))} -> "
                    f"{code_to_city_label(str(r.get('to', '')))}",
                    "类型": "PLUS专享" if r.get("fare_type") == "plus" else "普通票价",
                    "航班": r.get("flight", ""),
                    "票价(元)": r.get("price", ""),
                }
            )
        st.dataframe(display_rows, width=800, hide_index=True)
    else:
        st.info("暂无价格历史（daemon 抓到价格后才会写入）。")

    st.subheader("运行日志（最近 20 行）")
    if st.button("刷新日志"):
        st.rerun()

    logs = read_last_log_lines(LOG_PATH, line_count=20)
    if logs:
        st.code("\n".join(logs), language="text")
    else:
        st.info("暂无日志输出。")


if __name__ == "__main__":
    main()
