# 海航监控 · 接手提示词（复制给下一个 agent）

> 用法：把下面整段（含「【粘贴开始】…【粘贴结束】」之间内容）直接粘贴给接手 agent 作为第一轮 prompt，
> 或由用户在对话里 @ 新 agent 并发送本文件路径让其先读。

【粘贴开始】

你接手「海航监控」项目，路径 `/Users/Wcg/Desktop/Project_local/海航监控`，项目根在用户桌面，会持续迭代。

**开始之前（必读）**
1. 完整读 `AGENT_HANDOFF.md`（项目快照 + 协作准则 + 遗留事项，是总入口）、`README.md`（架构/启动/票据）、`PROJECT_NOTES.md`（迭代历史与关键结论）。
2. 执行 `cd /Users/Wcg/Desktop/Project_local/海航监控 && git log --oneline -10` 了解最近工作；确认 `git status` 干净。
3. 确认服务在跑：`pgrep -fl "daemon.py|web_api.py"`（web_api 端口 8501，仅 127.0.0.1）。pid 文件可能过期，一律以 pgrep/lsof 为准。

**项目一句话**：海航随心飞（666/2666 会员专享价）低价监控 + 航线查询控制台。底表提供静态航班信息（航线/班期/时刻/可飞日期/档位规则），价格/经停/余票为官方接口实时查价（需抓包票据，存 config.json，不入库、不回显凭证）。

**协作契约（必须遵守，详见 AGENT_HANDOFF.md §6）**
- Git：每批功能一个提交（`feat: 第 N 轮迭代 — …` / `docs:` / `data:` / `fix:`，中文、可读），提交信息末尾附验证结果；工作区保持干净；运行数据/凭证（config.json、tasks.json、runtime_state.json、notification_history.jsonl、web/dist/ 等）绝不入库。
- 改动：最小改动、只动相关文件；改前先读再改；不添加臆测功能；前端改默认补暗色主题、后端改默认补 pytest 用例；破坏性/不可逆操作先问用户。
- 验证：前端改必 `cd web && pnpm build` 成功，交互涉及时用 Playwright 冒烟（系统 Chrome：`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，playwright 包在 `/Users/Wcg/.npm/_npx/360550e4913b8759/node_modules/playwright`，可参考 /tmp 下既有脚本）；后端改必 `.venv/bin/python -m pytest tests/ -q` 全量（当前 180 passed）。
- 热加载边界：改前端 build 即可，web_api 无需重启；**改后端 Python 必须重启对应服务**——按 AGENT_HANDOFF.md §3：先 `lsof -nP -i :8501` 确认端口释放，kill 要同时停 bash 包装 + Python 子进程，启动用 `env -u ELECTRON_RUN_AS_NODE .venv/bin/python web_api.py` 且必须由 harness 以 run_in_background 方式运行（严禁命令里 `&` 自后台化，进程组会被清理）。
- 测试时间炸弹：后端不得写"相对今天"的硬限制/断言，锚点日期用固定值（如 2026-10-10）。
- 每轮收尾：更新 README（结构性变化）/ PROJECT_NOTES（进度条目）/ AGENT_HANDOFF（§1 快照与机制如有变化），并更新 agent 侧记忆；然后 git 提交，最后 `git status` 干净。
- 敏感约束：任何输出不回显完整凭证（SendKey 掩码、AppSecret 永不回显、票据摘要去 query）；不读 `.project_config.toml` / `.vault.toml`。

**当前基线（2026-09-04，第 8 轮后）**：pytest 180 passed；pnpm build 产物 `web/dist/assets/index-BunzIT-l.js`；git HEAD `b10878c`；遗留事项见 AGENT_HANDOFF.md §7（如有不明，先读文档再问用户，不猜）。

开始工作后，按用户指令执行；每轮交付用中文，结论先行，复杂任务收尾四段：做了什么 / 验证了什么 / 遗留限制与风险 / 是否更新了记忆。

【粘贴结束】