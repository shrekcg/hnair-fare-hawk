# 海航低价监控项目 · 代码审阅记录

> 审阅时间：2026-09-02
> 审阅对象：`hna-low-fare-monitor` Skill 包（GitHub: huiMiluMilu/hna-hna-low-fare-monitor-skill-shihui）
> 审阅人：PenguinHarness（default_agent）
> 状态：已按 TODO 流程安装并跑通基础环节；本文件为发现记录，**优化暂缓执行**（等基础链路验收通过后再做）。

---

## 一、项目机制速览

- 本质是一个 **AI 工具 Skill 包**（ZIP 交付），不是独立 App。
- 由 AI 工具代做：模板安装 → venv → 依赖 → 配置初始化 → 导入用户 cURL → 真实查价验证 → 启动双进程。
- 运行期为双进程解耦：
  - **前端** `app.py`（Streamlit，端口 8501）：只写配置（SendKey / 监控时段 / 任务 / 启停 / 看日志）。
  - **后端** `daemon.py`（常驻循环）：读 `config.json.status == running` 且在监控时段内 → 逐任务抓价 → 阈值命中 → Server酱推微信。
- 通信骨架：`config.json`（配置+凭证模板）、`tasks.json`（任务）、`runtime_state.json`（去重）、`run_log.txt`（日志），portalocker 文件锁保证并发安全。
- 抓价主路径：**保留用户抓包 cURL 作为请求模板**，每轮只动态替换 `origin/destination/departureDate`，签名 `hnairSign` 与时间戳 `stime` 均为抓包时刻原值（`preserve_captured_sign=True`）。
- 凭证加载优先级：环境变量 > `.env` > 远端接口（默认关）> config.json 的 curl > 本地凭证文件（默认关）> 占位符。
- 错误分类：401/403/验签失败/非 JSON → `TokenExpiredError` → `[阻断]` 日志 + 高优先级微信告警（30 分钟节流）；429/5xx → 静默跳过；其余异常 → 返回空列表。
- 提醒去重指纹：`任务ID|日期|出发|到达|票价类型|航班|价格`，冷却默认 1 小时。

---

## 二、审阅发现（按严重程度）

### 🔴 安全问题

1. **Streamlit 默认监听所有网卡（已实测确认）**
   - 现象：`lsof` 显示 `TCP *:8501 (LISTEN)`，同一局域网内任意设备可打开 `http://<本机IP>:8501`，能看到并**修改** SendKey、任务、日志，还能启动/停止监控。
   - 与 README「localhost 只在这台电脑上有效」的描述不符。
   - 修复方向：启动加 `--server.address 127.0.0.1`（属优化项，暂缓）。

2. **凭证明文落盘，Windows 无权限保护**
   - `config.json` 明文存整段 cURL（Cookie/token/签名）与 SendKey。
   - `import_request.py` 仅在非 Windows 执行 `chmod 600`；Windows 无等效保护（已实测 macOS 权限为 `-rw-------`，OK）。

3. **远端凭证拉取是隐藏风险面**
   - `fetcher.py` 支持 `credential_auto.url` 从远端拉凭证（默认关闭）。若拿到被篡改的配置可能被诱导拉取恶意 URL。默认关闭 OK，但手改 `config.json` 要谨慎。

### 🟠 机制弱点（抓包 / 签名 / 风控）

4. **签名不刷新，stime 永久停留抓包时刻**
   - 抓包模板路径保留原始 `hnairSign` 与原始 `stime`：验签必然通过，但「几周前的 stime 反复出现在请求里」是典型风控特征。
   - 运行时没有「刷新 stime + 重签、失败回退」路径（只有静态兜底模板会重签，而那套几乎没人用）。
   - 优化方向：请求前刷新 `common.stime` 并调用 `_make_hnair_sign` 重签，验签失败再回退原始签名；需先用真实 cURL 验证签名算法一致性。

5. **多任务每轮连发，无任务级错峰**
   - `daemon.py` `for task in tasks` 顺序请求，N 个任务即 N 连发（间隔毫秒级），无任务间抖动。
   - 优化方向：任务间加 15–45s 随机间隔或固定相位。

6. **凭证失效后无退避，每轮都撞**
   - `TokenExpiredError` 只做日志+告警节流，daemon 下一轮仍会继续用失效凭证请求。
   - 优化方向：按任务指数退避（2^n × 5min，上限 60min）。

7. **429/5xx 静默跳过，用户无法感知限流**
   - 与「真没航班」「网络错误」「解析失败」在日志中无法区分；`test_query.py` 返回结构也无法区分。
   - 优化方向：结果分类返回 `network / auth / empty / parse`。

8. **无价格历史数据**
   - 每次响应只做阈值比较后丢弃；无法做趋势、故障恢复、自适应频率。

### 🟡 健壮性与体验

9. `real_fetch_price` 末尾 `except Exception` 吞掉所有异常 → 排障靠猜。
10. `run_log.txt` 无限增长，无轮转（一天约 500–1000 行）。
11. 任务 `enabled` 字段存在但页面只能全局启停，无法单独禁用某任务。
12. daemon 无守护/自愈；电脑关机、休眠、断网即停（上游设计使然）。
13. cURL 解析用 `shlex.split`，对 Chrome 的 `$'...'` ANSI-C 引号、`--compressed` 等写法兼容性弱。
14. 无 LICENSE；单 commit；依赖用 `>=` 未锁版本；无 CI/类型检查。

---

## 三、优化方案清单（暂缓，等基础链路跑通后再议）

| # | 优化 | 解决的问题 | 成本 |
|---|---|---|---|
| 1 | 启动绑定 `127.0.0.1` | 页面不再暴露给局域网 | 一行 |
| 2 | 任务级错峰（15–45s 随机） | 防连发、降风控特征 | 小 |
| 3 | 认证失败指数退避 | 不再拿死凭证撞接口 | 中 |
| 4 | 结果分类返回 + 日志区分 | 限流不再静默 | 中 |
| 5 | 价格历史存储 | 趋势/故障恢复/自适应频率 | 中 |
| 6 | 自适应轮询频率 | 请求量可降一个量级 | 中 |
| 7 | stime 刷新 + 重签兜底 | 消除陈旧时间戳风控特征 | 中 |
| 8 | 凭证字段齐全性校验（sens/blackBox/riskToken） | 避免缺失风控指纹 | 小 |
| 9 | 可选代理支持 | 网络受限场景 | 小 |
| 10 | 日志轮转 | 长期运行磁盘 | 小 |
| 11 | 页面暴露任务 enabled 开关 | 体验 | 小 |

**不做红线**：绕过验证码/安全校验、多身份伪装、自动购票锁票。

---

## 四、实测验证记录（2026-09-02）

| 项目 | 结果 |
|---|---|
| 安装（venv + 依赖 + pytest） | 7/7 通过 |
| Streamlit 页面 | HTTP 200 |
| daemon 存活 | 是 |
| 导入 cURL（假数据） | 校验通过、写入 config、权限 600、不输出凭证 |
| 抓价循环（假数据） | running → 监控时段 → 任务 → 请求 → 空结果 → 随机间隔 |
| 占位符阻断 | `{"ok": false, "error_type": "credential"}` exit 2 |
| 监听地址 | `*:8501`（见问题 1） |

---

## 五、上线后追加发现（2026-09-02 15:20，真实凭证实测）

### F1. 抓包接口选错会导致「官方有票、程序无票」
- 用户抓到的 cURL 是 `https://app.hnair.com/ticket/lfs/airCtLowFareSearch`，该接口对 PLUS 查询恒返回 `0903 抱歉，暂时没有可预订的航班`（换日期、航线均如此）。
- 官网「PLUS会员专属抢票通道」展示 ¥199 的请求实际走 **`https://app.hnair.com/ticket/lfs/ffl/airLowFareSearch`**（fetcher 模板 REQUEST_URL_PLUS 原本就正确）。
- 已修复：`_build_request_profile` 对 `fare_type == "plus"` 强制使用 REQUEST_URL_PLUS，不再沿用抓包 URL。修复后实测返回 JD5290 / HU7397，`minLowPrice=199`（含税 319，税费 120），与官网截图一致；daemon 命中阈值并成功推送微信。

### F2. 本地签名算法与线上不一致（重要，影响后续优化）
- 实测：`_make_hnair_sign` 用抓包的 appver/did/stime/token 重算，结果与抓包 `hnairSign` **不一致**（比对 False）。
- 后果：任何「改动 payload 字段 + 重签」的请求都会收到 `E00001 验签错误`（试过 passenger=ADT:1、去掉 specialZone 均如此）。
- 约束：**目前只能原样保留抓包 payload**，每轮只替换 origin/destination/departureDate（实测这三个字段不在签名内，服务器接受）。
- 影响优化项 4/7（stime 刷新 + 重签兜底）：必须先逆向官方前端 JS 拿到真实签名算法，否则该项无法落地。可留作后续专项。

### F3. 签名不覆盖 URL 路径
- 同一条抓包签名 + 原 payload，换 URL 路径：
  - `/lfs/ffl/airCtLowFareSearch` → HTTP 404（该路径不存在）
  - `/lfs/ffl/airLowFareSearch` → 200 正常出票
  - `/lfs/airCtLowFareSearch` → 200（无 PLUS 航班，返回 0903）
- 说明签名验的是 query + headers + payload 要素，不含 path；这也是 F1 修复能成立的前提。