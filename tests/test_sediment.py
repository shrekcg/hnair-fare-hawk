"""backend/sediment.py 底表查询模块测试（只读，基于真实底表数据）。"""

import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import sediment  # noqa: E402
from city_codes import code_to_city_label, resolve_code  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def loaded():
    """确保每个测试共享一次加载（模块级缓存）。"""
    sediment.load_records(force=True)
    return sediment.load_records()


def test_records_loaded(loaded):
    assert len(loaded) >= 1700
    assert all({"flight_no", "carrier", "origin", "dest", "product", "days", "effective_dates"} <= set(r.keys()) for r in loaded[:10])


def test_matches_product_split_rule():
    """product 必须按 / 切分判成员：2666 记录不能匹配 666。"""
    rec_2666 = {"product": "2666"}
    rec_both = {"product": "666/2666"}
    rec_666 = {"product": "666"}
    assert sediment.matches_product(rec_666, "666") is True
    assert sediment.matches_product(rec_both, "666") is True
    assert sediment.matches_product(rec_2666, "666") is False  # 坑：直接 in 会误判
    assert sediment.matches_product(rec_2666, "2666") is True
    assert sediment.matches_product(rec_both, "2666") is True
    assert sediment.matches_product(rec_both, None) is True
    assert sediment.matches_product(rec_both, "all") is True


def test_matches_date_range_and_days():
    rec = {
        "effective_dates": [["2026-09-01", "2026-10-24"]],
        "days": [1, 3, 5],  # 周一三五
    }
    # 2026-10-03 是周六，不在班期
    assert sediment.matches_date(rec, "2026-10-03") is False
    # 2026-10-05 是周一，且在有效区间内
    assert sediment.matches_date(rec, "2026-10-05") is True
    # 区间外
    assert sediment.matches_date(rec, "2026-08-15") is False
    # 无日期不过滤
    assert sediment.matches_date(rec, "") is True
    # 非法日期
    assert sediment.matches_date(rec, "not-a-date") is False


def test_tier_block_rules_loaded(loaded):
    """档位×日期屏蔽规则文件加载：存在、档位齐全、十一屏蔽区间与 sxfroute 一致。"""
    rules = sediment.tier_block_rules()
    assert isinstance(rules, dict) and "tiers" in rules
    tiers = rules["tiers"]
    assert "666" in tiers and "2666" in tiers
    blocks666 = [b for b in tiers["666"]["blocks"]]
    assert ["2026-09-30", "2026-10-09"] in blocks666  # 2026 十一（国庆法定及前后各一天）
    assert ["2026-04-30", "2026-05-06"] in blocks666  # 2026 五一
    assert ["2026-07-01", "2026-08-31"] in blocks666  # 暑运
    blocks2666 = tiers["2666"]["blocks"]
    assert ["2026-09-30", "2026-10-09"] not in blocks2666  # 2666 不受国庆限制（sxfroute 同结论）
    # 缺失文件时的兜底：不抛错、返回空 tiers
    assert sediment.tier_blocks("不存在档位") == []
    assert sediment.product_tiers(None) == []
    assert sediment.product_tiers("all") == []
    assert sediment.product_tiers("666/2666") == ["666", "2666"]


def test_blocked_by_tier():
    """blocked_by_tier：单日/区间/多档位判断。"""
    assert sediment.blocked_by_tier("666", date_str="2026-10-03") is True   # 十一屏蔽内
    assert sediment.blocked_by_tier("666", date_str="2026-10-10") is False  # 屏蔽区间外
    assert sediment.blocked_by_tier("666", date_str="2026-09-29") is False  # 屏蔽区间前一日
    assert sediment.blocked_by_tier("2666", date_str="2026-10-03") is False  # 2666 不受国庆限制
    assert sediment.blocked_by_tier("666/2666", date_str="2026-10-03") is True  # 任一档被屏蔽即 True
    assert sediment.blocked_by_tier(None, date_str="2026-10-03") is False  # 不限档位不屏蔽
    assert sediment.blocked_by_tier("666", date_str="2026-07-15") is True  # 暑运（2666 也屏蔽）
    assert sediment.blocked_by_tier("2666", date_str="2026-07-15") is True
    # 区间：完全落在屏蔽区间内才屏蔽；部分重叠不屏蔽（由前端按具体日期收窄）
    assert sediment.blocked_by_tier("666", date_start="2026-10-01", date_end="2026-10-05") is True
    assert sediment.blocked_by_tier("666", date_start="2026-10-05", date_end="2026-10-15") is False
    assert sediment.blocked_by_tier("2666", date_start="2026-10-01", date_end="2026-10-05") is False
    # 2026-10-03 确实是周六（锚点换日依据）
    import datetime
    assert datetime.date(2026, 10, 3).isoweekday() == 6


def test_query_acceptance_anchor_haikou_666():
    """验收锚点：海口相关（出发或到达）+ 666 + 2026-10-10（周六，国庆屏蔽区间外）。

    2026-10-03 原锚点落在 666 档十一屏蔽区间（09-30~10-09）内，按真实规则该档该日不可兑，
    故锚点移到屏蔽区间外的周六 2026-10-10。当前底表（v1）约 102 条。"""
    rows = sediment.query(city="海口", product="666", date_str="2026-10-10", direction="both")
    assert len(rows) >= 100
    assert len(rows) <= 120
    # 每条都与海口相关
    for r in rows:
        assert r["origin"]["city"] == "海口" or r["dest"]["city"] == "海口"
    # 每条都匹配 666 档位 + 日期
    for r in rows:
        assert "666" in r["product"].split("/")
        assert sediment.matches_date(r, "2026-10-10")
    # 校验 2026-10-10 确实是周六
    import datetime
    assert datetime.date(2026, 10, 10).isoweekday() == 6


def test_query_tier_date_block():
    """档位×日期屏蔽：666 在十一屏蔽区间内查不到航班；2666/不限档不受影响。"""
    # 666 + 10-03（十一屏蔽内）→ 空
    assert sediment.query(city="海口", product="666", date_str="2026-10-03") == []
    # 666 + 10-10（屏蔽外）→ 有航班
    assert sediment.query(city="海口", product="666", date_str="2026-10-10")
    # 2666 + 10-03 → 有航班（2666 不受国庆限制）
    assert sediment.query(city="海口", product="2666", date_str="2026-10-03")
    # 666/2666 + 10-03 → 空（任一档被屏蔽即空，与前端日期池口径一致）
    assert sediment.query(city="海口", product="666/2666", date_str="2026-10-03") == []
    # 不限档位 + 10-03 → 有航班（屏蔽只看指定档位）
    assert sediment.query(city="海口", date_str="2026-10-03")
    # 区间完全落在屏蔽内 → 空；部分重叠 → 保留（前端再按具体日期收窄）
    assert sediment.query(city="海口", product="666", date_start="2026-10-01", date_end="2026-10-05") == []
    assert sediment.query(city="海口", product="666", date_start="2026-10-05", date_end="2026-10-15")


def test_meta_includes_tier_block_rules():
    """meta() 下发 tier_block_rules 字段（前端日期选择事实源）。"""
    m = sediment.meta()
    rules = m["tier_block_rules"]
    assert "source" in rules and "notes" in rules and "tiers" in rules
    assert [b for b in rules["tiers"]["666"]["blocks"]].count(["2026-09-30", "2026-10-09"]) == 1


def test_query_from_to_direction():
    # 海口出发 vs 到达 应互斥（用屏蔽区间外的周六，避免档位×日期屏蔽导致全空）
    depart = sediment.query(city="海口", product="666", date_str="2026-10-10", direction="depart")
    arrive = sediment.query(city="海口", product="666", date_str="2026-10-10", direction="arrive")
    both = sediment.query(city="海口", product="666", date_str="2026-10-10", direction="both")
    assert len(depart) + len(arrive) == len(both)
    for r in depart:
        assert r["origin"]["city"] == "海口"
    for r in arrive:
        assert r["dest"]["city"] == "海口"
    # from/to 精确过滤
    rows = sediment.query(from_city="海口", to_city="深圳")
    assert rows and all(r["origin"]["city"] == "海口" and r["dest"]["city"] == "深圳" for r in rows)


def test_query_accepts_iata_for_from_to():
    """出发/到达支持 IATA 三字码 / 机场名。"""
    rows = sediment.query(from_city="HAK", to_city="ZUH", product="666")
    assert rows
    for r in rows:
        assert r["origin"]["iata"] == "HAK" and r["dest"]["iata"] == "ZUH"
    # 机场名
    rows2 = sediment.query(from_city="海口美兰", product="2666")
    assert rows2 and all(r["origin"]["city"] == "海口" for r in rows2)


def test_query_flight_no_filter():
    rows = sediment.query(from_city="海口", flight_no="JD", limit=500)
    assert rows and all(r["flight_no"].startswith("JD") for r in rows)


def test_query_product_2666_does_not_include_666_only():
    rows = sediment.query(product="2666", limit=2000)
    assert rows
    for r in rows:
        assert "2666" in r["product"].split("/")


def test_meta_shape():
    m = sediment.meta()
    assert m["count"] == len(sediment.load_records())
    assert m["version"] and m["version"]["count"] == m["count"]
    assert "city_options" in m and len(m["city_options"]) > 0
    assert set(m["product_dist"].keys()) <= {"666", "2666", "666/2666"}
    assert "海口" in "".join(m["city_options"])


def test_city_codes_merged_with_sediment():
    """底表机场已并入 city_codes：丽江等底表城市可直接解析为三字码。"""
    code, tips = resolve_code("丽江")
    assert code == "LJG"
    # 三字码能显示中文标签
    assert "丽江" in code_to_city_label("LJG")
    # 原有基础城市仍正常
    assert resolve_code("深圳")[0] == "SZX"
    assert code_to_city_label("SZX") == "深圳宝安（SZX）"


def test_matches_date_in_range():
    """区间匹配：与 effective_dates 有交集且交集内有班期日即命中。"""
    rec = {"effective_dates": [["2026-09-01", "2026-10-24"]], "days": [1, 3, 5]}
    # 空区间不过滤
    assert sediment.matches_date_in_range(rec, "", "") is True
    # 2026-10-05 是周一（班期），区间命中
    import datetime
    assert datetime.date(2026, 10, 5).isoweekday() == 1
    assert sediment.matches_date_in_range(rec, "2026-10-03", "2026-10-10") is True
    # 区间完全在放票窗口外
    assert sediment.matches_date_in_range(rec, "2026-12-01", "2026-12-05") is False
    # 区间覆盖整周：只要班期非空必命中
    assert sediment.matches_date_in_range(rec, "2026-09-01", "2026-09-30") is True
    # 起点大于终点
    assert sediment.matches_date_in_range(rec, "2026-10-10", "2026-10-01") is False
    # 非法日期
    assert sediment.matches_date_in_range(rec, "not-a-date", "") is False


def test_query_date_range():
    """query 支持 date_start/date_end 区间（任意一天可飞即命中）。"""
    single = sediment.query(from_city="深圳", to_city="杭州", date_str="2026-10-15")
    ranged = sediment.query(from_city="深圳", to_city="杭州", date_start="2026-09-05", date_end="2026-09-12")
    assert single and ranged
    for r in ranged:
        assert any(
            a <= d <= b for a, b in (r.get("effective_dates") or []) for d in ("2026-09-05", "2026-09-12")
        )


def test_options_linkage():
    """出发/到达联动选项：以「当前可搜到的航线」两端城市为依据。"""
    # 深圳出发 → 到达下拉应有杭州（底表存在 SZX->HGH）
    opts = sediment.options(from_city="深圳")
    assert any(o.startswith("深圳（SZX）") for o in opts["from_options"])
    assert any(o.startswith("杭州（HGH）") for o in opts["to_options"])
    # 枚举格式含机场名（「城市（IATA）·机场」，机场名去「机场」后缀）
    szx = [o for o in opts["from_options"] if o.startswith("深圳（SZX）")]
    assert szx and "·" in szx[0] and "机场" not in szx[0].split("·", 1)[1]
    # 反向：到达杭州 → 出发下拉应有深圳
    opts2 = sediment.options(to_city="杭州")
    assert any(o.startswith("深圳（SZX）") for o in opts2["from_options"])
    assert any(o.startswith("杭州（HGH）") for o in opts2["to_options"])
    # 双向都选时，两端都收敛到该航线
    opts3 = sediment.options(from_city="深圳", to_city="杭州")
    assert all(o.startswith("深圳（SZX）") for o in opts3["from_options"])
    assert all(o.startswith("杭州（HGH）") for o in opts3["to_options"])
    # 档位过滤同样生效
    opts4 = sediment.options(from_city="深圳", to_city="杭州", product="2666")
    assert opts4["to_options"]  # 不抛错、非空即可（数据可能全 666 或双档）