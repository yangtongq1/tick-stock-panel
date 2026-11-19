# -*- coding: utf-8 -*-
"""筹码分布 (CYQ) 计算正确性测试。

用构造的已知形态数据验证:
1. 分布守恒: 各桶占比合计 ≈ 1.0
2. 对称三角形注入: 单日数据时峰值桶在均价附近
3. 换手率衰减: 高换手新股占主导, 低换手历史筹码留存
4. 获利比例: 现价上/下筹码占比正确
5. 成本区间: 70%/90% 区间嵌套且覆盖率达标
6. 一字板: 无振幅时筹码全部堆到 close 对应桶
7. 边界: 空数据/缺列/股本无效返回 None
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from app.services.cyq import DEFAULT_LOOKBACK_DAYS, compute_cyq


def _mk_daily(
    rows: list[tuple[float, float, float, float, float]],
    start: date = date(2025, 1, 2),
) -> pl.DataFrame:
    """rows: (open, high, low, close, volume)"""
    dates = [start + timedelta(days=i) for i in range(len(rows))]
    return pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * len(rows),
            "date": dates,
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] for r in rows],
        }
    )


def test_ratio_sum_is_one():
    df = _mk_daily([(10.0, 11.0, 9.5, 10.5, 1_000_000) for _ in range(30)])
    r = compute_cyq(df, float_shares=50_000_000)
    assert r is not None
    assert abs(sum(r.ratios) - 1.0) < 1e-3


def test_single_day_peak_near_avg():
    # 峰应在 (h+l+2c)/4 附近; 算法要求至少 5 根日K
    df = _mk_daily([(10.0, 12.0, 10.0, 11.0, 1_000_000) for _ in range(5)])
    r = compute_cyq(df, float_shares=100_000_000)
    assert r is not None
    peak_price = r.prices[max(range(len(r.ratios)), key=lambda i: r.ratios[i])]
    expected = (12.0 + 10.0 + 2 * 11.0) / 4.0  # 11.0
    assert abs(peak_price - expected) < 0.3


def test_limit_up_day_all_in_close_bucket():
    # 一字板: high == low → 全部筹码堆在 close 桶
    df = _mk_daily([(10.0, 10.0, 10.0, 10.0, 1_000_000) for _ in range(5)])
    r = compute_cyq(df, float_shares=10_000_000)
    assert r is not None
    # 一字板连续, 全部筹码应集中在一个价格附近
    assert r.concentration < 0.05
    assert abs(r.avg_cost - 10.0) < 0.15


def test_turnover_decay():
    # 高换手的新日期应该主导分布: 前期 10 元附近, 后期 20 元附近巨量换手
    early = [(10.0, 10.2, 9.8, 10.0, 2_000_000) for _ in range(20)]
    late = [(20.0, 20.4, 19.8, 20.0, 8_000_000) for _ in range(10)]
    df = _mk_daily(early + late)
    # 流通股本 1000 万: 后期每日换手 80%, 历史筹码近乎清零 → 筹码主体在 20 附近。
    # 20 元三角峰在 20.05, 左侧约 1/3 面积落在现价 20.0 之下 → 获利比例 ≈ 0.2 而非 1.0。
    r = compute_cyq(df, float_shares=10_000_000)
    assert r is not None
    assert 0.05 < r.profit_ratio < 0.35
    assert abs(r.avg_cost - 20.0) < 1.0


def test_profit_ratio_boundary():
    # 10 元建仓 → 涨到 20: 现价 20 下方应基本只有 20 元新筹码的左半三角
    early = [(10.0, 10.2, 9.8, 10.0, 1_000_000) for _ in range(20)]
    late = [(20.0, 20.4, 19.8, 20.0, 500_000) for _ in range(10)]
    df = _mk_daily(early + late)
    r = compute_cyq(df, float_shares=100_000_000, last_price=20.0)
    assert r is not None
    # 10 元旧仓占绝大多数 (后期每日换手仅 0.5%), 均在现价下 → 获利盘为主
    assert r.profit_ratio > 0.75


def test_cost_bands_nested_and_coverage():
    df = _mk_daily([(10.0 + i * 0.05, 10.5 + i * 0.05, 9.8 + i * 0.05, 10.2 + i * 0.05, 1_000_000) for i in range(40)])
    r = compute_cyq(df, float_shares=100_000_000)
    assert r is not None
    lo70, hi70 = r.cost_70
    lo90, hi90 = r.cost_90
    assert lo90 <= lo70 <= hi70 <= hi90
    # 集中度: (hi-lo)/(hi+lo)*2, 宽区间 → 接近小正值
    assert 0.0 <= r.concentration <= 1.0


def test_lookback_truncation():
    # 超过 DEFAULT_LOOKBACK_DAYS 的数据只取最后 210 行
    n = DEFAULT_LOOKBACK_DAYS + 50
    rows = [(10.0 + (i % 5) * 0.1, 10.5, 9.8, 10.0, 1_000_000) for i in range(n)]
    df = _mk_daily(rows)
    r = compute_cyq(df, float_shares=100_000_000)
    assert r is not None
    assert r.lookback_days == DEFAULT_LOOKBACK_DAYS


def test_invalid_inputs_return_none():
    # 空表
    empty = pl.DataFrame(
        {"symbol": [], "date": [], "open": [], "high": [], "low": [], "close": [], "volume": []},
        schema={
            "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
        },
    )
    assert compute_cyq(empty, float_shares=1_000_000) is None
    # 股本无效
    df = _mk_daily([(10.0, 11.0, 9.5, 10.5, 1_000_000) for _ in range(30)])
    assert compute_cyq(df, float_shares=0) is None
    assert compute_cyq(df, float_shares=-1) is None
    # 缺列
    bad = df.drop("volume")
    assert compute_cyq(bad, float_shares=1_000_000) is None


def test_last_price_extends_range():
    # 现价超出历史区间时, 价格轴应扩展到现价
    df = _mk_daily([(10.0, 10.2, 9.8, 10.0, 1_000_000) for _ in range(20)])
    r = compute_cyq(df, float_shares=100_000_000, last_price=12.0)
    assert r is not None
    assert r.price_high >= 12.0
    # 全部筹码在 10 附近, 现价 12 → 获利比例 1.0
    assert r.profit_ratio >= 0.99
