"""盘中同步中断后的当日日K补数脚本 (一次性, 用后即删)。

背景 (2026-11-23): 桌面代理 11:46 死亡 → 运行中后端 (旧代码, httpx 默认
trust_env=True) 到 aksource 127.0.0.1:3021 的请求被死代理端口拒绝
(WinError 10061), 15:35 盘后管道日K 59 批全败。且本机到新浪出站极不稳定
(DNS 失败/连接超时/11s 慢响应), aksource /daily (新浪 scale=240 串行) 单批
100 只必然超时 — 故绕过 aksource, 直接用腾讯日K接口 (aksource adj_factor
同源, 实测首连 12s 后 0.1-0.2s/只, 稳定)。

数据口径:
- 腾讯 day K: [date, open, close, high, low, volume(手)] → volume×100 转股
  (与现有分区 volume 口径一致, 见 000001.SZ: 1.7e8 股级)
- amount 腾讯日K不带 → 0 (aksource /daily 同款处理)
- 只补今日 (date=今天); merge-upsert 语义由 _persist_daily_chunks 保证,
  已有盘中快照行被收盘定值覆盖 (keep="last")

用法 (backend 目录): .venv/Scripts/python.exe ../scripts/backfill_today.py
前置: 等运行中后端的管道任务跑完 (GET /api/pipeline/jobs active=null),
避免 parquet 并发写。
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

# 让 backend/app 可导入
BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import polars as pl  # noqa: E402

QT_KLINE = "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/kline"
TODAY = date.today().isoformat()


def to_qt(symbol: str) -> str:
    code, _, suffix = symbol.partition(".")
    return f"{suffix.lower()}{code}"


def fetch_one(client: httpx.Client, symbol: str) -> list[dict]:
    """单标的腾讯日K → 只取今天的行 (无则空, 停牌/新股)。"""
    try:
        r = client.get(QT_KLINE, params={"param": f"{to_qt(symbol)},day,,,5"})
        r.raise_for_status()
        days = r.json().get("data", {}).get(to_qt(symbol), {}).get("day") or []
        rows = []
        for row in days:
            if len(row) >= 6 and str(row[0])[:10] == TODAY:
                rows.append({
                    "symbol": symbol,
                    "date": TODAY,
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]) * 100,  # 手 → 股
                    "amount": 0.0,
                })
        return rows
    except Exception:
        return []


def main() -> None:
    from app.config import settings  # noqa: E402
    from app.services.kline_sync import _persist_daily_chunks  # noqa: E402
    from app.tickflow.repository import DataStore, KlineRepository  # noqa: E402

    inst = pl.read_parquet(settings.data_dir / "instruments" / "instruments.parquet")
    symbols = sorted(inst["symbol"].cast(pl.Utf8).unique().to_list())
    print(f"symbols = {len(symbols)}, today = {TODAY}")

    client = httpx.Client(timeout=15.0, trust_env=False)
    all_rows: list[dict] = []
    failed: list[str] = []
    t0 = time.time()

    # 16 并发: 实测单只 0.1-0.2s (首连 12s), 5893 只 ≈ 1.5-2 分钟
    with ThreadPoolExecutor(max_workers=16) as ex:
        for symbol, rows in zip(symbols, ex.map(lambda s: fetch_one(client, s), symbols)):
            if rows:
                all_rows.extend(rows)
            else:
                failed.append(symbol)

    print(f"fetched: {len(all_rows)} rows, empty/failed: {len(failed)} "
          f"(样例: {failed[:5]}), elapsed={time.time() - t0:.0f}s")
    client.close()
    if not all_rows:
        print("no rows, abort")
        return

    df = pl.DataFrame(all_rows).with_columns(
        pl.col("date").str.to_date()
    ).sort(["symbol", "date"])

    # 分块交给正规落盘路径 (merge-upsert, keep="last" 覆盖盘中快照)
    repo = KlineRepository(DataStore())
    written = _persist_daily_chunks([df], repo)
    print(f"done: written={written}")


if __name__ == "__main__":
    main()
