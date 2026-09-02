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

# 2) 单源解析验证（可选）
.venv/bin/python scripts/sedimentation/load_sxfroute.py
.venv/bin/python scripts/sedimentation/load_hna666.py
```

## 统一 schema（Flight）

跨源归一化的记录字段（`scripts/sedimentation/models.py`）：
`source` / `product` / `carrier` / `flight_no` / 起降城市·机场·IATA / `dep_time` / `arr_time` /
`days`(1=周一…7=周日) / `date_ranges`(ISO 区间可多段) / `notes` / `raw`(原文保留)。

- **product 语义**：`666`、`2666`、`666/2666`（双档）。
- HNA666 的产品由文件名推导；`66666.html` 记为 `666/2666` 但**只是全量底册，不代表可兑档位**（对比时单独统计）。
- HNA666 多航段记录（ticketable_segments 多个）拆分一条航段一条 Flight，与 CSV 一行一航段对齐。
- 跨源主键：`(航班号, 出港城市, 到港城市)`——城市级对齐，机场名书写差异不影响。

## 对比维度与当前基线（2026-09-02 首跑，秋航季 09-01~10-24）

- 存在性：CSV 1520 键 / HNA666 可兑 1668 键 / 共同 1455；仅 CSV 65、仅 HNA666 可兑 213、仅全量底册 2121（白班，符合预期）。
- 指纹(时刻+班期)一致 897；不一致 308 + HNA666 缺时刻 250（经停中间段无 time_details，非错误）。
- 产品归属一致 1411、差异 44：
  - CSV `2666` 而 HNA666 有 `666`（22 条，如 8L9877 昆明→泉州）→ 可能 CSV 漏标 666；
  - CSV `666/2666` 而 HNA666 仅 `2666`（22 条，如 FU6683 福州→兰州）→ 可能 HNA666 少列 666。
  **这些键直接影响「666 卡能飞哪些航班」，是人工复核重点。**
- 日期范围差异 1399：主体是 CSV 记全季 `[09-01,10-24]`、HNA666 记实际执飞日期（如 `[10-01,10-24]`），信息粒度差异非错误；但「CSV 说 9 月初有、HNA666 说没有」的键值得关注（如 8L9501 昆明→郑州 HNA666 从 10/01 才开始）。

## 生成物

- `data/sediment/reports/latest_compare.json` —— 最近一次对比全量明细（入 git，作基线）
- `data/sediment/reports/compare_<ts>.json` —— 带时间戳历史报告（gitignore，本地留档）

## 纪律

- 只读解析，不写参考资料/、不读取敏感文件；
- 新增对比逻辑必须带 pytest（`tests/test_sedimentation.py`，样本内嵌不依赖参考资料）。