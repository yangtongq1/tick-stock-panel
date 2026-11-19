# -*- coding: utf-8 -*-
"""筹码分布 (CYQ / Position Cost Distribution).

经典三要素:
1. 三角形分布假设: 每日成交筹码均匀分布在 [low, high], 峰在 (open+close)/2 附近
   (简化版: 对称三角形, 顶点取当日均价 VWAP)
2. 换手率衰减: 每个价格桶的存量筹码按当日换手率衰减, 新筹码按当日分布注入
3. 归一化: 输出各价格桶的持仓占比 (合计 1.0), 用于画筹码峰

参考: InStock 的 CYQ 实现 (与东方财富结果一致的公开口径):
    每日 volume 按 [low..high] 区间三角加权分配到价格桶;
    历史存量每日乘 (1 - turnover_rate) 衰减。

精度口径: 价格桶数默认 120 (视觉上足够平滑, 计算量可控);
turnover = volume / float_shares (流通股本, instruments 维表)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl


DEFAULT_BUCKETS = 120
DEFAULT_LOOKBACK_DAYS = 210          # InStock 默认 210 交易日
DISTRIBUTION_EPS = 1e-12


@dataclass(slots=True)
class CYQResult:
    symbol: str
    as_of: date
    lookback_days: int
    buckets: int
    price_low: float            # 覆盖区间的最低价
    price_high: float           # 覆盖区间的最高价
    prices: list[float]         # 桶中心价 (升序)
    ratios: list[float]        # 各桶筹码占比 (合计 ≈ 1.0)
    profit_ratio: float         # 获利盘比例: 现价下方筹码占比
    avg_cost: float             # 平均成本 (加权均价)
    cost_70: tuple[float, float]  # 70% 成本区间 (低, 高)
    cost_90: tuple[float, float]  # 90% 成本区间 (低, 高)
    concentration: float         # 筹码集中度: (p90高-p90低)/(p90高+p90低) ×2, 越小越集中


def compute_cyq(
    daily: pl.DataFrame,
    *,
    float_shares: float,
    last_price: float | None = None,
    buckets: int = DEFAULT_BUCKETS,
) -> CYQResult | None:
    """计算单股筹码分布.

    Parameters
    ----------
    daily : 需含 date/open/high/low/close/volume (升序或乱序均可, 内部排序),
            只取最近 ``lookback`` 行。
    float_shares : 流通股本 (股). None/<=0 时退化为 volume 加权 (不衰减).
    last_price : 现价 (默认取最后一日 close).
    """
    if daily.is_empty() or float_shares <= 0:
        return None
    need = {"date", "open", "high", "low", "close", "volume"}
    if not need.issubset(daily.columns):
        return None

    df = daily.sort("date").tail(DEFAULT_LOOKBACK_DAYS)
    if df.height < 5:
        return None

    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    v = df["volume"].to_numpy().astype(np.float64)

    last = float(last_price if last_price is not None else c[-1])

    # 价格轴: 覆盖区间 [min low, max high], 末值贴现价
    p_min = float(np.nanmin(l)); p_max = float(np.nanmax(h))
    p_min = min(p_min, last); p_max = max(p_max, last)
    if p_max - p_min < DISTRIBUTION_EPS:
        p_max = p_min * 1.01 + 0.01

    edges = np.linspace(p_min, p_max, buckets + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    dist = np.zeros(buckets, dtype=np.float64)

    def add_day(day_o: float, day_h: float, day_l: float, day_c: float,
                day_v: float, existing: np.ndarray) -> np.ndarray:
        """注入一日成交量: 对称三角形分布 [low, high], 峰在均价."""
        if day_v <= 0 or day_h <= day_l:
            # 一字/极小振幅: 全部堆到 close 对应桶
            idx = int(np.clip(np.searchsorted(edges, day_c) - 1, 0, buckets - 1))
            existing[idx] += day_v
            return existing
        # 对称三角形权重: 峰在 vwap ≈ (o+h+l+c)/4 附近, 简化为 (h+l+2c)/4
        peak = (day_h + day_l + 2.0 * day_c) / 4.0
        # 三角形: 从 low 线性升到 peak, 再降到 high
        w_left = np.clip((centers - day_l) / max(peak - day_l, DISTRIBUTION_EPS), 0.0, 1.0)
        w_right = np.clip((day_h - centers) / max(day_h - peak, DISTRIBUTION_EPS), 0.0, 1.0)
        w = np.where(centers <= peak, w_left, w_right)
        s = w.sum()
        if s <= DISTRIBUTION_EPS:
            idx = int(np.clip(np.searchsorted(edges, day_c) - 1, 0, buckets - 1))
            existing[idx] += day_v
        else:
            existing += day_v * (w / s)
        return existing

    # 逐日迭代: 存量衰减 + 新筹码注入
    for i in range(df.height):
        turnover = float(np.clip(v[i] / float_shares, 0.0, 1.0))
        dist *= (1.0 - turnover)
        dist = add_day(o[i], h[i], l[i], c[i], v[i], dist)

    total = dist.sum()
    if total <= DISTRIBUTION_EPS:
        return None
    ratios = dist / total

    # 统计量
    below = ratios[centers <= last].sum()
    avg_cost = float((ratios * centers).sum() / ratios.sum())

    order = np.sort(ratios)[::-1]
    # 70%/90% 成本区间: 从最高占比桶向两侧扩展累计到目标
    def cost_band(q: float) -> tuple[float, float]:
        idx_sorted = np.argsort(ratios)[::-1]
        cum = 0.0
        sel = []
        for j in idx_sorted:
            sel.append(j)
            cum += ratios[j]
            if cum >= q:
                break
        lo = float(centers[min(sel)]); hi = float(centers[max(sel)])
        return lo, hi

    c70 = cost_band(0.70)
    c90 = cost_band(0.90)
    conc = (c90[1] - c90[0]) / max(c90[1] + c90[0], DISTRIBUTION_EPS) * 2.0

    return CYQResult(
        symbol=str(df["symbol"][0]) if "symbol" in df.columns else "",
        as_of=df["date"][-1],
        lookback_days=df.height,
        buckets=buckets,
        price_low=p_min,
        price_high=p_max,
        prices=[round(float(x), 4) for x in centers],
        ratios=[round(float(x), 6) for x in ratios],
        profit_ratio=round(float(below), 4),
        avg_cost=round(avg_cost, 4),
        cost_70=(round(c70[0], 4), round(c70[1], 4)),
        cost_90=(round(c90[0], 4), round(c90[1], 4)),
        concentration=round(float(conc), 4),
    )
