"""后端常驻进程入口。"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List

import portalocker

from backend.fetcher import TokenExpiredError, fetch_price_status
from backend.notifier import (
    send_feishu_price_alert,
    send_feishu_token_alert,
    send_price_alert,
    send_token_expired_alert,
)
from backend.scheduler import get_next_interval_seconds
from backend.state import AlertStateManager
from city_codes import code_to_city_only

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TASKS_PATH = os.path.join(BASE_DIR, "tasks.json")
LOG_PATH = os.path.join(BASE_DIR, "run_log.txt")
STATE_PATH = os.path.join(BASE_DIR, "runtime_state.json")
HISTORY_PATH = os.path.join(BASE_DIR, "price_history.jsonl")

MAX_LOG_BYTES = 1 * 1024 * 1024  # 单份运行日志最多 1MB，超出轮转
MAX_HISTORY_BYTES = 5 * 1024 * 1024  # 价格历史最多 5MB，超出轮转保留 1 份
TASK_STAGGER_MIN = 10  # 任务间错峰（秒）
TASK_STAGGER_MAX = 30


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
    "alert_cooldown": {
        "hours": 1,  # 价格提醒冷却时间（小时），默认1小时
    },
}

DEFAULT_TASKS = {"tasks": []}


def ensure_file(path: str, default_data: Any) -> None:
    """若文件不存在则创建默认内容。"""
    if os.path.exists(path):
        return

    if path.endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)
    else:
        with open(path, "w", encoding="utf-8"):
            pass


def read_json(path: str, default_data: Any) -> Any:
    """带锁读取 JSON；读取失败时返回默认值。"""
    ensure_file(path, default_data)

    try:
        with portalocker.Lock(path, mode="a+", timeout=5, encoding="utf-8") as f:
            f.seek(0)
            text = f.read().strip()
            if not text:
                return default_data
            return json.loads(text)
    except Exception:
        return default_data


def append_log(message: str) -> None:
    """追加写运行日志（超 1MB 自动轮转，保留 1 份旧档）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"

    ensure_file(LOG_PATH, "")
    # 单写者（daemon）顺序执行：写前检查体积，超出则轮转。
    try:
        if os.path.getsize(LOG_PATH) >= MAX_LOG_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass

    with portalocker.Lock(LOG_PATH, mode="a", timeout=5, encoding="utf-8") as f:
        f.write(line)
        f.flush()


def append_history(record: Dict[str, Any]) -> None:
    """追加写价格历史（JSONL），超 5MB 轮转保留 1 份旧档。"""
    try:
        if os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) >= MAX_HISTORY_BYTES:
            os.replace(HISTORY_PATH, HISTORY_PATH + ".1")
    except OSError:
        pass

    line = json.dumps(record, ensure_ascii=False) + "\n"
    with portalocker.Lock(HISTORY_PATH, mode="a", timeout=5, encoding="utf-8") as f:
        f.write(line)
        f.flush()


def load_config() -> Dict[str, Any]:
    """读取配置并兜底关键字段。"""
    config = read_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not isinstance(config, dict):
        return DEFAULT_CONFIG.copy()

    config.setdefault("status", "stopped")
    config.setdefault("send_keys", [])
    config.setdefault("polling", DEFAULT_CONFIG["polling"])
    config.setdefault("runtime", DEFAULT_CONFIG["runtime"])
    config.setdefault("monitor_window", DEFAULT_CONFIG["monitor_window"])

    if not isinstance(config["send_keys"], list):
        config["send_keys"] = []

    if not isinstance(config["monitor_window"], dict):
        config["monitor_window"] = dict(DEFAULT_CONFIG["monitor_window"])
    else:
        config["monitor_window"].setdefault("start", DEFAULT_CONFIG["monitor_window"]["start"])
        config["monitor_window"].setdefault("end", DEFAULT_CONFIG["monitor_window"]["end"])
    return config


def load_tasks() -> List[Dict[str, Any]]:
    """读取任务列表。"""
    data = read_json(TASKS_PATH, DEFAULT_TASKS)
    if not isinstance(data, dict):
        return []

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        return []

    result: List[Dict[str, Any]] = []
    for task in tasks:
        if isinstance(task, dict):
            result.append(task)
    return result


def expand_dates(task: Dict[str, Any]) -> List[str]:
    """展开任务监控日期。

    - 单日任务（无 date_end 或 date_end == date）返回 [date]；
    - 区间任务返回 date ~ date_end 之间的每一天（含首尾）；
    - 日期无效时原样返回 [date]，交给下游按单日处理。
    """
    start = str(task.get("date", "") or "").strip()
    end = str(task.get("date_end", "") or "").strip() or start
    if not start:
        return []
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return [start]
    if d1 < d0:
        d0, d1 = d1, d0
    dates: List[str] = []
    cur = d0
    while cur <= d1:
        dates.append(cur.isoformat())
        cur += timedelta(days=1)
    return dates


def sleep_with_status_check(seconds: int, check_interval: int) -> None:
    """分段休眠，便于快速响应 stop。"""
    elapsed = 0
    while elapsed < seconds:
        step = min(max(1, check_interval), seconds - elapsed)
        time.sleep(step)
        elapsed += step

        cfg = load_config()
        if str(cfg.get("status", "stopped")).lower() != "running":
            append_log("检测到状态已停止，提前结束等待。")
            return


def _build_fingerprint(task: Dict[str, Any], flight: str, price: int) -> str:
    """构建去重指纹。"""
    return "|".join(
        [
            str(task.get("id", "")),
            str(task.get("date", "")),
            str(task.get("from_code", "")),
            str(task.get("to_code", "")),
            str(task.get("fare_type", "normal")),
            str(flight),
            str(price),
        ]
    )


def _parse_hhmm(value: str, fallback: str) -> dt_time:
    """解析 HH:MM，异常时回退 fallback。"""
    for candidate in (str(value or "").strip(), fallback):
        try:
            return datetime.strptime(candidate, "%H:%M").time()
        except ValueError:
            continue
    return dt_time(hour=7, minute=0)


def _is_in_monitor_window(now_time: dt_time, start_hhmm: str, end_hhmm: str) -> bool:
    """
    判断当前时刻是否位于监控时段。
    - 同日时段：start <= now < end
    - 跨天时段：now >= start 或 now < end
    - start == end：按全天可监控处理，避免误配置导致永久停机
    """
    start_t = _parse_hhmm(start_hhmm, DEFAULT_CONFIG["monitor_window"]["start"])
    end_t = _parse_hhmm(end_hhmm, DEFAULT_CONFIG["monitor_window"]["end"])

    if start_t == end_t:
        return True
    if start_t < end_t:
        return start_t <= now_time < end_t
    return now_time >= start_t or now_time < end_t


def run_one_round(state_mgr: AlertStateManager) -> bool:
    """
    执行一轮抓价与提醒。

    返回：本轮是否取到至少一条价格（用于自适应轮询频率）。
    """
    config = load_config()
    send_keys = config.get("send_keys", [])
    feishu_cfg = config.get("feishu") or {}
    all_tasks = load_tasks()
    tasks = [task for task in all_tasks if task.get("enabled", True)]
    if not tasks:
        append_log("当前没有监控任务。")
        return False

    append_log(f"开始执行新一轮监控，共 {len(all_tasks)} 条任务（启用 {len(tasks)} 条）。")
    state_mgr.prune_old()

    saw_prices = False

    for index, task in enumerate(tasks):
        task_id = str(task.get("id", ""))

        from_code = str(task.get("from_code", "")).strip().upper()
        to_code = str(task.get("to_code", "")).strip().upper()
        fare_type = str(task.get("fare_type", "normal")).strip().lower()
        if fare_type not in ("normal", "plus"):
            fare_type = "normal"
        fare_label = "PLUS专享" if fare_type == "plus" else "普通票价"

        try:
            target_price = int(task.get("target_price", 199))
        except (TypeError, ValueError):
            target_price = 199

        dates = expand_dates(task)
        if not dates:
            append_log(f"任务缺少日期，已跳过：{task}")
            continue
        if not (from_code and to_code):
            append_log(f"任务参数不完整，已跳过：{task}")
            continue
        if len(dates) > 15:
            append_log(f"任务为一日期区间（共 {len(dates)} 天），每轮将逐日查询。")

        # 凭证失败退避：仍在退避窗口内的任务直接跳过本轮。
        _, next_ok_ts = state_mgr.get_task_backoff(task_id)
        if next_ok_ts > time.time():
            remain = int(next_ok_ts - time.time())
            append_log(f"任务退避中（剩余约 {remain} 秒），跳过本轮。")
            continue

        # 任务级错峰：>1 条任务时，任务之间随机等待，避免连发。
        if index > 0:
            stagger = random.randint(TASK_STAGGER_MIN, TASK_STAGGER_MAX)
            append_log(f"任务间错峰 {stagger} 秒…")
            time.sleep(stagger)

        from_city = code_to_city_only(from_code)
        to_city = code_to_city_only(to_code)
        pushed_count = 0

        for date in dates:
            append_log(f"查询任务：{date} | {from_city}({from_code})->{to_city}({to_code}) | {fare_label} | 目标<= {target_price}")

            try:
                status, fares = fetch_price_status(
                    from_code=from_code,
                    to_code=to_code,
                    date=date,
                    fare_type=fare_type,
                )
            except TokenExpiredError as exc:
                state_mgr.mark_task_failure(task_id)
                reason = str(exc)
                append_log(f"[鉴权失败] {reason}（该任务进入退避）")

                if state_mgr.should_send_token_alert():
                    send_token_expired_alert(send_keys, reason)
                    send_feishu_token_alert(feishu_cfg, reason)
                    state_mgr.mark_token_alert()
                    append_log("已发送 Token 过期高优先级告警。")
                else:
                    append_log("Token 告警处于冷却期，跳过重复推送。")
                break

            if status == "network":
                append_log("请求失败（网络不通 / 网关错误 / 429 限流），本轮跳过。")
                continue
            if status == "parse":
                append_log("接口返回结构异常（非 JSON 或字段变化），本轮跳过。")
                continue
            if not fares:
                append_log("接口正常返回但无可用航班（无票或价格不可购）。")
                continue

            # 取到价格：任务恢复，清零退避计数。
            state_mgr.mark_task_success(task_id)
            saw_prices = True

            for item in fares:
                flight = str(item.get("flight", "UNKNOWN"))
                price = item.get("price")
                if not isinstance(price, int):
                    continue

                append_log(f"{flight} | {price} 元")
                append_history(
                    {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "task_id": task_id,
                        "date": date,
                        "from": from_code,
                        "to": to_code,
                        "fare_type": fare_type,
                        "flight": flight,
                        "price": price,
                    }
                )
                if price <= target_price:
                    # 提醒用“命中当天”的任务视图：正文显示具体日期，指纹含日期可避免区间内重复推送。
                    alert_task = dict(task)
                    alert_task["date"] = date
                    alert_task["date_end"] = str(task.get("date_end", "") or "")
                    fingerprint = _build_fingerprint(alert_task, flight, price)
                    if state_mgr.should_alert(fingerprint):
                        send_price_alert(send_keys, alert_task, flight, price)
                        send_feishu_price_alert(feishu_cfg, alert_task, flight, price)
                        state_mgr.mark_alert(fingerprint)
                        pushed_count += 1

        if pushed_count > 0:
            append_log(f"已发送 {pushed_count} 条低价提醒。")

    return saw_prices


def main() -> None:
    """daemon 主循环。"""
    ensure_file(CONFIG_PATH, DEFAULT_CONFIG)
    ensure_file(TASKS_PATH, DEFAULT_TASKS)
    ensure_file(LOG_PATH, "")
    ensure_file(STATE_PATH, {"price_alerts": {}, "token_alert": {"last_ts": 0}})

    # 读取配置中的冷却时间
    config = load_config()
    alert_cooldown_hours = int(config.get("alert_cooldown", {}).get("hours", 1))
    window_seconds = alert_cooldown_hours * 3600

    state_mgr = AlertStateManager(state_path=STATE_PATH, window_seconds=window_seconds)
    append_log(f"状态管理器已初始化，价格提醒冷却期：{alert_cooldown_hours}小时")

    append_log("daemon 已启动。")
    monitor_window_active: bool | None = None

    while True:
        config = load_config()
        status = str(config.get("status", "stopped")).lower()
        check_interval = int(config.get("runtime", {}).get("check_status_interval_sec", 3))
        if check_interval <= 0:
            check_interval = 3

        if status != "running":
            monitor_window_active = None
            time.sleep(check_interval)
            continue

        window_cfg = config.get("monitor_window", DEFAULT_CONFIG["monitor_window"])
        start_hhmm = str(window_cfg.get("start", DEFAULT_CONFIG["monitor_window"]["start"]))
        end_hhmm = str(window_cfg.get("end", DEFAULT_CONFIG["monitor_window"]["end"]))
        now_time = datetime.now().time()
        in_window = _is_in_monitor_window(now_time=now_time, start_hhmm=start_hhmm, end_hhmm=end_hhmm)

        if not in_window:
            if monitor_window_active is not False:
                append_log(f"当前不在监控时段（{start_hhmm}-{end_hhmm}），daemon 待机中。")
            monitor_window_active = False
            time.sleep(check_interval)
            continue

        if monitor_window_active is not True:
            append_log(f"进入监控时段（{start_hhmm}-{end_hhmm}），开始执行监控。")
        monitor_window_active = True

        saw_prices = run_one_round(state_mgr)

        polling_cfg = config.get("polling", DEFAULT_CONFIG["polling"]) or {}
        day_min = int(polling_cfg.get("day_min_sec", 90))
        night_max = int(polling_cfg.get("night_max_sec", 600))
        wait_seconds = get_next_interval_seconds(polling=polling_cfg)
        if saw_prices:
            # 有价：加密轮询，但不低于日间隔下限。
            wait_seconds = max(int(wait_seconds / 2), day_min)
            append_log(f"本轮有价格数据，下一轮间隔缩短至 {wait_seconds} 秒。")
        else:
            # 无价：拉长轮询，减少无效请求，封顶夜间上限的 2 倍。
            wait_seconds = min(int(wait_seconds * 1.5), night_max * 2)
            append_log(f"本轮无价格数据，下一轮间隔拉长至 {wait_seconds} 秒。")
        sleep_with_status_check(wait_seconds, check_interval)


if __name__ == "__main__":
    main()
