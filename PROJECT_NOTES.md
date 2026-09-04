# 海航监控 · 项目说明与开发约定

> 本文件是本项目（`/Users/Wcg/Desktop/Project_local/海航监控`）的常驻说明，任何 agent 接手前先读我。
> 更新时间：2026-09-04

## 一、这是什么

海航低价机票监控 + 航班数据校正工具（最初基于上游 `hna-low-fare-monitor` Skill 包，
GitHub: huiMiluMilu/hna-hna-low-fare-monitor-skill-shihui，现已大幅自研迭代）。

- 前端：React 18 + AntD 5（`web/`，Vite 构建），由 `web_api.py`（标准库 HTTP API，
  绑定 127.0.0.1:8501）托管页面 + API。
- 后端：`daemon.py` 常驻循环——定时抓价、命中阈值推微信（Server酱）/ 飞书。
- 数据：`data/sediment/flights_normalized.json` 正式底表（航线/班期/档位以
  海航随心飞基线为准），第三方校正与全量自扫只更新时刻/航站楼/经停等基础事实。
- 只读查询与提醒，**不**自动购票/锁票/付款。

## 二、目录与关键文件

```
海航监控/
├── web/                    # React + AntD 前端源码（pnpm，构建产物 web/dist）
├── web_api.py              # 轻量 Web API（127.0.0.1:8501，托管页面+API）
├── daemon.py               # 后端常驻抓价循环
├── app.py                  # Streamlit 旧前端（保留读写逻辑供 web_api 复用，非主入口）
├── backend/
│   ├── fetcher.py          # 官方查价（_make_hnair_sign 签名逆向后已对齐线上）
│   ├── notifier.py         # 微信 Server酱 + 飞书推送
│   ├── channels.py         # 多通道通知（企业微信/钉钉/Bark/ntfy/飞书 + 加急 + 通知历史）
│   ├── feishu_ws.py        # 飞书长连接事件接收（卡片回调确认闭环）
│   ├── observations.py     # 观测库（record_fares）
│   ├── sediment.py         # 底表加载（load_records）
│   ├── third_party.py      # 第三方校正（校验/匹配/diff/应用）
│   └── adapters/           # 外部平台请求骨架（juhe.py 待 key 校准）
├── scripts/sedimentation/
│   ├── sync_third_party.py       # 第三方校正预览/合入（--preview/--apply + 快照）
│   ├── sync_from_price_api.py    # 免费全量自扫（60s 间隔、断点续传、--supervised）
│   └── apply_observations.py     # 观测固化进底表
├── data/sediment/
│   ├── flights_normalized.json    # 正式底表
│   ├── tier_block_rules.json      # 档位×日期屏蔽规则（666/2666 条款，单一事实源，meta 下发前端）
│   ├── observations.json          # 自扫观测（dict，key=航班号|城市|城市）
│   ├── sync_from_price_api_progress.json  # 自扫进度（done/failed/total）
│   ├── third_party/               # 第三方校正数据 + README
│   └── snapshots/ versions.json   # 合入快照与版本记录
├── tests/                  # pytest（当前 180 个）
├── city_codes.py           # 城市→三字码
├── start_all.sh / stop_all.sh
├── requirements.txt
├── .gitignore              # 已排除 config.json / tasks.json / runtime_state.json / run_log.txt / .venv / notification_history.jsonl
├── README.md               # 项目简介与快速使用
└── docs/notify_channels_guide.md   # 消息通知配置指南（企微/飞书/钉钉/Bark/ntfy）
```

**敏感文件（不入 git）**：`config.json`（含 cURL 凭证模板、SendKey、飞书 App Secret）、`tasks.json`、`runtime_state.json`、`run_log.txt`、`.env`。

## 三、git 约定（多 agent 协作）

- 仓库已 `git init`，全局身份：`晨光 <chenguang_wu@lebo.cn>`。
- 提交纪律：
  1. **绝不提交** `config.json`、`tasks.json`、`runtime_state.json`、`run_log.txt`、`.env`、任何含 Cookie/token/SendKey 的文件（.gitignore 已兜底）。
  2. 每个功能/修复一个提交，信息写清楚「改了什么、为什么」。
  3. 改代码前先 `git status` / `git log` 看别人是否已动过；改完跑 `pytest` 再提交。
- 其他 agent 在本目录开发时同样遵守上述纪律。

## 四、日常启动方式

```bash
cd /Users/Wcg/Desktop/Project_local/海航监控
# 一键启动双进程（Web 已绑定 127.0.0.1，仅本机可访问；前端产物缺失会自动构建）
./start_all.sh
# 停止
./stop_all.sh
```

手动启动（通常不需要）：
```bash
# Web 控制台（React 页面 + API，绑定 127.0.0.1:8501）
env -u ELECTRON_RUN_AS_NODE .venv/bin/python web_api.py
# 后端抓价
env -u ELECTRON_RUN_AS_NODE .venv/bin/python daemon.py
```

- 页面：http://127.0.0.1:8501（**仅本机可访问**，局域网已不可见）
- 由 PenguinHarness 托管时用 run_in_background 双进程常驻（daemon + web_api），
  不使用 nohup 脚本；重启 web_api 前先 `lsof -nP -i :8501` 确认端口释放。
- 注意：exec_command 环境带 `ELECTRON_RUN_AS_NODE` 会破坏 Electron 相关进程，
  长驻启动一律 `env -u` 去掉。

## 五、当前进度（2026-09-02）

- [x] 安装到本目录（venv、依赖、7/7 测试）
- [x] git 初始化 + 首次提交
- [x] 双进程启动验证（页面 200、daemon 存活）
- [x] 审阅记录落盘（当时存 REVISION_NOTES.md，2026-09-03 文档清理时已删）
- [x] 用户提供 PLUS 抓包 cURL 并导入（`.private/requests/hna-plus.txt`，config.json plus_curl）
- [x] 绑定 Server酱 SendKey（用户已收到测试消息）
- [x] **端到端验收通过**：PLUS 端点修正后 daemon 抓到 JD5290/HU7397 各 199 元 → 命中阈值 → 微信已推送（15:20，2 条低价提醒 + 1 条链路验证）
- [x] **优化批次1 已上线（提交 67086af）**：安全绑定 127.0.0.1、日志/历史轮转、任务级错峰、凭证失败退避、结果分类、价格历史、自适应轮询、可选代理、页面任务启停开关（12/12 测试）
- [ ] （可选）用户补抓「普通票价」cURL 并导入，开通普通票价监控（代码路径已完整：URL 两个入口、任务页「普通票价」选项、fare_type 分发都在；但无 normal_curl 时走静态模板+本地重签，实测 E00001 验签错误，见下方关键结论 4）
- [x] **签名算法逆向完成（未提交 → 已提交）**：官方 `_makeSign` = HMAC-SHA1(hna 头 + query 值 + payload 标量值 + certificateHash, hardCode)，本地 `_make_hnair_sign` 已对齐；离线复刻逐字符一致，在线双跑同航班同价格；`sign_refresh` 开关默认关闭，失败自动回退原签名（16/16 测试）
- [x] **控制台 UI 重构**：Streamlit → React+AntD（`web/` + `web_api.py` 8501），日期区间监控、票据管理、通知渠道（微信/飞书）、任务启停、历史日志（2026-09-02）
- [x] **防风控三层机制（2026-09-03）**：实时查价开关 `price_query`（enabled + min_interval 默认 8s）；HTTP 429 节流退避；前端频控锁。config.json 当前 `price_query={enabled: true, min_interval: 8}`
- [x] **第三方校正管线（2026-09-03）**：`backend/third_party.py` + `scripts/sedimentation/sync_third_party.py`（--preview/--apply + 快照）+ `tests/test_third_party.py`；只校正 dep/arr_time、terminal、stops；已调研渠道结论见下方「第三方渠道调研」
- [x] **免费全量自扫脚本（2026-09-03，两轮迭代完成并验证）**：`scripts/sedimentation/sync_from_price_api.py` 用官方查价接口按「航线+日期」整批免费扫表；默认 60s 保守间隔（拒绝 <10s）；失败任务（network/parse/error）**不标记 done**，进入 failed 集合后续轮自动重试；每条任务完成立即落盘；`--supervised` 无人值守（一轮跑完等待 60s 自动下一轮，连续 3 轮零进展通知并退出防凭证过期空转）；`--max-fail` 单轮连续失败暂停阈值（默认 6）；完成/暂停/中断三路径都会推微信+飞书通知（--no-notify 关闭）。底表 1733 条 → 1298 个查询任务；进度文件 `data/sediment/sync_from_price_api_progress.json` 结构 `{done, failed, total, updated_at}`；实测 normal 档覆盖最全（plus 只含会员价会 miss）；当前已完成 5 个任务，观测库 7 条（验证：133 测试通过 + dry-run + 真实 limit 1 成功写入观测）
- [x] **文档清理（2026-09-03）**：删除过时交接/历史文档（START_HERE、CHANGES_AND_USAGE、REVISION_NOTES、docs/QUICK_START_WITH_AI、WECHAT_NOTIFICATION_SETUP、ui_design_spec、消息通知渠道调研、docs/images、docs/feishu_notify_guide.md、ddg_auth.html、*.bak）；通知配置统一收拢到 `docs/notify_channels_guide.md`；README 重写为 React 现状；本文件同步目录与进度
- [x] **多通道通知 + 飞书闭环（2026-09-03）**：`backend/channels.py` 统一企业微信/钉钉/Bark/ntfy/飞书 + 加急路由（important 加急默认开，critical 强制全渠道并写 `notification_history.jsonl`）；`backend/feishu_ws.py` 飞书长连接事件接收（lark-oapi ws.Client，卡片回调确认闭环，状态机记录卡片/确认到 runtime_state.json）；前端「通知设置」重构为渠道卡片 + 全局加急开关 + 飞书配置/长连接状态/测试/确认卡片 + 通知历史 + 飞书回执表；API：`/api/notify/save`、`/api/notify/test`、`/api/feishu/callback`。真实自测：飞书卡片发送成功、模拟回调后卡片更新、长连接真实连上；**遗留（已解决 2026-09-04）**：加急权限 `im:message.urgent` 已由用户在开放平台开通并发布版本，加急现可正常发送（此前失败 code=99991672 消息可发、不阻断）
- [x] **余票/舱位监控（2026-09-03）**：fetcher `_extract_seat_info` 按 bookingClass 去重取最大 qty（status "A"→qty>=10）；任务转监控/新增支持 `min_seats`（至少 N 张才触发，默认 1=旧行为）与 `cabins` 舱位白名单；daemon `_task_seat_ok` 按白名单舱位 qty 和或总余票判断；「实时查询」返回 `seats:{normal:{}, plus:{}}`；前端余票/舱位列（plus 优先）、批转弹窗「至少 N 张」+舱位白名单、任务表「余票条件」列；测试 169 通过
- [x] **第 6 轮迭代 + UI 对齐（2026-09-03 深夜，测试 170）**：
  - 批转任务合并语义改「**航线+航班号+起降时刻**」分组——同日多个起飞时间的航班各自成独立任务，仅同航班号且起降时刻相同才合并日期；daemon 加 queried 查询缓存 + flight_no 过滤 + history_seen 去重。
  - 通知渠道六卡**等高同构**（ChannelCard：状态行 `.channel-status` + 操作区 + 测试结果占位 `.channel-result`，无提示也占位）；标题简化（飞书/企业微信/钉钉）；去掉外层「重要/阻断告警默认加急」Tag（加急固定开启）；测试结果 per-card 不串扰（告别全局 testMsg）；通知历史与飞书回执独立成块可滚动；历史日志去掉价格查询历史；每卡标题旁「?」教学（hover 可关闭）。
  - 监控任务**多日期行收敛**：日期行最多显示前 3 个日期 + 「+N」（真实任务 HU7851 52 天 / Y87569 50 天），鼠标悬停 Tooltip 查看全部日期；航线列固定 width 320——原实现日期 Tag 平铺会让 flex-wrap 的 max-content 把列撑到整行一字排开的宽度，横向滚很久。
  - 航线查询卡片去掉右上角「底表 N 条 · 航季…」底标（同时移除 flightMeta 请求）。
  - 前端 UI 关键坑备忘：ChannelCard 无条件 `fields.some()`，微信/飞书卡无 fields → 全站白屏（`Cannot read properties of undefined (reading 'some')`），已给 `fields = []` 默认值。
- [x] **第 7 轮迭代 + 档位×日期真实规则底表化（2026-09-04，测试 180，提交 96844a9）**：
  - 前端第 5 批 6 项：去批转弹窗 Alert；航线查询「冻结查询列」默认关；任务列表航线列 320→520、日期行单行最多 4 个 Tag +「+N」悬停看全部；操作列 250→190（Switch 改小 Button + icon-only 编辑/删除）；监控频率卡去掉「实时查价」对比说明。
  - 日夜间隔确认（用户已确认）：config.json `polling`（day 90-240s / night 300-600s）+ scheduler.py 按时段随机取整秒（07-23 日间、23-07 夜间）；daemon 自适应（有价→减半保底 min，无价→×1.5 封顶 night_max*2）。
  - **档位×日期真实规则**（搜狗公众号「666元海航随心飞」条款 × 参考资料/sxfroute 交叉验证，推翻旧中秋猜测）：666 元版屏蔽**春运/五一/暑运/十一**，2666 元版仅屏蔽**春运/暑运**；区间定义：春节=农历腊月十五~正月廿五、五一=法定假期及前后各一天、暑运=7/1~8/31、十一=国庆法定假期及前后各一天（2026 换算 09-30~10-09，与 sxfroute 常量一致）；**中秋 9/25~9/27 不在真实规则内**；2026 秋航季（9/1~10/24）内 666 生效区间只有十一。
  - **底表实装**：新建 `data/sediment/tier_block_rules.json`（单一事实源：source/notes/tiers，含 label 与 blocks）；`backend/sediment.py` 新增 `tier_block_rules()`（懒加载缓存）/`tier_blocks()`/`product_tiers()`/`date_in_blocks()`/`range_fully_in_blocks()`/`blocked_by_tier()`；`meta()` 下发 `tier_block_rules`；`query()` 指定档位 + 日期/区间落在屏蔽区间内**直接返回空**（666 国庆区间 0 条、2666 不受影响；区间部分重叠保留原始语义由前端收窄）。
  - 前端：常量改名 `DEFAULT_TIER_BLOCK_RULES`（镜像同份规则兜底，build 时嵌入）；App 根拉 `/api/flights/meta` 归一后传给 Tasks/FlightQuery；批转/编辑弹窗日期池随「档位条件」Select 联动收窄、已选被禁日期自动剔除（666 档选不到 09-30~10-09）。
  - **验收锚点调整**：原锚点 `海口+666+2026-10-03` 落在国庆屏蔽区间内（按真实规则该档该日不可兑），移到屏蔽区间外的周六 **2026-10-10**（双向 102 条，仍 100~120）；`test_query_from_to_direction` 同步换日。
  - 其他：`notification_history.jsonl` 运行数据入 .gitignore；git 一次性提交 `96844a9`（17 文件 1684 insertions），工作区干净。
  - 验证：pytest 180 passed、pnpm build OK（dist/index-B52nWOjl.js）、web_api 重启后 meta 下发规则 + 查询屏蔽 + 页面 200 全过。
- [x] **第 8 轮迭代（2026-09-04，纯前端，测试 180）**：
  - 排查「产品档位多了一个 3666」：航线查询搜索表单档位 Select 内联 options 有手误残留 `{label:'3666', value:'3666'}`（真实可兑档位只有 666/2666），已删除。
  - **日期限制「只能查/显示当天及以后」**（今天的日期不允许选早于今天）：航线查询单日/区间选择器加 `disabledDate` 禁过去；班期日历新增「已过期」状态——过去日期灰底 + not-allowed、无 ✓、tooltip「已过期（仅可查看当天及以后）」，图例新增灰点（`.cal-cell-past` / `.cal-dot-past`，含暗色主题）；批转弹窗日期池（openBatch 生成 + 档位条件 onChange 重算）与编辑弹窗日期池（dateOptions + 联动 useEffect + editRow 兜底池）统一按 `todayStr` 过滤过去日期。后端 `query()` 不加强制，避免测试随真实日期推移挂掉（限制全在前端入口）。
  - 去掉监控任务列表头部说明文字「15 秒自动刷新 · 同一航班多个监控日期已合并；不同航班分别成条」，「去航线查询添加」按钮保留。
  - 验证：pnpm build OK（dist/index-BunzIT-l.js，产物中 3666 出现 0 次、含「已过期」文案）、pytest 180 passed（后端未动）、运行中 web_api 直接服务新产物无需重启。git 提交见提交记录。
- [x] **第 9 轮迭代（2026-09-04，航线雷达台视觉优化）**：
  - 顶部双层白色导航合并为紧凑 command bar，保留六个 tab 常驻挂载与原有导航行为；新增「航线雷达台」品牌副标与运行状态。
  - 总览新增深海墨监控状态带，统计卡和低价命中区块重排；任务/总览航线字段复用航段状态线；统一面板、表格、控件、标签和日志的颜色/边框/间距层级。
  - 新增航线雷达台浅色/暗色 token，主题默认跟随系统偏好并支持顶栏手动切换；暗色同步 AntD darkAlgorithm；补齐键盘 focus、reduced-motion、移动端导航/表格横向滚动/表单堆叠。
  - 未改 API、业务规则、查价频控、通知/票据脱敏、日期/档位/余票逻辑；未新增依赖，运行中 web_api 直接服务新前端产物，无需重启。
  - 验证：pnpm build OK（dist/index-DEzNlizm.js / index-B3yj8J2O.css）；git diff --check OK；既有 Playwright 冒烟全部通过；主题切换与暗色空表格/状态带专项检查通过。提交见 Git 历史。

### 关键结论（2026-09-02 / 09-03 排查记录）

1. **PLUS 抓包接口抓错了**：用户抓的是 `airCtLowFareSearch`（普通低价接口），对该接口 PLUS 查询恒返回 `0903 无航班`。
   **正确的 PLUS 端点固定为 `https://app.hnair.com/ticket/lfs/ffl/airLowFareSearch`**（fetcher 模板里原本就有），
   已在 `backend/fetcher.py` 的 `_build_request_profile` 中对 plus 强制使用该 URL（2026-09-02 提交）。
2. **签名算法已对齐线上（2026-09-02 解决）**：逆向 m.hnair.com 前端 bundle `app.ff7f308e1a.js` 拿到官方 `_makeSign`，
   本地重写 `_make_hnair_sign` 后重算签名与抓包逐字符一致；刷新 `common.stime=now` 重签被服务器接受（双跑：原签名 vs 重签均 200，同航班同价格）。
   因此现在可以安全地改 payload 字段（passenger/specialZone 等）再重签；旧结论「不要改动任何字段」仅适用于未开启签名对齐前的旧实现。
   - 算法：`message = 拼接(headers 中 "hna" 前缀值按字典序 + query 值按字典序(排除 hnairSign) + payload common∪data 标量值按字典序 + certificateHash 6093941774D84495A5D15D8F909CAA1E)`；
     `sign = HMAC-SHA1(message, hardCode 21047C596EAD45209346AE29F0350491).hexdigest().upper()`（实现见 `backend/fetcher.py::_make_hnair_sign`）。
3. **签名不覆盖 URL 路径（F3）**：签名验的是 query + headers + payload 要素，不含 path——所以换路径
   `/lfs/ffl/airLowFareSearch` → 200 正常、`/lfs/ffl/airCtLowFareSearch` → 404、`/lfs/airCtLowFareSearch` → 200 但无 PLUS 航班。手动 `"&".join(query)` 拼 URL 会破坏 token 编码，应始终用 `requests.post(params=...)` 自动编码。
4. **普通票价无 normal_curl 时不可用（F4）**：未配置 `normal_curl` 时走「静态模板 + 本地重签」会 `E00001 验签错误`；
   要跑普通票价监控必须由用户抓 `airLowFareSearch`（非 `ffl/`）的 cURL 导入。
5. **第三方渠道调研结论（2026-09-03 实测）**：
   - 高德 / 腾讯位置服务：**无航班相关 API**，不做候选；
   - 聚合数据 juhe.cn「航班动态」（ID 20）：页面标注**维护中**，市场里无其它国内航班接口；个人可注册但要实名 + 场景审核；
   - AviationStack：免费 100 次/月，实时航班状态为主、**国内覆盖弱**，适合验证管线不适合全量；
   - 飞常准（open.variflight.com）：**企业认证门槛高**，个人难申请。
   - 结论：第三方按次计费 + 覆盖差，不适合 1733 条全量校正，改走**官方查价接口免费自扫**（见进度）。
6. 抓包 cURL 含 token/hnairSign/cookie 等凭证，已暴露在聊天记录中；验收完成后建议用户重新抓取覆盖。

## 六、卡点处理约定

- 需要用户本人操作的事（官方登录/验证码/微信扫码/复制 SendKey）**不能由 agent 代办**，agent 负责给出步骤并协助排查。
- 凭证失效后：用户重新抓 cURL 覆盖本地文件 → 让 agent 重新导入并重启 daemon → 再验收。
