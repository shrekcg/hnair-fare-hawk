"""数据沉淀第二阶段测试：规范化合并与查询 CLI（样本内嵌，不依赖 参考资料/）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sedimentation"))

from models import Flight
from build_normalized import (
    parse_csv_notes, intersect_ranges, is_date_in_ranges, build,
)
from query import query, matches_product, matches_date


class TestParseCsvNotes:
    def test_single_only(self):
        assert parse_csv_notes("仅9.9") == [["2026-09-09", "2026-09-09"]]

    def test_multi_only(self):
        assert parse_csv_notes("仅9.2 9.3") == [
            ["2026-09-02", "2026-09-02"], ["2026-09-03", "2026-09-03"],
        ]

    def test_start(self):
        assert parse_csv_notes("9.28始") == [["2026-09-28", "9999-12-31"]]

    def test_end(self):
        assert parse_csv_notes("10.8止") == [["0000-01-01", "2026-10-08"]]

    def test_range(self):
        assert parse_csv_notes("9.28~10.9") == [["2026-09-28", "2026-10-09"]]

    def test_none(self):
        assert parse_csv_notes("") is None
        assert parse_csv_notes(None) is None

    def test_cross_year(self):
        # 2027-01-05 跨年
        assert parse_csv_notes("1.5始") == [["2027-01-05", "9999-12-31"]]


class TestIntersect:
    def test_start_patch(self):
        base = [["2026-09-01", "2026-10-24"]]
        assert intersect_ranges(base, [["2026-09-28", "9999-12-31"]]) \
            == [["2026-09-28", "2026-10-24"]]

    def test_end_patch(self):
        base = [["2026-09-01", "2026-10-24"]]
        assert intersect_ranges(base, [["0000-01-01", "2026-10-08"]]) \
            == [["2026-09-01", "2026-10-08"]]

    def test_merge_overlap(self):
        base = [["2026-09-01", "2026-10-24"]]
        assert intersect_ranges(base, [["2026-09-10", "9999-12-31"], ["2026-09-20", "9999-12-31"]]) \
            == [["2026-09-10", "2026-10-24"]]

    def test_in_ranges(self):
        assert is_date_in_ranges("2026-09-15", [["2026-09-10", "2026-09-20"]])
        assert not is_date_in_ranges("2026-09-25", [["2026-09-10", "2026-09-20"]])


def _f(source, product, fl_no, o, d, dep="20:00", arr="22:00", days=(1, 2, 3, 4, 5, 6, 7),
       dates=None, notes=None):
    return Flight(
        source=source, product=product, carrier="海南航空", flight_no=fl_no,
        origin_city=o, origin_airport=f"{o}机场", origin_iata="" if source == "hna666" else "XXX",
        dest_city=d, dest_airport=f"{d}机场", dest_iata="",
        dep_time=dep, arr_time=arr, days=list(days),
        date_ranges=dates or [["2026-09-01", "2026-10-24"]],
        notes=notes,
        raw={},
    )


class TestBuild:
    def test_union_product_and_conflict(self):
        csv = [_f("sxfroute", "2666", "HU7000", "海口", "广州")]
        hna = {
            "666fms.html": [_f("hna666", "666", "HU7000", "海口", "广州")],
            "2666fms.html": [_f("hna666", "2666", "HU7000", "海口", "广州")],
            "66666.html": [],
        }
        recs = build(csv, hna)
        assert len(recs) == 1
        r = recs[0]
        assert r["product"] == "666/2666"
        assert r["product_conflict"] is True          # csv={2666} vs hna={666,2666}
        assert r["source"] == "both"

    def test_effective_dates_prefers_hna(self):
        csv = [_f("sxfroute", "666/2666", "HU7000", "海口", "广州",
                  dates=[["2026-09-01", "2026-10-24"]])]
        hna = {
            "666fms.html": [_f("hna666", "666", "HU7000", "海口", "广州",
                               dates=[["2026-10-01", "2026-10-24"]])],
            "2666fms.html": [], "66666.html": [],
        }
        recs = build(csv, hna)
        assert recs[0]["effective_dates"] == [["2026-10-01", "2026-10-24"]]
        assert recs[0]["csv_dates"] == [["2026-09-01", "2026-10-24"]]

    def test_csv_note_patch_when_no_hna(self):
        csv = [_f("sxfroute", "2666", "HU7001", "海口", "广州",
                  dates=[["2026-09-01", "2026-10-24"]], notes="9.28始")]
        hna = {"666fms.html": [], "2666fms.html": [], "66666.html": []}
        recs = build(csv, hna)
        r = recs[0]
        assert r["effective_dates"] == [["2026-09-28", "2026-10-24"]]
        assert r["source"] == "csv"

    def test_only_hna_source(self):
        csv = []
        hna = {
            "666fms.html": [_f("hna666", "666", "HU7002", "海口", "三亚")],
            "2666fms.html": [], "66666.html": [],
        }
        recs = build(csv, hna)
        assert len(recs) == 1
        assert recs[0]["source"] == "hna"
        assert recs[0]["product"] == "666"


class TestQuery:
    def setup_method(self):
        self.cases = [
            _f("sxfroute", "666/2666", "HU7000", "海口", "广州", dep="20:00", arr="22:00",
               days=(1, 3, 5), dates=[["2026-09-01", "2026-10-24"]]),
            _f("sxfroute", "2666", "HU7101", "深圳", "海口", dep="19:30", arr="21:00",
               days=(2, 4, 6), dates=[["2026-09-01", "2026-10-24"]]),
            _f("hna666", "666", "HU7202", "海口", "北京", dep="21:00", arr="23:50",
               days=(1, 2, 3, 4, 5, 6, 7), dates=[["2026-10-01", "2026-10-24"]]),
        ]
        self.records = build(self.cases, {"666fms.html": [], "2666fms.html": [], "66666.html": []})

    def _patch(self, tmp_path):
        import query as q
        q.NORMALIZED = tmp_path / "flights_normalized.json"
        q.NORMALIZED.write_text(
            json.dumps({"records": self.records}, ensure_ascii=False), encoding="utf-8")
        return q

    def test_city_both_directions(self, tmp_path):
        q = self._patch(tmp_path)
        rows = q.query("海口")
        cities = {("出" if r["origin"]["city"] == "海口" else "入") for r in rows}
        assert rows and "出" in cities and "入" in cities
        assert len(rows) == 3  # HU7000 出 + HU7101 入 + HU7202 出

    def test_product_filter(self, tmp_path):
        q = self._patch(tmp_path)
        rows = q.query("海口", product="2666")
        assert rows and all("2666" in r["product"].split("/") for r in rows)

    def test_date_filter(self, tmp_path):
        self._patch(tmp_path)
        recs = build(self.cases, {"666fms.html": [], "2666fms.html": [], "66666.html": []})
        # HU7202 海口→北京：10-01 在 HNA666 日期内；9-15 不在
        assert matches_date(recs[2], "2026-10-01") is True
        assert matches_date(recs[2], "2026-09-15") is False
        # HU7000 班期(1,3,5)=周一三五；2026-09-02 是周三(3) → 飞；09-03 是周四(4) → 不飞
        assert matches_date(recs[0], "2026-09-02") is True
        assert matches_date(recs[0], "2026-09-03") is False

    def test_direction_filter(self, tmp_path):
        q = self._patch(tmp_path)
        rows = q.query("海口", direction="depart")
        assert rows and all(r["origin"]["city"] == "海口" for r in rows)

    def test_matches_product(self):
        assert matches_product({"product": "666/2666"}, None) is True
        assert matches_product({"product": "666/2666"}, "2666") is True
        assert matches_product({"product": "2666"}, "666") is False