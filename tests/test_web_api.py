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

def test_add_task_plus_forces_199(api_server):
    """PLUS 专享任务强制 199 阈值，忽略用户自定义价格。"""
    base, tmp = api_server
    # 先配置一个 PLUS 票据（否则 _post_task 会因缺少票据拒绝 plus 任务）
    curl = "curl 'https://app.hnair.com/ticket/lfs/airLowFareSearch?token=T&hnairSign=S' -H 'x: y' --data-raw '{}'"
    r = requests.post(f"{base}/api/ticket", json={"fare_type": "plus", "raw": curl}, timeout=5)
    assert r.json()["ok"] is True

    r = requests.post(
        f"{base}/api/tasks",
        json={"date": "2026-09-25", "from_city": "深圳", "to_city": "杭州", "target_price": 999, "fare_type": "plus"},
        timeout=5,
    )
    assert r.status_code == 200
    tasks = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    assert tasks[0]["fare_type"] == "plus"
    assert tasks[0]["target_price"] == 199


def test_add_task_normal_keeps_custom_threshold(api_server):
    """普通票价任务保留用户设置的自定义阈值。"""
    base, tmp = api_server
    r = requests.post(
        f"{base}/api/tasks",
        json={"date": "2026-09-25", "from_city": "深圳", "to_city": "杭州", "target_price": 350, "fare_type": "normal"},
        timeout=5,
    )
    assert r.status_code == 200
    tasks = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    assert tasks[0]["target_price"] == 350


def test_clear_log_archives(api_server):
    base, tmp = api_server
    log = tmp / "run_log.txt"
    log.write_text("line1\nline2\n", encoding="utf-8")

    r = requests.post(f"{base}/api/log/clear", json={}, timeout=5)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert (tmp / "run_log.txt.bak").exists()
    assert log.stat().st_size == 0


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


def test_flights_options_linkage(api_server):
    """/api/flights/options 出发/到达联动：查深圳出发到达下拉应有杭州。"""
    base, _ = api_server
    r = requests.get(f"{base}/api/flights/options?from=深圳", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "深圳（SZX）" in "".join(body["from_options"])
    assert "杭州（HGH）" in "".join(body["to_options"])
    # 反向：到杭州时出发应有深圳
    r2 = requests.get(f"{base}/api/flights/options?to=杭州", timeout=5)
    assert "深圳（SZX）" in "".join(r2.json()["from_options"])
    # 档位过滤
    r3 = requests.get(f"{base}/api/flights/options?from=深圳&to=杭州&product=2666", timeout=5)
    assert r3.status_code == 200


def test_tasks_batch_create_and_state_grouped(api_server):
    """批量建任务：state 按航线分组，返回 ids/dates/档位/目的地机场。"""
    base, tmp = api_server
    payload = {"items": [
        {
            "from_code": "深圳",
            "to_code": "杭州",
            "dates": ["2026-09-20", "2026-09-21"],
            "product": "666",
            "flight_no": "HU1234",
            "dep_time": "08:00",
            "arr_time": "10:00",
        },
        {
            "from_code": "深圳",
            "to_code": "上海",
            "dates": ["2026-09-22"],
            "product": "2666",
        },
    ]}
    r = requests.post(f"{base}/api/tasks/batch", json=payload, timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["created"] == 2
    assert body["merged"] == 0

    state = requests.get(f"{base}/api/state", timeout=5).json()
    rows = {row["from_code"] + "|" + row["to_code"]: row for row in state["tasks"]}
    assert "SZX|HGH" in rows and "SZX|PVG" in rows
    szx_hgh = rows["SZX|HGH"]
    assert szx_hgh["dates"] == ["2026-09-20", "2026-09-21"]
    assert szx_hgh["product"] == "666"
    assert szx_hgh["flight_no"] == "HU1234"
    assert szx_hgh["target_price"] == 199
    assert szx_hgh["fare_type"] == "plus"
    assert szx_hgh["enabled"] is True
    assert len(szx_hgh["ids"]) == 1
    assert szx_hgh["from_city"] == "深圳"
    assert szx_hgh["to_city"] == "杭州"
    assert szx_hgh["to_airport"]  # 机场全称非空


def test_tasks_batch_merge_same_route(api_server):
    """同航线再次批量建任务：日期/档位并入已有任务（merged）。"""
    base, tmp = api_server
    first = {"items": [{"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"], "product": "666"}]}
    r = requests.post(f"{base}/api/tasks/batch", json=first, timeout=5)
    assert r.json()["created"] == 1

    second = {"items": [{"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-22", "2026-09-23"], "product": "2666"}]}
    r = requests.post(f"{base}/api/tasks/batch", json=second, timeout=5)
    body = r.json()
    assert body["ok"] is True
    assert body["created"] == 0
    assert body["merged"] == 1

    state = requests.get(f"{base}/api/state", timeout=5).json()
    row = [x for x in state["tasks"] if x["from_code"] == "SZX" and x["to_code"] == "HGH"][0]
    assert row["dates"] == ["2026-09-20", "2026-09-22", "2026-09-23"]
    assert set(row["product"].split("/")) == {"666", "2666"}
    assert len(row["ids"]) == 1  # 仍然是一条底层任务


def test_tasks_batch_rejects_missing_dates(api_server):
    """批量建任务缺监控日期时 400 报错。"""
    base, _ = api_server
    r = requests.post(f"{base}/api/tasks/batch", json={"items": [{"from_code": "SZX", "to_code": "HGH"}]}, timeout=5)
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert r.json()["error"] == "没有可创建的任务。"
    assert any("缺少监控日期" in e for e in r.json()["errors"])


def test_tasks_delete_ids(api_server):
    """按 ids 数组删除分组行。"""
    base, tmp = api_server
    r = requests.post(f"{base}/api/tasks/batch", json={"items": [
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"]},
        {"from_code": "SZX", "to_code": "PVG", "dates": ["2026-09-21"]},
    ]}, timeout=5)
    assert r.json()["created"] == 2

    state = requests.get(f"{base}/api/state", timeout=5).json()
    ids = [x["ids"][0] for x in state["tasks"]]
    r = requests.post(f"{base}/api/tasks/delete", json={"ids": [ids[0]]}, timeout=5)
    assert r.json()["ok"] is True
    state = requests.get(f"{base}/api/state", timeout=5).json()
    assert len(state["tasks"]) == 1
    assert state["tasks"][0]["ids"] == [ids[1]]
    # 空 ids 拒绝
    r = requests.post(f"{base}/api/tasks/delete", json={}, timeout=5)
    assert r.status_code == 400


def test_tasks_enabled_ids(api_server):
    """按 ids 数组批量启停；enabled=any 分组语义。"""
    base, tmp = api_server
    payload = {"items": [
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"]},
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-21"], "product": "2666"},
    ]}
    r = requests.post(f"{base}/api/tasks/batch", json=payload, timeout=5)
    assert r.json()["created"] == 1
    assert r.json()["merged"] == 1
    # 两条同航线被合并成一条底层任务（batch 内部合并）
    state = requests.get(f"{base}/api/state", timeout=5).json()
    row = state["tasks"][0]
    assert row["from_code"] == "SZX" and row["to_code"] == "HGH"
    assert len(row["ids"]) == 1
    # 停用该分组
    r = requests.post(f"{base}/api/tasks/enabled", json={"ids": row["ids"], "enabled": False}, timeout=5)
    assert r.json()["ok"] is True
    state = requests.get(f"{base}/api/state", timeout=5).json()
    assert state["tasks"][0]["enabled"] is False
    # 重新启用
    r = requests.post(f"{base}/api/tasks/enabled", json={"ids": row["ids"], "enabled": True}, timeout=5)
    state = requests.get(f"{base}/api/state", timeout=5).json()
    assert state["tasks"][0]["enabled"] is True
