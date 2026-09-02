# 海航监控 · 项目说明与开发约定

> 本文件是本项目（`/Users/Wcg/Desktop/Project_local/海航监控`）的常驻说明，任何 agent 接手前先读我。
> 更新时间：2026-09-02

## 一、这是什么

海航低价机票监控项目（基于上游 `hna-low-fare-monitor` Skill 包，GitHub: huiMiluMilu/hna-hna-low-fare-monitor-skill-shihui）。

- 前端：Streamlit（`app.py`，端口 8501）——配置、任务、日志。
- 后端：`daemon.py` 常驻循环——定时抓价、命中阈值推微信（Server酱）。
- 只读查询与提醒，**不**自动购票/锁票/付款。

## 二、目录与关键文件

```
海航监控/
├── app.py                  # Streamlit 前端
├── daemon.py               # 后端常驻进程
├── backend/                # fetcher / scheduler / notifier / state
├── city_codes.py           # 城市→三字码
├── tests/                  # pytest（7 个）
├── requirements.txt
├── .gitignore              # 已排除 config.json / tasks.json / runtime_state.json / run_log.txt / .venv
├── REVISION_NOTES.md       # 代码审阅记录（发现问题清单）
├── CHANGES_AND_USAGE.md    # 改动说明与使用指南（改了什么/使用方式变化/票据管理入口）
├── PROJECT_NOTES.md        # 本文件：项目说明与开发约定
└── docs/                   # 上游自带的教程（QUICK_START_WITH_AI.md 等）
```

**敏感文件（不入 git）**：`config.json`（含 cURL 凭证模板、SendKey）、`tasks.json`、`runtime_state.json`、`run_log.txt`、`.env`。

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
# 一键启动双进程（Web 已绑定 127.0.0.1，仅本机可访问；启动/停止都用脚本）
./start_all.sh
# 停止
./stop_all.sh
```

手动启动（通常不需要）：
```bash
# 前端
.venv/bin/python -m streamlit run app.py --server.headless true --server.port 8501 --server.address 127.0.0.1
# 后端
.venv/bin/python daemon.py
```

- 页面：http://127.0.0.1:8501（**仅本机可访问**，局域网已不可见）
- 停止：`./stop_all.sh`，或页面点「停止监控」只停抓价循环（daemon 仍待机）。

## 五、当前进度（2026-09-02）

- [x] 安装到本目录（venv、依赖、7/7 测试）
- [x] git 初始化 + 首次提交
- [x] 双进程启动验证（页面 200、daemon 存活）
- [x] 审阅记录落盘（REVISION_NOTES.md）
- [x] 用户提供 PLUS 抓包 cURL 并导入（`.private/requests/hna-plus.txt`，config.json plus_curl）
- [x] 绑定 Server酱 SendKey（用户已收到测试消息）
- [x] **端到端验收通过**：PLUS 端点修正后 daemon 抓到 JD5290/HU7397 各 199 元 → 命中阈值 → 微信已推送（15:20，2 条低价提醒 + 1 条链路验证）
- [x] **优化批次1 已上线（提交 67086af）**：安全绑定 127.0.0.1、日志/历史轮转、任务级错峰、凭证失败退避、结果分类、价格历史、自适应轮询、可选代理、页面任务启停开关（12/12 测试）
- [ ] （可选）用户补抓「普通票价」cURL 并导入，开通普通票价监控（代码路径已完整：URL 两个入口、任务页「普通票价」选项、fare_type 分发都在；但无 normal_curl 时走静态模板+本地重签，实测 E00001 验签错误，见 REVISION_NOTES F4）
- [ ] 优化项 7「stime 刷新+重签」：阻塞于签名算法逆向（见 REVISION_NOTES F2），后续专项

### 关键结论（2026-09-02 排查记录）

1. **PLUS 抓包接口抓错了**：用户抓的是 `airCtLowFareSearch`（普通低价接口），对该接口 PLUS 查询恒返回 `0903 无航班`。
   **正确的 PLUS 端点固定为 `https://app.hnair.com/ticket/lfs/ffl/airLowFareSearch`**（fetcher 模板里原本就有），
   已在 `backend/fetcher.py` 的 `_build_request_profile` 中对 plus 强制使用该 URL（2026-09-02 提交）。
2. **签名算法对不上线上**：`_make_hnair_sign` 重算结果与抓包签名不一致（比对为 False），
   因此改 passenger/specialZone 等任何参数后重新签名必然 `验签错误 E00001`。
   结论：**不要改动抓包 payload 里的任何字段**，只换 origin/destination/departureDate（这三个字段不在签名内，已验证服务器接受）。
   签名算法本身留给后续优化（对齐官方前端 JS 算法）再做。
3. 抓包 cURL 含 token/hnairSign/cookie 等凭证，已暴露在聊天记录中；验收完成后建议用户重新抓取覆盖。

## 六、卡点处理约定

- 需要用户本人操作的事（官方登录/验证码/微信扫码/复制 SendKey）**不能由 agent 代办**，agent 负责给出步骤并协助排查。
- 凭证失效后：用户重新抓 cURL 覆盖本地文件 → 让 agent 重新导入并重启 daemon → 再验收。