# 海航监控 · Agent 交接与协作准则（AGENT_HANDOFF）

> 本文件 = **项目现状快照** + **任何 agent（含本项目原 agent）接手/修改时的协作契约**。
> 新 agent 接手第一步：完整读完本文件、`README.md`、`PROJECT_NOTES.md`，再看一遍 `git log --oneline -10`。
> 每轮改完必须按「§6.6 交接循环」收尾，并更新本文件 §1 的快照。

---

## 1. 项目快照（2026-09-04，第 10 轮迭代后）

- **是什么**：海航随心飞（666/2666 会员专享价）机票低价监控 + 航线查询控制台。监控任务命中低价（≤199 元）自动推微信/飞书等渠道通知；航线查询基于底表静态数据 + 官方实时查价。
- **路径**：`/Users/Wcg/Desktop/Project_local/海航监控`（用户桌面，会持续迭代）
- **技术栈**：前端 React + Vite + Ant Design 5（`web/`，lucide-react 图标）；后端为纯标准库 HTTP API `web_api.py`（托管静态文件 + 业务路由），监控循环 `daemon.py`；Python venv `.venv/bin/python`（3.14）
- **端口**：8501，仅绑定 127.0.0.1
- **服务进程（以 `pgrep -fl "daemon.py|web_api.py|sync_from_price_api|progress_reporter"` 为准，pid 文件常过期）**：
  - web_api：bash 包装 33125 + Python 33207（第 10 轮重启）
  - daemon：bash 包装 33213 + Python 33296（第 10 轮重启，监控 2 条任务）
  - 自扫：bash 包装 44415 + Python 44497（`--supervised --interval 60`，进度约 80%，输出见 `/tmp/autoscan.log`）
  - 进度汇报 watchdog：bash 包装 17608 + Python 17690（`progress_reporter.py`，每 10% 节点推飞书）
- **git HEAD**：`188b207`（feat: 第 9 轮迭代补遗 — Graphite Night 主题重构；前一提交 `9ff7736` 观测库价格快照落库），工作区干净
- **验证基线**：pytest 181 passed（约 18s）；`pnpm build` OK，当前产物 `web/dist/assets/index-BsaoFcvD.js` / `index-6perWR6R.css`（dist 不入库，构建产物覆盖即生效，前端改动**无需重启 web_api**）

---

## 2. 目录地图（改哪看哪）

| 路径 | 作用 |
|---|---|
| `web/src/App.jsx` (~2741 行) | 全部前端页面/组件（多 tab 常驻挂载，display:none 切换；command bar 与主题切换） |
| `web/src/styles.css` | 航线雷达台全局样式、浅/暗色 token、响应式与 reduced-motion；班期日历 cal-* 系列 |
| `web/src/icons.jsx` / `cityProvince.js` | lucide 图标包装 / 175 城省份映射 |
| `web_api.py` | HTTP API：/api/tasks/*、/api/flights/*、/api/notify/*、/api/price_query 等；`_build_state`/`_build_task_rows` 组装页面状态 |
| `daemon.py` | 监控主循环：任务展开/查价/阈值命中/渠道通知；queried 缓存、任务错峰 |
| `backend/app.py` | 任务/配置/票据/通知保存等核心逻辑（`_task_key`、`expand_task_dates`、`add_tasks_batch`、`save_price_query`） |
| `backend/fetcher.py` | 海航官方接口：抓包模板、签名 `_make_hnair_sign`、票据派生、经停识别、余票/舱位 |
| `backend/sediment.py` | 底表只读查询（懒加载缓存）：`query()`/`options()`/`meta()`/档位×日期规则 `tier_block_rules()` 等 |
| `backend/city_codes.py` / `observations.py` / `third_party.py` / `channels.py` / `feishu_ws.py` | 城市码、实时观测覆盖、第三方校正、多渠道通知、飞书长连接 |
| `scripts/sedimentation/` | 底表构建/更新管线：`build_normalized.py`、`load_hna666.py`、`compare.py`、`update.py`、`sync_from_price_api.py`（免费自扫）、`apply_observations.py`、`sync_third_party.py` |
| `data/sediment/` | `flights_normalized.json`(1733 条)、`tier_block_rules.json`(档位规则单一事实源)、`observations.json`、`third_party/`、`snapshots/`、`versions.json` |
| `tests/` | pytest 180 用例（fetcher/sediment/observations/third_party/notify/task_batch/web_api…） |
| `docs/` | `notify_channels_guide.md`（渠道配置）、`plus-remain-seat-feasibility.md`、`消息通知渠道调研.md` |

---

## 3. 运行命令速查

```bash
cd /Users/Wcg/Desktop/Project_local/海航监控

# 前端构建（dist 不入库；改前端后必须 build）
cd web && pnpm build

# 后端测试（改后端后必须全量跑）
cd /Users/Wcg/Desktop/Project_local/海航监控 && .venv/bin/python -m pytest tests/ -q

# 进程确认（⚠️ 勿用 pgrep -f "python web_api.py"：实际命令行是大写 Python 路径，匹配不到）
pgrep -fl "daemon.py|web_api.py"
lsof -nP -i :8501   # 端口占用/连接状况

# 一键启停（agent 环境里不可靠：nohup & 会被进程组清理，见下）
./stop_all.sh / ./start_all.sh
```

**长驻服务重启规范（重要，违反会死进程）**：
1. 先 `lsof -nP -i :8501` 确认端口释放；残留旧进程会 bind 失败。
2. kill 时**两个都要停**：bash 包装进程 + Python 子进程（`kill <bash_pid> <py_pid>`，或 `kill 0` 组内）。
3. 启动必须用 harness 的 `exec_command(run_in_background=true)` 且命令前加 `env -u ELECTRON_RUN_AS_NODE`：
   `env -u ELECTRON_RUN_AS_NODE .venv/bin/python web_api.py`
   严禁 shell 里 `... &` 自己后台化——整个进程组在命令退出时会被清理。
4. 启动后 curl 冒烟：`curl -s http://127.0.0.1:8501/api/tasks` 等。

---

## 4. 核心机制（改动前必读）

- **数据链路**：底表（静态：航线/班期/时刻/可飞日期）→ 航线查询；价格/经停/余票/舱位 = 官方接口**实时查价**（`/api/flights/prices`，需抓包票据，`config.json` 中，不入库）。无静态价格数据。
- **档位**：真实可兑档位仅 **666**（限 20:00 后/08:00 前航班，屏蔽春运/五一/暑运/十一）与 **2666**（限 19:00 后/09:00 前，仅屏蔽春运/暑运）。
- **档位×日期规则**：`data/sediment/tier_block_rules.json` 单一事实源 → `sediment.meta()` 下发 → 前端 `DEFAULT_TIER_BLOCK_RULES` 兜底；`filterTierDates()` 三处调用（编辑弹窗 x2、批转弹窗 x2）。**日期限制（只能当天及以后）只在前端做**，后端 query() 不加强制（避免测试随真实日期推移挂掉）。
- **监控任务**：`tasks.json`（不入库）；同航线+航班号+起降时刻分组、日期合并且行内展示最多 4 个+「+N」；任务不能手动建，只能从航线查询批转/编辑/启停/删除。
- **通知**：7 渠道（微信 Server酱/企业微信/钉钉/Bark/ntfy/飞书）；important 加急默认开、critical 强制全渠道 + 通知历史；飞书长连接 `feishu_ws.py` 收卡片回执；**加急权限 `im:message.urgent` 已于 2026-09-04 由用户开通**（遗留关闭）。
- **观测/校正优先级**：实时观测（海航查价）> 第三方校正（落底表）> 旧静态；班期/可飞日期**绝不回写**。实时观测的价格/会员档位/舱位/余票会落观测库 `data/sediment/observations.json` 的 `price_snapshot` 子对象（带 `queried_at` 时效值，新查询覆盖更新、无价格时保留旧快照），只作历史参考、**绝不回写底表**；`apply_to_record` 仍只消费时刻类字段。
- **前端结构**：紧凑 command bar + 六个 tab 常驻挂载（不要在切 tab 时条件卸载组件）；航段状态线作为航线识别元素；浅/暗主题跟随系统并可手动切换；查价频控锁（前端锁 + 后端 min_interval+429）。

---

## 5. Git 边界

**入库**：源码、测试、底表静态数据（flights_normalized.json、tier_block_rules.json）、文档。
**不入库（.gitignore，动态运行数据/凭证）**：`config.json`（票据/通知/时段）、`tasks.json`、`runtime_state.json`、`notification_history.jsonl`、`price_history.jsonl`、`run_log.txt`、`*.pid`、`*.bak`、`web/dist/`、`web/node_modules/`、`.private/`、`参考资料/`（各带 .git 可独立更新）、`data/sediment/observations.json`、`data/sediment/sync_from_price_api_progress.json`、`data/sediment/progress_reporter_state.json`（watchdog 断点）、`data/sediment/snapshots/`、`.impeccable/`（前端评审缓存）。
**敏感约束**：任何输出不回显完整凭证（SendKey 掩码、AppSecret 永不回显、票据摘要去 query）；绝不读 `.project_config.toml` / 任何 `.vault.toml`。

---

## 6. 协作准则（双方契约——本项目所有 agent 必须遵守）

### 6.1 Git 纪律
1. **每批功能一个提交**，延续现有风格：`feat: 第 N 轮迭代 — …` / `docs: …` / `data: …` / `fix: …`，中文描述、可读。
2. 提交信息末尾附验证结果（`验证：pytest 180 passed、pnpm build OK（产物名）`）。
3. 工作区保持干净：不留未提交脏文件过夜；运行数据/凭证绝不 `git add`。
4. 复杂混合改动一次收尾（历史教训：App.jsx 大 hunk 交织导致长期无法提交，最终 `6443b50` 一次性收尾）。
5. 只提交本批相关文件；`git status` 先自查再提交。

### 6.2 改动规范
1. **最小改动**：只改任务相关文件；不动无关文件；不添加臆测功能/抽象/配置/依赖。
2. **改前先读**：用 read_file 精确定位原内容再 edit_file（整串替换），不盲改。
3. **修根因，不修表象**；Bug 先建最小复现再定位（可参考 tests/ 已有用例写法）。
4. 前端改动默认同时补好暗色主题样式；后端改动默认补 pytest 用例。
5. **破坏性/不可逆操作需用户授权**（删文件、清数据、对外发送、发布）。本地验证性操作可直接做。
6. 尊重既有命名与结构约定（如 `cityNameOf`、`filterTierDates`、mono 字体类、Row/Col 栅格风格）。

### 6.3 验证纪律
1. **前端改动**：必须 `pnpm build` 成功；涉及交互（表单/弹窗/日历/表格）需 Playwright 冒烟——系统 Chrome `executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'`，playwright 包在 `/Users/Wcg/.npm/_npx/360550e4913b8759/node_modules/playwright`；可参考 `/tmp/smoke_r8.mjs` 等既有脚本（注意：多 tab 常驻下表格/选项定位必须用 `:visible` 或可见 Dropdown 限定，antd 两字按钮会插空格）。
2. **后端改动**：必须全量 `pytest tests/ -q`（180 个），新增逻辑配套用例。
3. **热加载边界**：改前端 → build 即可（web_api 静态托管 dist，无需重启）；**改后端 Python → 必须按 §3 规范重启 web_api/daemon**（内存代码）。
4. **测试时间炸弹**：后端不写"相对今天"的硬限制/断言（如"过去日期查空"）；锚点日期用固定值（如 2026-10-10）。
5. 长驻服务任何操作遵守 §3 重启规范。

### 6.4 文档与记忆同步（每轮必做）
1. 每轮结束更新：`README.md`（结构性变化）、`PROJECT_NOTES.md`（进度条目：本轮做了什么/验证/提交号）、**本文件 §1 快照 + 目录地图/机制如有变化**。
2. 更新 agent 侧记忆 `hna-monitor-project.md`（用户目录，仅事实/决策/教训，不重复代码状态）：新增本轮条目、删除过时遗留。
3. 文档删除需用户确认或留备份（历史教训：曾删一批文档，备份在 scratchpad）。

### 6.5 沟通与授权
1. 中文沟通，结论先行；复杂交付用四段：做了什么 / 验证了什么 / 遗留限制与风险 / 是否更新了记忆。
2. 明确目标就直接执行；模糊处声明假设后继续（低风险可回滚），影响范围/安全/授权类先问。
3. 用户是项目主人：所有"可选项/歧义项"在交付时列明，等用户拍板。

### 6.6 交接循环（每轮结束 checklist）
- [ ] 代码改动完成、无无关文件
- [ ] 验证过（build / pytest / 冒烟）并把结果写进提交信息
- [ ] `git add` 只含本批文件，提交完成，`git status` 干净
- [ ] README（如涉及）/ PROJECT_NOTES / 本文件快照已同步
- [ ] agent 记忆已更新，遗留事项移出/标记
- [ ] 需要重启的服务已按规范重启并 curl 验证

---

## 7. 当前遗留与待办

- **「去航线查询添加」按钮**：第 8 轮只删了头部说明文字、按钮保留；用户原话有歧义，若指按钮需再确认。
- **规则近似值**：春运区间（02-02~03-13）为 2026 农历近似换算；五一/暑运/十一为法定±1 天，航季外不影响当前选择。
- **自扫**：`scripts/sedimentation/sync_from_price_api.py --supervised --interval 60` 后台续跑中（约 80.6%，输出 `/tmp/autoscan.log`）；全量约 21.6h。
  - **已知特性**：empty 任务会进 failed 并反复重试（脚本 empty 分支未置 task_done，与注释「empty 视为完成」不符）——BAR→HAK 等空航线被重试属正常，不阻塞整体进度。
  - **待用户拍板**：是否 `--reset-progress` 全量补扫一轮（约 21.6h），补齐已扫过部分（约 80.6%）的价格快照；不补则这些历史任务无 `price_snapshot`。

---

## 8. 接手提示词

> 完整可复制的接手提示词在独立文件 **`handoff_prompt.md`**（含【粘贴开始】…【粘贴结束】标记，直接整段粘贴给新 agent 即可）。
> 用法：让新 agent 先读 `handoff_prompt.md` 并按其执行；本文件与它保持同步，改动时两边一起改。
