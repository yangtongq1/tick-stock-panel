# 一键部署（Windows / Linux / macOS）

> 适用于想 `git clone` 下来立刻跑起来的用户。
> 已在 Windows 10 + Python 3.13 + Node 22 实测通过（Dev 模式，免 Docker）。
> 有 Docker 的用户直接看 [上游 README · 方式 A](README.md)，一条 `docker run` 即可。

## clone 即得完整行情

**仓库自带全量历史行情数据（约 140MB Parquet，随 git 分发）**：日K、增强日K、
指数日K、分钟K、财务四表、除权因子、instruments 维表。clone 完成即拥有与作者
机器一模一样的行情，**无需等首次拉取**，打开就是全市场数据看板。

之后各机器独立增量：盘后管道（工作日 15:35 自动，或手动触发）从免费源拉当日新
分区追加，与仓库历史分区互不干扰（按日分区只追加不覆盖，多机各自增量无冲突）。

想同步最新数据到本机：`git pull` 即可；你的增量也会在盘后管道成功后自动
commit + push 回 GitHub（见下「数据自动发布」）。

## 方式一：一键脚本（推荐）

```bash
git clone https://github.com/yangtongq1/tick-stock-panel.git
cd tick-stock-panel
./quick-start.sh          # Windows 用 PowerShell: .\quick-start.ps1
```

脚本自动完成：检查/安装依赖（uv + pnpm）→ 创建 .env → 启动前后端 → 拉取全市场数据。

## 方式二：手动三步

```bash
# 1. 依赖（首次）
cp .env.example .env          # TICKFLOW_API_KEY 留空 = 免费模式（历史日K）
npm i -g pnpm                 # 或 corepack enable
# uv: winget install astral-sh.uv   (Windows)
#     curl -LsSf https://astral.sh/uv/install.sh | sh   (Linux/macOS)

# 2. 启动
./dev.sh                      # Windows: .\dev.ps1
# 后端 http://localhost:3018 · 前端 http://localhost:3011

# 3. 拉数据（浏览器打开 http://localhost:3011 点"开始获取"，或直接调 API）
curl -X POST http://127.0.0.1:3018/api/pipeline/run
```

约 6 分钟拉完 **5892 只 A 股 × 近 1 年日K**（TickFlow 免费服务器，无需 Key），
之后打开 <http://localhost:3011> 即是全市场数据看板。

## 后台常驻（无终端窗口，Windows）

`dev.ps1` 适合交互开发；想后台常驻用：

```powershell
# 后端
cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --env-file ..\.env --host 127.0.0.1 --port 3018
# 前端（另一个终端）
cd frontend; npx pnpm dev
```

## 常见问题（实测踩坑）

| 现象 | 原因与解决 |
|---|---|
| 首次打开强制 5 步向导 | 正常流程点完即可；卡住可 `curl -X POST http://127.0.0.1:3018/api/settings/onboarding/complete` 跳过 |
| 拉取数据 0 行 | TickFlow 免费服务器要求带 `.SH/.SZ` 后缀；首次拉取会自动建 instruments 维表后再补全，耐心等管线跑完 |
| `dev.ps1` 报 "handle is invalid" | 在无控制台的后台环境跑导致；用上面"后台常驻"方式分起前后端 |
| uv 装完找不到命令 | 装到 `~/.local/bin`，新终端或手动加 PATH |
| 实时行情/分钟K/财务/板块为空 | 确认免费数据源 aksource 在跑：`curl http://127.0.0.1:3021/boards?kind=concept`；没起就 `cd aksource && ../backend/.venv/bin/python server.py`（见 `aksource/README.md`）。**无需任何 API Key** |

## 免费数据源 aksource（板块/财务/实时/分钟，零 Key）

`data/data_sources/aksource.yaml` 已内置（随 git clone 附带），指向本机 `127.0.0.1:3021` 的免费聚合服务（新浪/腾讯/东财公开接口）。启动方式：

```bash
cd aksource
../backend/.venv/bin/python server.py        # Linux/macOS
# Windows: ..\backend\.venv\Scripts\python.exe server.py
```

`quick-start.ps1` / `quick-start.sh` / `start-tsp.ps1` 均会自动带起。能力一览见 `aksource/README.md`。

## 数据落盘位置

全部在本仓库 `data/` 目录（Parquet 文件，零外部数据库）：

```
data/kline_daily/           全 A 股日K（约 29MB/年）
data/kline_daily_enriched/  68 列指标富集数据（约 51MB/年）
data/kline_index_daily/     指数日K（599 个指数）
data/instruments/           股票维表
data/data_sources/          自定义数据源配置（aksource.yaml，随仓库分发）
```

升级：`git pull` 后重启即可，`data/` 运行时数据不受影响（已在 .gitignore 中；
`data/data_sources/` 是配置而非数据，随仓库走）。
