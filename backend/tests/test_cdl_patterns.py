# -*- coding: utf-8 -*-
"""K 线形态识别 (cdl_patterns) 正确性测试。

构造已知形态的 OHLC 序列, 验证:
1. 各形态在构造日命中 (非 0), 方向正确
2. 非形态日不命中 (0)
3. scan_market_patterns 批量输出 long 格式
4. 边界: <5 根 K 线返回空
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.services.cdl_patterns import (
    PATTERNS,
    PATTERN_NAMES,
    compute_patterns,
    scan_market_patterns,
)


def _df(rows: list[tuple], symbol: str = "000001.SZ") -> pl.DataFrame:
    """rows: (open, high, low, close)"""
    dates = [date(2025, 1, 2) + timedelta(days=i) for i in range(len(rows))]
    return pl.DataFrame({
        "symbol": [symbol] * len(rows),
        "date": dates,
        "open": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "volume": [1_000_000.0] * len(rows),
    })


def _prefix(downtrend: bool = True, n: int = 4, price: float = 20.0) -> list[tuple]:
    """造 4 根前置趋势 (默认下跌): 从 price 到 price*(1-0.03n)。"""
    rows = []
    p = price
    for _ in range(n):
        step = 0.03 if downtrend else -0.03
        p2 = p * (1 - step)
        rows.append((p, p * 1.002, p2 * 0.999, p2))
        p = p2
    return rows


def test_hammer_bullish():
    # 下跌后锤子: 长下影 (≥2×实体), 短上影 (≤0.5×实体)
    # 实体 0.1 → 下影需 ≥0.2, 上影需 ≤0.05: 用下影 0.9, 上影 0.04
    rows = _prefix(downtrend=True) + [(15.0, 15.14, 13.9, 15.1)]
    res = compute_patterns(_df(rows))
    assert res["CDL_HAMMER"][-1] == 100, "锤子未命中"
    assert res["CDL_HAMMER"][-2] == 0


def test_shooting_star_bearish():
    # 上涨后流星: 长上影 (≥2×实体), 短下影 (≤0.3×实体), 当日收阴
    # 实体 0.1 → 上影需 ≥0.2, 下影需 ≤0.03: 用上影 1.6, 下影 0.02
    rows = _prefix(downtrend=False) + [(21.0, 22.6, 20.88, 20.9)]
    res = compute_patterns(_df(rows))
    assert res["CDL_SHOOTING_STAR"][-1] == -100


def test_bullish_engulfing():
    # 阴线后阳线完全包住
    prefix = _prefix(downtrend=True, n=3)
    rows = prefix + [(15.0, 15.1, 14.5, 14.6), (14.4, 15.4, 14.3, 15.3)]
    res = compute_patterns(_df(rows))
    assert res["CDL_ENGULFING_BULL"][-1] == 100


def test_bearish_engulfing():
    prefix = _prefix(downtrend=False, n=3)
    rows = prefix + [(21.0, 21.2, 20.8, 21.1), (21.3, 21.4, 20.6, 20.7)]
    res = compute_patterns(_df(rows))
    assert res["CDL_ENGULFING_BEAR"][-1] == -100


def test_morning_star():
    # 阴长 → 跳空小实体 → 阳线收复上半
    prefix = _prefix(downtrend=True, n=3, price=20.0)
    rows = prefix + [
        (18.0, 18.1, 16.8, 16.9),   # 长阴
        (16.7, 17.1, 16.5, 16.9),   # 跳空小实体 (星)
        (17.0, 18.2, 16.9, 18.0),   # 阳线收进第一根实体上半 (17.45+)
    ]
    res = compute_patterns(_df(rows))
    assert res["CDL_MORNING_STAR"][-1] == 100


def test_three_white_soldiers():
    prefix = _prefix(downtrend=False, n=2, price=15.0)
    rows = prefix + [
        (15.5, 16.1, 15.5, 16.0),   # 兵1: 实体 15.5→16.0
        (15.7, 16.7, 15.65, 16.6),  # 兵2: 开 15.7 在兵1实体内, 收 16.6
        (16.2, 17.3, 16.15, 17.2),  # 兵3: 开 16.2 在兵2实体内, 收 17.2
    ]
    res = compute_patterns(_df(rows))
    assert res["CDL_THREE_WHITE_SOLDIERS"][-1] == 100


def test_doji():
    prefix = _prefix(downtrend=True, n=4)
    rows = prefix + [(15.0, 15.5, 14.5, 15.0)]  # 开=收, 长影
    res = compute_patterns(_df(rows))
    assert res["CDL_DOJI"][-1] == 100


def test_patterns_registry_consistency():
    # 注册表: 26 种, 方向合法, 输出列齐全
    assert len(PATTERNS) == 26
    for p in PATTERNS:
        assert p.direction in (1, -1, 0)
        assert p.name.startswith("CDL_")
    rows = _prefix() + [(15.0, 15.3, 13.0, 15.1)]
    res = compute_patterns(_df(rows))
    for name in PATTERN_NAMES:
        assert name in res.columns, f"输出缺列 {name}"
        assert set(res[name].to_list()).issubset({-100, 0, 100}), f"{name} 输出越界"


def test_scan_market_long_format():
    # 批量: 两只股票, 输出命中形态 long 表
    df1 = _df(_prefix() + [(15.0, 15.14, 13.9, 15.1)], "000001.SZ")
    df2 = _df(_prefix(downtrend=False) + [(21.0, 22.6, 20.88, 20.9)], "600519.SH")
    both = pl.concat([df1, df2])
    out = scan_market_patterns(both, last_n=1)
    assert {"symbol", "date", "pattern", "direction"}.issubset(out.columns)
    syms = set(out["symbol"].to_list())
    assert "000001.SZ" in syms
    assert "600519.SH" in syms
    # 方向与形态定义一致
    from app.services.cdl_patterns import PATTERN_BY_NAME
    for row in out.to_dicts():
        expected = PATTERN_BY_NAME[row["pattern"]].direction
        if expected != 0:
            assert row["direction"] == expected, f"{row['pattern']} 方向 {row['direction']} ≠ 注册表 {expected}"


def test_insufficient_rows_return_empty():
    rows = _prefix()[:3]  # 3 根 < 5
    res = compute_patterns(_df(rows))
    assert res.is_empty()


def test_missing_columns_return_empty():
    df = pl.DataFrame({"date": [date(2025, 1, 2)], "close": [10.0]})
    assert compute_patterns(df).is_empty()
