# 一键部署（Windows / Linux / macOS）

> `git clone` 后**一条命令**，浏览器打开 <http://localhost:3018> 即是完整系统。
> 运行只需 Python 3.11+（免 Docker、免数据库、免 API Key、**无需 Node**——前端构建产物随仓库分发）。
> 已在 Windows 10 + Python 3.13 实测通过。

## clone 即得完整行情 + 前端

**仓库自带两类开箱即用内容**：
1. **全量历史行情**（约 140MB Parquet）：日K、增强日K、指数日K、分钟K、财务四表、除权因子、instruments 维表——clone 完成即拥有与作者机器一模一样的行情，无需等首次拉取
2. **前端构建产物**（`frontend/dist`，约 7MB）：后端直接伺服，无需 Node/npm/pnpm 即可运行

之后各机器独立增量：盘后管道（工作日 15:35 自动，或手动触发）从免费源拉当日新
分区追加，与仓库历史分区互不干扰（按日分区只追加不覆盖，多机各自增量无冲突）。

想同步最新数据到本机：`git pull` 即可；你的增量也会在盘后管道成功后自动
commit + push 回 GitHub（见下「数据自动发布」）。

## 一键启动（推荐）

```bash
git clone https://github.com/yangtongq1/tick-stock-panel.git
cd tick-stock-panel
./quick-start.sh          # Windows PowerShell: .\quick-start.ps1
```

Windows 也可直接**双击 `start.bat`**（等价于 `.\quick-start.ps1`，同样支持 `-Rebuild` 参数）。

脚本幂等，自动完成五步（已装/已建自动跳过，日常启动秒过）：

1. 检查/安装依赖（Python 3.11+、uv）——**无需 Node**
2. 创建 `.env`
3. 后端依赖 `uv sync`
4. 前端：使用仓库自带的 `frontend/dist` 构建产物（跳过）
5. 启动三件套并自动打开浏览器：
   - **后端 3018**（直接伺服前端页面，页面+API 同一地址）
   - 免费数据源 aksource 3021（实时/分钟/板块/财务）

就绪后浏览器自动打开 <http://localhost:3018>。

**改了前端代码才需要重建**（此时才需要 Node 20+）：
`./quick-start.sh rebuild`（Windows: `.\quick-start.ps1 -Rebuild`）。
重建用的 pnpm 优先走官方独立安装器（get.pnpm.io），不依赖 npm——避免 npm 组件损坏的机器卡死。

## 手动分步（可选）

```bash
cp .env.example .env                       # 配置（免费模式无需填 Key）
cd backend && uv sync && cd ..             # 后端依赖
cd aksource && ../backend/.venv/bin/python server.py &  # 数据源 3021
cd backend && ./.venv/bin/python -m uvicorn app.main:app \
  --env-file ../.env --host 0.0.0.0 --port 3018       # 后端 (伺服自带的前端 dist)
```

## 常见问题（实测踩坑）

| 现象 | 原因与解决 |
|---|---|
| 首次打开强制 5 步向导 | 正常流程点完即可；卡住可 `curl -X POST http://127.0.0.1:3018/api/settings/onboarding/complete` 跳过 |
| 打开 3018 是 404 | `frontend/dist` 缺失（极少数情况）；带 Node 的机器跑 `.\quick-start.ps1 -Rebuild` 重建 |
| 后端 90s 未就绪 | 看 `data/logs/backend.err.log`（Windows）/ `data/logs/backend.log`（Linux/macOS） |
| uv 装完找不到命令 | 装到 `~/.local/bin`，新终端或手动加 PATH |
| `npm i -g pnpm` 报 MODULE_NOT_FOUND | 该机 npm 组件损坏；用官方独立安装器：Windows `iwr https://get.pnpm.io/install.ps1 -UseBasicParsing \| iex`，Linux/macOS `curl -fsSL https://get.pnpm.io/install.sh \| sh -` |
| 实时行情/分钟K/财务/板块为空 | 确认免费数据源 aksource 在跑：`curl http://127.0.0.1:3021/boards?kind=concept`；没起就重跑 quick-start 脚本（见 `aksource/README.md`）。**无需任何 API Key** |
| 前端改动后页面不变 | 构建产物有版本缓存；`./quick-start.sh rebuild` 重建 |

## 免费数据源 aksource（板块/财务/实时/分钟，零 Key）

`data/data_sources/aksource.yaml` 已内置（随 git clone 附带），指向本机 `127.0.0.1:3021` 的免费聚合服务（新浪/腾讯/东财公开接口）。quick-start 脚本会自动带起。能力一览见 `aksource/README.md`。

## 数据落盘位置

全部在本仓库 `data/` 目录（Parquet 文件，零外部数据库）：

```
data/kline_daily/           全 A 股日K（约 29MB/年）
data/kline_daily_enriched/  68 列指标富集数据（约 51MB/年）
data/kline_index_daily/     指数日K（599 个指数）
data/instruments/           股票维表
data/data_sources/          自定义数据源配置（aksource.yaml，随仓库分发）
```

数据**随仓库分发**：`git pull` 即同步最新行情（各机盘后管道自动 commit+push 增量）。
代码升级：`git pull` 后重跑 quick-start 脚本即可（幂等）；前端有更新时 dist 也随仓库到达，无需手动构建。
