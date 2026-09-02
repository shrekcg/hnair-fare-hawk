"""第三轮测试：机场对齐(AirportIndex) 与 复核(review export/apply + build 应用裁决)。
样本内嵌，不依赖 参考资料/。
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sedimentation"))

from models import Flight
from city_align import AirportIndex
import build_normalized as bn
import review


def _write_airports(tmp_path) -> Path:
    p = tmp_path / "CN271.csv"
    p.write_text(
        "简名(城市/机场名),全名,IATA,ICAO,坐标\n"
        "昆明/长水,昆明长水国际机场,KMG,ZPPP,\"1,2\"\n"
        "北京/首都,北京首都国际机场,PEK,ZBAA,\"1,2\"\n"
        "北京/大兴,北京大兴国际机场,PKX,ZBAD,\"1,2\"\n"
        "海口/美兰,海口美兰国际机场,HAK,ZJHK,\"1,2\"\n",
        encoding="utf-8",
    )
    return p


class TestAirportIndex:
    def _idx(self, tmp_path):
        return AirportIndex(_write_airports(tmp_path))

    def test_exact(self, tmp_path):
        idx = self._idx(tmp_path)
        assert idx.lookup("昆明", "长水") == ("KMG", False)
        assert idx.lookup("北京", "首都") == ("PEK", False)
        assert idx.lookup("北京", "大兴") == ("PKX", False)

    def test_variant_full_name(self, tmp_path):
        idx = self._idx(tmp_path)
        assert idx.lookup("北京", "首都国际机场") == ("PEK", False)

    def test_single_airport_city(self, tmp_path):
        idx = self._idx(tmp_path)
        assert idx.lookup("海口", "") == ("HAK", False)

    def test_multi_airport_unknown(self, tmp_path):
        idx = self._idx(tmp_path)
        assert idx.lookup("北京", "") == ("", True)

    def test_unknown_city(self, tmp_path):
        idx = self._idx(tmp_path)
        assert idx.lookup("不存在市", "") == ("", False)


def _f(source, product, fl_no, o, d, dep="20:00", arr="22:00", days=(1, 2, 3),
       iata_o="", iata_d="", airport_o="", airport_d=""):
    return Flight(
        source=source, product=product, carrier="海南航空", flight_no=fl_no,
        origin_city=o, origin_airport=airport_o or f"{o}机场", origin_iata=iata_o,
        dest_city=d, dest_airport=airport_d or f"{d}机场", dest_iata=iata_d,
        dep_time=dep, arr_time=arr, days=list(days),
        date_ranges=[["2026-09-01", "2026-10-24"]],
    )


class TestReviewExportApply:
    def _normalized(self, tmp_path):
        payload = {
            "records": [
                {"flight_no": "A1001", "origin": {"city": "昆明", "airport": "长水", "iata": "KMG"},
                 "dest": {"city": "北京", "airport": "首都", "iata": "PEK"},
                 "dep_time": "19:30", "arr_time": "22:00", "days": [1, 2, 3],
                 "csv_dates": [["2026-09-01", "2026-10-24"]], "hna_dates": [["2026-09-02", "2026-10-24"]],
                 "csv_products": ["2666"], "hna_products": ["666", "2666"],
                 "product": "666/2666", "product_conflict": True, "notes": None},
                {"flight_no": "B2002", "origin": {"city": "海口", "airport": "美兰", "iata": "HAK"},
                 "dest": {"city": "广州", "airport": "", "iata": "CAN"},
                 "dep_time": "08:30", "arr_time": "10:00", "days": [1],
                 "csv_dates": [["2026-09-01", "2026-09-26"]], "hna_dates": [["2026-09-29", "2026-10-24"]],
                 "csv_products": ["666", "2666"], "hna_products": ["2666"],
                 "product": "666/2666", "product_conflict": True, "notes": "9.28始"},
            ]
        }
        p = tmp_path / "flights_normalized.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    def _patch_paths(self, tmp_path):
        review.NORMALIZED = tmp_path / "flights_normalized.json"
        review.REVIEW_DIR = tmp_path / "review"
        review.DECISIONS = tmp_path / "review_decisions.json"

    def test_export_apply_flow(self, tmp_path):
        self._patch_paths(tmp_path)
        self._normalized(tmp_path)
        review.export()
        csv_path = review.REVIEW_DIR / "conflicts.csv"
        assert csv_path.exists()
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
        assert len(rows) == 2
        assert rows[0]["suggestion"].startswith("非 666 时段")  # 19:30
        # 用户填 decision
        rows[0]["decision"] = "2666"
        rows[1]["decision"] = "both"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        review.apply()
        decisions = review.load_decisions()
        assert decisions == {"A1001|昆明|北京": "2666", "B2002|海口|广州": "both"}

    def test_apply_invalid(self, tmp_path):
        self._patch_paths(tmp_path)
        self._normalized(tmp_path)
        review.export()
        csv_path = review.REVIEW_DIR / "conflicts.csv"
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
        rows[0]["decision"] = "都行"  # 非法
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        assert review.apply() == 0
        decisions = review.load_decisions()
        assert "A1001|昆明|北京" not in decisions
        assert decisions == {}


class TestBuildWithDecisions:
    def test_review_overrides(self, tmp_path):
        airports = AirportIndex(_write_airports(tmp_path))
        csv_flights = [_f("sxfroute", "2666", "A1001", "昆明", "北京",
                          dep="19:30", iata_o="KMG", iata_d="PEK")]
        hna = {"666fms.html": [_f("hna666", "666", "A1001", "昆明", "北京")],
               "2666fms.html": [_f("hna666", "2666", "A1001", "昆明", "北京")],
               "66666.html": []}
        # 无裁决：冲突保留，并集
        recs = bn.build(csv_flights, hna, decisions={}, airports=airports)
        assert recs[0]["product"] == "666/2666"
        assert recs[0]["product_conflict"] is True
        # 裁决 2666：冲突清除，product 改为 2666
        recs = bn.build(csv_flights, hna,
                        decisions={"A1001|昆明|北京": "2666"}, airports=airports)
        r = recs[0]
        assert r["product"] == "2666"
        assert r["product_conflict"] is False
        assert r["review_decision"] == "2666"

    def test_hna_iata_filled(self, tmp_path):
        airports = AirportIndex(_write_airports(tmp_path))
        hna = {"666fms.html": [_f("hna666", "666", "A1002", "昆明", "北京",
                                  airport_o="长水", airport_d="首都")],
               "2666fms.html": [], "66666.html": []}
        recs = bn.build([], hna, decisions={}, airports=airports)
        r = recs[0]
        assert r["origin"]["iata"] == "KMG"
        assert r["dest"]["iata"] == "PEK"

    def test_multi_airport_city_in_hna(self, tmp_path):
        airports = AirportIndex(_write_airports(tmp_path))
        hna = {"666fms.html": [_f("hna666", "666", "A1003", "北京", "海口",
                                  iata_o="", iata_d="")],
               "2666fms.html": [], "66666.html": []}
        # 北京多机场但机场名缺失无法唯一 → origin iata 留空（歧义）
        recs = bn.build([], hna, decisions={}, airports=airports)
        assert recs[0]["origin"]["iata"] == ""
        assert recs[0]["dest"]["iata"] == "HAK"