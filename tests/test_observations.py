"""观测层测试：写入、去重、覆盖底表记录、固化脚本。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.observations as obs  # noqa: E402
from scripts.sedimentation.apply_observations import apply  # noqa: E402


def test_record_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "OBS_PATH", tmp_path / "observations.json")
    ok = obs.record("Y87531", "深圳", "长春", dep_time="07:40", arr_time="15:50",
                    dep_terminal="T3", arr_terminal="T2", observed_at="2026-09-05")
    assert ok is True
    data = obs.load(force=True)
    ob = data["observations"]["Y87531|深圳|长春"]
    assert ob["dep_time"] == "07:40"
    assert ob["arr_terminal"] == "T2"
    # 同日相同内容不重复写
    ok2 = obs.record("Y87531", "深圳", "长春", dep_time="07:40", arr_time="15:50",
                     dep_terminal="T3", arr_terminal="T2", observed_at="2026-09-05")
    assert ok2 is False
    # 同日新内容（时刻变化）会更新
    ok3 = obs.record("Y87531", "深圳", "长春", dep_time="09:15", arr_time="15:50",
                     observed_at="2026-09-05")
    assert ok3 is True
    assert obs.load(force=True)["observations"]["Y87531|深圳|长春"]["dep_time"] == "09:15"


def test_record_fares_aggregate(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "OBS_PATH", tmp_path / "observations.json")
    fares = [
        {"flight": "Y87531", "price": 1650, "times": {"dep": "07:40", "arr": "15:50",
                                                     "dep_terminal": "T3", "arr_terminal": "T2"},
         "stop": {"kind": "stopover", "stops": 1}},
        {"flight": "HU7001", "price": 900, "times": {"dep": "08:10", "arr": "09:40"}},
    ]
    n = obs.record_fares(fares, "深圳", "长春")
    assert n == 2
    data = obs.load(force=True)["observations"]
    assert "Y87531|深圳|长春" in data
    assert "HU7001|深圳|长春" in data
    assert data["HU7001|深圳|长春"]["dep_terminal"] == ""


def test_apply_to_record(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "OBS_PATH", tmp_path / "observations.json")
    obs.record("Y87531", "深圳", "长春", dep_time="07:40", arr_time="15:50",
               dep_terminal="T3", arr_terminal="T2", observed_at="2026-09-05")
    rec = {
        "flight_no": "Y87531",
        "origin": {"city": "深圳", "airport": "深圳宝安国际机场", "iata": "SZX"},
        "dest": {"city": "长春", "airport": "长春龙嘉国际机场", "iata": "CGQ"},
        "dep_time": "08:50",
        "arr_time": "15:50",
    }
    out = obs.apply_to_record(rec)
    assert out["dep_time"] == "07:40"
    assert out["origin"]["terminal"] == "T3"
    assert out["dest"]["terminal"] == "T2"
    assert out["_obs_at"] == "2026-09-05"
    # 入参不被修改（dict 副本语义）
    assert rec["dep_time"] == "08:50"
    # 无观测时不改动
    rec2 = {"flight_no": "ZH9001", "origin": {"city": "深圳"}, "dest": {"city": "北京"},
            "dep_time": "07:00", "arr_time": "10:00"}
    out2 = obs.apply_to_record(rec2)
    assert out2["dep_time"] == "07:00"
    assert "_obs_time" not in out2


def test_apply_script_fuses_into_normalized(tmp_path, monkeypatch):
    # 构造迷你底表 + 观测，跑固化脚本
    monkeypatch.setattr(obs, "OBS_PATH", tmp_path / "observations.json")
    obs.record("Y87531", "深圳", "长春", dep_time="07:40", arr_time="15:50",
               dep_terminal="T3", arr_terminal="T2", observed_at="2026-09-05")

    norm = tmp_path / "flights_normalized.json"
    norm.write_text(json.dumps({
        "generated_at": "2026-09-01", "count": 1, "note": "",
        "records": [{
            "flight_no": "Y87531", "carrier": "金鹏航空",
            "origin": {"city": "深圳", "airport": "深圳宝安国际机场", "iata": "SZX"},
            "dest": {"city": "长春", "airport": "长春龙嘉国际机场", "iata": "CGQ"},
            "dep_time": "08:50", "arr_time": "15:50", "product": "666/2666",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    versions = tmp_path / "versions.json"
    versions.write_text('{"versions": []}', encoding="utf-8")
    snaps = tmp_path / "snapshots"

    monkeypatch.setattr("scripts.sedimentation.apply_observations.NORMALIZED", norm)
    monkeypatch.setattr("scripts.sedimentation.apply_observations.VERSIONS", versions)
    monkeypatch.setattr("scripts.sedimentation.apply_observations.SNAPSHOTS", snaps)
    monkeypatch.setattr("scripts.sedimentation.apply_observations.OBS_FILE",
                       obs.OBS_PATH if obs.OBS_PATH.parent == tmp_path else tmp_path / "observations.json")

    result = apply()
    assert result["changed"] == 1
    payload = json.loads(norm.read_text(encoding="utf-8"))
    rec = payload["records"][0]
    assert rec["dep_time"] == "07:40"
    assert rec["origin"]["terminal"] == "T3"
    v = json.loads(versions.read_text(encoding="utf-8"))
    assert v["versions"][0]["action"] == "apply_observations"
    assert snaps.exists()