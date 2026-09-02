"""数据沉淀第三阶段测试：更新机制（update.py 的自动裁决规则与日期区间解析）。"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sedimentation"))

from update import (
    auto_decide_conflicts,
    decide,
    has_product,
    parse_ranges,
    ranges_overlap,
)


class TestParseRanges:
    def test_range_cross_month(self):
        assert parse_ranges("09-01~10-24") == [((9, 1), (10, 24))]

    def test_ranges_multi_segment_and_single_day(self):
        assert parse_ranges("09-01~09-26 09-29~10-24") == [
            ((9, 1), (9, 26)), ((9, 29), (10, 24)),
        ]
        assert parse_ranges("09-03") == [((9, 3), (9, 3))]

    def test_overlap_positive(self):
        assert ranges_overlap("09-01~09-26 09-29~10-24", "09-29~10-24")
        assert ranges_overlap("09-03", "09-03")

    def test_overlap_negative(self):
        assert not ranges_overlap("09-01~09-26", "09-29~10-24")


class TestDecide:
    def test_hna_has_666_is_both(self):
        assert decide({"hna_products": "2666/666", "csv_products": "2666",
                       "csv_dates": "09-01~10-24", "hna_dates": "09-02~10-24"}) == "both"

    def test_b_same_period_2666(self):
        assert decide({"hna_products": "2666", "csv_products": "2666/666",
                       "csv_dates": "09-01~10-24", "hna_dates": "09-02~10-24"}) == "2666"

    def test_b_no_overlap_both(self):
        assert decide({"hna_products": "2666", "csv_products": "2666/666",
                       "csv_dates": "09-01~09-26", "hna_dates": "09-29~10-24"}) == "both"

    def test_plain_2666(self):
        assert decide({"hna_products": "2666", "csv_products": "2666",
                       "csv_dates": "09-01~10-24", "hna_dates": "09-02~10-24"}) == "2666"


class TestHasProduct:
    def test_partial_match_not_confused(self):
        assert not has_product("2666", "666")  # "666" in "2666" 是常见坑
        assert has_product("666/2666", "666")
        assert has_product("2666/666", "2666")


class TestAutoDecideConflicts:
    def test_fills_decision_column(self, tmp_path):
        p = tmp_path / "conflicts.csv"
        p.write_text(
            "flight_no,origin,dest,csv_products,hna_products,csv_dates,hna_dates,decision\n"
            "A1,北京,上海,2666,2666/666,09-01~10-24,09-02~10-24,\n"
            "B1,广州,深圳,2666/666,2666,09-01~10-24,09-02~10-24,\n",
            encoding="utf-8-sig")
        out = auto_decide_conflicts(p)
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        assert rows[0]["decision"] == "both"
        assert rows[1]["decision"] == "2666"
        assert out == {"A1|北京|上海": "both", "B1|广州|深圳": "2666"}

    def test_keeps_existing_decision(self, tmp_path):
        p = tmp_path / "conflicts.csv"
        p.write_text(
            "flight_no,origin,dest,csv_products,hna_products,csv_dates,hna_dates,decision\n"
            "A1,北京,上海,2666,2666/666,09-01~10-24,09-02~10-24,both\n",
            encoding="utf-8-sig")
        out = auto_decide_conflicts(p)
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        assert rows[0]["decision"] == "both"
        assert out == {}