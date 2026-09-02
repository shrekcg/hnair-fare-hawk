# jipiaojiankong

海航机票低价监控（Streamlit 前端 + daemon 后端）。

## 收到项目后怎么使用

- 首先打开 [`START_HERE.md`](START_HERE.md)。
- Windows 和 macOS 用户都只需从 [`docs/QUICK_START_WITH_AI.md`](docs/QUICK_START_WITH_AI.md) 开始。
- 这份极简教程包含安装、获取本人海航查询请求、绑定微信、创建任务和真实查价验收。

## 本地启动

```bash
pip install -r requirements.txt
cp config.example.json config.json
cp tasks.example.json tasks.json
cp runtime_state.example.json runtime_state.json
streamlit run app.py
```

后端常驻进程：

```bash
python daemon.py
```

## 说明

- `config.json`、`tasks.json`、`runtime_state.json`、`.env` 含本地配置与敏感信息，已在 `.gitignore` 中排除。
- 发布包不得包含作者的本地配置、请求文件、日志或通知密钥。
