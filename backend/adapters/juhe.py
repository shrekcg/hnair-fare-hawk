"""聚合数据（juhe.cn）「航班动态」适配器骨架。

状态（2026-09-03 实测）：
- 文档页 https://www.juhe.cn/docs/api/id/20 标注「维护中」，
  申请前需先联系客服确认是否仍可开通；
- 本模块为请求骨架：参数构造、错误处理、normalize 流程已就位，
  但**响应 JSON 的具体字段未用真实 key 实测**，`_extract` 里的字段名
  是从文档功能描述推演的占位，拿到 key 后必须实测校准，否则结果不可信。

用法（拿到 key 后）：
    from backend.adapters.juhe import JuheFlightAdapter
    adapter = JuheFlightAdapter(appkey="你的key")
    corrections = adapter.fetch("Y87531", date="2026-09-03",
                                origin="SZX", dest="CGQ")
    # 把 corrections 写入 data/sediment/third_party/*.json 后执行固化脚本
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from backend.third_party import normalize_correction

# 文档页给出的正式域名（未登录可见）；具体接口路径待 key 实测确认
BASE_URL = "https://apis.juhe.cn"
_DEFAULT_PATH = "/flight/line"  # 占位：实际路径以申请后文档页为准


class JuheFlightAdapter:
    """聚合数据航班动态查询适配器。

    构造参数：
      appkey: 聚合数据 AppKey（申请接口后获得）
      path:   接口路径（默认占位 /flight/line，拿到 key 后按文档校准）
      timeout: 请求超时秒数
    """

    def __init__(self, appkey: str, path: str = _DEFAULT_PATH, timeout: float = 10.0) -> None:
        if not appkey:
            raise ValueError("appkey 不能为空")
        self.appkey = appkey
        self.path = path
        self.timeout = timeout

    def _request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """发起 GET 请求并返回响应 JSON。错误统一抛 RuntimeError。"""
        import requests

        payload = dict(params)
        payload.setdefault("key", self.appkey)
        payload.setdefault("dtype", "json")
        url = BASE_URL + self.path
        resp = requests.get(url, params=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        # 聚合数据约定：error_code 为 0 表示成功，非 0 为业务错误
        err = data.get("error_code", 0)
        if err:
            raise RuntimeError(f"juhe error_code={err}: {data.get('reason')}")
        return data

    def fetch(
        self,
        flight_no: str,
        day: Optional[str] = None,
        origin: Optional[str] = None,
        dest: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询一条航班并产出一条（或若干条）corrections 数组项。

        参数：
          flight_no: 航班号，如 Y87531
          day:       出发日期 YYYY-MM-DD（可选，按文档决定是否必填）
          origin:    出发城市/机场（可选，按文档决定）
          dest:      到达城市/机场（可选，按文档决定）
        """
        if not flight_no:
            return []
        params: Dict[str, Any] = {"flight_no": flight_no}
        if day:
            params["date"] = day
        if origin:
            params["start_city"] = origin
        if dest:
            params["end_city"] = dest
        data = self._request(params)
        corrected = self._extract(flight_no, data)
        return [c for c in corrected if c]

    def _extract(self, flight_no: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把平台响应转成 corrections 项。

        ⚠️ 以下字段名为占位（根据文档功能描述推演），**未经真实 key 实测**。
        拿到 key 后按实际响应逐字段校准：把这里打印/核对真实 JSON 再固化。
        校准前，本函数返回空列表以**避免臆造数据污染底表**。
        """
        return []