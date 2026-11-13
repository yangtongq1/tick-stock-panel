#!/usr/bin/env python3
"""AkShare + Baostock 免费数据源服务 — 为 TSP 提供全量数据能力(绕过 TickFlow 付费 API)

数据源分工:
  日K     ← Baostock (免注册, 无限流, 稳定)
  分钟K   ← AkShare 东财 (现价分钟)
  实时快照 ← AkShare 东财 spot_em
  财务    ← AkShare 新浪/东财财务指标

接口契约见 tick-stock-panel/docs/custom-data-source.md
依赖: pip install fastapi uvicorn akshare pandas baostock
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any

# 绕开本机代理: 所有上游请求直连
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

import akshare as ak  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aksrc] %(message)s")
log = logging.getLogger("aksrc")

app = FastAPI(title="TSP Free Data Source (AkShare+Baostock)")


def to_ak(symbol: str) -> str:
    return symbol.split(".")[0]


class BatchReq(BaseModel):
    symbols: list[str]
    start_time: str | None = None
    end_time: str | None = None
    table: str | None = None
    latest: bool | None = None


# ---------------- Baostock 连接池 (线程安全锁) ----------------
import baostock as bs  # noqa: E402

_BS_LOCK = threading.Lock()
_BS_LOGGED = False


def bs_query(sql: str, params: dict) -> pd.DataFrame | None:
    """线程安全的 baostock 查询: 登录(一次) → 查询 → 不登出(复用连接)"""
    global _BS_LOGGED
    with _BS_LOCK:
        if not _BS_LOGGED:
            r = bs.login()
            if r.error_code != "0":
                log.error("baostock login fail: %s", r.error_msg)
                return None
            _BS_LOGGED = True
        rs = bs.query_history_k_data_plus(**params, fields=FIELDS_K)
        if rs.error_code != "0":
            # 连接可能过期, 重登录一次再试
            bs.logout()
            r = bs.login()
            if r.error_code != "0":
                return None
            rs = bs.query_history_k_data_plus(**params, fields=FIELDS_K)
            if rs.error_code != "0":
                log.warning("baostock query fail: %s", rs.error_msg)
                return None
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)


FIELDS_K = "date,open,high,low,close,volume,amount"


# ---------------- 日K (Baostock 主力) ----------------
@app.post("/daily")
def daily(req: BatchReq) -> dict:
    start = (req.start_time or "2020-01-01").replace("-", "")[:8]
    end = (req.end_time or datetime.now().strftime("%Y%m%d")).replace("-", "")[:8]
    start = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    rows: list[dict] = []
    for sym in req.symbols[:200]:
        code, _, suffix = sym.partition(".")
        bs_code = f"sh.{code}" if suffix == "SH" else f"sz.{code}"
        try:
            df = bs_query("k", {
                "code": bs_code,
                "start_date": start, "end_date": end,
                "frequency": "d", "adjustflag": "3",  # 3=不复权
            })
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                def n(v):
                    try:
                        return float(v) if v not in ("", None) else None
                    except (TypeError, ValueError):
                        return None
                if n(r["close"]) is None:
                    continue
                rows.append({
                    "symbol": sym, "date": r["date"],
                    "open": n(r["open"]), "high": n(r["high"]),
                    "low": n(r["low"]), "close": n(r["close"]),
                    "volume": n(r["volume"]) or 0,
                    "amount": n(r["amount"]) or 0,
                })
        except Exception as e:  # noqa: BLE001
            log.warning("daily %s fail: %s", bs_code, e)
    log.info("daily %d syms -> %d rows", len(req.symbols), len(rows))
    return {"data": rows}


# ---------------- 除权因子 (Baostock 复权因子) ----------------
@app.post("/adj_factor")
def adj_factor(req: BatchReq) -> dict:
    rows: list[dict] = []
    today = datetime.now().strftime("%Y-%m-%d")
    for sym in req.symbols[:100]:
        code, _, suffix = sym.partition(".")
        bs_code = f"sh.{code}" if suffix == "SH" else f"sz.{code}"
        try:
            df = bs_query("k", {
                "code": bs_code, "start_date": "2020-01-01", "end_date": today,
                "frequency": "d", "adjustflag": "2",  # 2=前复权
            })
            raw = bs_query("k", {
                "code": bs_code, "start_date": "2020-01-01", "end_date": today,
                "frequency": "d", "adjustflag": "3",  # 3=不复权
            })
            if df is None or raw is None or df.empty or raw.empty:
                continue
            try:
                q = float(df.tail(1).iloc[0]["close"])
                r0 = float(raw.tail(1).iloc[0]["close"])
                if r0:
                    rows.append({"symbol": sym, "trade_date": str(raw.tail(1).iloc[0]["date"]), "ex_factor": round(q / r0, 6)})
            except (TypeError, ValueError):
                continue
        except Exception as e:  # noqa: BLE001
            log.warning("adj %s fail: %s", bs_code, e)
    return {"data": rows}


# ---------------- 实时快照 (东财 spot_em, 30s 缓存) ----------------
_RT_STATE: dict[str, Any] = {"last": 0.0, "data": []}

@app.get("/realtime")
def realtime() -> dict:
    import time
    now = time.time()
    if now - _RT_STATE["last"] < 30 and _RT_STATE["data"]:
        return {"data": _RT_STATE["data"]}
    try:
        df = ak.stock_zh_a_spot_em()
        rows = []
        for _, r in df.iterrows():
            code = str(r["代码"])
            if code.startswith(("4", "8", "92")):
                sym = f"{code}.BJ"
            elif code.startswith(("6", "5", "9")):
                sym = f"{code}.SH"
            elif code.startswith(("0", "1", "2", "3")):
                sym = f"{code}.SZ"
            else:
                sym = f"{code}.SZ"
            def f(v):
                try:
                    return float(v) if pd.notna(v) else 0.0
                except (TypeError, ValueError):
                    return 0.0
            rows.append({
                "symbol": sym, "name": str(r["名称"]),
                "last_price": f(r["最新价"]), "prev_close": f(r["昨收"]),
                "open": f(r["今开"]), "high": f(r["最高"]), "low": f(r["最低"]),
                "volume": f(r["成交量"]), "amount": f(r["成交额"]),
                "change_pct": f(r["涨跌幅"]) / 100,
                "change_amount": f(r["涨跌额"]),
                "amplitude": f(r["振幅"]) / 100,
                "turnover_rate": f(r["换手率"]) / 100,
            })
        _RT_STATE["last"] = now
        _RT_STATE["data"] = rows
        log.info("realtime(东财): %d rows", len(rows))
        return {"data": rows}
    except Exception as e:  # noqa: BLE001
        log.error("realtime fail: %s", e)
        return {"data": _RT_STATE["data"]}


# ---------------- 分钟K (AkShare 东财 1m) ----------------
@app.post("/minute")
def minute(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        code = to_ak(sym)
        try:
            df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="")
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                rows.append({
                    "symbol": sym, "datetime": str(r["时间"]),
                    "open": float(r["开盘"]), "high": float(r["最高"]),
                    "low": float(r["最低"]), "close": float(r["收盘"]),
                    "volume": float(r["成交量"]),
                })
        except Exception as e:  # noqa: BLE001
            log.warning("minute %s fail: %s", code, e)
    return {"data": rows}


# ---------------- 五档盘口 (留空回退, 不写脏数据) ----------------
@app.post("/depth5")
def depth5(req: BatchReq) -> dict:
    return {"data": []}


# ---------------- 财务 (AkShare 财务指标) ----------------
@app.post("/financial")
def financial(req: BatchReq) -> dict:
    rows: list[dict] = []
    table = req.table or "metrics"
    for sym in req.symbols[:80]:
        code = to_ak(sym)
        try:
            if table == "metrics":
                df = ak.stock_financial_analysis_indicator(symbol=code)
                if df is None or df.empty:
                    continue
                for _, r in df.tail(12).iterrows():
                    def num(col, _r=r):
                        try:
                            v = _r.get(col)
                            return float(v) if v not in (None, "--", "") else None
                        except (TypeError, ValueError):
                            return None
                    rows.append({
                        "symbol": sym, "period_end": str(r.get("日期", ""))[:10],
                        "eps_basic": num("基本每股收益(元)"),
                        "roe": num("加权净资产收益率(%)"),
                        "gross_margin": num("销售毛利率(%)"),
                        "net_margin": num("销售净利率(%)"),
                        "debt_to_asset_ratio": num("资产负债率(%)"),
                    })
        except Exception as e:  # noqa: BLE001
            log.warning("financial %s fail: %s", code, e)
    return {"data": rows}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3021, log_level="info")
