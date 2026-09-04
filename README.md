# 海航监控

本机常驻的海航机票低价监控 + 航班数据校正工具。

- **监控**：按你设定的航线/日期/阈值轮询查价，命中低价后推送**微信（Server酱）/ 飞书**。
- **数据校正**：把航班时刻、航站楼、经停等基础事实校正进正式底表，供页面与查询使用。
- **界面**：React 控制台采用「航线雷达台」视觉，提供紧凑 command bar、监控状态带、航段线、浅/暗主题切换和移动端基础适配。
- 只读查询与提醒，**不**自动购票/锁票/付款。

## 架构

```
web/              React 18 + AntD 5 + Vite 前端（构建产物 web/dist）
web_api.py        轻量 Web API（标准库 http.server），绑定 127.0.0.1:8501，
                  托管静态页面 + 读写 config/tasks/历史/日志
daemon.py         常驻抓价循环：按 config.json 轮询任务、命中阈值推送通知
app.py            Streamlit 旧前端（保留复用其读写逻辑，非主入口）
backend/          fetcher（官方查价 + 签名）/ notifier（微信+飞书）/ observations
                  / sediment（底表）/ third_party（第三方校正）/ adapters（平台请求骨架）
scripts/sedimentation/  数据校正与全量自扫脚本（见下）
data/sediment/    flights_normalized.json（正式底表）、tier_block_rules.json（档位×日期规则）、
                  observations.json（观测库）、third_party/（第三方校正目录）、snapshots/（合入快照）
config.json      本地配置（含凭证模板，不入 git）
```

## 启动 / 停止

```bash
./start_all.sh   # 一键启动 daemon + Web 控制台（自动构建缺失的 web/dist）
./stop_all.sh    # 停止双进程
```

- 页面：http://127.0.0.1:8501（**仅本机可访问**）
- 日常由 PenguinHarness 托管双进程时，用 harness 的 run_in_background 常驻，不使用 nohup 脚本。
- 顶栏主题按钮默认跟随系统偏好，也可手动切换浅色/暗色；选择保存在当前浏览器本地。

## 使用流程

1. **票据管理**（顶栏「票据管理」）：粘贴海航官网抓包的 cURL——普通票价入口
   `airLowFareSearch`，PLUS 专享入口 `ffl/airLowFareSearch`（两者需分别抓取）。
   保存即生效，daemon 每轮重读 config，无需重启。
2. **任务**：添加航线/日期（单日或起止区间）/票价类型/目标价、启停任务。
   同一航班号且起降时刻相同会自动合并多个日期为一条任务；日期较多时鼠标悬停
   日期行可查看全部日期。
3. **设置**：通知渠道（微信 Server酱、企业微信、飞书、钉钉、Bark、ntfy）、加急开关、
   飞书加急/卡片确认/长连接（配置步骤见 `docs/notify_channels_guide.md`）、监控时段、代理、签名刷新开关。
4. **实时查价开关**：config.json 的 `price_query`（防风控）——
   - `enabled=false` 时 daemon 不实时查价、自扫脚本直接退出；
   - `min_interval`（默认 8s）限制同一任务的查询间隔；HTTP 429 自动退避。

## 档位×日期规则

随心飞 666 / 2666 两档有可兑日期限制（真实规则来源见
`data/sediment/tier_block_rules.json` 的 source/notes，搜狗公众号条款 × sxfroute 交叉验证）：

- **666 元版**：屏蔽春运 / 五一 / 暑运 / 十一；**2666 元版**：仅屏蔽春运 / 暑运。
- 2026 秋航季（9/1~10/24）内 666 生效屏蔽区间为**十一 09-30 ~ 10-09**（2666 不受影响）。
- 规则由底表下发（`/api/flights/meta` 的 `tier_block_rules`）：航线查询 `product=666` 查屏蔽日返回空；
  转监控 / 编辑监控的日期池**选不到 666 档被屏蔽的天**（档位条件变化时已选被禁日期自动剔除）。
- 修改规则只改底表 JSON，无需动前端代码；前端常量仅作加载前的兜底镜像。

## 数据校正与全量自扫

- **第三方校正**（低频、按次、慎用）：把任意平台的航班计划数据按
  `data/sediment/third_party/README.md` 的 JSON 格式放入 `third_party/` 目录，
  运行 `scripts/sedimentation/sync_third_party.py --apply` 合入底表（只校正
  起降时刻/航站楼/经停，不覆盖航线集合与班期）。已调研：高德/腾讯无航班 API、
  聚合数据「航班动态」维护中、AviationStack 免费档国内覆盖弱、飞常准需企业认证。
- **免费全量自扫**（推荐）：`scripts/sedimentation/sync_from_price_api.py` 用自有
  官方查价接口按「航线+日期」整批免费扫表，自动断点续传、异常自动恢复：

  ```bash
  # 只打印计划（不请求）
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --dry-run
  # 无人值守全自动（默认 60s 间隔；中断/失败自动恢复，直到全部完成）
  .venv/bin/python scripts/sedimentation/sync_from_price_api.py --supervised
  # 扫完把观测固化进正式底表
  .venv/bin/python scripts/sedimentation/apply_observations.py
  ```

  进度文件 `data/sediment/sync_from_price_api_progress.json` 记录 done/failed/total，
  每条任务完成即时落盘，Ctrl-C / 掉电 / 崩溃后重跑即续扫；完成/暂停/中断都会推送
  通知（`--no-notify` 关闭）。

## 文档索引

- `PROJECT_NOTES.md` —— 项目说明、目录结构、git 约定、当前进度与关键结论
- `docs/notify_channels_guide.md` —— 消息通知配置指南（企微/飞书/钉钉/Bark/ntfy 小白步骤）
- `docs/消息通知渠道调研.md` —— 渠道能力与个人门槛调研
- `docs/plus-remain-seat-feasibility.md` —— PLUS 余票监控可行性结论

## 安全

`config.json`、`tasks.json`、`run_log.txt`、`.env` 含本地配置与敏感信息，已在
`.gitignore` 排除，不提交仓库；分享/发布时只保留空白示例。
