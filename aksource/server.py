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
import threading
import time
from datetime import datetime
from typing import Any

# 绕开本机代理: 所有上游请求直连
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
os.environ.setdefault("NO_PROXY", "*")

import pandas as pd  # noqa: E402
try:  # requests 优先; 环境只有 httpx 时 (TSP backend venv) 自动降级
    import requests as _requests_mod
    _requests_exc = (_requests_mod.RequestException,)
    def _http_get_raw(url: str, headers: dict, timeout: float):
        return _requests_mod.get(url, headers=headers, timeout=timeout)
except ImportError:  # httpx 兼容层
    import httpx as _requests_mod  # type: ignore[no-redef]
    _requests_exc = (Exception,)
    def _http_get_raw(url: str, headers: dict, timeout: float):
        return _requests_mod.get(url, headers=headers, timeout=timeout)
from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [aksrc] %(message)s")
log = logging.getLogger("aksrc")

app = FastAPI(title="TSP Free Data Source (Sina HTTP)")

SINA_KLINE = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
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
            r = _http_get_raw(url, HEADERS, timeout)
            if r.status_code == 200:
                if not r.content.strip():
                    return "[]"
                try:
                    return r.content.decode("gbk", errors="replace")
                except UnicodeDecodeError:
                    return r.text
        except _requests_exc:
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


# ---------------- 除权因子 (腾讯 qfq/raw 对比推导) ----------------
QT_FQKLINE = "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"
QT_KLINE = "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/kline"


def adj_factor_rows(sym: str, days: int = 400) -> list[dict]:
    """推导除权因子: 腾讯前复权(qfq)与不复权(raw)日K收盘对比。

    ex_factor 定义 (TSP minute_adjust 契约): 当日 qfq/raw 比值 ÷ 前一日比值,
    即相邻除权事件比。TSP 侧 cum_prod 后 cum/total 做 ratio, 历史价 ×ratio
    即得前复权口径。波动 <0.2% 的比值噪声用 3 位小数圆整滤掉 (真实除权
    事件比值跳变通常 >1%)。
    """
    s = to_sina(sym)
    qfq_body = http_get(f"{QT_FQKLINE}?param={s},day,,,{days},qfq", retries=2, timeout=12.0)
    raw_body = http_get(f"{QT_KLINE}?param={s},day,,,{days}", retries=2, timeout=12.0)
    if not qfq_body or not raw_body:
        return []
    try:
        import json as _json
        node1 = _json.loads(qfq_body).get("data", {}).get(s, {})
        qfq = node1.get("qfqday") or node1.get("day") or []
        node2 = _json.loads(raw_body).get("data", {}).get(s, {})
        raw = node2.get("day") or []
        qfq_close = {str(r[0]): float(r[2]) for r in qfq if len(r) >= 3}
        raw_close = {str(r[0]): float(r[2]) for r in raw if len(r) >= 3}
        common = sorted(set(qfq_close) & set(raw_close))
        if len(common) < 2:
            return []

        rows: list[dict] = []
        prev_ratio: float | None = None
        for d in common:
            rc = raw_close[d]
            if rc <= 0:
                continue
            ratio = qfq_close[d] / rc
            if prev_ratio is not None:
                # 圆整 3 位小数滤噪声; 与前日比值差异 >0.5% 视为除权事件
                r_now, r_prev = round(ratio, 3), round(prev_ratio, 3)
                if abs(r_now - r_prev) > 0.005:
                    rows.append({
                        "symbol": sym,
                        "trade_date": d,
                        "ex_factor": r_now / r_prev,
                    })
            prev_ratio = ratio
        return rows
    except Exception as e:  # noqa: BLE001
        log.warning("adj_factor %s fail: %s", s, e)
        return []


@app.post("/adj_factor")
def adj_factor(req: BatchReq) -> dict:
    """并行推导除权因子 (每只 2 次 HTTP, 串行太慢; 12 并发压到 ~1/12 耗时)"""
    from concurrent.futures import ThreadPoolExecutor

    symbols = req.symbols[:50]
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for events in ex.map(adj_factor_rows, symbols):
            rows.extend(events)
    log.info("adj_factor %d syms -> %d events", len(symbols), len(rows))
    return {"data": rows}


# ---------------- 腾讯行情源 (qt.gtimg.cn, 实时+五档共用) ----------------
QT_QUOTE = "http://qt.gtimg.cn/q="

# 指数行情: TSP CORE_INDEX_SYMBOLS(核心四只) + BENCHMARK_INDEX_SYMBOLS(板块基准)
# 自定义 YAML 源无法实现插件协议的 get_realtime_indices(), 指数走全市场快照内嵌:
# TSP _process_full_market_records 按 symbol 集合自动分流 index 记录
INDEX_SYMBOLS = [
    "000001.SH",  # 上证指数
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "000680.SH",  # 科创综指
    "000002.SH",  # A股指数 (SH 基准)
    "000688.SH",  # 科创50 (STAR 基准)
    "399107.SZ",  # 深证A指 (SZ/GEM 基准)
    "399102.SZ",  # 创业板综 (GEM 基准)
    "899050.BJ",  # 北证50 (BJ 基准)
]


def parse_qt_index(sym: str, text: str) -> dict | None:
    """解析腾讯指数行情 (字段布局与个股相同, 指数无换手/量纲不同)"""
    row = parse_qt(sym, text)
    if row is None:
        return None
    # 指数成交量单位为手(百股), 金额为万元; 指数无换手率/振幅语义, 置 0
    row["turnover_rate"] = 0.0
    return row


# ---------------- 实时快照 (腾讯 qt, 单股 5s TTL + 全市场后台刷新) ----------------

# 单股查询缓存 (自选股场景, 5s TTL)
_HQ_CACHE: dict[str, dict] = {}

# 全市场快照: 后台线程 90s 一轮全量刷新, 请求命中缓存秒回
# 背景: TSP quote_service._fetch_full_market_quotes 调 get_realtime() 无参,
#      腾讯批量限速 ~3 QPS, 5892 只一轮实测 65-80s, 同步等待会顶爆 TSP 的 60s timeout
_FULL_RT_TTL = 30.0  # 服务化后连接热, 实测一轮 10-13s, 30s TTL 数据足够新鲜
_FULL_RT_WORKERS = 8
_FULL_RT_STATE: dict[str, Any] = {
    "rows": [],          # 全市场行情列表
    "by_symbol": {},     # symbol -> row
    "last_ok": 0.0,      # 上次成功刷新时间
    "refreshing": False,
    "refresh_count": 0,
    "errors": 0,
}
_FULL_RT_LOCK = threading.Lock()
_INSTRUMENTS: list[str] | None = None

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_INSTRUMENTS_PATH = os.path.join(_BASE_DIR, "..", "data", "instruments", "instruments.parquet")


def _load_instruments() -> list[str]:
    """全市场代码表 (instruments.parquet, 首次调用加载后常驻)"""
    global _INSTRUMENTS
    if _INSTRUMENTS is None:
        try:
            df = pd.read_parquet(_INSTRUMENTS_PATH, columns=["symbol"])
            syms = [str(s) for s in df["symbol"].tolist() if s]
            _INSTRUMENTS = syms
            log.info("instruments loaded: %d symbols", len(syms))
        except Exception as e:  # noqa: BLE001
            log.warning("load instruments fail: %s", e)
            _INSTRUMENTS = []
    return _INSTRUMENTS


def _refresh_full_market() -> None:
    """全量拉一轮腾讯行情 (30 只/批 × 197 批, 8 并发, 实测 65-80s)"""
    from concurrent.futures import ThreadPoolExecutor

    symbols = _load_instruments()
    if not symbols:
        return
    t0 = time.time()
    sina_syms = [to_sina(s) for s in symbols]
    batches = [sina_syms[i:i + 30] for i in range(0, len(sina_syms), 30)]
    # 指数并进同一批刷新 (指数 9 只单独一批, 腾讯指数代码与个股同接口)
    batches.append([to_sina(s) for s in INDEX_SYMBOLS])
    raw_map: dict[str, str] = {}
    errors = 0

    def fetch(batch: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        body = http_get(f"{QT_QUOTE}{','.join(batch)}", retries=3)
        if not body:
            return out
        for line in body.splitlines():
            m = re.search(r'v_(\w+)="([^"]+)"', line)
            if m:
                out[m.group(1)] = f'"{m.group(2)}"'
        return out

    with ThreadPoolExecutor(max_workers=_FULL_RT_WORKERS) as ex:
        for out in ex.map(fetch, batches):
            if out:
                raw_map.update(out)
            else:
                errors += 1

    rows: list[dict] = []
    by_symbol: dict[str, dict] = {}
    for sym in symbols:
        body = raw_map.get(to_sina(sym))
        if not body:
            continue
        row = parse_qt(sym, body)
        if row:
            rows.append(row)
            by_symbol[sym] = row

    # 指数行情 (与个股同字段布局, TSP 按 symbol 集合自动分流为 index)
    index_rows: list[dict] = []
    for sym in INDEX_SYMBOLS:
        body = raw_map.get(to_sina(sym))
        if not body:
            continue
        row = parse_qt_index(sym, body)
        if row:
            index_rows.append(row)
            by_symbol[sym] = row

    with _FULL_RT_LOCK:
        if rows:
            _FULL_RT_STATE["rows"] = rows + index_rows
            _FULL_RT_STATE["by_symbol"] = by_symbol
            _FULL_RT_STATE["last_ok"] = time.time()
            _FULL_RT_STATE["refresh_count"] += 1
        _FULL_RT_STATE["errors"] = errors
        _FULL_RT_STATE["refreshing"] = False
    log.info(
        "full-market refresh #%d: %d/%d rows + %d indices in %.1fs (%d batch errors)",
        _FULL_RT_STATE["refresh_count"], len(rows), len(symbols),
        len(index_rows), time.time() - t0, errors,
    )


def _full_market_refresher() -> None:
    """后台守护线程: 每 90s 刷新一轮全市场快照 (启动后立即跑第一轮)"""
    while True:
        with _FULL_RT_LOCK:
            if _FULL_RT_STATE["refreshing"]:
                time.sleep(5)
                continue
            _FULL_RT_STATE["refreshing"] = True
        try:
            _refresh_full_market()
        except Exception as e:  # noqa: BLE001
            with _FULL_RT_LOCK:
                _FULL_RT_STATE["refreshing"] = False
            log.warning("full-market refresh fail: %s", e)
            time.sleep(10)
        time.sleep(_FULL_RT_TTL)


# 后台刷新线程经 FastAPI startup 事件拉起 (见文件底部 @app.on_event),
# 避免 import 时竞态 (parse_qt/qt_get 定义在模块后半部)


_RT_CACHE_TTL = 5.0  # 单股缓存秒数: 腾讯源本身 3-5s 延迟, 缓存 5s 保证端到端 10s 内且不放大请求量


@app.get("/realtime")
def realtime(symbol: str = "") -> dict:
    now = time.time()
    if symbol:
        cached = _HQ_CACHE.get(symbol)
        if cached and now - cached["t"] < _RT_CACHE_TTL:
            return {"data": [cached["row"]]}
        # 单股优先走全市场缓存 (90s 内), 过期才单独拉
        full = _FULL_RT_STATE["by_symbol"].get(symbol)
        if full and now - _FULL_RT_STATE["last_ok"] < _FULL_RT_TTL:
            return {"data": [full]}
        raw = qt_get([symbol])
        body = raw.get(to_sina(symbol))
        if body:
            row = parse_qt(symbol, body)
            if row:
                _HQ_CACHE[symbol] = {"t": now, "row": row}
                return {"data": [row]}
        return {"data": [cached["row"]] if cached else []}
    # 无参 = 全市场快照 (TSP _fetch_full_market_quotes 契约), 后台缓存直接秒回
    return {"data": _FULL_RT_STATE["rows"]}


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


# ---------------- 分钟K (腾讯 m1 主源 + 新浪 5 分钟K 降级) ----------------
QT_MKLINE = "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"


def qt_minute_rows(sym: str, count: int = 320) -> list[dict]:
    """拉腾讯 1 分钟K。m1 数组: [时间YYYYMMDDHHMM, 开, 收, 高, 低, 量(手)]
    注意: 腾讯 mkline 高峰/被限流时响应 12-15s, timeout 用 20s。"""
    s = to_sina(sym)
    body = http_get(f"{QT_MKLINE}?param={s},m1,,{count}", retries=2, timeout=20.0)
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


def sina_minute_rows(sym: str, count: int = 320) -> list[dict]:
    """新浪 5 分钟K (腾讯 m1 失败时的降级源)。
    scale=5 无 1 分钟粒度, 返回 5 分钟K (聚合近似)。"""
    s = to_sina(sym)
    n = max(2, min(count // 5 + 8, 1023))
    body = http_get(f"{SINA_KLINE}?symbol={s}&scale=5&ma=no&datalen={n}", retries=2, timeout=12.0)
    if not body:
        return []
    try:
        import json as _json
        arr = _json.loads(body.strip())
        rows = []
        for it in arr:
            try:
                dt = str(it.get("day", ""))[:16] + ":00"
                rows.append({
                    "symbol": sym, "datetime": dt,
                    "open": float(it.get("open", 0) or 0),
                    "high": float(it.get("high", 0) or 0),
                    "low": float(it.get("low", 0) or 0),
                    "close": float(it.get("close", 0) or 0),
                    "volume": float(it.get("volume", 0) or 0),
                })
            except (ValueError, TypeError):
                continue
        return rows
    except Exception as e:  # noqa: BLE001
        log.warning("sina minute %s fail: %s", s, e)
        return []


def minute_rows(sym: str, count: int = 320) -> list[dict]:
    """分钟K: 腾讯 m1 优先, 失败降级新浪 5 分钟K"""
    rows = qt_minute_rows(sym, count)
    if rows:
        return rows
    return sina_minute_rows(sym, count)


@app.post("/minute")
def minute(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        rows.extend(minute_rows(sym, 320))
    log.info("minute %d syms -> %d rows", len(req.symbols), len(rows))
    return {"data": rows}


# ---------------- 全量分钟 (与 minute 同形, 当日窗口批量) ----------------
@app.post("/full_minute")
def full_minute(req: BatchReq) -> dict:
    rows: list[dict] = []
    for sym in req.symbols[:50]:
        rows.extend(minute_rows(sym, 320))
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


# ---------------- 财务 (新浪财务页: 指标页网页版 + 三大报表 vDOWN 下载版) ----------------
# 网络实测 (2026-11-17): HTTPS 出站已恢复, 新浪财务接口可用。
#   指标页   https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine/stockid/{code}/ctrl/{year}/displaytype/4.phtml
#            (网页版可达; vDOWN_FinancialGuideLine 恒 0B 不可用)
#   利润表   vDOWN_ProfitStatement (vFD_ProfitSheet 网页版恒 0B, 下载版正常)
#   资产负债表 vDOWN_BalanceSheet (网页版与下载版均可, 统一走下载版)
#   现金流量表 vDOWN_CashFlow
# vDOWN 一次返回全部报告期 (2020 至今), 单只 ~0.3s。
SINA_FIN_GUIDE = "https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine/stockid/{code}/ctrl/{year}/displaytype/4.phtml"
SINA_VDOWN = "https://money.finance.sina.com.cn/corp/go.php/vDOWN_{sheet}/displaytype/4/stockid/{code}/ctrl/all.phtml"

# vDOWN 报表行名 → TSP canonical 列 (对照 fuyao provider 字段映射, 单位: 元)
_INCOME_ROWS = {
    "营业收入": "revenue",
    "营业成本": "operating_cost",
    "销售费用": "selling_expense",
    "管理费用": "admin_expense",
    "研发费用": "rd_expense",
    "三、营业利润": "operating_profit",
    "财务费用": "financial_expense",
    "四、利润总额": "total_profit",
    "减：所得税费用": "income_tax",
    "五、净利润": "net_income",
    "归属于母公司所有者的净利润": "net_income_attributable",
    "基本每股收益(元/股)": "basic_eps",
}
_BALANCE_ROWS = {
    "货币资金": "cash_and_equivalents",
    "应收账款": "accounts_receivable",
    "流动资产合计": "total_current_assets",
    "非流动资产合计": "total_non_current_assets",
    "资产总计": "total_assets",
    "负债合计": "total_liabilities",
    "归属于母公司股东权益合计": "total_equity",
}
_CASHFLOW_ROWS = {
    "经营活动产生的现金流量净额": "net_operating_cash_flow",
    "投资活动产生的现金流量净额": "net_investing_cash_flow",
    "筹资活动产生的现金流量净额": "net_financing_cash_flow",
    "购建固定资产、无形资产和其他长期资产所支付的现金": "capex",
    "五、现金及现金等价物净增加额": "net_cash_change",
}


def _num(text: str | None) -> float | None:
    """'1,234.56' → 1234.56; '--'/'' → None"""
    if text is None:
        return None
    t = text.strip().replace(",", "")
    if not t or t == "--":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _fin_years() -> list[int]:
    """指标页按年拉: 近 4 年足够覆盖 TSP 历史累积需求"""
    return [datetime.now().year - i for i in range(4)]


def financial_metrics_rows(sym: str) -> list[dict]:
    """指标页网页版解析: 每年一页, 行=指标, 列=报告期 → 转置成 (period_end, 指标) 行。"""
    code = sym.split(".")[0]
    # 行名 → TSP 列 (新浪百分数值原样保留, TSP 因子按 % 消费)
    guide_rows = {
        "摊薄每股收益(元)": "eps_basic",
        "加权净资产收益率(%)": "roe",
        "主营业务利润率(%)": "gross_margin",
        "销售净利率(%)": "net_margin",
        "资产负债率(%)": "debt_to_asset_ratio",
        "主营业务收入增长率(%)": "revenue_yoy",
        "净利润增长率(%)": "net_income_yoy",
        "每股净资产_调整后(元)": "bps",
    }
    out: dict[str, dict] = {}
    for year in _fin_years():
        html = http_get(SINA_FIN_GUIDE.format(code=code, year=year), retries=2, timeout=15.0)
        if not html or "FinancialGuideLine" not in html and "财务指标" not in html:
            continue
        m = re.search(r'<table[^>]*id="BalanceSheetNewTable0"[^>]*>(.*?)</table>', html, re.S)
        if not m:
            continue
        periods: list[str] = []
        data: dict[str, list[str | None]] = {}
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
            cells = [re.sub(r"<[^>]+>|\s+", "", c).strip() for c in cells]
            if not cells:
                continue
            if cells[0] == "报告日期":
                periods = [c for c in cells[1:] if c]
                continue
            if cells[0] in guide_rows:
                vals = cells[1:]
                data[cells[0]] = [vals[i] if i < len(vals) else None for i in range(len(periods))]
        for i, p in enumerate(periods):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
                continue
            row = out.setdefault(p, {"symbol": sym, "period_end": p})
            for src, dst in guide_rows.items():
                if src in data:
                    row[dst] = _num(data[src][i])
    # 过滤全空指标行 (只有 symbol/period_end 无任何数值)
    return [r for r in out.values() if any(v is not None for k, v in r.items() if k not in ("symbol", "period_end"))]


def financial_sheet_rows(sym: str, sheet: str, rows_map: dict[str, str]) -> list[dict]:
    """vDOWN 下载版解析: TSV, 首行=报表日期, 后续行=科目 → 转置成 (period_end, 科目) 行。"""
    code = sym.split(".")[0]
    text = http_get(SINA_VDOWN.format(sheet=sheet, code=code), retries=2, timeout=20.0)
    if not text or "报表日期" not in text:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return []
    header = lines[0].split("\t")
    periods = [p for p in header[1:] if re.fullmatch(r"\d{8}", p)]
    if not periods:
        return []
    # 列对齐: header 第 1..N 列对应 periods
    out: dict[str, dict] = {}
    for ln in lines[2:]:  # 跳过报表日期行与"单位"行
        parts = ln.split("\t")
        name = parts[0].strip()
        if name not in rows_map:
            continue
        for i, p in enumerate(periods):
            val = _num(parts[i + 1]) if i + 1 < len(parts) else None
            if val is None:
                continue
            row = out.setdefault(p, {"symbol": sym, "period_end": f"{p[:4]}-{p[4:6]}-{p[6:8]}"})
            row[rows_map[name]] = val
    return [out[p] for p in sorted(out, reverse=True)]


@app.post("/financial")
def financial(req: BatchReq) -> dict:
    """财务数据: table=metrics/income/balance_sheet/cash_flow (新浪财务页)。

    shares 表新浪无对应接口 → 返回空 (TSP 侧合并写入保留存量)。
    vDOWN 全报告期一次拿齐, latest 参数不影响返回行数 (TSP 侧自行按
    period_end 合并); metrics 指标页按年拉 4 年。
    """
    from concurrent.futures import ThreadPoolExecutor

    table = req.table or "metrics"
    if table == "shares":
        return {"data": []}

    symbols = req.symbols[:100]

    def fetch_one(sym: str) -> list[dict]:
        try:
            if table == "metrics":
                return financial_metrics_rows(sym)
            if table == "income":
                return financial_sheet_rows(sym, "ProfitStatement", _INCOME_ROWS)
            if table == "balance_sheet":
                return financial_sheet_rows(sym, "BalanceSheet", _BALANCE_ROWS)
            if table == "cash_flow":
                return financial_sheet_rows(sym, "CashFlow", _CASHFLOW_ROWS)
        except Exception as e:  # noqa: BLE001
            log.warning("financial %s %s fail: %s", table, sym, e)
        return []

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for r in ex.map(fetch_one, symbols):
            rows.extend(r)
    log.info("financial %s %d syms -> %d rows", table, len(symbols), len(rows))
    return {"data": rows}


@app.on_event("startup")
def _start_full_rt_refresher() -> None:
    """FastAPI startup: 拉起全市场行情后台刷新线程 (90s 一轮)"""
    if not any(t.name == "full-rt" for t in threading.enumerate()):
        t = threading.Thread(target=_full_market_refresher, daemon=True, name="full-rt")
        t.start()
        log.info("full-market refresher started (TTL %.0fs)", _FULL_RT_TTL)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3021, log_level="info")
