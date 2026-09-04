"""自扫进度飞书汇报（watchdog）：跨过每 10% 节点向飞书推送进度与预计剩余时间。

- 只读 data/sediment/sync_from_price_api_progress.json，不干扰正在跑的自扫进程；
- 节点：10% / 20% / … / 90%（100% 由自扫脚本自身的 notify_done 推送「全部完成」）；
- 断点：data/sediment/progress_reporter_state.json 记录已报节点，重启不重复发；
- 若进度文件 total 变化（--reset-progress 重扫），自动从 0 重新记节点；
- 剩余时间按「本 watchdog 启动以来的平均速率」推算，样本不足时回退到 75 秒/条；
- 用法：.venv/bin/python scripts/sedimentation/progress_reporter.py [--interval 60]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROGRESS_FILE = ROOT / "data" / "sediment" / "sync_from_price_api_progress.json"
STATE_FILE = ROOT / "data" / "sediment" / "progress_reporter_state.json"
DEFAULT_INTERVAL = 60  # 轮询间隔秒数（<=10s 拒绝，与自扫风控一致）
FALLBACK_SECONDS_PER_TASK = 75.0  # 样本不足时的历史速率回退值


def load_config() -> Dict[str, Any]:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def send_feishu(title: str, content: str) -> bool:
    """向 config.json 的 feishu.receiver（即「鱼票通知」飞书）发一条文本消息。"""
    from backend.notifier import send_feishu_message

    cf = (load_config().get("feishu") or {})
    app_id = str(cf.get("app_id") or "").strip()
    app_secret = str(cf.get("app_secret") or "").strip()
    receiver = str(cf.get("receiver") or "").strip()
    if not (app_id and app_secret and receiver):
        print("飞书配置不完整，跳过发送。")
        return False
    ok, err = send_feishu_message(app_id, app_secret, receiver, title, content)
    if not ok:
        print(f"飞书发送失败: {err}")
    return ok


def load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def fmt_eta(seconds: float) -> str:
    if seconds <= 0:
        return "无法估算"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"约 {minutes} 分钟"
    return f"约 {minutes // 60} 小时 {minutes % 60} 分"


def main() -> None:
    parser = argparse.ArgumentParser(description="自扫进度飞书汇报 watchdog")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"轮询间隔秒数（默认 {DEFAULT_INTERVAL}，最少 10）")
    args = parser.parse_args()
    if args.interval < 10:
        print(f"间隔 {args.interval}s 太激进，最少 10s。")
        return

    state = load_state()
    last_node = int(state.get("last_node") or -1)
    known_total = int(state.get("total") or 0)
    base_ts: Optional[float] = None
    base_done = 0

    print(f"进度汇报 watchdog 启动（间隔 {args.interval}s，已报节点 {last_node}）。")

    while True:
        try:
            data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(args.interval)
            continue
        done = len(data.get("done") or [])
        failed = len(data.get("failed") or [])
        total = int(data.get("total") or 0)
        if total <= 0:
            time.sleep(args.interval)
            continue

        # --reset-progress 重扫：进度文件 total 变化即重新记节点
        if known_total and total != known_total:
            print(f"检测到 total 变化（{known_total} → {total}），按重扫处理，节点归零。")
            last_node = -1
            base_ts = None
        known_total = total

        now = time.time()
        if base_ts is None:
            base_ts, base_done = now, done

        pct = done / total * 100
        node = int(pct // 10)  # 当前已跨过的 10% 节点（0-10）

        # 启动首条：确认通道 + 当前进度 + 下一个节点预告
        if last_node == -1:
            eta = calculate_eta(base_ts, base_done, now, done, total)
            content = (
                "🔔 进度汇报已开启（每过 10% 推送一次）\n"
                f"当前进度：{pct:.1f}%　{done}/{total} 完成（失败 {failed} 条待重试）\n"
                f"预计还需：{fmt_eta(eta)}（约完成于 {(datetime.now() + timedelta(seconds=eta)).strftime('%H:%M')}）\n"
                f"下一节点：{min(node + 1, 10) * 10}%"
            )
            if send_feishu("📊 海航自扫 · 进度汇报开启", content):
                last_node = node
                save_state({"last_node": last_node, "total": total})

        # 跨节点推送（10% ~ 90%；100% 由自扫脚本自身推送「全部完成」）
        elif node > last_node and node <= 9:
            eta = calculate_eta(base_ts, base_done, now, done, total)
            content = (
                f"已完成 {pct:.1f}%（{done}/{total}，失败 {failed} 条待重试）\n"
                f"预计还需：{fmt_eta(eta)}（约完成于 {(datetime.now() + timedelta(seconds=eta)).strftime('%H:%M')}）\n"
                f"下一节点：{(node + 1) * 10}%"
            )
            if send_feishu(f"📊 海航自扫 · 进度 {node * 10}%", content):
                last_node = node
                save_state({"last_node": last_node, "total": total})
                print(f"已推送 {node * 10}% 节点（{pct:.1f}%）。")

        time.sleep(args.interval)


def calculate_eta(base_ts: float, base_done: int, now: float, done: int, total: int) -> float:
    elapsed = now - base_ts
    delta = done - base_done
    if elapsed >= 120 and delta > 0:
        rate = delta / elapsed  # 条/秒
    else:
        rate = 1.0 / FALLBACK_SECONDS_PER_TASK
    remaining = total - done
    return remaining / rate if rate > 0 else 0.0


if __name__ == "__main__":
    main()