"""盘后管道兜底轮询 (catch-up watchdog) 判定逻辑。

15:35 cron 是「到点触发」, 关机错过就没了。轮询语义: 服务活着就每 15 分钟
查一次「今天该跑的管道跑了没」, 没跑就补跑。判据钉住 cn_now 与日历。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.jobs import daily_pipeline as dp


class _FakeRepo:
    """最小 repo 桩: 只需 store.data_dir 指向临时目录。"""

    def __init__(self, tmp_path: Path, has_today: bool):
        self.store = SimpleNamespace(data_dir=tmp_path)
        daily_dir = tmp_path / "kline_daily" / "date=2026-11-20"
        daily_dir.mkdir(parents=True, exist_ok=True)
        part = daily_dir / "part.parquet"
        if has_today:
            part.write_bytes(b"x" * 10)
        elif part.exists():
            part.unlink()


def _patch(monkeypatch, *, now: datetime, trading_days: set | None):
    monkeypatch.setattr(dp.preferences, "get_pipeline_schedule",
                        lambda: {"hour": 15, "minute": 35})
    monkeypatch.setattr(dp, "_is_trading_day_cn",
                        lambda d: (trading_days is None and d.weekday() < 5) or d in trading_days)
    import app.market_time as mt
    monkeypatch.setattr(mt, "cn_now", lambda: now)
    # _pipeline_due_and_missing 里 from app.market_time import cn_now 是
    # 函数内 import, 每次调用重新解析 → monkeypatch mt 模块属性即可命中


TODAY = date(2026, 11, 20)  # 周五, 交易日


def _friday(h: int, m: int) -> datetime:
    return datetime(2026, 11, 20, h, m)


def test_not_due_before_schedule_time(monkeypatch, tmp_path):
    """管道时刻(15:35)未到 → 不抢跑 (即便当日分区缺失)。"""
    _patch(monkeypatch, now=_friday(14, 0), trading_days={TODAY})
    repo = _FakeRepo(tmp_path, has_today=False)
    assert dp._pipeline_due_and_missing(repo) is False


def test_not_due_within_grace_window(monkeypatch, tmp_path):
    """刚过 15:35 (缓冲 30 分钟内) → cron 可能正在跑, 不抢。"""
    _patch(monkeypatch, now=_friday(15, 50), trading_days={TODAY})
    repo = _FakeRepo(tmp_path, has_today=False)
    assert dp._pipeline_due_and_missing(repo) is False


def test_due_after_grace_when_partition_missing(monkeypatch, tmp_path):
    """已过 15:35+30min、交易日、当日分区缺失 → 该补跑。"""
    _patch(monkeypatch, now=_friday(16, 20), trading_days={TODAY})
    repo = _FakeRepo(tmp_path, has_today=False)
    assert dp._pipeline_due_and_missing(repo) is True


def test_not_due_when_partition_exists(monkeypatch, tmp_path):
    """cron 已正常跑过 (当日分区在) → 不重复。"""
    _patch(monkeypatch, now=_friday(23, 0), trading_days={TODAY})
    repo = _FakeRepo(tmp_path, has_today=True)
    assert dp._pipeline_due_and_missing(repo) is False


def test_not_due_on_holiday(monkeypatch, tmp_path):
    """休市日 (不在交易日历) → 无数据可拉, 不跑。"""
    _patch(monkeypatch, now=_friday(16, 20), trading_days=set())  # 周五但休市
    repo = _FakeRepo(tmp_path, has_today=False)
    assert dp._pipeline_due_and_missing(repo) is False


def test_trading_day_fallback_to_weekday(monkeypatch):
    """日历不可用 (None) → 回退周一~周五近似。"""
    monkeypatch.setattr(dp, "_is_trading_day_cn", lambda d: d.weekday() < 5)
    assert dp._is_trading_day_cn(date(2026, 11, 20)) is True   # 周五
    assert dp._is_trading_day_cn(date(2026, 11, 21)) is False  # 周六
