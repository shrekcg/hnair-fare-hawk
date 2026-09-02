"""web_api.py 的 HTTP 冒烟测试：状态、任务增删、票据校验、SendKey 空值保护。

基于真实文件（monkeypatch 到临时目录），不碰用户的 config.json。
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

# 让 web_api 可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import web_api  # noqa: E402


@pytest.fixture()
def api_server(tmp_path, monkeypatch):
    """在临时目录里起一个 web_api 实例（随机端口），并注入临时 data 文件。"""
    # 把数据文件路径指到临时目录
    import app as app_mod

    monkeypatch.setattr(app_mod, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(app_mod, "TASKS_PATH", tmp_path / "tasks.json")
    monkeypatch.setattr(app_mod, "STATE_PATH", tmp_path / "runtime_state.json")
    monkeypatch.setattr(app_mod, "LOG_PATH", tmp_path / "run_log.txt")
    monkeypatch.setattr(app_mod, "HISTORY_PATH", tmp_path / "price_history.jsonl")

    # web_api 内部用的是 app 模块的路径，重新加载一份独立配置到临时目录
    import importlib

    app_mod.ensure_data_files()

    # web_api 用已加载的 app 函数，所以直接起服务即可
    server = web_api.ThreadingHTTPServer(("127.0.0.1", 0), web_api.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}", tmp_path
    server.shutdown()
    server.server_close()


def test_state_returns_sane_shape(api_server):
    base, _ = api_server
    r = requests.get(f"{base}/api/state", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert set(["config", "tasks", "history", "stats", "logs"]) <= set(body.keys())
    assert body["config"]["status"] in ("running", "stopped")
    assert isinstance(body["tasks"], list)


def test_add_and_delete_task(api_server):
    base, tmp = api_server
    payload = {
        "date": "2026-09-25",
        "from_city": "深圳",
        "to_city": "杭州",
        "target_price": 199,
        "fare_type": "normal",
    }
    r = requests.post(f"{base}/api/tasks", json=payload, timeout=5)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    tasks = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["from_code"] == "SZX"
    assert tasks[0]["to_code"] == "HGH"
    assert tasks[0]["fare_type"] == "normal"
    tid = tasks[0]["id"]

    r = requests.post(f"{base}/api/tasks/delete", json={"id": tid}, timeout=5)
    assert r.json()["ok"] is True
    tasks = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    assert tasks == []


def test_add_task_bad_city_rejected(api_server):
    base, _ = api_server
    r = requests.post(
        f"{base}/api/tasks",
        json={"date": "2026-09-25", "from_city": "不存在的城市xyz", "to_city": "HGH", "target_price": 100},
        timeout=5,
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert "无法唯一匹配" in r.json()["error"]


def test_ticket_rejects_non_hnair(api_server):
    base, _ = api_server
    r = requests.post(f"{base}/api/ticket", json={"fare_type": "plus", "raw": "curl https://baidu.com -d x=1"}, timeout=5)
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "不是海航接口" in r.json()["error"]


def test_send_keys_empty_does_not_overwrite(api_server):
    base, _ = api_server
    # 先保存一个 key
    r = requests.post(f"{base}/api/send_keys", json={"raw": "SCT1234567890abcdef"}, timeout=5)
    assert r.json()["ok"] is True

    # 再传空字符串，不应覆盖
    r = requests.post(f"{base}/api/send_keys", json={"raw": ""}, timeout=5)
    assert r.json()["ok"] is True
    assert r.json().get("skipped") is True

    state = requests.get(f"{base}/api/state", timeout=5).json()
    assert state["config"]["send_keys_count"] == 1


def test_send_keys_mask_hides_value(api_server):
    base, _ = api_server
    requests.post(f"{base}/api/send_keys", json={"raw": "SCT1234567890abcdef"}, timeout=5)
    state = requests.get(f"{base}/api/state", timeout=5).json()
    masked = state["config"]["send_keys"][0]
    assert masked != "SCT1234567890abcdef"
    assert "****" in masked


def test_ticket_summary_does_not_leak_query(api_server, tmp_path):
    """票据摘要只显示域名+路径，绝不出现 query/token。"""
    from web_api import _ticket_summary

    summary = _ticket_summary("curl 'https://app.hnair.com/ticket/lfs/airLowFareSearch?token=SECRETTOKEN&hnairSign=SECRET' -H 'x: y' --data-raw '{}'")
    assert "SECRETTOKEN" not in summary["url"]
    assert summary["url"] == "https://app.hnair.com/ticket/lfs/airLowFareSearch"


def test_add_task_with_date_range(api_server):
    base, tmp = api_server
    payload = {
        "date": "2026-09-20",
        "date_end": "2026-09-30",
        "from_city": "深圳",
        "to_city": "杭州",
        "target_price": 199,
        "fare_type": "normal",
    }
    r = requests.post(f"{base}/api/tasks", json=payload, timeout=5)
    assert r.status_code == 200
    tasks = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    assert tasks[0]["date"] == "2026-09-20"
    assert tasks[0]["date_end"] == "2026-09-30"

    # state 接口应返回 date_end
    state = requests.get(f"{base}/api/state", timeout=5).json()
    assert state["tasks"][0]["date_end"] == "2026-09-30"


def test_add_task_date_range_invalid_rejected(api_server):
    base, _ = api_server
    r = requests.post(
        f"{base}/api/tasks",
        json={"date": "2026-09-30", "date_end": "2026-09-20", "from_city": "SZX", "to_city": "HGH", "target_price": 199},
        timeout=5,
    )
    assert r.status_code == 400
    assert "结束日期不能早于开始日期" in r.json()["error"]


def test_feishu_save_and_mask(api_server):
    base, _ = api_server
    r = requests.post(
        f"{base}/api/feishu",
        json={"app_id": "cli_a1b2c3d4", "app_secret": "SECRETSECRETSECRET", "receiver": "me@example.com"},
        timeout=5,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    state = requests.get(f"{base}/api/state", timeout=5).json()
    feishu = state["config"]["feishu"]
    assert feishu["configured"] is True
    assert feishu["has_secret"] is True
    # AppID 掩码，不完整回显
    assert feishu["app_id"] != "cli_a1b2c3d4"
    assert "****" in feishu["app_id"]
    # receiver 不敏感可展示
    assert feishu["receiver"] == "me@example.com"

    # 序列化后的完整 state 里也绝不能出现 app_secret
    raw = requests.get(f"{base}/api/state", timeout=5).text
    assert "SECRETSECRETSECRET" not in raw


def test_feishu_empty_secret_keeps_old(api_server):
    base, _ = api_server
    requests.post(
        f"{base}/api/feishu",
        json={"app_id": "cli_a1b2c3d4", "app_secret": "SECRETSECRETSECRET", "receiver": "me@example.com"},
        timeout=5,
    )
    # 再次保存时不带 secret：secret 保持不变，receiver 更新
    r = requests.post(f"{base}/api/feishu", json={"app_id": "cli_a1b2c3d4", "app_secret": "", "receiver": "new@example.com"}, timeout=5)
    assert r.status_code == 200
    state = requests.get(f"{base}/api/state", timeout=5).json()
    feishu = state["config"]["feishu"]
    assert feishu["has_secret"] is True
    assert feishu["receiver"] == "new@example.com"
    # app_id 清空会生效（数据层）
    requests.post(f"{base}/api/feishu", json={"app_id": "", "app_secret": "", "receiver": ""}, timeout=5)
    state = requests.get(f"{base}/api/state", timeout=5).json()
    assert state["config"]["feishu"]["configured"] is False


def test_feishu_message_missing_config_no_network():
    """缺配置时直接返回失败，不发网络请求。"""
    from backend.notifier import send_feishu_message

    ok, err = send_feishu_message("", "", "", "title", "content")
    assert ok is False
    assert "不完整" in err

def test_expand_dates():
    """daemon 日期区间展开逻辑。"""
    from daemon import expand_dates

    # 单日（无 date_end）
    assert expand_dates({"date": "2026-09-20"}) == ["2026-09-20"]
    # 单日（date_end 与 date 相同）
    assert expand_dates({"date": "2026-09-20", "date_end": "2026-09-20"}) == ["2026-09-20"]
    # 区间
    dates = expand_dates({"date": "2026-09-20", "date_end": "2026-09-22"})
    assert dates == ["2026-09-20", "2026-09-21", "2026-09-22"]
    # 倒序被纠正
    dates = expand_dates({"date": "2026-09-22", "date_end": "2026-09-20"})
    assert dates == ["2026-09-20", "2026-09-21", "2026-09-22"]
    # 非法日期：原样返回单日
    assert expand_dates({"date": "not-a-date", "date_end": "2026-09-20"}) == ["not-a-date"]
    # 空任务
    assert expand_dates({}) == []
