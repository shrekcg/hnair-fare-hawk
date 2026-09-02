"""分时段随机调度策略。"""

from __future__ import annotations

import random
from datetime import datetime


def get_next_interval_seconds(now: datetime | None = None, polling: dict | None = None) -> int:
    """
    根据当前时间返回下一轮查询间隔（秒）。

    - 07:00-23:00：90-240 秒
    - 23:00-07:00：300-600 秒
    """
    current = now or datetime.now()
    hour = current.hour

    polling_cfg = polling or {}
    day_min = int(polling_cfg.get("day_min_sec", 90))
    day_max = int(polling_cfg.get("day_max_sec", 240))
    night_min = int(polling_cfg.get("night_min_sec", 300))
    night_max = int(polling_cfg.get("night_max_sec", 600))

    if 7 <= hour < 23:
        return random.randint(day_min, day_max)

    return random.randint(night_min, night_max)
