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
# 前端
.venv/bin/python -m streamlit run app.py --server.port 8501
# 后端
.venv/bin/python daemon.py
```

- 页面：http://localhost:8501
- 停止：Ctrl-C，或页面点「停止监控」只停抓价循环（daemon 仍待机）。

> ⚠️ 已知安全限制（见 REVISION_NOTES 问题 1）：页面默认监听所有网卡，局域网内可访问。未做 `--server.address 127.0.0.1` 修复前，请勿在不可信网络使用。

## 五、当前进度（2026-09-02）

- [x] 安装到本目录（venv、依赖、7/7 测试）
- [x] git 初始化 + 首次提交
- [x] 双进程启动验证（页面 200、daemon 存活）
- [x] 审阅记录落盘（REVISION_NOTES.md）
- [ ] **用户提供本人海航查询请求 cURL 文件路径**（普通票价 / PLUS 专享各一份）
- [ ] 导入请求 → 真实查价验收（至少一条「完整航班号 + 票面价」）
- [ ] 用户绑定 Server酱 SendKey（微信扫码自助）→ 测试消息到达微信
- [ ] 创建监控任务 → 端到端验收（日志 + 微信）
- [ ] 之后再做优化（REVISION_NOTES 第三节清单）

## 六、卡点处理约定

- 需要用户本人操作的事（官方登录/验证码/微信扫码/复制 SendKey）**不能由 agent 代办**，agent 负责给出步骤并协助排查。
- 凭证失效后：用户重新抓 cURL 覆盖本地文件 → 让 agent 重新导入并重启 daemon → 再验收。