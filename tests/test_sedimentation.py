"""数据沉淀模块测试：解析器与对比器（样本内嵌，不依赖 参考资料/ 目录）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "sedimentation"))

from models import Flight, canonical_key
from load_sxfroute import parse_week, load_csv
from load_hna666 import parse_date_range, load_html
from compare import compare, product_set


class TestParseWeek:
    def test_normal(self):
        assert parse_week("246") == [2, 4, 6]
        assert parse_week("13457") == [1, 3, 4, 5, 7]

    def test_unsorted_dedup(self):
        assert parse_week("7711") == [1, 7]

    def test_empty(self):
        assert parse_week("") == []


class TestLoadCsv:
    def test_full_row(self, tmp_path):
        p = tmp_path / "airport.csv"
        p.write_text(
            "航班号,出港城市,到港城市,出发时刻,班期,产品,航空公司,起飞城市所属省/市,降落城市所属省/市,"
            "起飞城市机场名称,降落城市机场名称,起飞机场IATA代码,降落机场IATA代码,降落时刻,备注,有效开始日期,"
            "有效结束日期,指定日期,来源行\n"
            "GX8855,南宁,成都,19:15,246,2666,北部湾航空,广西,四川,南宁吴圩国际机场,成都天府国际机场,"
            "NNG,TFU,20:55,,2026-09-01,2026-10-24,,2\n"
            "HU7000,海口,广州,20:00,1357,666/2666,海南航空,海南,广东,海口美兰国际机场,广州白云国际机场,"
            "HAK,CAN,22:10,仅9.9,2026-09-01,2026-10-24,,",
            encoding="utf-8",
        )
        flights = load_csv(p)
        assert len(flights) == 2
        f = flights[0]
        assert f.product == "2666"
        assert f.origin_city == "南宁" and f.origin_iata == "NNG"
        assert f.dep_time == "19:15" and f.arr_time == "20:55"
        assert f.days == [2, 4, 6]
        assert f.date_ranges == [["2026-09-01", "2026-10-24"]]
        assert f.notes is None
        assert flights[1].product == "666/2666"
        assert flights[1].notes == "仅9.9"

    def test_unknown_product_default(self, tmp_path):
        p = tmp_path / "airport.csv"
        p.write_text(
            "航班号,产品,出港城市,到港城市,出发时刻,班期,航空公司,起飞城市所属省/市,降落城市所属省/市,"
            "起飞城市机场名称,降落城市机场名称,起飞机场IATA代码,降落机场IATA代码,降落时刻,备注,有效开始日期,"
            "有效结束日期,指定日期,来源行\n"
            "HU7001,X,海口,广州,20:00,1,海南航空,海南,广东,a,b,HAK,CAN,22:00,,,,2\n",
            encoding="utf-8",
        )
        flights = load_csv(p)
        assert flights[0].product == "666/2666"


class TestParseDateRange:
    def test_single(self):
        assert parse_date_range("2026/10/01-2026/10/24") == [["2026-10-01", "2026-10-24"]]

    def test_multi(self):
        assert parse_date_range("2026/09/02-2026/09/02 & 2026/09/13-2026/09/27") == [
            ["2026-09-02", "2026-09-02"],
            ["2026-09-13", "2026-09-27"],
        ]

    def test_non_zero_padded(self):
        assert parse_date_range("2026/9/2-2026/10/24") == [["2026-09-02", "2026-10-24"]]


class TestLoadHna666:
    HTML = """<!doctype html><html><body><script>
    const flights=[
      {"airline":"祥鹏航空","flight_number":"8L9501","days":[1,2,3,4,5,6,7],
       "date_range":"2026/09/02-2026/09/02 & 2026/09/13-2026/09/27",
       "route":["昆明/长水","郑州/新郑"],
       "ticketable_segments":["昆明/长水-郑州/新郑"],
       "time_details":{"昆明/长水-郑州/新郑":{"dep":"21:15","dep_note":null,"arr":"23:55","arr_note":null}},
       "aircraft_type":"B738","aircraft_size":"中型","is_morning":false,"is_early_departure":false,
       "is_late_arrival":true,"time_category":"evening"},
      {"airline":"海南航空","flight_number":"HU7002","days":[3,5],
       "date_range":"2026/09/01-2026/10/24",
       "route":["海口","广州","深圳"],
       "ticketable_segments":["海口-广州","广州-深圳"],
       "time_details":{"海口-广州":{"dep":"20:10","dep_note":null,"arr":"21:30","arr_note":null},
                       "广州-深圳":{"dep":"21:50","dep_note":null,"arr":"22:30","arr_note":null}},
       "aircraft_type":"A320","aircraft_size":"中型","is_morning":false,"is_early_departure":false,
       "is_late_arrival":true,"time_category":"evening"}
    ];
    </script></body></html>"""

    def test_load_and_split(self, tmp_path):
        p = tmp_path / "666fms.html"
        p.write_text(self.HTML, encoding="utf-8")
        flights = load_html(p)
        assert len(flights) == 3  # 单段 1 条 + 多段拆 2 条
        f = flights[0]
        assert f.source == "hna666" and f.product == "666"
        assert f.flight_no == "8L9501"
        assert f.origin_city == "昆明" and f.origin_airport == "长水"
        assert f.dest_city == "郑州" and f.dest_airport == "新郑"
        assert f.dep_time == "21:15" and f.arr_time == "23:55"
        assert f.days == [1, 2, 3, 4, 5, 6, 7]
        assert f.date_ranges == [["2026-09-02", "2026-09-02"], ["2026-09-13", "2026-09-27"]]

    def test_product_by_filename(self, tmp_path):
        p = tmp_path / "2666fms.html"
        p.write_text(self.HTML, encoding="utf-8")
        flights = load_html(p)
        assert flights[0].product == "2666"

    def test_no_json_raises(self, tmp_path):
        p = tmp_path / "666fms.html"
        p.write_text("<html></html>", encoding="utf-8")
        try:
            load_html(p)
            assert False, "应抛出 ValueError"
        except ValueError:
            pass


class TestProductSet:
    def test_values(self):
        assert product_set("666/2666") == {"666", "2666"}
        assert product_set("2666") == {"2666"}


class TestCompare:
    def _csv_flight(self, product, dep="21:15", arr="23:55", days=(1, 2, 3), flight_no="8L9501", **kw):
        return Flight(
            source="sxfroute", product=product, carrier="祥鹏航空", flight_no=flight_no,
            origin_city="昆明", origin_airport="昆明长水国际机场", origin_iata="KMG",
            dest_city="郑州", dest_airport="郑州新郑国际机场", dest_iata="CGO",
            dep_time=dep, arr_time=arr, days=list(days),
            date_ranges=[["2026-09-01", "2026-10-24"]], **kw,
        )

    def _hna_flight(self, product, dep="21:15", arr="23:55", days=(1, 2, 3), flight_no="8L9501"):
        return Flight(
            source="hna666", product=product, carrier="祥鹏航空", flight_no=flight_no,
            origin_city="昆明", origin_airport="长水", origin_iata="",
            dest_city="郑州", dest_airport="新郑", dest_iata="",
            dep_time=dep, arr_time=arr, days=list(days),
            date_ranges=[["2026-09-02", "2026-09-27"]],
        )

    def test_consistent_key_and_product(self):
        csv = [self._csv_flight("666/2666")]
        hna = {"666fms.html": [self._hna_flight("666")],
               "2666fms.html": [self._hna_flight("2666")], "66666.html": []}
        rep = compare(csv, hna)
        assert rep["key_cross"]["common_keys"] == 1
        assert rep["time_mismatch_count"] == 0
        assert rep["product_diff_count"] == 0
        assert rep["consistent"]["product_consistent_keys"] == 1

    def test_product_diff(self):
        # CSV 只标 2666；HNA666 在 666 档也有 → 产品差异
        csv = [self._csv_flight("2666")]
        hna = {"666fms.html": [self._hna_flight("666")],
               "2666fms.html": [self._hna_flight("2666")], "66666.html": []}
        rep = compare(csv, hna)
        assert rep["product_diff_count"] == 1
        d = rep["product_diffs"][0]
        assert d["csv"] == ["2666"] and set(d["hna"]) == {"666", "2666"}

    def test_time_mismatch(self):
        csv = [self._csv_flight("666/2666", dep="21:15", arr="23:50")]
        hna = {"666fms.html": [self._hna_flight("666")],
               "2666fms.html": [], "66666.html": []}
        rep = compare(csv, hna)
        assert rep["time_mismatch_count"] == 1

    def test_only_csv_key(self):
        csv = [self._csv_flight("666/2666", flight_no="HU9999")]
        hna = {"666fms.html": [self._hna_flight("666")],
               "2666fms.html": [], "66666.html": []}
        rep = compare(csv, hna)
        assert rep["key_cross"]["keys_only_csv"] == [("HU9999", "昆明", "郑州")]
        assert len(rep["key_cross"]["keys_only_hna_tier"]) == 1

    def test_only_hna_all(self):
        csv = []
        hna = {"666fms.html": [], "2666fms.html": [],
               "66666.html": [self._hna_flight("666/2666", flight_no="HU1111")]}
        rep = compare(csv, hna)
        assert rep["key_cross"]["keys_only_hna_all"] == [("HU1111", "昆明", "郑州")]


class TestCanonicalKey:
    def test_city_level_key(self):
        a = Flight("sxfroute", "666", "海南航空", "HU7000", "海口", "海口美兰国际机场", "HAK",
                   "广州", "广州白云国际机场", "CAN", "20:00", "22:00", [1], [["2026-09-01", "2026-10-24"]])
        b = Flight("hna666", "666", "海南航空", "HU7000", "海口", "美兰", "",
                   "广州", "白云", "", "20:00", "22:00", [1], [["2026-09-01", "2026-10-24"]])
        assert canonical_key(a) == canonical_key(b)