"""通知模块：微信（Server酱）+ 飞书（企业自建应用机器人）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, Iterable, List, Optional

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


# ==================== 飞书（企业自建应用机器人） ====================


def _feishu_token(app_id: str, app_secret: str) -> Optional[str]:
    """用 App ID / App Secret 换取 tenant_access_token。"""
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=10,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("code") == 0:
            return str(data.get("tenant_access_token", "") or "")
    except (requests.RequestException, ValueError):
        return None
    return None


def send_feishu_message(
    app_id: str, app_secret: str, receiver: str, title: str, content: str
) -> tuple[bool, str]:
    """通过自建应用机器人发送消息到接收人。

    receiver 以 ``ou_`` 开头时按 open_id 发送，否则按邮箱（email）发送。
    返回 (是否成功, 失败原因)。任何情况下都不回显 app_secret。
    """
    if not (app_id and app_secret and receiver):
        return False, "飞书配置不完整（缺少 App ID / App Secret / 接收人）"

    token = _feishu_token(app_id, app_secret)
    if not token:
        return False, "获取 tenant_access_token 失败，请检查 App ID / App Secret 是否正确"

    receive_id_type = "open_id" if str(receiver).startswith("ou_") else "email"
    text = f"{title}\n\n{content}"
    payload = {
        "receive_id": str(receiver).strip(),
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages"
            f"?receive_id_type={receive_id_type}&user_id_type=open_id",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return True, ""
            code = data.get("code")
            msg = str(data.get("msg", "") or "")
            if code in (99991661, 99991664):
                return False, f"权限不足（code={code}），请在开放平台为应用开通 im:message:send_as_bot 权限并重新发布版本"
            if code == 99991668:
                return False, f"接收人不存在或不在应用可见范围（code={code}），请确认邮箱/OpenID 正确，并在应用可用范围中加入自己"
            return False, f"飞书接口返回错误 code={code}: {msg}"
        return False, f"飞书接口 HTTP {resp.status_code}，请检查网络或代理"
    except requests.RequestException as exc:
        return False, f"请求飞书接口失败：{exc}"


def _feishu_price_text(task: Dict, flight: str, price: int) -> tuple[str, str]:
    """构造与微信一致的低价提醒标题与正文。"""
    from_code = str(task.get("from_code", "-"))
    to_code = str(task.get("to_code", "-"))
    from_city_short = code_to_city_only(from_code)
    to_city_short = code_to_city_only(to_code)
    title = f"{from_city_short}-{to_city_short}{price}"
    content = (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"日期：{task.get('date', '-')}{' 至 ' + str(task.get('date_end', '')) if str(task.get('date_end', '')) != str(task.get('date', '')) else ''}\n"
        f"航线：{code_to_city_label(from_code)} -> {code_to_city_label(to_code)}\n"
        f"航线代码：{from_code} -> {to_code}\n"
        f"航班：{flight}\n"
        f"价格：{price} 元"
    )
    return title, content


def send_feishu_price_alert(feishu_cfg: Dict, task: Dict, flight: str, price: int) -> tuple[bool, str]:
    """飞书低价提醒。feishu_cfg 缺失或未配置时直接返回 (False, '')。"""
    if not feishu_cfg:
        return False, ""
    title, content = _feishu_price_text(task, flight, price)
    return send_feishu_message(
        str(feishu_cfg.get("app_id", "") or ""),
        str(feishu_cfg.get("app_secret", "") or ""),
        str(feishu_cfg.get("receiver", "") or ""),
        title,
        content,
    )


def send_feishu_token_alert(feishu_cfg: Dict, reason: str) -> tuple[bool, str]:
    """飞书高优先级 Token 过期告警。"""
    if not feishu_cfg:
        return False, ""
    title = "[阻断告警] 海航接口凭证可能已过期"
    content = (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"原因：{reason}\n"
        "处理建议：请更新本机保存的查询请求，然后重启 daemon。"
    )
    return send_feishu_message(
        str(feishu_cfg.get("app_id", "") or ""),
        str(feishu_cfg.get("app_secret", "") or ""),
        str(feishu_cfg.get("receiver", "") or ""),
        title,
        content,
    )


def test_feishu(app_id: str, app_secret: str, receiver: str) -> tuple[bool, str]:
    """发送飞书测试消息，验证配置与权限是否就绪。"""
    title = "海航监控：飞书通知测试"
    content = (
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "这是一条测试消息。\n"
        "若你收到此消息，说明飞书通知渠道已配置成功，后续低价与阻断告警可正常推送。"
    )
    return send_feishu_message(
        str(app_id or "").strip(),
        str(app_secret or "").strip(),
        str(receiver or "").strip(),
        title,
        content,
    )
