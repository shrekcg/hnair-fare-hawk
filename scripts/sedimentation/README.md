# 数据沉淀模块（scripts/sedimentation）

把两份全量航班计划数据源归一化、对比校验、输出可查询资产的工具集。

## 数据源

| 源 | 位置（参考资料/ 已 gitignore，各自带 .git） | 内容 |
|---|---|---|
| sxfroute | `参考资料/sxfroute/data/airport.csv` | 1594 条，产品分 `666/2666`(1072) 与 `2666`(522)；含班期/时刻/机场/IATA/备注限定 |
| HNA666-flight-map | `参考资料/HNA666-flight-map/666fms.html` 等 | `666fms`=995（666 档）、`2666fms`=1341（2666 档）、`66666`=2671（全量计划底册，含白班不可兑航班） |

## 用法

```bash
# 1) 两源对比校验（输出控制台摘要 + data/sediment/reports/latest_compare.json）
.venv/bin/python scripts/sedimentation/compare.py

# 2) 构建规范化全量数据快照（data/sediment/flights_normalized.json）
.venv/bin/python scripts/sedimentation/build_normalized.py

# 3) 查询某城市可飞航班（666/2666 档位、指定日期、方向过滤）
.venv/bin/python scripts/sedimentation/query.py 海口 --product 666
.venv/bin/python scripts/sedimentation/query.py 海口 --date 2026-09-15 --json data/sediment/out/海口_666_20260915.json

# 4) 产品归属冲突复核（自动裁决，见下节）
.venv/bin/python scripts/sedimentation/review.py export   # 导出 data/sediment/review/conflicts.csv
.venv/bin/python scripts/sedimentation/review.py apply    # 读取填好的 decision，写 review_decisions.json
.venv/bin/python scripts/sedimentation/build_normalized.py  # 重新构建使裁决生效

# 5) 航季更新（拉上游 → 校验 → 自动裁决新冲突 → 重建 → 版本记录+时点快照）
.venv/bin/python scripts/sedimentation/update.py              # 上游有更新则重跑全流程
.venv/bin/python scripts/sedimentation/update.py --dry-run    # 只拉取并报告是否有更新，不写任何产物
.venv/bin/python scripts/sedimentation/update.py --force      # 即使上游无更新也强制重建基线
```

查询规则：`--product 666` 返回档位含 666 的航班（666/2666 双档，因为 666 卡能飞）；
`--date` 校验落在 effective_dates 且当天班期包含该日星期；`--json` 输出清单文件（第 5 阶段与实时监控联动用）。

## 产品归属冲突复核

现状：首跑基线中两源对同一航线的「666/2666 档位归属」判定不同共 44 条，**已按「以官网为准」规则全部自动裁决**（23 条 both + 21 条 2666），结果在 `data/sediment/review_decisions.json`；底表 `product_conflict = 0`。

自动裁决规则（`update.py` 对后续新冲突沿用同一规则；如不同意可在 conflicts.csv 改 decision 后 apply+build 纠正）：
- 官网（hna_products）含 666 → `both`（官网硬证据）；
- CSV 含 666 但 csv/hna 日期区间无交集 → `both`（官网快照未覆盖 CSV 有效期，宁多勿漏）；
- 其余（官网覆盖相同日期区间且仅 2666）→ `2666`。

手工复核步骤（需要人工干预时）：
1. `review.py export` → 打开 `data/sediment/review/conflicts.csv`（Numbers/Excel/WPS，带表头）：
   - 每行含两源档位、时刻、班期、日期、备注、当前 product、**suggestion**（按起飞时刻规则给出建议：20:00–08:00 内 → 666 或 both；19-20/08-09 点 → 仅 2666）
2. 逐行在最后一列 `decision` 填：`666` / `2666` / `both`（两档都行）/ `ignore`，保存；
3. `review.py apply` → 写入裁决文件；
4. `build_normalized.py` → product 按裁决生效、`product_conflict` 清除；未裁决的仍标冲突并在下次 export 继续出现。

## 航季更新机制（update.py）

两个上游（`参考资料/sxfroute`、`参考资料/HNA666-flight-map`）都各自带 .git 且可 `git pull`，作者随航季不定期更新。`update.py` 一键完成：

1. `git pull` 两个上游（`--ff-only` 保护，无新提交则提示「无更新」退出）；
2. 重建前把旧底表存档为 `data/sediment/snapshots/flights_normalized_<ts>.json`（gitignore，可回滚）；
3. `compare.py` 重跑两源校验，刷新 `reports/latest_compare.json` 基线；
4. `review.py export` → 未决新冲突按「以官网为准」规则自动填 decision → apply；
5. `build_normalized.py` 重建底表；
6. 版本记录追加 `data/sediment/versions.json`（上游 commit、条数、冲突数、快照路径）。

上游是第三方非官方仓库，数据质量以官网为准；换航季后等新 CSV/HTML 发布再更新。

## 机场对齐（城市级 → IATA 级）

- 用 `参考资料/HNA666-flight-map/CN271_cityairport_name_IATA_ICAO_coords.csv`（271 机场）把 HNA666 侧缺失的 IATA 补全；
- 同城多机场（北京首都 PEK/大兴 PKX、上海 PVG/SHA、成都 CTU/TFU 等）按 hna 机场名精确匹配，无法唯一确定时保留空 IATA；
- 实测：HNA666 侧 1668 条记录 origin/dest IATA 填充率 100%。

## 统一 schema（Flight）

跨源归一化的记录字段（`scripts/sedimentation/models.py`）：
`source` / `product` / `carrier` / `flight_no` / 起降城市·机场·IATA / `dep_time` / `arr_time` /
`days`(1=周一…7=周日) / `date_ranges`(ISO 区间可多段) / `notes` / `raw`(原文保留)。

- **product 语义**：`666`、`2666`、`666/2666`（双档）。
- HNA666 的产品由文件名推导；`66666.html` 记为 `666/2666` 但**只是全量底册，不代表可兑档位**（对比时单独统计）。
- HNA666 多航段记录（ticketable_segments 多个）拆分一条航段一条 Flight，与 CSV 一行一航段对齐。
- 跨源主键：`(航班号, 出港城市, 到港城市)`——城市级对齐，机场名书写差异不影响。

## 规范化快照（flights_normalized.json）

1733 条（2026 秋航季），每条含两源独立视角 + 合并视角：
- `csv_products` / `hna_products` / `product`（并集，"宁多勿漏"）/ `product_conflict`（两源档位判定不同，首跑 44 条已按自动规则全部裁决清零）/ `review_decision`（裁决结果）
- `csv_dates`（CSV 全季日期按备注修正："仅9.9"/"9.28始"/"10.8止"/"9.28~10.9" 等）/ `hna_dates`（HNA666 实际执飞日期）/ `effective_dates`（HNA666 优先、CSV 备注修正兜底）
- `source`：both=两源都有(1455) / csv=仅 CSV(65) / hna=仅 HNA666 可兑(213)
- `origin.iata` / `dest.iata`：CSV 侧原生，HNA666 侧用 CN271 对照表补齐

## 对比维度与当前基线（2026-09-02 首跑，秋航季 09-01~10-24）

- 存在性：CSV 1520 键 / HNA666 可兑 1668 键 / 共同 1455；仅 CSV 65、仅 HNA666 可兑 213、仅全量底册 2121（白班，符合预期）。
- 指纹(时刻+班期)一致 897；不一致 308 + HNA666 缺时刻 250（经停中间段无 time_details，非错误）。
- 产品归属一致 1411、差异 44：
  - CSV `2666` 而 HNA666 有 `666`（22 条，如 8L9877 昆明→泉州）→ 可能 CSV 漏标 666；
  - CSV `666/2666` 而 HNA666 仅 `2666`（22 条，如 FU6683 福州→兰州）→ 可能 HNA666 少列 666。
  **这些键直接影响「666 卡能飞哪些航班」；首跑 44 条已全部按「以官网为准」规则自动裁决（both 23 / 2666 21，见 review_decisions.json），后续新冲突由 update.py 自动裁决。**
- 日期范围差异 1399：主体是 CSV 记全季 `[09-01,10-24]`、HNA666 记实际执飞日期（如 `[10-01,10-24]`），信息粒度差异非错误；但「CSV 说 9 月初有、HNA666 说没有」的键值得关注（如 8L9501 昆明→郑州 HNA666 从 10/01 才开始）。

## 生成物

- `data/sediment/reports/latest_compare.json` —— 最近一次对比全量明细（入 git，作基线）
- `data/sediment/reports/compare_<ts>.json` —— 带时间戳历史报告（gitignore，本地留档）
- `data/sediment/versions.json` —— 更新版本记录（上游 commit、条数、冲突数、快照路径；入 git）
- `data/sediment/snapshots/flights_normalized_<ts>.json` —— 每次更新前的旧底表时点快照（gitignore，可回滚）

## 纪律

- 只读解析，不写参考资料/、不读取敏感文件；
- 新增对比逻辑必须带 pytest（`tests/test_sedimentation.py`，样本内嵌不依赖参考资料）。