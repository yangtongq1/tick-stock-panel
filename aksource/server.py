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
import re
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


def http_get(url: str, retries: int = 3, timeout: float = 6.0) -> str | None:
    """带重试的 HTTP GET (腾讯接口首次请求常瞬时失败, 快退快重试)"""
    backoff = 0.3
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                if not r.content.strip():
                    return "[]"
                try:
                    return r.content.decode("gbk", errors="replace")
                except UnicodeDecodeError:
                    return r.text
        except requests.RequestException:
            pass
        if i < retries - 1:
            time.sleep(backoff)
            backoff = min(backoff * 2, 1.5)
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


# ---------------- 腾讯实时 (qt.gtimg.cn, 含五档) ----------------
QT_QUOTE = "http://qt.gtimg.cn/q="
# 腾讯 v_ 字段索引 (gbk 解码后按 ~ 切分)
# 1名称 2代码 3最新 4昨收 5今开 6成交量(手) ... 30时间 31涨跌 32涨跌% 33最高 34最低
# 36成交量 37成交额(万) 38换手% 39PE ... 41振幅% ... 43流通市值 44总市值 45PB 46涨停 47跌停
# 买五档: 9-18 (价量交替) 卖五档: 19-28


def parse_qt(sym: str, text: str) -> dict | None:
    try:
        m = re.search(r'"([^"]+)"', text)
        if not m:
            return None
        p = m.group(1).split("~")
        if len(p) < 48 or not p[3]:
            return None
        def f(i):
            try:
                v = float(p[i])
                return v if v != -1 else 0.0
            except (ValueError, IndexError):
                return 0.0
        last, prev = f(3), f(4)
        chg = f(31)
        return {
            "symbol": sym, "name": p[1],
            "last_price": last, "prev_close": prev,
            "open": f(5), "high": f(33), "low": f(34),
            "volume": f(36) * 100, "amount": f(37) * 10000,
            "change_pct": f(32) / 100, "change_amount": chg,
            "amplitude": f(41) / 100, "turnover_rate": f(38) / 100,
        }
    except Exception:  # noqa: BLE001
        return None


def parse_qt_depth5(sym: str, text: str) -> dict | None:
    """腾讯五档: 买一价9 量10 买二价11 量12... 卖一价19 量20..."""
    try:
        m = re.search(r'"([^"]+)"', text)
        if not m:
            return None
        p = m.group(1).split("~")
        if len(p) < 48:
            return None
        def fv(i):
            try:
                return float(p[i])
            except (ValueError, IndexError):
                return 0.0
        bids = [[fv(9 + k * 2), fv(10 + k * 2)] for k in range(5)]
        asks = [[fv(19 + k * 2), fv(20 + k * 2)] for k in range(5)]
        return {"symbol": sym, "bids": bids, "asks": asks}
    except Exception:  # noqa: BLE001
        return None


def qt_get(symbols: list[str]) -> dict[str, str]:
    """批量拉腾讯行情原文, 返回 {新浪代码: "行情体"}"""
    out: dict[str, str] = {}
    syms = [to_sina(s) for s in symbols]
    for i in range(0, len(syms), 30):
        batch = syms[i:i + 30]
        qs = ",".join(batch)
        body = http_get(f"{QT_QUOTE}{qs}", retries=4)
        if not body:
            continue
        try:
            for line in body.splitlines():
                m = re.search(r'v_(\w+)="([^"]+)"', line)
                if m:
                    out[m.group(1)] = f'"{m.group(2)}"'
        except Exception as e:  # noqa: BLE001
            log.warning("qt batch fail: %s", e)
    return out


_RT_CACHE_TTL = 5.0  # 实时缓存秒数: 腾讯源本身 3-5s 延迟, 缓存 5s 保证端到端 10s 内且不放大请求量


@app.get("/realtime")
def realtime(symbol: str = "") -> dict:
    now = time.time()
    if symbol:
        cached = _HQ_CACHE.get(symbol)
        if cached and now - cached["t"] < _RT_CACHE_TTL:
            return {"data": [cached["row"]]}
        raw = qt_get([symbol])
        body = raw.get(to_sina(symbol))
        if body:
            row = parse_qt(symbol, body)
            if row:
                _HQ_CACHE[symbol] = {"t": now, "row": row}
                return {"data": [row]}
        return {"data": [cached["row"]] if cached else []}
    return {"data": _RT_STATE["data"]}


# ---------------- 分钟K (腾讯 m1, 1分钟K) ----------------
QT_MKLINE = "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"


def qt_minute_rows(sym: str, count: int = 320) -> list[dict]:
    """拉腾讯 1 分钟K。m1 数组: [时间YYYYMMDDHHMM, 开, 收, 高, 低, 量(手)]"""
    s = to_sina(sym)
    body = http_get(f"{QT_MKLINE}?param={s},m1,,{count}", retries=3)
    if not body:
        return []
    try:
        import json as _json
        d = _json.loads(body)
        node = d.get("data", {}).get(s, {})
        mk = node.get("m1") or []
        rows = []
        for it in mk:
            if len(it) < 6:
                continue
            t = str(it[0])
            if len(t) == 12:
                dt = f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:00"
            else:
                dt = t
            try:
                rows.append({
                    "symbol": sym, "datetime": dt,
                    "open": float(it[1]), "close": float(it[2]),
                    "high": float(it[3]), "low": float(it[4]),
                    "volume": float(it[5]) * 100,
                })
            except (ValueError, TypeError):
                continue
        return rows
    except Exception as e:  # noqa: BLE001
        log.warning("qt minute %s fail: %s", s, e)
        return []


@app.post("/minute")
def minute(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        rows.extend(qt_minute_rows(sym, 320))
    log.info("minute %d syms -> %d rows", len(req.symbols), len(rows))
    return {"data": rows}


# ---------------- 全量分钟 (与 minute 同形, 当日窗口批量) ----------------
@app.post("/full_minute")
def full_minute(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        rows.extend(qt_minute_rows(sym, 320))
    log.info("full_minute %d syms -> %d rows", len(req.symbols), len(rows))
    return {"data": rows}


# ---------------- 五档盘口 (腾讯 qt, asks/bids 嵌套数组契约) ----------------
@app.post("/depth5")
def depth5(req: BatchReq) -> dict:
    rows: list[dict] = []
    for i in range(0, len(req.symbols[:50]), 30):
        batch = req.symbols[i:i + 30]
        raw = qt_get(batch)
        for sym in batch:
            body = raw.get(to_sina(sym))
            if not body:
                continue
            row = parse_qt_depth5(sym, body)
            if row:
                rows.append({
                    "code": sym, "sell": row["asks"], "buy": row["bids"],
                })
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
