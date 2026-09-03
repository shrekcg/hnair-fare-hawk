"""任务批处理测试：add_tasks_batch 同航线合并、delete_tasks/set_tasks_enabled ids、expand_task_dates。

数据落在临时目录，不碰用户 tasks.json。
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
from daemon import expand_dates  # noqa: E402


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """把 app 模块的数据路径指到临时目录。"""
    monkeypatch.setattr(app_mod, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(app_mod, "TASKS_PATH", tmp_path / "tasks.json")
    monkeypatch.setattr(app_mod, "STATE_PATH", tmp_path / "runtime_state.json")
    monkeypatch.setattr(app_mod, "LOG_PATH", tmp_path / "run_log.txt")
    monkeypatch.setattr(app_mod, "HISTORY_PATH", tmp_path / "price_history.jsonl")
    app_mod.ensure_data_files()
    return tmp_path


def _read_tasks(tmp_path):
    return json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))["tasks"]


ITEMS = [
    {
        "from_code": "SZX",
        "to_code": "HGH",
        "dates": ["2026-09-20", "2026-09-21"],
        "product": "666",
        "flight_no": "HU1234",
        "dep_time": "08:00",
        "arr_time": "10:00",
    }
]


def test_add_tasks_batch_creates_new(app_env):
    result = app_mod.add_tasks_batch(ITEMS)
    assert result == {"created": 1, "merged": 0}
    tasks = _read_tasks(app_env)
    assert len(tasks) == 1
    t = tasks[0]
    assert t["from_code"] == "SZX"
    assert t["to_code"] == "HGH"
    assert t["dates"] == ["2026-09-20", "2026-09-21"]
    assert t["date"] == "2026-09-20"
    assert t["date_end"] == "2026-09-21"
    assert t["fare_type"] == "plus"
    assert t["target_price"] == 199
    assert t["enabled"] is True
    assert t["product"] == "666"
    assert t["flight_no"] == "HU1234"
    assert t["dep_time"] == "08:00"
    assert t["arr_time"] == "10:00"


def test_add_tasks_batch_same_flight_merges_dates(app_env):
    """同一航班（同航班号且起降时刻相同）：多个日期并入同一条任务。"""
    app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"], "product": "666",
         "flight_no": "HU1234", "dep_time": "08:00", "arr_time": "10:00"}
    ])
    result = app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-21", "2026-09-22"], "product": "2666",
         "flight_no": "HU1234", "dep_time": "08:00", "arr_time": "10:00"},
        {"from_code": "SZX", "to_code": "PVG", "dates": ["2026-09-23"], "product": "666"},
    ])
    assert result == {"created": 1, "merged": 1}
    tasks = _read_tasks(app_env)
    assert len(tasks) == 2
    merged = next(t for t in tasks if t["from_code"] == "SZX" and t["to_code"] == "HGH")
    # dates 并集去重排序
    assert merged["dates"] == ["2026-09-20", "2026-09-21", "2026-09-22"]
    assert merged["date"] == "2026-09-20"
    assert merged["date_end"] == "2026-09-22"
    # product 并集
    assert set(merged["product"].split("/")) == {"666", "2666"}
    # 快照保留
    assert merged["flight_no"] == "HU1234"
    assert merged["dep_time"] == "08:00"
    assert merged["arr_time"] == "10:00"
    # 统一 plus/199
    assert merged["fare_type"] == "plus"
    assert merged["target_price"] == 199


def test_add_tasks_batch_different_flight_not_merged(app_env):
    """同一航线不同航班号/起降时刻：各自成为独立监控任务（穷举，不按航线合并）。"""
    result = app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"], "product": "666",
         "flight_no": "HU1234", "dep_time": "08:00", "arr_time": "10:00"},
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"], "product": "666",
         "flight_no": "HU5678", "dep_time": "09:30", "arr_time": "11:30"},
    ])
    assert result == {"created": 2, "merged": 0}
    tasks = _read_tasks(app_env)
    assert len(tasks) == 2
    assert {t["flight_no"] for t in tasks} == {"HU1234", "HU5678"}
    assert all(t["from_code"] == "SZX" and t["to_code"] == "HGH" for t in tasks)


def test_add_tasks_batch_no_flight_route_merge(app_env):
    """无航班号的旧式条目保持按航线合并（向后兼容旧添加入口）。"""
    app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"], "product": "666"}
    ])
    result = app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-21"], "product": "2666"},
    ])
    assert result == {"created": 0, "merged": 1}
    tasks = _read_tasks(app_env)
    assert len(tasks) == 1
    assert tasks[0]["dates"] == ["2026-09-20", "2026-09-21"]


def test_add_tasks_batch_skips_invalid(app_env):
    """缺出发/到达/日期/空项的条目跳过；城市名解析在 web_api 层，此层不校验。"""
    result = app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": []},
        {"from_code": "X", "to_code": "HGH", "dates": ["2026-09-20"]},  # 此层不解析，直接当三字码
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"]},
        {},
    ])
    assert result == {"created": 2, "merged": 0}
    assert len(_read_tasks(app_env)) == 2


def test_delete_tasks_ids(app_env):
    app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"]},
        {"from_code": "SZX", "to_code": "PVG", "dates": ["2026-09-21"]},
        {"from_code": "CAN", "to_code": "HGH", "dates": ["2026-09-22"]},
    ])
    ids = [t["id"] for t in _read_tasks(app_env)]
    assert app_mod.delete_tasks([ids[0], ids[2]]) == 2
    assert [t["id"] for t in _read_tasks(app_env)] == [ids[1]]
    assert app_mod.delete_tasks([]) == 0


def test_set_tasks_enabled_ids(app_env):
    app_mod.add_tasks_batch([
        {"from_code": "SZX", "to_code": "HGH", "dates": ["2026-09-20"]},
        {"from_code": "SZX", "to_code": "PVG", "dates": ["2026-09-21"]},
    ])
    ids = [t["id"] for t in _read_tasks(app_env)]
    assert app_mod.set_tasks_enabled([ids[0]], False) == 1
    state = {t["id"]: t["enabled"] for t in _read_tasks(app_env)}
    assert state[ids[0]] is False
    assert state[ids[1]] is True
    assert app_mod.set_tasks_enabled(ids, True) == 2
    state = {t["id"]: t["enabled"] for t in _read_tasks(app_env)}
    assert state[ids[0]] is True and state[ids[1]] is True


def test_expand_task_dates_new_and_old_model(app_env):
    # 新模型 dates 列表去重排序
    assert app_mod.expand_task_dates({"dates": ["2026-09-21", "2026-09-20", "2026-09-21"]}) == ["2026-09-20", "2026-09-21"]
    # 旧模型 date~date_end 逐日
    assert app_mod.expand_task_dates({"date": "2026-09-20", "date_end": "2026-09-22"}) == ["2026-09-20", "2026-09-21", "2026-09-22"]
    assert app_mod.expand_task_dates({}) == []
    assert app_mod.expand_task_dates({"dates": ["bad", "2026-09-20"]}) == ["2026-09-20"]


def test_daemon_expand_dates_dates_field():
    """daemon.expand_dates 优先使用 dates 列表字段，空时回退旧模型。"""
    assert expand_dates({"dates": ["2026-09-21", "2026-09-20", "2026-09-21"]}) == ["2026-09-20", "2026-09-21"]
    assert expand_dates({"date": "2026-09-20", "date_end": "2026-09-22"}) == ["2026-09-20", "2026-09-21", "2026-09-22"]
    assert expand_dates({"dates": [], "date": "2026-09-20", "date_end": "2026-09-21"}) == ["2026-09-20", "2026-09-21"]
    assert expand_dates({"dates": ["2026-09-20", "bad"], "date": "2026-08-01"}) == ["2026-09-20"]