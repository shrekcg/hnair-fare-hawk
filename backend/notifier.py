"""微信（Server酱）通知模块。"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List

import requests
from city_codes import code_to_city_label, code_to_city_only


def _send_serverchan(send_key: str, title: str, desp: str) -> bool:
    """向单个 send_key 发送消息。"""
    if not send_key:
        return False

    url = f"https://sctapi.ftqq.com/{send_key}.send"

    try:
        resp = requests.post(url, data={"title": title, "desp": desp}, timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _normalize_keys(send_keys: Iterable[str]) -> List[str]:
    """清理空 key，去除两端空白。"""
    cleaned: List[str] = []
    for key in send_keys:
        k = str(key).strip()
        if k:
            cleaned.append(k)
    return cleaned


def send_price_alert(
    send_keys: Iterable[str],
    task: Dict,
    flight: str,
    price: int,
) -> Dict[str, bool]:
    """发送普通低价提醒。"""
    keys = _normalize_keys(send_keys)
    results: Dict[str, bool] = {}
    from_code = str(task.get("from_code", "-"))
    to_code = str(task.get("to_code", "-"))
    from_city = code_to_city_label(from_code)
    to_city = code_to_city_label(to_code)
    
    # 纯城市名用于标题（不带机场名称）
    from_city_short = code_to_city_only(from_code)
    to_city_short = code_to_city_only(to_code)

    # 修改标题格式为：城市-城市价格，例如：深圳-乌鲁木齐199
    title = f"{from_city_short}-{to_city_short}{price}"
    desp = (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"日期：{task.get('date', '-') }\n\n"
        f"航线：{from_city} -> {to_city}\n\n"
        f"航线代码：{from_code} -> {to_code}\n\n"
        f"航班：{flight}\n\n"
        f"价格：{price} 元"
    )
    for key in keys:
        results[key] = _send_serverchan(key, title, desp)

    return results


def send_token_expired_alert(send_keys: Iterable[str], reason: str) -> Dict[str, bool]:
    """发送高优先级 Token 过期提醒。"""
    keys = _normalize_keys(send_keys)
    results: Dict[str, bool] = {}

    title = "[阻断告警] 海航接口凭证可能已过期"
    desp = (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"原因：{reason}\n\n"
        "处理建议：请更新本机保存的查询请求，然后重启 daemon。"
    )

    for key in keys:
        results[key] = _send_serverchan(key, title, desp)

    return results


def send_test_alert(send_keys: Iterable[str]) -> Dict[str, bool]:
    """发送微信测试消息，帮助用户验证绑定是否成功。"""
    keys = _normalize_keys(send_keys)
    results: Dict[str, bool] = {}
    title = "海航监控：微信绑定测试"
    desp = (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "这是一条测试消息。\n\n"
        "若你收到此消息，说明当前 SendKey 已绑定成功，后续低价与阻断告警可正常推送。"
    )

    for key in keys:
        results[key] = _send_serverchan(key, title, desp)

    return results
