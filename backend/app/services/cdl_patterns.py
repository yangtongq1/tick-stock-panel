# -*- coding: utf-8 -*-
"""K 线形态识别 (Candlestick Patterns) — 纯 numpy 向量化实现。

对标 TA-Lib CDL 系列 (InStock 61 种的实用子集)。每形态输出三态:
  +100 = 看涨 (bullish) / -100 = 看跌 (bearish) / 0 = 无形态。

设计:
  - 输入: 单股日K DataFrame (date/open/high/low/close), 升序
  - 全部用 O/H/L/C 相对关系表达 (无一字板/缺口/窗口依赖时跳过输出 0)
  - 长实体/影线用 ATR-style 自适应阈值 (body >= 0.6 * avg_body) 判定,
    避免低价股/高价股用固定百分比误判

实现清单 (26 种高频形态):
  多头: HAMMER 锤子 / INVERTED_HAMMER 倒锤子 / ENGULFING_BULL 吞没看涨 /
        MORNING_STAR 晨星 / THREE_WHITE_SOLDIERS 红三兵 /
        PIERCING_LINE 刺透 / TWEEZER_BOTTOM 镊子底 /
        THREE_INSIDE_UP 三个内部人上涨 / THREE_OUTSIDE_UP 三个外部人上涨
  空头: SHOOTING_STAR 流星 / HANGING_MAN 上吊线 / ENGULFING_BEAR 吞没看跌 /
        EVENING_STAR 黄昏星 / THREE_BLACK_CROWS 乌鸦 /
        DARK_CLOUD_COVER 乌云盖顶 / TWEEZER_TOP 镊子顶 /
        THREE_INSIDE_DOWN 三个内部人下跌 / THREE_OUTSIDE_DOWN 三个外部人下跌
  中性/反转: DOJI 十字星 / LONG_LEGGED_DOJI 长脚十字 /
        SPINNING_TOP 纺锤 / ABANDONED_BABY 弃婴 (含缺口) /
        BREAKAWAY 脱离 / COUNTERATTACK 反击线 / LADDER_BOTTOM 梯底
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(slots=True, frozen=True)
class PatternDef:
    name: str          # 英文标识 (API 字段名)
    label_cn: str      # 中文名
    direction: int      # +1 看涨 / -1 看跌 / 0 中性


# 形态注册表: name → 定义 (顺序即文档顺序)
PATTERNS: tuple[PatternDef, ...] = (
    PatternDef("CDL_HAMMER", "锤子", 1),
    PatternDef("CDL_INVERTED_HAMMER", "倒锤子", 1),
    PatternDef("CDL_ENGULFING_BULL", "看涨吞没", 1),
    PatternDef("CDL_MORNING_STAR", "晨星", 1),
    PatternDef("CDL_THREE_WHITE_SOLDIERS", "红三兵", 1),
    PatternDef("CDL_PIERCING_LINE", "刺透", 1),
    PatternDef("CDL_TWEEZER_BOTTOM", "镊子底", 1),
    PatternDef("CDL_THREE_INSIDE_UP", "内部人上涨", 1),
    PatternDef("CDL_THREE_OUTSIDE_UP", "外部人上涨", 1),
    PatternDef("CDL_SHOOTING_STAR", "流星", -1),
    PatternDef("CDL_HANGING_MAN", "上吊线", -1),
    PatternDef("CDL_ENGULFING_BEAR", "看跌吞没", -1),
    PatternDef("CDL_EVENING_STAR", "黄昏星", -1),
    PatternDef("CDL_THREE_BLACK_CROWS", "乌鸦", -1),
    PatternDef("CDL_DARK_CLOUD_COVER", "乌云盖顶", -1),
    PatternDef("CDL_TWEEZER_TOP", "镊子顶", -1),
    PatternDef("CDL_THREE_INSIDE_DOWN", "内部人下跌", -1),
    PatternDef("CDL_THREE_OUTSIDE_DOWN", "外部人下跌", -1),
    PatternDef("CDL_DOJI", "十字星", 0),
    PatternDef("CDL_LONG_LEGGED_DOJI", "长脚十字", 0),
    PatternDef("CDL_SPINNING_TOP", "纺锤", 0),
    PatternDef("CDL_ABANDONED_BABY", "弃婴", 0),
    PatternDef("CDL_BREAKAWAY", "脱离", 0),
    PatternDef("CDL_COUNTERATTACK", "反击线", 0),
    PatternDef("CDL_LADDER_BOTTOM", "梯底", 1),
    PatternDef("CDL_RISING_THREE_METHODS", "上升三法", 1),
)

PATTERN_NAMES = [p.name for p in PATTERNS]
PATTERN_BY_NAME = {p.name: p for p in PATTERNS}


def _shift(v: np.ndarray, n: int) -> np.ndarray:
    """v.shift(n): n>0 取前 n 根; 边界填 False (布尔) / 0 (整数) / NaN (浮点)。"""
    # 注意: 整数数组不能用 NaN 填充 (cast 后成 INT64_MIN 等非零垃圾值)
    if not np.issubdtype(v.dtype, np.floating):
        out = np.zeros_like(v)
    else:
        out = np.full_like(v, np.nan)
    if n > 0:
        out[n:] = v[:-n]
    elif n < 0:
        out[:n] = v[-n:]
    else:
        out[:] = v
    return out


def compute_patterns(daily: pl.DataFrame) -> pl.DataFrame:
    """输入单股日K (升序), 返回 date + 各形态三态列 (+100/-100/0)。

    需要 ≥ 5 根 K 线 (多数形态要 1~3 根历史 + 自适应实体阈值)。
    """
    need = {"date", "open", "high", "low", "close"}
    if daily.is_empty() or not need.issubset(daily.columns):
        return pl.DataFrame()
    df = daily.sort("date")
    if df.height < 5:
        return pl.DataFrame()

    o = df["open"].to_numpy().astype(np.float64)
    h = df["high"].to_numpy().astype(np.float64)
    l = df["low"].to_numpy().astype(np.float64)
    c = df["close"].to_numpy().astype(np.float64)

    body = np.abs(c - o)
    rng = h - l
    up_body = (c - o) > 0
    down_body = (c - o) < 0
    upper = h - np.maximum(o, c)     # 上影线
    lower = np.minimum(o, c) - l    # 下影线

    # 自适应阈值: 近 14 根平均实体 (滚动, 前 14 根用已有均值)
    body_f = np.where(body > 0, body, np.nan)
    avg_body = np.full_like(body, np.nan)
    for i in range(len(body)):
        lo = max(0, i - 14)
        w = body[lo:i + 1]
        w = w[~np.isnan(w)]
        avg_body[i] = w.mean() if len(w) else body[i]
    long_body = body >= 0.85 * avg_body          # 长实体
    small_body = body <= 0.35 * avg_body          # 小实体
    eps = 1e-12

    out: dict[str, np.ndarray] = {}

    # ── 单根形态 ─────────────────────────────────────
    # 锤子: 长下影 ≥ 2×实体, 上影短, 出现在下跌后
    prev_c = _shift(c, 1)
    downtrend_local = prev_c < _shift(c, 4) if len(c) > 4 else np.zeros(len(c), bool)
    out["CDL_HAMMER"] = np.where(
        downtrend_local & (lower >= 2.0 * body) & (upper <= 0.5 * body) & (rng > eps),
        100, 0,
    )
    # 倒锤子: 长上影 ≥ 2×实体, 下影短, 下跌后
    out["CDL_INVERTED_HAMMER"] = np.where(
        downtrend_local & (upper >= 2.0 * body) & (lower <= 0.5 * body) & (rng > eps),
        100, 0,
    )
    # 流星: 长上影 + 上涨后 + 当日收跌 (小实体在上部)
    uptrend_local = prev_c > _shift(c, 4) if len(c) > 4 else np.zeros(len(c), bool)
    out["CDL_SHOOTING_STAR"] = np.where(
        uptrend_local & (upper >= 2.0 * body) & (lower <= 0.3 * body) & down_body,
        -100, 0,
    )
    # 上吊线: 形似锤子但出现在上涨后
    out["CDL_HANGING_MAN"] = np.where(
        uptrend_local & (lower >= 2.0 * body) & (upper <= 0.5 * body) & (rng > eps),
        -100, 0,
    )
    # 十字星: 实体极小 (≤ 10% 波幅)
    out["CDL_DOJI"] = np.where(body <= 0.1 * np.where(rng > eps, rng, np.inf), 100, 0)
    # 长脚十字: 上下影都长 + 十字
    out["CDL_LONG_LEGGED_DOJI"] = np.where(
        (body <= 0.1 * np.where(rng > eps, rng, np.inf))
        & (lower >= body) & (upper >= body) & (rng > eps),
        100, 0,
    )
    # 纺锤: 小实体 + 上下影均衡
    out["CDL_SPINNING_TOP"] = np.where(
        small_body & (lower > 0.5 * body) & (upper > 0.5 * body) & (rng > eps),
        100, 0,
    )

    # ── 双根形态 ─────────────────────────────────────
    p_o, p_h, p_l, p_c = _shift(o, 1), _shift(h, 1), _shift(l, 1), _shift(c, 1)
    p_body = np.abs(p_c - p_o)
    # 看涨吞没: 阴线后阳线实体完全包住前实体
    out["CDL_ENGULFING_BULL"] = np.where(
        (p_c < p_o) & up_body & (o <= p_c) & (c >= p_o) & (p_body > eps),
        100, 0,
    )
    # 看跌吞没
    out["CDL_ENGULFING_BEAR"] = np.where(
        (p_c > p_o) & down_body & (o >= p_c) & (c <= p_o) & (p_body > eps),
        -100, 0,
    )
    # 刺透: 阴线后阳线开盘低于昨低, 收盘深入前实体上半
    mid_prev = (p_o + p_c) / 2.0
    out["CDL_PIERCING_LINE"] = np.where(
        (p_c < p_o) & up_body & (o < p_l) & (c > mid_prev) & (c < p_o),
        100, 0,
    )
    # 乌云盖顶: 阳线后阴线开盘高于昨高, 收盘深入前实体下半
    out["CDL_DARK_CLOUD_COVER"] = np.where(
        (p_c > p_o) & down_body & (o > p_h) & (c < mid_prev) & (c > p_o),
        -100, 0,
    )
    # 镊子底: 两根 K 线低点几乎相同 (≤ 0.15×avg_body), 第一根阴第二根阳
    out["CDL_TWEEZER_BOTTOM"] = np.where(
        (p_c < p_o) & up_body & (np.abs(l - p_l) <= 0.15 * avg_body),
        100, 0,
    )
    # 镊子顶
    out["CDL_TWEEZER_TOP"] = np.where(
        (p_c > p_o) & down_body & (np.abs(h - p_h) <= 0.15 * avg_body),
        -100, 0,
    )

    # ── 三根形态 ─────────────────────────────────────
    pp_o, pp_h, pp_l, pp_c = _shift(o, 2), _shift(h, 2), _shift(l, 2), _shift(c, 2)
    # 晨星: 阴长 → 小实体跳空低开 → 阳线收进第一根实体上半
    star_body_small = _shift(small_body, 1)
    out["CDL_MORNING_STAR"] = np.where(
        (pp_c < pp_o) & _shift(long_body, 2) & star_body_small
        & (_shift(o, 1) < pp_c) & up_body & (c > (pp_o + pp_c) / 2.0),
        100, 0,
    )
    # 黄昏星: 阳长 → 小实体跳空高开 → 阴线收进第一根实体下半
    out["CDL_EVENING_STAR"] = np.where(
        (pp_c > pp_o) & _shift(long_body, 2) & star_body_small
        & (_shift(o, 1) > pp_c) & down_body & (c < (pp_o + pp_c) / 2.0),
        -100, 0,
    )
    # 弃婴 (看涨): 阴线 → 十字星 (两侧跳空) → 阳线收在前实体上
    star_doji = _shift(out["CDL_DOJI"], 1) > 0   # 前一根是十字星
    gap_down = _shift(h, 1) < pp_l
    gap_up = l > _shift(h, 1)
    out["CDL_ABANDONED_BABY"] = np.where(
        (pp_c < pp_o) & star_doji & gap_down & gap_up & up_body,
        100, 0,
    )
    # 红三兵: 三连阳, 每根开盘在前实体内, 收盘新高
    # 注意: & 优先级高于 >, 每个比较必须加括号
    out["CDL_THREE_WHITE_SOLDIERS"] = np.where(
        up_body & _shift(up_body, 1) & _shift(up_body, 2)
        & (o > _shift(o, 1)) & (o < _shift(c, 1))
        & (c > _shift(c, 1)) & (_shift(c, 1) > _shift(c, 2))
        & long_body,
        100, 0,
    )
    # 乌鸦: 三连阴镜像
    out["CDL_THREE_BLACK_CROWS"] = np.where(
        down_body & _shift(down_body, 1) & _shift(down_body, 2)
        & (o < _shift(o, 1)) & (o > _shift(c, 1))
        & (c < _shift(c, 1)) & (_shift(c, 1) < _shift(c, 2))
        & long_body,
        -100, 0,
    )
    # 内部人上涨: 看涨孕育 (第4根阳线收高于前3根高点) — 简化为:
    # 阴线被阳线孕育 + 第三根阳线收盘高于孕育阳线收盘
    out["CDL_THREE_INSIDE_UP"] = np.where(
        (pp_c < pp_o) & (p_c > p_o) & (p_o <= pp_c) & (p_c >= pp_o)
        & up_body & (c > p_c),
        100, 0,
    )
    out["CDL_THREE_INSIDE_DOWN"] = np.where(
        (pp_c > pp_o) & (p_c < p_o) & (p_o >= pp_c) & (p_c <= pp_o)
        & down_body & (c < p_c),
        -100, 0,
    )
    # 外部人上涨: 看涨吞没 + 第三根阳线收更高
    out["CDL_THREE_OUTSIDE_UP"] = np.where(
        (out["CDL_ENGULFING_BULL"] > 0) & up_body & (c > p_c),
        100, 0,
    )
    out["CDL_THREE_OUTSIDE_DOWN"] = np.where(
        (out["CDL_ENGULFING_BEAR"] < 0) & down_body & (c < p_c),
        -100, 0,
    )
    # 反击线 (看涨): 阴线 → 阳线跳空低开后收在昨收附近
    out["CDL_COUNTERATTACK"] = np.where(
        (p_c < p_o) & (o < p_l) & (np.abs(c - p_c) <= 0.1 * avg_body) & up_body,
        100, 0,
    )
    # 脱离: 下跌后三连小实体后长阳突破
    out["CDL_BREAKAWAY"] = np.where(
        downtrend_local & _shift(small_body, 1) & _shift(small_body, 2)
        & _shift(small_body, 3) & up_body & long_body & (c > _shift(h, 2)),
        100, 0,
    )
    # 梯底: 五根, 前三阴 (低点渐降), 后两阳收复
    out["CDL_LADDER_BOTTOM"] = np.where(
        _shift(down_body, 4) & _shift(down_body, 3) & _shift(down_body, 2)
        & (_shift(l, 3) < _shift(l, 4)) & (_shift(l, 2) < _shift(l, 3))
        & up_body & _shift(up_body, 1) & (c > _shift(c, 2)),
        100, 0,
    )
    # 上升三法: 长阳 + 三根小阴回调 (不破长阳实体) + 长阳新高
    out["CDL_RISING_THREE_METHODS"] = np.where(
        up_body & long_body & (c > _shift(h, 1))
        & _shift(small_body & down_body, 1)
        & _shift(small_body & down_body, 2)
        & _shift(small_body & down_body, 3)
        & (_shift(o, 1) > _shift(c, 4)) & (_shift(c, 3) > _shift(c, 4)),
        100, 0,
    )

    result = {"date": df["date"].to_list()}
    for name in PATTERN_NAMES:
        result[name] = [int(x) for x in out[name]]
    return pl.DataFrame(result)


def scan_market_patterns(daily_all: pl.DataFrame, *, last_n: int = 1) -> pl.DataFrame:
    """批量扫全市场: 输入多股日K (symbol/date/OHLC), 输出 [最近 last_n 日 × 形态命中表]。

    行: symbol, date, pattern (名), direction (+1/-1/0)
    只输出命中形态 (值 ≠ 0)。
    """
    if daily_all.is_empty():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "pattern": pl.Utf8, "direction": pl.Int8})
    frames = []
    for (sym,), g in daily_all.group_by(["symbol"], maintain_order=True):
        res = compute_patterns(g)
        if res.is_empty():
            continue
        res = res.tail(last_n)
        # 融成 long: (date, pattern, direction) 只留命中
        for name in PATTERN_NAMES:
            hits = res.filter(pl.col(name) != 0)
            if hits.is_empty():
                continue
            frames.append(
                hits.select(
                    pl.lit(sym).alias("symbol"),
                    pl.col("date"),
                    pl.lit(name).alias("pattern"),
                    pl.when(pl.col(name) > 0).then(1).otherwise(-1).alias("direction").cast(pl.Int8),
                )
            )
    if not frames:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Date, "pattern": pl.Utf8, "direction": pl.Int8})
    return pl.concat(frames)
