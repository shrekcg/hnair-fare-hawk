"""运行状态与去重缓存管理。"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

import portalocker


class AlertStateManager:
    """用于管理低价提醒去重与 Token 告警节流。"""

    def __init__(
        self,
        state_path: str = "runtime_state.json",
        window_seconds: int = 24 * 3600,
        token_alert_cooldown_seconds: int = 30 * 60,
    ) -> None:
        self.state_path = state_path
        self.window_seconds = window_seconds
        self.token_alert_cooldown_seconds = token_alert_cooldown_seconds
        self._ensure_file()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "price_alerts": {},
            "token_alert": {"last_ts": 0},
            "task_backoff": {},
        }

    def _ensure_file(self) -> None:
        if os.path.exists(self.state_path):
            return

        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._default_state(), f, ensure_ascii=False, indent=2)

    def _load_state(self) -> Dict[str, Any]:
        # 使用文件锁防止并发读写冲突。
        with portalocker.Lock(self.state_path, mode="a+", timeout=5, encoding="utf-8") as f:
            f.seek(0)
            raw = f.read().strip()
            if not raw:
                data = self._default_state()
            else:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = self._default_state()

        if not isinstance(data, dict):
            data = self._default_state()
        if not isinstance(data.get("price_alerts"), dict):
            data["price_alerts"] = {}
        if not isinstance(data.get("token_alert"), dict):
            data["token_alert"] = {"last_ts": 0}

        return data

    def _save_state(self, data: Dict[str, Any]) -> None:
        with portalocker.Lock(self.state_path, mode="r+", timeout=5, encoding="utf-8") as f:
            f.seek(0)
            f.truncate()
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()

    def prune_old(self, now_ts: float | None = None) -> None:
        """清理超过滑动窗口的老记录。"""
        now = now_ts or time.time()
        data = self._load_state()

        cleaned: Dict[str, float] = {}
        for k, ts in data.get("price_alerts", {}).items():
            try:
                ts_val = float(ts)
            except (TypeError, ValueError):
                continue

            if now - ts_val <= self.window_seconds:
                cleaned[k] = ts_val

        data["price_alerts"] = cleaned
        self._save_state(data)

    def should_alert(self, fingerprint: str, now_ts: float | None = None) -> bool:
        """判断是否应发送低价提醒。"""
        now = now_ts or time.time()
        data = self._load_state()
        last_ts = data.get("price_alerts", {}).get(fingerprint)

        if last_ts is None:
            return True

        try:
            return now - float(last_ts) > self.window_seconds
        except (TypeError, ValueError):
            return True

    def mark_alert(self, fingerprint: str, now_ts: float | None = None) -> None:
        """记录一次低价提醒。"""
        now = now_ts or time.time()
        data = self._load_state()
        data.setdefault("price_alerts", {})[fingerprint] = now
        self._save_state(data)

    def should_send_token_alert(self, now_ts: float | None = None) -> bool:
        """Token 过期告警节流判断。"""
        now = now_ts or time.time()
        data = self._load_state()
        last_ts = data.get("token_alert", {}).get("last_ts", 0)

        try:
            last_ts_val = float(last_ts)
        except (TypeError, ValueError):
            return True

        return now - last_ts_val > self.token_alert_cooldown_seconds

    def mark_token_alert(self, now_ts: float | None = None) -> None:
        """记录最近一次 Token 告警时间。"""
        now = now_ts or time.time()
        data = self._load_state()
        data.setdefault("token_alert", {})["last_ts"] = now
        self._save_state(data)

    # ---------- 任务级失败退避（持久化到 runtime_state.json） ----------

    @staticmethod
    def _backoff_delay_seconds(fail_count: int, base_minutes: int = 5, max_minutes: int = 60) -> int:
        """指数退避：5min → 10 → 20 → 40 → 60（封顶 60min）。"""
        delay = base_minutes * (2 ** max(0, fail_count - 1))
        return min(delay, max_minutes) * 60

    def get_task_backoff(self, task_id: str) -> tuple[int, float]:
        """返回 (连续失败次数, next_ok_ts)。"""
        data = self._load_state()
        info = (data.get("task_backoff") or {}).get(task_id) or {}
        try:
            fail_count = int(info.get("fail_count", 0))
        except (TypeError, ValueError):
            fail_count = 0
        try:
            next_ok_ts = float(info.get("next_ok_ts", 0))
        except (TypeError, ValueError):
            next_ok_ts = 0
        return fail_count, next_ok_ts

    def mark_task_failure(self, task_id: str) -> None:
        """记录一次失败并延长该任务退避窗口。"""
        fail_count, _ = self.get_task_backoff(task_id)
        fail_count += 1
        delay = self._backoff_delay_seconds(fail_count)
        data = self._load_state()
        data.setdefault("task_backoff", {})[task_id] = {
            "fail_count": fail_count,
            "next_ok_ts": time.time() + delay,
        }
        self._save_state(data)

    def mark_task_success(self, task_id: str) -> None:
        """任务成功后清零失败计数。"""
        data = self._load_state()
        backoff = data.setdefault("task_backoff", {})
        if task_id in backoff:
            backoff.pop(task_id)
            self._save_state(data)
