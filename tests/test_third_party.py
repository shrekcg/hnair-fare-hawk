"""第三方校正层测试：规范化、匹配、diff、应用、固化脚本预览/合入。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.third_party as tp  # noqa: E402
from scripts.sedimentation.sync_third_party import run  # noqa: E402


def test_normalize_correction_valid():
    raw = {
        "flight_no": "Y87531", "origin_iata": "SZX", "dest_iata": "CGQ",
        "dep_time": "7:40", "arr_time": "15:50", "dep_terminal": "t3", "arr_terminal": "T2",
        "stops": [{"city": "杭州", "airport": "萧山", "terminal": "T3",
                   "arrive": "09:55", "depart": "12:50", "stay": "2h55m"}],
        "source": "juhe", "observed_at": "2026-09-03",
    }
    c = tp.normalize_correction(raw)
    assert c is not None
    assert c["dep_time"] == "07:40"  # H:MM → HH:MM
    assert c["dep_terminal"] == "T3"  # 小写 → 大写
    assert c["stops"][0]["arrive"] == "09:55"
    assert c["flight_no"] == "Y87531"


def test_normalize_correction_invalid():
    assert tp.normalize_correction({}) is None
    assert tp.normalize_correction({"flight_no": "Y87531"}) is None  # 缺 IATA
    # 非法时间被丢弃（不导致整条失效）
    c = tp.normalize_correction({
        "flight_no": "Y87531", "origin_iata": "SZX", "dest_iata": "CGQ",
        "dep_time": "25:99", "arr_time": "15:50",
    })
    assert c is not None and "dep_time" not in c and c["arr_time"] == "15:50"


def test_load_corrections_and_skip_invalid():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "corr.json"
        p.write_text(json.dumps({
            "corrections": [
                {"flight_no": "Y87531", "origin_iata": "SZX", "dest_iata": "CGQ",
                 "dep_time": "07:40", "arr_time": "15:50"},
                {"flight_no": "BAD"},  # 非法，跳过
            ]
        }, ensure_ascii=False), encoding="utf-8")
        got = tp.load_corrections(p)
        assert len(got) == 1
        assert got[0]["flight_no"] == "Y87531"


def test_diff_fields_and_apply():
    c = tp.normalize_correction({
        "flight_no": "Y87531", "origin_iata": "SZX", "dest_iata": "CGQ",
        "dep_time": "07:40", "arr_time": "15:50",
        "dep_terminal": "T3", "arr_terminal": "T2",
        "stops": [{"city": "杭州"}], "source": "juhe", "observed_at": "2026-09-03",
    })
    rec = {
        "flight_no": "Y87531",
        "origin": {"city": "深圳", "iata": "SZX", "airport": "深圳宝安国际机场"},
        "dest": {"city": "长春", "iata": "CGQ", "airport": "长春龙嘉国际机场"},
        "dep_time": "08:50", "arr_time": "15:50",
        "product": "666/2666", "days": [2, 4, 6],
    }
    # 匹配键一致
    assert tp.correction_key(c) == tp.record_key(rec)
    diffs = tp.diff_fields(rec, c)
    # arr_time 一致 → 不出现在 diff；dep_time/terminal/stops 差异
    assert "arr_time" not in diffs
    assert diffs["dep_time"] == ("08:50", "07:40")
    assert diffs["dep_terminal"] == (None, "T3")
    assert "stops" in diffs

    out = tp.apply_to_record(rec, c)
    assert out["dep_time"] == "07:40"
    assert out["origin"]["terminal"] == "T3"
    assert out["dest"]["terminal"] == "T2"
    assert out["stops"][0]["city"] == "杭州"
    # 入参不变 + 班期/产品不动
    assert rec["dep_time"] == "08:50"
    assert out["product"] == "666/2666"
    assert out["days"] == [2, 4, 6]


def test_sync_preview_and_apply(tmp_path, monkeypatch):
    import shutil
    from scripts.sedimentation import sync_third_party as mod

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

    corr = tp.normalize_correction({
        "flight_no": "Y87531", "origin_iata": "SZX", "dest_iata": "CGQ",
        "dep_time": "07:40", "arr_time": "15:50", "dep_terminal": "T3",
        "source": "juhe", "observed_at": "2026-09-03",
    })

    monkeypatch.setattr(mod, "NORMALIZED", norm)
    monkeypatch.setattr(mod, "VERSIONS", versions)
    monkeypatch.setattr(mod, "SNAPSHOTS", snaps)

    # 预览：不写文件
    recs = json.loads(norm.read_text(encoding="utf-8"))["records"]
    res = run(preview=True, corrections=[corr], records=recs)
    assert res["preview"] and res["changed"] == 1
    assert json.loads(norm.read_text(encoding="utf-8"))["records"][0]["dep_time"] == "08:50"

    # 应用：写文件 + 快照 + 版本
    recs = json.loads(norm.read_text(encoding="utf-8"))["records"]
    res2 = run(preview=False, corrections=[corr], records=recs)
    assert res2["changed"] == 1
    payload = json.loads(norm.read_text(encoding="utf-8"))
    assert payload["records"][0]["dep_time"] == "07:40"
    assert payload["records"][0]["origin"]["terminal"] == "T3"
    assert snaps.exists()
    v = json.loads(versions.read_text(encoding="utf-8"))
    assert v["versions"][0]["action"] == "sync_third_party"
    assert v["versions"][0]["changed"] == 1