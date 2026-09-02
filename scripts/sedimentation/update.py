"""航季更新机制：一键拉取上游 → 校验 → 自动裁决新冲突 → 重建底表 → 版本记录 + 时点快照。

用法：
    .venv/bin/python scripts/sedimentation/update.py             # 拉取上游，有更新则重跑全流程
    .venv/bin/python scripts/sedimentation/update.py --dry-run   # 只拉取并报告是否有更新，不写任何产物
    .venv/bin/python scripts/sedimentation/update.py --force     # 即使上游无更新也强制重建（基线重跑用）

流程：
  1. git pull 两个上游仓库（sxfroute / HNA666-flight-map，均在 参考资料/ 下，各自带 .git）
  2. 重建前把旧底表存档为 data/sediment/snapshots/flights_normalized_<ts>.json（gitignore，可回滚）
  3. compare.py 重跑两源对比校验（更新 data/sediment/reports/latest_compare.json 基线）
  4. review.py export → 对仍无裁决的冲突按「以官网为准」规则自动填 decision → apply 写入 review_decisions.json
  5. build_normalized.py 重建底表（裁决生效、冲突清零）
  6. 版本记录追加 data/sediment/versions.json（上游 commit、条数、冲突数、快照路径）
  7. 提示跑全量 pytest 回归

自动裁决规则（与首跑 44 条裁决一致的约定；如不同意，可在 conflicts.csv 手工改 decision 后
  执行 review.py apply + build_normalized.py 重跑纠正）：
  - 官网(hna_products)含 666                → both（官网硬证据）
  - CSV 含 666 但 csv/hna 日期区间无交集     → both（官网快照未覆盖 CSV 有效期，宁多勿漏）
  - 其余（官网覆盖相同日期区间且仅 2666）   → 2666
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEDIMENT = ROOT / "data" / "sediment"
SNAPSHOTS = SEDIMENT / "snapshots"
VERSIONS = SEDIMENT / "versions.json"
NORMALIZED = SEDIMENT / "flights_normalized.json"
CONFLICTS_CSV = SEDIMENT / "review" / "conflicts.csv"
REPO_BASE = ROOT / "参考资料"

REPOS = [
    ("sxfroute", REPO_BASE / "sxfroute", "CSV 航班计划"),
    ("hna666", REPO_BASE / "HNA666-flight-map", "官网随心飞查询快照"),
]


# ---------- 自动裁决规则 ----------

def _parse_md(s: str) -> tuple[int, int]:
    m, d = s.split("-")
    return int(m), int(d)


def parse_ranges(s: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """把 '09-01~10-24 09-03' 这类压缩日期串解析成 [(起,(月,日), 止,(月,日)), ...]。"""
    out = []
    for tok in (s or "").split():
        if "~" in tok:
            a, b = tok.split("~")
            out.append((_parse_md(a), _parse_md(b)))
        else:
            p = _parse_md(tok)
            out.append((p, p))
    return out


def ranges_overlap(a: str, b: str) -> bool:
    ra, rb = parse_ranges(a), parse_ranges(b)
    return any(a1 <= b2 and b1 <= a2 for a1, a2 in ra for b1, b2 in rb)


def has_product(prods: str | None, tag: str) -> bool:
    return tag in (prods or "").split("/")


def decide(row: dict) -> str:
    """自动裁决单条冲突，返回 666 / 2666 / both。"""
    if has_product(row.get("hna_products"), "666"):
        return "both"
    if has_product(row.get("csv_products"), "666"):
        if not ranges_overlap(row.get("csv_dates", ""), row.get("hna_dates", "")):
            return "both"
        return "2666"
    return "2666"


def auto_decide_conflicts(csv_path: Path = CONFLICTS_CSV) -> dict[str, str]:
    """读取 conflicts.csv，给未填 decision 的行按规则填，返回新增裁决 {key: decision}。"""
    if not csv_path.exists():
        return {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    decided: dict[str, str] = {}
    for r in rows:
        if (r.get("decision") or "").strip():
            continue
        d = decide(r)
        r["decision"] = d
        decided[f"{r['flight_no']}|{r['origin']}|{r['dest']}"] = d
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return decided


# ---------- 编排 ----------

def git_pull(name: str, repo: Path) -> bool:
    """git pull（--ff-only 保护），返回是否有新提交。"""
    r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise RuntimeError(f"{name} git pull 失败：\n{out}")
    return "Already up to date" not in out


def git_head_short(repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    return (r.stdout or "").strip() or "?"


def snapshot_old() -> Path | None:
    if not NORMALIZED.exists():
        return None
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    dst = SNAPSHOTS / f"flights_normalized_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    shutil.copy2(NORMALIZED, dst)
    return dst


def run_script(script: str, *args: str) -> None:
    py = ROOT / "scripts" / "sedimentation" / script
    r = subprocess.run([sys.executable, str(py), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{script} 执行失败（exit {r.returncode}）：\n{r.stdout}\n{r.stderr}")
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)


def next_version() -> int:
    if not VERSIONS.exists():
        return 1
    data = json.loads(VERSIONS.read_text(encoding="utf-8"))
    return len(data.get("versions", [])) + 1


def append_version(entry: dict) -> None:
    data = {"versions": []}
    if VERSIONS.exists():
        data = json.loads(VERSIONS.read_text(encoding="utf-8"))
    data["versions"].append(entry)
    VERSIONS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    force = "--force" in argv

    commits: dict[str, str] = {}
    changed = False
    for name, repo, desc in REPOS:
        if not repo.exists():
            print(f"[{name}] 跳过：目录不存在 {repo}")
            continue
        c = git_pull(name, repo)
        changed = changed or c
        commits[name] = git_head_short(repo)
        print(f"[{name}]（{desc}）{'有更新' if c else '无更新'} -> {commits[name]}")

    if not changed and not force:
        print("\n两个上游均无更新，跳过重建（底表已是最新）。可用 --force 强制重建基线。")
        return 0
    if dry_run:
        print("\n[dry-run] 检测到更新/强制，将执行：快照 → compare → 自动裁决 → build → 版本记录。")
        return 0

    print("\n>>> 1/5 存档旧底表快照")
    snap = snapshot_old()
    print("旧底表快照:", snap.relative_to(ROOT) if snap else "（无旧底表）")

    print("\n>>> 2/5 重跑两源对比校验")
    run_script("compare.py")

    print("\n>>> 3/5 导出并自动裁决未决冲突")
    run_script("review.py", "export")
    decided = auto_decide_conflicts()
    print("自动裁决新增:", decided if decided else "（无新增冲突）")
    if decided:
        run_script("review.py", "apply")
    else:
        print("无未决冲突，跳过 apply（裁决已全部生效）")

    print("\n>>> 4/5 重建底表")
    run_script("build_normalized.py")
    payload = json.loads(NORMALIZED.read_text(encoding="utf-8"))
    records = payload["records"]
    prod = dict(Counter(r["product"] for r in records))
    src = dict(Counter(r["source"] for r in records))
    conflicts = sum(1 for r in records if r.get("product_conflict"))

    print("\n>>> 5/5 版本记录")
    entry = {
        "version": next_version(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "upstream_commits": commits,
        "changed": changed,
        "force": force,
        "count": payload["count"],
        "product_dist": prod,
        "source_dist": src,
        "product_conflict": conflicts,
        "new_decisions": decided,
        "snapshot": str(snap.relative_to(ROOT)) if snap else None,
    }
    append_version(entry)
    print("已追加版本记录:", VERSIONS.name, "v" + str(entry["version"]))
    print("\n建议回归：.venv/bin/python -m pytest tests -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())