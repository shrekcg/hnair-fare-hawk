"""全量航线自扫脚本：用海航官方查价接口（免费）把底表航线的实时时刻/经停扫入观测库。

动机（2026-09-03）：
- 第三方（聚合/飞常准）按航班号逐条计费，底表 1733 条全量成本不划算；
- 自有查价接口按「航线+日期」一次返回当天该航线全部航班，且返回体自带
  times（起降时刻/航站楼）与 stop（经停），免费、官方精度最高；
- 本脚本只把观测写入 data/sediment/observations.json（查价成功自动记录），
  再用现有 scripts/sedimentation/apply_observations.py 固化进正式底表。

断点续传 / 异常恢复（2026-09-03 增强）：
- 进度文件 data/sediment/sync_from_price_api_progress.json 记录：
    done   —— 业务完成（查询成功 / 所有候选日都确认无数据）；
    failed —— 本轮到末尾仍未成功的任务（网络/解析/凭证异常），**不标记完成**，
              随后续轮次或重跑自动重试；
- 每条任务处理完成立即落盘，Ctrl-C / 掉电 / 崩溃都不丢进度，重跑自动续扫；
- --supervised 模式：一轮跑完若仍有未完成任务，等待后自动开启下一轮，
  直到全部 done（或连续 3 轮零进展则通知并退出，避免凭证过期时空转）。

风控设计：
- 慢速：每条航线/每个候选日期之间 sleep --interval 秒（默认 60s，拒绝 <10s）；
- 尊重总开关：config.json 的 price_query.enabled=false 时直接退出；
- 连续 network/parse 超过 --max-fail 次自动暂停本轮（防令牌被风控后空转）；
- 同一任务多个候选日期、失败不烧第二日期（避免重复撞风控）。

用法：
  # 只打印计划（不请求）
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --dry-run

  # 无人值守全自动（推荐）：跑完自动继续，中断/失败自动恢复
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --supervised --interval 60

  # 单轮手动跑（跑完即停）
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --interval 60

  # 只扫前 N 条航线（验证用）
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --limit 5

  # 清空断点重新全扫
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --reset-progress

  # 跑完/暂停时默认推送微信 + 飞书通知（--no-notify 关闭）：
  #   完成后、风控暂停、手动中断都会各发一条。

  # 扫完固化：
  .venv/bin/python scripts/sedimentation/apply_observations.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.fetcher import fetch_price_status  # noqa: E402
from backend.observations import record_fares  # noqa: E402
from backend.sediment import load_records  # noqa: E402

PROGRESS_FILE = ROOT / "data" / "sediment" / "sync_from_price_api_progress.json"
DEFAULT_INTERVAL = 60  # 秒，慢速防抖（60~90 秒更保守）
LOOKAHEAD_DAYS = 45  # 选日期时向前找班期匹配的窗口（天）
MAX_FAIL = 6  # 单轮内连续 network 达到该值即暂停本轮
MAX_TRIES = 3  # 每个任务尝试的候选日期上限（同一航线短间隔不同日期，容忍单日无票）
RESTART_DELAY = 60  # supervised 模式轮与轮之间等待秒数
ZERO_PROGRESS_LIMIT = 3  # 连续多少轮 done 无增长则通知并退出


def load_config() -> Dict[str, Any]:
    cfg_path = ROOT / "config.json"
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_tasks(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把底表记录折叠成「航线 × 最少覆盖班期」的查询任务。

    对每条航线，取其下所有航班号的 days（周几）并集，贪心选出最少个周几，
    使每个航班号至少在其中一个周几被查询覆盖；再为每个周几选候选日期。
    返回按 (origin, dest) 排序的任务列表：
      {"key", "origin", "dest", "origin_city", "dest_city", "dates", "weekday", "flight_nos"}
    """
    routes: Dict[Tuple[str, str], List[Tuple[int, List[int]]]] = {}
    for idx, rec in enumerate(records):
        o = (rec.get("origin") or {}).get("iata", "")
        d = (rec.get("dest") or {}).get("iata", "")
        if not o or not d:
            continue
        days = sorted(set(int(x) for x in (rec.get("days") or []) if str(x).isdigit())) or [1]
        routes.setdefault((o, d), []).append((idx, days))

    tasks: List[Dict[str, Any]] = []
    for (origin, dest), infos in sorted(routes.items()):
        # 城市名（观测键需要中文城市名与底表一致）
        origin_city = ""
        dest_city = ""
        for idx, _ in infos:
            rec = records[idx]
            origin_city = origin_city or str((rec.get("origin") or {}).get("city") or "").strip()
            dest_city = dest_city or str((rec.get("dest") or {}).get("city") or "").strip()
            if origin_city and dest_city:
                break
        # 贪心选周几：每次选能覆盖最多未覆盖记录的周几
        pending = [(idx, set(days)) for idx, days in infos]
        weekdays: List[int] = []
        while pending:
            counts: Dict[int, int] = {}
            for _, ds in pending:
                for w in ds:
                    counts[w] = counts.get(w, 0) + 1
            best = max(counts, key=lambda w: (counts[w], -w))
            weekdays.append(best)
            pending = [(idx, ds) for idx, ds in pending if best not in ds]
            if not weekdays or max(counts.values()) == 0:  # 防御死循环
                break
        if not weekdays:
            weekdays = [1]

        for w in weekdays:
            covered_idxs = [idx for idx, ds in infos if w in ds]
            covered_records = [records[idx] for idx in covered_idxs]
            flight_nos = sorted({
                str((rec.get("flight_no") or "")).strip().upper()
                for rec in covered_records
                if str((rec.get("flight_no") or "")).strip()
            })
            tasks.append({
                "key": f"{origin}|{dest}|{w}",
                "origin": origin,
                "dest": dest,
                "origin_city": origin_city,
                "dest_city": dest_city,
                "dates": _candidate_dates(w, covered_records),
                "weekday": w,
                "flight_nos": flight_nos,
            })
    return tasks


def _next_weekday(w: int, today: Optional[date] = None) -> date:
    """返回从明天（含）起向前找、星期几==w 的最近日期。w: 1=周一 … 7=周日。"""
    base = today or date.today()
    for k in range(1, LOOKAHEAD_DAYS + 1):
        cand = base + timedelta(days=k)
        if cand.isoweekday() == w:
            return cand
    return base + timedelta(days=7)


def _candidate_dates(w: int, covered_records: List[Dict[str, Any]], max_tries: int = MAX_TRIES) -> List[str]:
    """为任务生成候选日期（最多 max_tries 个，按时间先后）。

    优先取 covered_records 的 effective_dates 窗口内、且星期几==w 的日期；
    窗口内不足时回退到 _next_weekday 向前找的备选。返回 YYYY-MM-DD 字符串列表。
    """
    cands: List[date] = []
    for rec in covered_records:
        for span in (rec.get("effective_dates") or []):
            if not (isinstance(span, list) and len(span) >= 2):
                continue
            try:
                start = date.fromisoformat(str(span[0]))
                end = date.fromisoformat(str(span[1]))
            except ValueError:
                continue
            d = start
            while d <= end and len(cands) < max_tries:
                if d.isoweekday() == w:
                    cands.append(d)
                    if len(cands) >= max_tries:
                        break
                d += timedelta(days=1)
            if len(cands) >= max_tries:
                break
        if len(cands) >= max_tries:
            break
    # 窗口内不够时用未来星期几补齐
    seen = {d.isoformat() for d in cands}
    d = _next_weekday(w)
    while len(cands) < max_tries:
        iso = d.isoformat()
        if iso not in seen:
            cands.append(d)
            seen.add(iso)
        d += timedelta(days=7)
    return [d.isoformat() for d in sorted(cands)]


def load_progress() -> Dict[str, Any]:
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return {
            "done": list(data.get("done") or []),
            "failed": list(data.get("failed") or []),
            "total": int(data.get("total") or 0),
            "updated_at": str(data.get("updated_at") or ""),
        }
    except (OSError, ValueError, TypeError):
        return {"done": [], "failed": [], "total": 0, "updated_at": ""}


def save_progress(done: Set[str], failed: Set[str], total: int) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "done": sorted(done),
        "failed": sorted(failed),
        "total": total,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    PROGRESS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def notify_done(config: Dict[str, Any], title: str, lines: List[str]) -> Dict[str, bool]:
    """跑完/暂停时推送通知：微信（Server酱）+ 飞书。

    任一渠道失败不影响主流程；凭证内容从不回显。
    返回 {"wechat": bool, "feishu": bool}。
    """
    from backend.notifier import _send_serverchan, send_feishu_message

    results: Dict[str, bool] = {}
    desp = "\n".join(lines)
    results["wechat"] = False
    for key in config.get("send_keys") or []:
        if str(key).strip():
            results["wechat"] = _send_serverchan(str(key).strip(), title, desp) or results["wechat"]

    feishu = config.get("feishu") or {}
    if feishu.get("app_id") and feishu.get("app_secret") and feishu.get("receiver"):
        ok, _ = send_feishu_message(
            str(feishu["app_id"]), str(feishu["app_secret"]), str(feishu["receiver"]), title, desp
        )
        results["feishu"] = ok
    else:
        results["feishu"] = False
    return results


def run_round(
    config: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    done_set: Set[str],
    failed_set: Set[str],
    args: argparse.Namespace,
) -> Tuple[Set[str], Set[str], Dict[str, int], bool]:
    """执行一轮扫描：把 done_set/failed_set 处理完的任务落盘。

    返回 (done_set, failed_set, stat, interrupted)：
    - failed_set 为累积集合（历史失败 + 本轮新增），供后续轮次重试并持久化；
    - interrupted=True 表示本轮因连续失败/手动中断提前结束，需等待后重试。
    """
    failed = set(failed_set)
    remaining = [t for t in tasks if t["key"] not in done_set]
    if args.limit > 0:
        remaining = remaining[: args.limit]
    stat = {"ok": 0, "empty": 0, "network": 0, "parse": 0, "error": 0}
    if not remaining:
        return done_set, failed, stat, False

    print(f"本轮待扫: {len(remaining)} 个任务；已完成: {len(done_set)}/{len(tasks)}")
    fail_streak = 0
    interrupted = False
    try:
        for i, t in enumerate(remaining, 1):
            key = t["key"]
            from_ = t["origin"]
            to_ = t["dest"]
            dates = t["dates"]
            task_done = False
            task_ok = False
            for date_ in dates:
                try:
                    status, fares = fetch_price_status(from_, to_, date_, fare_type=args.fare_type)
                except Exception as exc:  # noqa: BLE001 凭证失效等，记录后继续
                    print(f"[{i}/{len(remaining)}] {from_}→{to_} {date_} 异常: {exc}")
                    stat["error"] += 1
                    fail_streak += 1
                    if fail_streak >= args.max_fail:
                        print(f"连续 {args.max_fail} 次异常，暂停本轮。进度已保存，supervised 会自动续跑。")
                        save_progress(done_set, failed, len(tasks))
                        if not args.no_notify:
                            lines = [
                                f"⛔️ 自扫暂停：连续 {args.max_fail} 次异常（凭证失效/接口异常），已保存进度。",
                                f"已完成 {len(done_set)}/{len(tasks)}；失败 {len(failed)} 条待重试。",
                                "若为凭证问题，请先在「票据管理」更新票据后重跑。",
                            ]
                            res = notify_done(config, "⚠️ 海航自扫：异常暂停", lines)
                            print(f"通知: {res}")
                        interrupted = True
                        return done_set, failed, stat, True
                    break  # 异常不再试下一日期，避免重复撞风控

                if status == "ok" and fares:
                    try:
                        written = record_fares(fares, t["origin_city"], t["dest_city"], observed_at=date_)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[{i}/{len(remaining)}] {from_}→{to_} 写观测失败: {exc}")
                        written = 0
                    stat["ok"] += 1
                    fail_streak = 0
                    print(f"[{i}/{len(remaining)}] {from_}→{to_} {date_} ok: {len(fares)} 条航班，新观测 {written} 条")
                    task_done = True
                    task_ok = True
                    break
                elif status == "empty":
                    print(f"[{i}/{len(remaining)}] {from_}→{to_} {date_} empty: 该日无航班/无价格，换下一日期")
                    time.sleep(args.interval)
                    continue
                else:
                    # network/parse 计入风控失败连续计数，达到阈值自动暂停
                    stat[status if status in stat else "empty"] += 1
                    fail_streak += 1
                    print(f"[{i}/{len(remaining)}] {from_}→{to_} {date_} {status}")
                    if fail_streak >= args.max_fail:
                        print(f"连续 {fail_streak} 次未成功，暂停本轮。进度已保存，supervised 会自动续跑。")
                        save_progress(done_set, failed, len(tasks))
                        if not args.no_notify:
                            lines = [
                                f"⛔️ 自扫暂停：连续 {fail_streak} 次网络/解析失败（限流或网络波动），已保存进度。",
                                f"已完成 {len(done_set)}/{len(tasks)}；失败 {len(failed)} 条待重试。",
                                "supervised 模式会自动继续；建议保持默认或更大的 --interval。",
                            ]
                            res = notify_done(config, "⚠️ 海航自扫：网络暂停", lines)
                            print(f"通知: {res}")
                        interrupted = True
                        return done_set, failed, stat, True
                    break  # 网络问题也不继续试下一日期

            if task_ok:
                done_set.add(key)
            elif task_done:
                # empty（所有候选日期都无数据）视为业务完成，避免无限重试消耗次数
                stat["empty"] += 1
                done_set.add(key)
                print(f"[{i}/{len(remaining)}] {from_}→{to_} 全部 {len(dates)} 个候选日期无数据，已标记完成")
            else:
                # network/parse/error —— 不标记完成，供后续轮次重试
                failed.add(key)
                print(f"[{i}/{len(remaining)}] {from_}→{to_} 未完成（失败待重试）")

            save_progress(done_set, failed, len(tasks))
            if i != len(remaining) and args.interval > 0:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n手动中断。进度已保存，重跑会续扫未完成航线。")
        save_progress(done_set, failed, len(tasks))
        if not args.no_notify:
            lines = [
                "⏸️ 自扫被手动中断（Ctrl-C），进度已保存。",
                f"已完成 {len(done_set)}/{len(tasks)}；失败 {len(failed)} 条待重试。",
                "重跑同一条命令会自动续扫。",
            ]
            res = notify_done(config, "海航自扫：已中断", lines)
            print(f"通知: {res}")
        raise SystemExit(130)
    return done_set, failed, stat, interrupted


def main() -> None:
    parser = argparse.ArgumentParser(description="全量航线自扫（官方查价接口 → 观测库）")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help=f"航线间间隔秒数（默认 {DEFAULT_INTERVAL}，风控起见别小于 10）")
    parser.add_argument("--limit", type=int, default=0, help="只扫前 N 条航线（验证用，0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不发起任何请求")
    parser.add_argument("--reset-progress", action="store_true", help="清空断点进度重新全扫")
    parser.add_argument("--fare-type", default="normal", choices=("normal", "plus"), help="档位：normal=普通价（覆盖最全，默认）；plus=会员价（部分航线无 PLUS 价会 miss）")
    parser.add_argument("--supervised", action="store_true", help="无人值守：一轮跑完若有未完成任务，等待后自动继续，直到全部完成或连续零进展")
    parser.add_argument("--max-fail", type=int, default=MAX_FAIL, help="单轮连续失败暂停阈值")
    parser.add_argument("--no-notify", action="store_true", help="跑完/暂停不推送微信/飞书通知")
    args = parser.parse_args()

    config = load_config()
    pq = config.get("price_query") or {}
    if not pq.get("enabled", True):
        print("config.json 的 price_query.enabled=false（防风控），已退出。需先在「监控管理」开启实时查价。")
        return
    if args.interval < 10:
        print(f"间隔 {args.interval}s 太激进，最少 10s。")
        return

    records = load_records(force=True)
    tasks = build_tasks(records)
    print(f"底表 {len(records)} 条记录 → {len(tasks)} 个查询任务（航线×班期去重）")
    print(f"预计耗时（间隔 {args.interval}s）: {len(tasks) * args.interval / 3600:.1f} 小时")

    progress = {"done": [], "failed": []} if args.reset_progress else load_progress()
    done_set = set(progress["done"])
    failed_set = set(progress["failed"])
    remaining = [t for t in tasks if t["key"] not in done_set]
    if args.limit > 0:
        remaining = remaining[: args.limit]

    now_done = len(done_set)
    print(f"已完成: {now_done}/{len(tasks)}；失败待重试: {len(set(t['key'] for t in tasks) & failed_set)}；本次待扫: {len(remaining)}")
    if args.dry_run:
        print("\n--- 计划（前 10 条）---")
        shown = 0
        for t in remaining:
            if shown >= 10:
                print("...")
                break
            print(f"  {t['origin']}→{t['dest']} 候选日期 {', '.join(t['dates'])} 覆盖航班号: {', '.join(t['flight_nos']) or '—'}")
            shown += 1
        print(f"\n共 {len(remaining)} 条将查询，每条最多尝试 {MAX_TRIES} 个日期。加 --interval 可调速；去掉 --dry-run 正式执行。")
        return

    if args.supervised:
        zero_rounds = 0
        while True:
            done_set, failed_set, stat, interrupted = run_round(config, tasks, done_set, failed_set, args)
            remaining_now = [t for t in tasks if t["key"] not in done_set]
            all_done = len(remaining_now) == 0
            print(f"\n本轮统计: {stat}；累计完成 {len(done_set)}/{len(tasks)}")
            if all_done:
                if not args.no_notify:
                    lines = [
                        "✅ 全量航线自扫已完成（supervised 模式，全部任务 completed）。",
                        f"累计完成 {len(done_set)}/{len(tasks)}；失败 {len(failed_set)} 条（已记录，可重跑重试）。",
                        "下一步：运行 scripts/sedimentation/apply_observations.py 把观测固化进底表。",
                    ]
                    res = notify_done(config, "✅ 海航自扫：全部完成", lines)
                    print(f"通知: {res}")
                break
            if len(done_set) == now_done:
                zero_rounds += 1
                print(f"本轮无新完成（连续零进展 {zero_rounds}/{ZERO_PROGRESS_LIMIT}），等待后重试…")
            else:
                zero_rounds = 0
                now_done = len(done_set)
            if zero_rounds >= ZERO_PROGRESS_LIMIT:
                if not args.no_notify:
                    lines = [
                        "⛔️ 自扫连续多轮无进展（疑似凭证过期/接口封禁），已停止。",
                        f"进度 {len(done_set)}/{len(tasks)} 已保存；请检查票据后重跑。",
                    ]
                    res = notify_done(config, "⛔️ 海航自扫：连续无进展停止", lines)
                    print(f"通知: {res}")
                break
            print(f"等待 {RESTART_DELAY}s 后开始下一轮（Ctrl-C 随时可中断，进度不丢）…")
            time.sleep(RESTART_DELAY)
        return

    # 单轮模式
    done_set, failed_set, stat, _ = run_round(config, tasks, done_set, failed_set, args)
    print(f"\n本次完成: {stat}")
    remaining_now = [t for t in tasks if t["key"] not in done_set]
    print(f"累计完成 {len(done_set)}/{len(tasks)}；本轮失败待重试: {len(failed_set)} 条")
    if not args.no_notify:
        done_now = len(done_set) - now_done
        lines = [
            "✅ 全量航线自扫已完成（单轮模式）。",
            f"本轮新完成 {done_now} 条（累计 {len(done_set)}/{len(tasks)}）；失败待重试 {len(failed_set)} 条。",
            "若还有未完成，重跑同一条命令会续扫。",
        ]
        res = notify_done(config, "✅ 海航自扫：本轮完成", lines)
        print(f"通知: {res}")
    if remaining_now:
        print("提示：仍有未完成任务，可重跑续扫；或用 --supervised 无人值守跑完。")


if __name__ == "__main__":
    main()