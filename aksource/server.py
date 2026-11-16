#!/usr/bin/env python3
"""TSP 免费数据源服务 v2 — 新浪 HTTP 行情为主源（适配当前网络环境）

网络环境变化 (2026-11-16): 本机 HTTPS 出站大面积中断, Baostock 端口(17777)不通,
仅部分 HTTP 行情接口可用。实测可用源:
  新浪日K   http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData  (scale=240)
  新浪分钟K 同上接口 scale=5/15/30/60
  新浪实时   http://hq.sinajs.cn/list=sh600519 (需 Referer)
  腾讯实时   http://qt.gtimg.cn/q=sh600519 (备用, 重试后可用)

接口契约见 tick-stock-panel/docs/custom-data-source.md
依赖: pip install fastapi uvicorn pandas requests
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

# 绕开本机代理: 所有上游请求直连
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

import pandas as pd  # noqa: E402
import requests  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aksrc] %(message)s")
log = logging.getLogger("aksrc")

app = FastAPI(title="TSP Free Data Source (Sina HTTP)")

SINA_KLINE = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
SINA_HQ = "http://hq.sinajs.cn/list="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}


def to_sina(symbol: str) -> str:
    """600519.SH -> sh600519"""
    code, _, suffix = symbol.partition(".")
    return f"{suffix.lower()}{code}"


def http_get(url: str, retries: int = 3) -> str | None:
    """带重试的 HTTP GET (网络抖动大, 首次请求常失败)"""
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200 and r.text.strip():
                return r.text
            if r.status_code == 200 and not r.text.strip():
                return "[]"
        except requests.RequestException:
            pass
        time.sleep(1.5)
    return None


class BatchReq(BaseModel):
    symbols: list[str]
    start_time: str | None = None
    end_time: str | None = None
    table: str | None = None
    latest: bool | None = None


# ---------------- 日K (新浪 scale=240) ----------------
@app.post("/daily")
def daily(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:100]:
        s = to_sina(sym)
        # datalen 上限 1023, 取足够覆盖 start_time
        datalen = 250
        txt = http_get(f"{SINA_KLINE}?symbol={s}&scale=240&ma=no&datalen={datalen}")
        if not txt:
            continue
        try:
            df = pd.read_json(txt.strip('[]'"'"), typ="series") if False else None
            import json as _json
            arr = _json.loads(txt)
            if not arr:
                continue
            start = (req.start_time or "2020-01-01")[:10]
            end = (req.end_time or datetime.now().strftime("%Y-%m-%d"))[:10]
            for it in arr:
                d = str(it.get("day", ""))[:10]
                if not (start <= d <= end):
                    continue
                rows.append({
                    "symbol": sym, "date": d,
                    "open": float(it.get("open", 0) or 0),
                    "high": float(it.get("high", 0) or 0),
                    "low": float(it.get("low", 0) or 0),
                    "close": float(it.get("close", 0) or 0),
                    "volume": float(it.get("volume", 0) or 0),
                    "amount": 0,
                })
        except Exception as e:  # noqa: BLE001
            log.warning("daily %s fail: %s", s, e)
    log.info("daily %d syms -> %d rows", len(req.symbols), len(rows))
    return {"data": rows}


# ---------------- 除权因子 (新浪无独立因子接口, 返回近似: 留空回退官方源) ----------------
@app.post("/adj_factor")
def adj_factor(req: BatchReq) -> dict:
    return {"data": []}


# ---------------- 实时快照 (新浪 hq, 30s 缓存) ----------------
_RT_STATE: dict[str, Any] = {"last": 0.0, "data": []}

# 全市场股票代码表在首次请求时从新浪全量接口拉取太重, 改为按需单查+缓存
# TSP 前端实时场景主要为自选股列表, 单查完全够用
_HQ_CACHE: dict[str, dict] = {}


def parse_sina_hq(sym: str, text: str) -> dict | None:
    try:
        import re
        m = re.search(r'"([^"]+)"', text)
        if not m:
            return None
        p = m.group(1).split(",")
        if len(p) < 32:
            return None
        # 新浪字段: 0名称 1今开 2昨收 3最新 4最高 5最低 ... 8成交量(股) 9成交额 ...
        def f(i):
            try:
                return float(p[i]) if p[i] not in ("", "000000") else 0.0
            except (ValueError, IndexError):
                return 0.0
        last, prev = f(3), f(2)
        chg = last - prev if prev else 0
        return {
            "symbol": sym, "name": p[0],
            "last_price": last, "prev_close": prev,
            "open": f(1), "high": f(4), "low": f(5),
            "volume": f(8), "amount": f(9),
            "change_pct": (chg / prev) if prev else 0,
            "change_amount": chg,
            "amplitude": ((f(4) - f(5)) / prev) if prev else 0,
            "turnover_rate": 0,
        }
    except Exception:  # noqa: BLE001
        return None


@app.get("/realtime")
def realtime(symbol: str = "") -> dict:
    import time as _t
    now = _t.time()
    if symbol:
        s = to_sina(symbol)
        cached = _HQ_CACHE.get(s)
        if cached and now - cached["t"] < 30:
            return {"data": [cached["row"]]}
        txt = http_get(f"{SINA_HQ}{s}")
        if txt:
            row = parse_sina_hq(symbol, txt)
            if row:
                _HQ_CACHE[s] = {"t": now, "row": row}
                return {"data": [row]}
        return {"data": [cached["row"]] if cached else []}
    # 无 symbol: 全量模式暂不支持(全市场拉取过重), 返回缓存
    return {"data": _RT_STATE["data"]}


# ---------------- 分钟K (新浪 scale=5/15/30/60) ----------------
@app.post("/minute")
def minute(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        s = to_sina(sym)
        txt = http_get(f"{SINA_KLINE}?symbol={s}&scale=5&ma=no&datalen=200")
        if not txt:
            continue
        try:
            import json as _json
            arr = _json.loads(txt)
            for it in arr:
                rows.append({
                    "symbol": sym, "datetime": str(it.get("day", "")),
                    "open": float(it.get("open", 0) or 0),
                    "high": float(it.get("high", 0) or 0),
                    "low": float(it.get("low", 0) or 0),
                    "close": float(it.get("close", 0) or 0),
                    "volume": float(it.get("volume", 0) or 0),
                })
        except Exception as e:  # noqa: BLE001
            log.warning("minute %s fail: %s", s, e)
    return {"data": rows}


# ---------------- 五档盘口 (新浪 hq 自带五档, 可解析) ----------------
@app.post("/depth5")
def depth5(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        s = to_sina(sym)
        txt = http_get(f"{SINA_HQ}{s}")
        if not txt:
            continue
        try:
            import re
            m = re.search(r'"([^"]+)"', txt)
            if not m:
                continue
            p = m.group(1).split(",")
            if len(p) < 32:
                continue
            def f(i):
                try:
                    return float(p[i]) if p[i] not in ("", "000000") else 0.0
                except (ValueError, IndexError):
                    return 0.0
            rows.append({
                "symbol": sym, "datetime": datetime.now().isoformat(timespec="seconds"),
                "bid1_price": f(11), "bid1_volume": f(12) * 100,
                "bid2_price": f(13), "bid2_volume": f(14) * 100,
                "bid3_price": f(15), "bid3_volume": f(16) * 100,
                "bid4_price": f(17), "bid4_volume": f(18) * 100,
                "bid5_price": f(19), "bid5_volume": f(20) * 100,
                "ask1_price": f(21), "ask1_volume": f(22) * 100,
                "ask2_price": f(23), "ask2_volume": f(24) * 100,
                "ask3_price": f(25), "ask3_volume": f(26) * 100,
                "ask4_price": f(27), "ask4_volume": f(28) * 100,
                "ask5_price": f(29), "ask5_volume": f(30) * 100,
            })
        except Exception as e:  # noqa: BLE001
            log.warning("depth5 %s fail: %s", s, e)
    return {"data": rows}


# ---------------- 财务 (新浪财务指标, HTTP 接口) ----------------
@app.post("/financial")
def financial(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:80]:
        code = sym.split(".")[0]
        # 新浪财务接口: https 需要, 当前网络 HTTPS 不通 → 留空回退官方源
        # 待网络恢复后可改走 akshare
        _ = code
    return {"data": rows}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3021, log_level="info")
