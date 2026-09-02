"""城市/机场中文名与三字码映射。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class AirportEntry:
    city: str
    airport: str
    code: str
    aliases: tuple[str, ...]


AIRPORTS: List[AirportEntry] = [
    AirportEntry("北京", "首都", "PEK", ("北京", "北京首都", "首都机场", "首都")),
    AirportEntry("北京", "大兴", "PKX", ("北京大兴", "大兴机场", "大兴")),
    AirportEntry("上海", "浦东", "PVG", ("上海", "上海浦东", "浦东机场", "浦东")),
    AirportEntry("上海", "虹桥", "SHA", ("上海虹桥", "虹桥机场", "虹桥")),
    AirportEntry("广州", "白云", "CAN", ("广州", "广州白云", "白云机场", "白云")),
    AirportEntry("深圳", "宝安", "SZX", ("深圳", "深圳宝安", "宝安机场", "宝安")),
    AirportEntry("乌鲁木齐", "地窝堡", "URC", ("乌鲁木齐", "地窝堡", "乌鲁木齐地窝堡")),
    AirportEntry("海口", "美兰", "HAK", ("海口", "海口美兰", "美兰机场", "美兰")),
    AirportEntry("三亚", "凤凰", "SYX", ("三亚", "三亚凤凰", "凤凰机场", "凤凰")),
    AirportEntry("重庆", "江北", "CKG", ("重庆", "重庆江北", "江北机场", "江北")),
    AirportEntry("成都", "双流", "CTU", ("成都", "成都双流", "双流机场", "双流")),
    AirportEntry("成都", "天府", "TFU", ("成都天府", "天府机场", "天府")),
    AirportEntry("西安", "咸阳", "XIY", ("西安", "咸阳机场", "西安咸阳", "咸阳")),
    AirportEntry("昆明", "长水", "KMG", ("昆明", "昆明长水", "长水机场", "长水")),
    AirportEntry("杭州", "萧山", "HGH", ("杭州", "杭州萧山", "萧山机场", "萧山")),
    AirportEntry("南京", "禄口", "NKG", ("南京", "南京禄口", "禄口机场", "禄口")),
    AirportEntry("武汉", "天河", "WUH", ("武汉", "武汉天河", "天河机场", "天河")),
    AirportEntry("长沙", "黄花", "CSX", ("长沙", "长沙黄花", "黄花机场", "黄花")),
    AirportEntry("郑州", "新郑", "CGO", ("郑州", "郑州新郑", "新郑机场", "新郑")),
    AirportEntry("青岛", "胶东", "TAO", ("青岛", "青岛胶东", "胶东机场", "胶东")),
    AirportEntry("厦门", "高崎", "XMN", ("厦门", "厦门高崎", "高崎机场", "高崎")),
    AirportEntry("福州", "长乐", "FOC", ("福州", "福州长乐", "长乐机场", "长乐")),
    AirportEntry("济南", "遥墙", "TNA", ("济南", "济南遥墙", "遥墙机场", "遥墙")),
    AirportEntry("天津", "滨海", "TSN", ("天津", "天津滨海", "滨海机场", "滨海")),
    AirportEntry("大连", "周水子", "DLC", ("大连", "大连周水子", "周水子机场", "周水子")),
    AirportEntry("沈阳", "桃仙", "SHE", ("沈阳", "沈阳桃仙", "桃仙机场", "桃仙")),
    AirportEntry("哈尔滨", "太平", "HRB", ("哈尔滨", "哈尔滨太平", "太平机场", "太平")),
    AirportEntry("长春", "龙嘉", "CGQ", ("长春", "长春龙嘉", "龙嘉机场", "龙嘉")),
    AirportEntry("南昌", "昌北", "KHN", ("南昌", "南昌昌北", "昌北机场", "昌北")),
    AirportEntry("南宁", "吴圩", "NNG", ("南宁", "南宁吴圩", "吴圩机场", "吴圩")),
    AirportEntry("贵阳", "龙洞堡", "KWE", ("贵阳", "贵阳龙洞堡", "龙洞堡机场", "龙洞堡")),
    AirportEntry("兰州", "中川", "LHW", ("兰州", "兰州中川", "中川机场", "中川")),
    AirportEntry("银川", "河东", "INC", ("银川", "银川河东", "河东机场", "河东")),
    AirportEntry("呼和浩特", "白塔", "HET", ("呼和浩特", "呼和浩特白塔", "白塔机场", "白塔")),
    AirportEntry("拉萨", "贡嘎", "LXA", ("拉萨", "拉萨贡嘎", "贡嘎机场", "贡嘎")),
    AirportEntry("桂林", "两江", "KWL", ("桂林", "桂林两江", "两江机场", "两江")),
    AirportEntry("宁波", "栎社", "NGB", ("宁波", "宁波栎社", "栎社机场", "栎社")),
    AirportEntry("珠海", "金湾", "ZUH", ("珠海", "珠海金湾", "金湾机场", "金湾")),
    AirportEntry("温州", "龙湾", "WNZ", ("温州", "温州龙湾", "龙湾机场", "龙湾")),
    AirportEntry("合肥", "新桥", "HFE", ("合肥", "合肥新桥", "新桥机场", "新桥")),
]


def _norm(text: str) -> str:
    return text.strip().replace("机场", "").replace(" ", "").lower()


def city_options() -> list[str]:
    """用于前端提示的选项列表。"""
    return [f"{a.city}{a.airport}（{a.code}）" for a in AIRPORTS]


def code_to_city_label(code: str) -> str:
    """三字码转中文展示，如：SZX -> 深圳宝安（SZX）。"""
    target = code.strip().upper()
    for a in AIRPORTS:
        if a.code == target:
            return f"{a.city}{a.airport}（{a.code}）"
    return target


def code_to_city_only(code: str) -> str:
    """三字码转纯城市名，如：SZX -> 深圳。"""
    target = code.strip().upper()
    for a in AIRPORTS:
        if a.code == target:
            return a.city
    return target


def resolve_code(raw: str) -> tuple[str | None, list[str]]:
    """
    将用户输入（城市名/机场名/三字码）解析为三字码。

    返回：
    - code: 解析成功时返回三字码
    - tips: 失败或歧义时返回候选项
    """
    text = raw.strip()
    if not text:
        return None, []

    # 允许直接输入任意合法 IATA 三字码，未收录机场也能创建监控任务。
    upper = text.upper()
    code_set = {a.code for a in AIRPORTS}
    if len(upper) == 3 and upper.isascii() and upper.isalpha():
        return upper, []

    n = _norm(text)

    exact = []
    fuzzy = []
    for a in AIRPORTS:
        aliases = {_norm(a.city), _norm(a.airport), _norm(a.city + a.airport), _norm(a.code)}
        aliases.update({_norm(x) for x in a.aliases})
        if n in aliases:
            exact.append(a)
            continue

        # 包含匹配：支持“乌鲁木齐地窝堡”“北京大兴”等模糊输入。
        if any(n in alias or alias in n for alias in aliases if alias):
            fuzzy.append(a)

    if len(exact) == 1:
        return exact[0].code, []

    if len(exact) > 1:
        return None, [f"{a.city}{a.airport}（{a.code}）" for a in exact]

    if len(fuzzy) == 1:
        return fuzzy[0].code, []

    if len(fuzzy) > 1:
        return None, [f"{a.city}{a.airport}（{a.code}）" for a in fuzzy[:8]]

    return None, city_options()[:10]
