"""多通道通知 + 余票/舱位监控测试。

覆盖：
- app.save_notify_channels 脱敏（凭证空串不修改 / 掩码不回写 / enabled 开关）；
- app.add_task / add_tasks_batch 的 min_seats、cabins 落盘与归一化；
- daemon._task_seat_ok 的各类判定；
- backend.channels.notify_channels 渠道过滤与历史写入（mock 发送）；
- backend.feishu_ws.process_card_action_payload（challenge 回显）；
- backend.fetcher._extract_seat_info 余票/舱位解析（mock 响应）。

全链路不触发真实网络请求。
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
import daemon  # noqa: E402
import backend.fetcher as fetcher  # noqa: E402
import backend.feishu_ws as feishu_ws  # noqa: E402
import backend.channels as channels  # noqa: E402


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """把 app 模块的数据路径指到临时目录。"""
    monkeypatch.setattr(app_mod, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(app_mod, "TASKS_PATH", tmp_path / "tasks.json")
    monkeypatch.setattr(app_mod, "STATE_PATH", tmp_path / "runtime_state.json")
    monkeypatch.setattr(app_mod, "LOG_PATH", tmp_path / "run_log.txt")
    monkeypatch.setattr(app_mod, "HISTORY_PATH", tmp_path / "price_history.jsonl")
    monkeypatch.setattr(app_mod, "NOTIFY_HISTORY_PATH", tmp_path / "notification_history.jsonl")
    app_mod.ensure_data_files()
    return tmp_path


def _read_tasks(tmp_path):
    return json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))["tasks"]


def _read_config(tmp_path):
    return json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


# ==================== app.save_notify_channels 脱敏 ====================


def test_save_notify_channels_defaults(app_env):
    cfg = _read_config(app_env)
    nc = cfg["notify_channels"]
    assert nc["urgent_enabled"] is True
    assert nc["wecom"]["enabled"] is False
    assert set(nc["wecom"]) == {"enabled", "corp_id", "agent_id", "secret", "user_id"}
    assert set(nc["dingtalk"]) == {"enabled", "webhook", "secret"}
    assert set(nc["bark"]) == {"enabled", "device_key", "server"}
    assert set(nc["ntfy"]) == {"enabled", "topic", "server"}


def test_save_notify_channels_secret_keep_on_empty(app_env):
    app_mod.save_notify_channels({
        "wecom": {"enabled": True, "corp_id": "ww123", "secret": "topsecret"},
        "ntfy": {"enabled": True, "topic": "my-topic"},
    })
    cfg = _read_config(app_env)
    assert cfg["notify_channels"]["wecom"]["secret"] == "topsecret"
    assert cfg["notify_channels"]["ntfy"]["topic"] == "my-topic"

    # 再次保存：秘密字段空串 = 不修改；corp_id 空串 = 清空（非秘密字段语义）
    app_mod.save_notify_channels({"wecom": {"secret": "", "corp_id": ""}})
    cfg = _read_config(app_env)
    assert cfg["notify_channels"]["wecom"]["secret"] == "topsecret"
    assert cfg["notify_channels"]["wecom"]["corp_id"] == ""
    assert cfg["notify_channels"]["wecom"]["enabled"] is True  # 未传 enabled 不改变


def test_save_notify_channels_enabled_and_urgent(app_env):
    # 加急开关已从界面移除：即使传入 urgent_enabled=False 也固定开启（重要/阻断告警默认加急）
    app_mod.save_notify_channels({"urgent_enabled": False, "bark": {"enabled": True}})
    cfg = _read_config(app_env)
    assert cfg["notify_channels"]["urgent_enabled"] is True
    assert cfg["notify_channels"]["bark"]["enabled"] is True
    assert cfg["notify_channels"]["wecom"]["enabled"] is False


def test_save_notify_channels_bad_shape_safe(app_env):
    # 非 dict channel / 空配置不会崩溃，也不覆盖已有
    app_mod.save_notify_channels({"wecom": "garbage", "dingtalk": None, "unknown_ch": {"enabled": True}})
    cfg = _read_config(app_env)
    assert cfg["notify_channels"]["wecom"]["enabled"] is False
    assert cfg["notify_channels"]["dingtalk"]["enabled"] is False
    assert "unknown_ch" not in cfg["notify_channels"]


# ==================== app 任务 min_seats / cabins 落盘 ====================


def test_add_task_min_seats_cabins(app_env):
    app_mod.add_task("2026-10-01", "PEK", "SHA", 199, fare_type="plus", min_seats=3, cabins=["b", "C", "b"])
    t = _read_tasks(app_env)[0]
    assert t["min_seats"] == 3
    assert t["cabins"] == ["B", "C"]  # 归一化大写去重


def test_add_task_min_seats_default(app_env):
    app_mod.add_task("2026-10-01", "PEK", "SHA", 199, fare_type="plus")
    t = _read_tasks(app_env)[0]
    assert t["min_seats"] == 1
    assert t["cabins"] == []


def test_add_tasks_batch_min_seats_cabins(app_env):
    app_mod.add_tasks_batch([
        {"from_code": "CAN", "to_code": "CTU", "dates": ["2026-10-03"], "min_seats": 4, "cabins": "z,r"},
        {"from_code": "CAN", "to_code": "CTU", "dates": ["2026-10-04"], "min_seats": 2, "cabins": ["Y"]},
    ])
    tasks = _read_tasks(app_env)
    merged = [t for t in tasks if t["from_code"] == "CAN"]
    assert len(merged) == 1
    assert merged[0]["min_seats"] == 2  # 显式指定时覆盖
    assert merged[0]["cabins"] == ["Y"]


# ==================== daemon._task_seat_ok ====================


def _item(seats=None, cabins=None):
    return {"seats": seats, "cabins": cabins or []}


def test_seat_ok_old_behavior_when_no_condition():
    # min_seats 默认 1 且无舱位白名单 → 只看价格（旧行为）
    assert daemon._task_seat_ok({}, _item(seats=0)) is True
    assert daemon._task_seat_ok({"min_seats": 1}, _item(seats=None)) is True


def test_seat_ok_seats_missing_passthrough():
    # 解析不到余票 → 防漏报 True
    assert daemon._task_seat_ok({"min_seats": 3}, _item(seats=None)) is True


def test_seat_ok_min_seats_total():
    assert daemon._task_seat_ok({"min_seats": 3}, _item(seats=3)) is True
    assert daemon._task_seat_ok({"min_seats": 3}, _item(seats=2)) is False


def test_seat_ok_cabin_whitelist_sums():
    task = {"min_seats": 2, "cabins": ["B", "C"]}
    item = _item(seats=99, cabins=[{"cabin": "B", "qty": 1}, {"cabin": "C", "qty": 1}, {"cabin": "Z", "qty": 50}])
    assert daemon._task_seat_ok(task, item) is True  # B+C=2
    item2 = _item(seats=99, cabins=[{"cabin": "B", "qty": 1}, {"cabin": "C", "qty": 0}])
    assert daemon._task_seat_ok(task, item2) is False  # B+C=1 < 2


def test_seat_ok_cabin_whitelist_case_insensitive():
    task = {"min_seats": 1, "cabins": ["b"]}
    item = _item(seats=99, cabins=[{"cabin": "B", "qty": 5}])
    assert daemon._task_seat_ok(task, item) is True
    item2 = _item(seats=99, cabins=[{"cabin": "R", "qty": 5}])
    assert daemon._task_seat_ok(task, item2) is False


# ==================== channels.notify_channels（mock 发送） ====================


def test_notify_channels_filters_and_history(tmp_path, monkeypatch):
    calls = []

    def fake_send_channel(cfg, name, level, title, content, card=None):
        calls.append((name, level))
        if name == "feishu":
            return True, "", "om_fake_id"
        return True, "", ""

    monkeypatch.setattr(channels, "send_channel", fake_send_channel)
    notify_cfg = {
        "urgent_enabled": True,
        "wecom": {"enabled": True},
        "dingtalk": {"enabled": False},
        "bark": {"enabled": True},
        "ntfy": {"enabled": False},
        "feishu": {"app_id": "cli_x", "app_secret": "s", "receiver": "ou_1"},
    }
    history = tmp_path / "notification_history.jsonl"
    results = channels.notify_channels(
        cfg=notify_cfg,
        level="important",
        title="测试",
        content="内容",
        card=None,
        history_path=str(history),
    )
    # 只发启用的 wecom/bark/feishu
    assert {n for n, _ in calls} == {"wecom", "bark", "feishu"}
    assert results["feishu"]["message_id"] == "om_fake_id"
    lines = history.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    entry = json.loads(lines[0])
    assert entry["ok"] is True
    assert entry["channel"] in ("wecom", "bark", "feishu")


def test_notify_channels_critical_forces_all(tmp_path, monkeypatch):
    calls = []

    def fake_send_channel(cfg, name, level, title, content, card=None):
        calls.append(name)
        if name == "feishu":
            return False, "未配置", ""
        return False, "未配置", ""

    monkeypatch.setattr(channels, "send_channel", fake_send_channel)
    notify_cfg = {"urgent_enabled": True, "wecom": {"enabled": False}, "feishu": {}, "dingtalk": {}, "bark": {}, "ntfy": {}}
    results = channels.notify_channels(cfg=notify_cfg, level="critical", title="阻断", content="x", history_path=str(tmp_path / "h.jsonl"))
    # critical 强制全渠道（未启用也尝试，失败记录）
    assert sorted(calls) == sorted(["wecom", "feishu", "dingtalk", "bark", "ntfy"])
    assert all(not results[n]["ok"] for n in results)


# ==================== feishu_ws 回调 ====================


def test_card_action_payload_challenge_echo():
    payload = {"challenge": "abc123", "token": "t", "type": "url_verification"}
    out = feishu_ws.process_card_action_payload(payload)
    assert out == {"challenge": "abc123"}


def test_card_action_payload_missing_message_id():
    payload = {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_x"},
            "context": {"open_message_id": ""},
            "action": {"value": {"callback_key": "price_alert", "action": "f", "confirm": "1"}},
            "token": "t",
        },
    }
    out = feishu_ws.process_card_action_payload(payload)
    assert "toast" in out


def test_card_action_payload_updates_card(tmp_path, monkeypatch):
    # 预置一张已发送卡片 → 模拟确认回调 → 应更新卡片并写入回执
    from backend.channels import build_confirm_card
    from datetime import datetime, timedelta

    state_path = str(tmp_path / "runtime_state.json")
    card = build_confirm_card("🎉 测试", "正文", callback_key="price_alert", action_value="fp")
    feishu_ws.record_sent_card("om_test12345", card, state_path=state_path)

    updated = []
    monkeypatch.setattr(feishu_ws, "update_feishu_card", lambda app_id, app_secret, message_id, card: (True, ""))
    payload = {
        "event": {
            "operator": {"open_id": "ou_1"},
            "context": {"open_message_id": "om_test12345"},
            "action": {"value": {"callback_key": "price_alert", "action": "fp", "confirm": "1"}},
            "token": "mock_token",
        }
    }
    out = feishu_ws.process_card_action_payload(payload, state_path=state_path)
    assert "toast" in out
    confirms = feishu_ws.read_confirmations(state_path=state_path)
    assert len(confirms) == 1
    assert confirms[0]["confirmed"] is True
    assert confirms[0]["message_id"] == "om_test12345"


# ==================== fetcher._extract_seat_info（mock 响应） ====================


def _itinerary_with_classes():
    return {
        "airItineraryPrices": [
            {
                "flightBookingClasses": [
                    {"bookingClass": "B", "inventoryQuantity": 2, "inventoryStatus": "2"},
                    {"bookingClass": "C", "inventoryQuantity": 3, "inventoryStatus": "3"},
                ]
            },
            {
                "flightBookingClasses": [
                    {"bookingClass": "B", "inventoryQuantity": 4, "inventoryStatus": "4"},
                    {"bookingClass": "Z", "inventoryQuantity": 1, "inventoryStatus": "A"},
                ]
            },
        ]
    }


def test_extract_seat_info_dedupe_and_cap():
    info = fetcher._extract_seat_info(_itinerary_with_classes())
    assert info is not None
    by_code = {c["cabin"]: c for c in info["cabins"]}
    # 同一舱位跨产品去重取最大余量：B 取 4
    assert by_code["B"]["qty"] == 4
    assert by_code["C"]["qty"] == 3
    # inventoryStatus=A → 至少 10（官方封顶 10）
    assert by_code["Z"]["qty"] == 10
    assert info["seats"] == 4 + 3 + 10


def test_extract_seat_info_none_when_no_classes():
    assert fetcher._extract_seat_info({"airItineraryPrices": []}) is None
    assert fetcher._extract_seat_info({"airItineraryPrices": [{"flightBookingClasses": []}]}) is None
    assert fetcher._extract_seat_info({}) is None