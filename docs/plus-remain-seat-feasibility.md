# PLUS 余票监控 · 可行性结论

> 结论日期：2026-09-03 · 调研方式：只读实测官方 PLUS 接口（未改代码、未落地）
> 用途：其他 agent 依据本结论实施开发。

## 结论

**可行。** 官方 PLUS 查价接口（`https://app.hnair.com/ticket/lfs/ffl/airLowFareSearch`，即项目 `backend/fetcher.py` 已用的端点）响应中**直接返回精确余票张数**，无需推算。已验证深圳→长春 9/8 返回 `inventoryQuantity=2`，与前端展示"剩 2 张"完全一致。

## 关键字段（响应 JSON 路径）

```
airItineraries[].airItineraryPrices[].flightBookingClasses[]
  ├─ fareFamilyName     "666海航PLUS会员专享" / "2666海航PLUS会员专享"
  ├─ bookingClass       "B"/"C"/"R" 等
  ├─ inventoryQuantity  精确剩余张数；充足时为 10（封顶）
  └─ inventoryStatus    "A"=充足(10张+，前端不显示余量)；数字=精确剩余张数(1/2/4…)
```

- 同一航班可能有多个 199 元档舱位（如 B 舱 10 张 + R 舱 1 张），余票各自独立。
- 当天无任何 PLUS 可售航班时：`data.success=False`，`airItineraries` 为空。

## 实测样本（2026-09-08，票面价均 199）

| 航线 | 航班 | 舱位 | qty | status |
|---|---|---|---|---|
| 深圳→长春 | Y87531 | B | 2 | 2 |
| 深圳→北京 | HU7714 | B / C | 4 / 2 | 4 / 2 |
| 北京→深圳 | HU7713 | R | 1 | 1 |
| 广州→北京 | HU7812 | B / R | 10 / 1 | A / 1 |
| 深圳→海口 | HU7022 等 | B | 10 | A |

## 开发建议

1. `backend/fetcher.py` 的解析（`_request_price`）目前只保留 flight/price/stop/times，需**扩展提取**每个 price option 的 `fareFamilyName`、`bookingClass`、`inventoryQuantity`、`inventoryStatus`。
2. 监控口径：对目标"航线×日期"定时轮询（复用 daemon 节奏），取 199 元档（666/2666 PLUS 专享）**各舱位余票之和**：≥2 提醒"可拍 2 张"；=1 告警"只出一张"。这正是用户核心诉求（两人出行，怕只出一张）。
3. 凭证依赖 config.json `plus_curl`（cookie/token，有有效期，失效需重新抓包）；`sign_refresh` 重签机制已可用。
4. 注意接口请求间隔与现有 `price_query.min_interval=8s` 风控策略一致。

## 遗留待确认（落地时补即可）

- 舱位售罄（qty=0/NS）时 `flightBookingClasses` 的精确表现未完整采样（仅确认"整线无票"返回 success=False）。
- 666 vs 2666 哪个是用户实际在用的套餐，开发前建议确认。