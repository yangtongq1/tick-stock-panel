# aksource — TSP 免费数据源服务

本地聚合代理：把新浪/腾讯/东方财富的公开行情接口，统一成 TSP 自定义数据源契约（`docs/custom-data-source.md`），跑在 `127.0.0.1:3021`。**零 API Key、零费用。**

## 覆盖能力（TSP 侧 `data/data_sources/aksource.yaml`）

| 端点 | 上游 | 说明 |
|---|---|---|
| `POST /daily` | 新浪日K | 日K（scale=240），单批 100 只 |
| `POST /adj_factor` | 腾讯 qfq/raw 对比 | 除权因子推导（相邻除权事件比） |
| `GET /realtime` | 腾讯 qt | 实时快照；无参=全市场（后台 30s 一轮刷新缓存秒回）；带 `symbol`=单股 5s TTL |
| `POST /minute` | 腾讯 m1 + 新浪 5min 降级 | 分钟K |
| `POST /full_minute` | 同上 | 全量分钟（当日窗口） |
| `POST /depth5` | 腾讯 qt | 五档盘口 |
| `POST /financial` | 新浪财务页 | metrics/income/balance_sheet/cash_flow 四表（含公告日法定时限推导；shares 恒空——上游无此数据） |
| `GET /boards` | 东方财富 push2 | **板块聚合**：`kind=concept`（554 概念）/ `kind=industry`（496 行业），一次返回「股票→板块清单」全量反转行，10 分钟缓存 |

## 启动

```bash
# 依赖复用 TSP backend venv（fastapi/uvicorn/pandas 齐全）
cd aksource
../backend/.venv/bin/python server.py     # Linux/macOS
..\backend\.venv\Scripts\python.exe server.py   # Windows
```

或直接用项目根的启动脚本（自动带起 aksource + 前后端）：

- 交互开发：`./dev.ps1` / `./quick-start.ps1`（Windows）、`./quick-start.sh`（Linux/macOS）
- 后台常驻：`./start-tsp.ps1`（Windows）

## 设计要点

- **代理绕过**：启动时清空 `HTTP_PROXY/HTTPS_PROXY` 等环境变量并设 `NO_PROXY=*`——本机代理会 502 部分行情域名（实测 `vip.stock.finance.sina.com.cn`）。
- **连接复用**：东财板块全量聚合（1050 板块 × 逐板块分页，约 1100+ 请求）必须复用 httpx Client 连接池——逐次新建连接单请求 13s+（DNS 慢），复用后 0.02s/请求，全量 20s。
- **编码**：新浪/腾讯接口 GBK，东财 UTF-8——各自走独立解码路径，不能共用 `http_get`。
- **全市场快照**：腾讯批量限速 ~3 QPS，5892 只一轮 65-80s，同步等待会顶爆 TSP 60s 超时——后台线程 30s 一轮预刷新，请求命中缓存秒回。
- **财务范围**：主板标的（剔除北交所/科创/创业板/ST，约 3239 只，用户约定 2026-11-17）。

## 故障排查

| 现象 | 处置 |
|---|---|
| 板块/财务拉取失败 | 看 `data/logs/aksource.log`；先 `curl http://127.0.0.1:3021/boards?kind=concept` 验证服务活着 |
| 全市场实时为空 | 后台首轮刷新需 10-80s，等一轮再看；日志有 `full-market refresh #N` |
| 东财偶发空响应 | 正常（~1/4 概率），内置 3 次重试 |
