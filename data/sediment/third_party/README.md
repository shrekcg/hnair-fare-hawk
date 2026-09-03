# 第三方航班数据校正目录

把第三方航班平台（聚合数据 / 去哪儿开放平台 / 高德 / 腾讯位置服务 / 手工整理等）的
**航班计划数据**放进本目录（任意 `*.json`），运行固化脚本即可把起降时刻/航站楼/经停
合入正式底表 `flights_normalized.json`。

## 原则

- 底表（航线集合、班期、可飞日期、档位 666/2666）以海航随心飞基线为准，**第三方不覆盖**；
- 第三方校正**只更新基础事实**：`dep_time` / `arr_time` / `dep_terminal` / `arr_terminal` / `stops`；
- 目标：航班的起降时刻表、经停城市等基础信息准确；
- 实时查价接口只服务查价（顺带观测），**不做表更新**（防风控）；表更新只走本机制，低频手动执行。

## 文件格式

```json
{
  "corrections": [
    {
      "flight_no": "Y87531",
      "origin_iata": "SZX",
      "dest_iata": "CGQ",
      "dep_time": "07:40",
      "arr_time": "15:50",
      "dep_terminal": "T3",
      "arr_terminal": "T2",
      "stops": [
        {
          "city": "杭州",
          "airport": "萧山",
          "terminal": "T3",
          "arrive": "09:55",
          "depart": "12:50",
          "stay": "2h55m"
        }
      ],
      "source": "juhe",
      "observed_at": "2026-09-03"
    }
  ]
}
```

说明：
- `flight_no` / `origin_iata` / `dest_iata` 必填（IATA 三字码，须与底表一致，如深圳 SZX、长春 CGQ）；
- 其余字段可选；提供哪个就校正哪个；时间必须 `HH:MM`，航站楼 1-4 位字母数字；
- `stops`：有经停给数组（`city` 必填，其余可选），直飞给 `[]` 或省略；
- 多条记录可放在同一个 `corrections` 数组里，多个文件也会合并处理；
- 脚本会跳过格式非法的条目并打印说明。

## 用法

```bash
# 预览 diff（默认，不写文件）
.venv/bin/python scripts/sedimentation/sync_third_party.py

# 指定文件预览
.venv/bin/python scripts/sedimentation/sync_third_party.py --file /tmp/corr.json

# 正式合入（自动快照 data/sediment/snapshots/ + 版本记录 versions.json）
.venv/bin/python scripts/sedimentation/sync_third_party.py --apply
```

## 免费全量自扫（可选，推荐）

第三方按次计费不适合 1733 条底表全量校正；**优先用自有官方查价接口**免费扫：

```bash
# 只看计划（不请求）
.venv/bin/python scripts/sedimentation/sync_from_price_api.py --dry-run

# 无人值守全自动（推荐；默认 60s 间隔，跑完自动继续，中断/失败自动恢复）
.venv/bin/python scripts/sedimentation/sync_from_price_api.py --supervised

# 单轮手动跑（跑完即停，可再加 --limit N 只扫前 N 条验证）
.venv/bin/python scripts/sedimentation/sync_from_price_api.py --interval 60

# 扫完把观测固化进正式底表
.venv/bin/python scripts/sedimentation/apply_observations.py
```

- 原理：查价接口按「航线+日期」返回当天该航线全部航班的实时时刻/航站楼/经停，
  免费、官方精度最高；脚本自动把底表 1733 条折叠成约 1300 个查询任务
  （航线×班期去重、每条最多试 3 个候选日期）；
- 风控与恢复：默认 normal 档（覆盖最全，plus 只含会员价会 miss）+ 保守间隔
  60s（拒绝 <10s）；单轮连续失败 `--max-fail`（默认 6）次自动暂停本轮；失败任务
  **不标记完成**，进入进度文件的 failed 集合自动重试；每条任务完成即时落盘
  （`data/sediment/sync_from_price_api_progress.json`，结构 `{done, failed, total, updated_at}`）；
  Ctrl-C / 掉电 / 崩溃后重跑同一条命令即断点续扫；完成/暂停/中断都会推
  微信+飞书通知（`--no-notify` 关闭）；`--supervised` 连续 3 轮零进展会
  通知并退出（防凭证过期空转）。

## 数据从哪来

| 平台 | 状态（2026-09-03 实测） | 接入方式 |
|---|---|---|
| 聚合数据（juhe.cn）「航班动态」 | 页面标注**维护中**；需实名认证 + 应用场景审核；个人可注册 | 拿到 key 后写 `backend/adapters/juhe.py`（骨架已就位） |
| AviationStack（aviationstack.com） | 免费档 100 次/月；实时航班状态，**国际为主，国内覆盖弱** | 注册后按需写 adapter |
| 飞常准（open.variflight.com） | **企业认证门槛高**，个人难申请 | 同上 |
| 高德 / 腾讯位置服务 | **已实测确认无航班相关 API**，不做候选 | — |
| 去哪儿开放平台（open.qunar.com) | 航班查询，需开发者审核；延期未实测 | 同上 |
| 任意网页/APP | 手动查到某条航线的时刻 | 直接按上面 JSON 格式填一条即可 |

拿到任意平台的 API key 后，把「平台响应 → corrections 数组」的转换逻辑写成
`backend/adapters/<平台>.py`，固化脚本（`sync_third_party.py`）不感知来源，只消费
`corrections` 数组——当前不绑定任何平台，随时可换。

## 申请指南

### 聚合数据 juhe.cn（推荐候选，注意当前维护中）

1. 注册/登录 https://www.juhe.cn/ ，完成**个人实名认证**；
2. 在 API 市场搜索「航班动态」（ID 20），点「申请接口」，按提示提交**应用场景审核**；
3. 审核通过后在线测试 → 充值购买套餐（按次计费，价格需登录后查看）；
4. 把 AppKey 填到 adapter 后运行 `scripts/sedimentation/sync_third_party.py` 预览。

> 注意：2026-09-03 实测该接口页面标注「该接口正在维护中，有问题请联系客服」，
> 申请前先联系在线客服确认是否仍可开通/使用，避免充值后不可用。

### AviationStack（备选，免费档可直接测）

1. 注册 https://aviationstack.com/ （邮箱即可，免费档 100 次/月）；
2. 免费档直接给 API key，无需审核；
3. 国内航班覆盖弱，适合先用它验证管线，正式跑国内数据再决定是否换平台。

## 现有实现

- `backend/third_party.py`：校验/匹配/diff/应用（已交付，测试通过）；
- `scripts/sedimentation/sync_third_party.py`：预览/合入底表（已交付）；
- `backend/adapters/juhe.py`：聚合数据请求骨架（**响应字段待真实 key 校准**，未实测联网）。