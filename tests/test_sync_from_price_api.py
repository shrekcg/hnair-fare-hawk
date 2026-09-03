"""scripts/sedimentation/sync_from_price_api.py 的单元测试。

覆盖：任务折叠去重、候选日期生成（窗口内优先 + 未来补齐）、
进度断点续跑逻辑。不发真实网络请求。
"""
from __future__ import annotations

from datetime import date

from scripts.sedimentation import sync_from_price_api as s


def _rec(flight_no, origin, dest, iata_o, iata_d, days, eff=None):
    return {
        "flight_no": flight_no,
        "origin": {"city": origin, "iata": iata_o},
        "dest": {"city": dest, "iata": iata_d},
        "days": days,
        "effective_dates": eff or [["2026-09-01", "2026-10-24"]],
    }


def test_build_tasks_dedup_weekday():
    # 两条记录同航线不同周几 → 折叠成 2 个任务（贪心选最小覆盖周几），key 含周几
    records = [
        _rec("HU111", "北京", "广州", "PEK", "CAN", [1, 3, 5]),
        _rec("HU222", "北京", "广州", "PEK", "CAN", [2, 4, 6]),
        _rec("HU333", "上海", "成都", "SHA", "CTU", [1, 2, 3]),
    ]
    tasks = s.build_tasks(records)
    keys = [t["key"] for t in tasks]
    assert "PEK|CAN|1" in keys
    assert "PEK|CAN|2" in keys
    assert "SHA|CTU|1" in keys
    # 覆盖航班号集合正确
    t1 = next(t for t in tasks if t["key"] == "PEK|CAN|1")
    assert t1["flight_nos"] == ["HU111"]
    t2 = next(t for t in tasks if t["key"] == "PEK|CAN|2")
    assert t2["flight_nos"] == ["HU222"]


def test_build_tasks_same_key_jointly():
    # 同一航线同一周几的多个航班号合并为一个任务覆盖
    records = [
        _rec("HU111", "北京", "广州", "PEK", "CAN", [1, 3, 5]),
        _rec("HU555", "北京", "广州", "PEK", "CAN", [1, 4]),
    ]
    tasks = s.build_tasks(records)
    t1 = next(t for t in tasks if t["key"] == "PEK|CAN|1")
    assert t1["flight_nos"] == ["HU111", "HU555"]


def test_candidate_dates_within_window_first():
    # 窗口内有匹配周几 → 日期来自窗口内且按时间先后
    recs = [_rec("HU111", "北京", "广州", "PEK", "CAN", [1], [["2026-09-07", "2026-09-21"]])]
    dates = s._candidate_dates(1, recs, max_tries=3)
    assert dates == ["2026-09-07", "2026-09-14", "2026-09-21"]


def test_candidate_dates_fallback_outside_window():
    # 窗口不足时用未来星期几补齐
    from datetime import timedelta

    recs = [_rec("HU111", "北京", "广州", "PEK", "CAN", [1], [["2026-09-07", "2026-09-07"]])]
    dates = s._candidate_dates(1, recs, max_tries=3)
    assert dates[0] == "2026-09-07"
    # 后续候选是未来的周一，且都比首个晚
    assert all(d > "2026-09-07" for d in dates[1:])


def test_next_weekday():
    base = date(2026, 9, 2)  # 周三
    # 从明天（9/3 周四）起找，周三应是 9/9
    assert s._next_weekday(3, base).isoformat() == date(2026, 9, 9).isoformat()
    assert s._next_weekday(4, base).isoformat() == date(2026, 9, 3).isoformat()
    assert s._next_weekday(1, base).isoformat() == date(2026, 9, 7).isoformat()
    assert s._next_weekday(7, base).isoformat() == date(2026, 9, 6).isoformat()


def test_load_progress_missing_file():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        orig = s.PROGRESS_FILE
        s.PROGRESS_FILE = Path(tmp) / "progress.json"
        assert s.load_progress() == {"done": [], "failed": [], "total": 0, "updated_at": ""}
        s.PROGRESS_FILE = orig


def test_save_load_progress_roundtrip():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        orig = s.PROGRESS_FILE
        s.PROGRESS_FILE = Path(tmp) / "progress.json"
        s.save_progress({"A|B|1"}, {"A|B|2"}, 10)
        data = s.load_progress()
        assert data["done"] == ["A|B|1"]
        assert data["failed"] == ["A|B|2"]
        assert data["total"] == 10
        assert data["updated_at"]
        s.PROGRESS_FILE = orig


def test_load_progress_old_format_without_failed():
    # 兼容重写前的进度文件（无 failed 字段）
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        orig = s.PROGRESS_FILE
        s.PROGRESS_FILE = Path(tmp) / "progress.json"
        s.PROGRESS_FILE.write_text('{"done": ["A|B|1"], "total": 5, "updated_at": "t"}', encoding="utf-8")
        data = s.load_progress()
        assert data["done"] == ["A|B|1"]
        assert data["failed"] == []
        assert data["total"] == 5
        s.PROGRESS_FILE = orig


def _task():
    return {
        "key": "PEK|CAN|1",
        "origin": "PEK",
        "dest": "CAN",
        "origin_city": "北京",
        "dest_city": "广州",
        "dates": ["2026-09-07"],
        "weekday": 1,
        "flight_nos": ["HU111"],
    }


def _args():
    import argparse

    return argparse.Namespace(interval=0, limit=10, fare_type="normal", max_fail=10, no_notify=True)


def test_run_round_failed_not_done(monkeypatch):
    # network 失败的任务进入 failed 且不标记 done；落盘进度带 failed
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        orig = s.PROGRESS_FILE
        s.PROGRESS_FILE = Path(tmp) / "progress.json"
        monkeypatch.setattr(s, "fetch_price_status", lambda *a, **k: ("network", []))
        done, failed, stat, interrupted = s.run_round({}, [_task()], set(), set(), _args())
        assert done == set()
        assert failed == {"PEK|CAN|1"}
        assert stat["network"] == 1
        data = s.load_progress()
        assert data["failed"] == ["PEK|CAN|1"]
        assert data["done"] == []
        s.PROGRESS_FILE = orig


def test_run_round_accumulates_prior_failed(monkeypatch):
    # 传入的历史 failed 集合必须保留，不能被本轮覆盖（断点恢复的关键）
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        orig = s.PROGRESS_FILE
        s.PROGRESS_FILE = Path(tmp) / "progress.json"
        monkeypatch.setattr(s, "fetch_price_status", lambda *a, **k: ("network", []))
        done, failed, stat, interrupted = s.run_round(
            {}, [_task()], set(), {"OLD|KEY|1"}, _args()
        )
        assert failed == {"OLD|KEY|1", "PEK|CAN|1"}
        data = s.load_progress()
        assert data["failed"] == ["OLD|KEY|1", "PEK|CAN|1"]
        s.PROGRESS_FILE = orig


def test_run_round_ok_marks_done(monkeypatch):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        orig = s.PROGRESS_FILE
        s.PROGRESS_FILE = Path(tmp) / "progress.json"
        monkeypatch.setattr(s, "fetch_price_status", lambda *a, **k: ("ok", [{"flight_no": "HU111"}]))
        monkeypatch.setattr(s, "record_fares", lambda *a, **k: 1)
        done, failed, stat, interrupted = s.run_round({}, [_task()], set(), set(), _args())
        assert done == {"PEK|CAN|1"}
        assert failed == set()
        assert stat["ok"] == 1
        data = s.load_progress()
        assert data["done"] == ["PEK|CAN|1"]
        s.PROGRESS_FILE = orig