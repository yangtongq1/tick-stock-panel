# -*- coding: utf-8 -*-
"""涨停板三池 preset (ext_zt_pool / ext_zb_pool / ext_dt_pool) 启动契约。

- 出厂配方: 本地 aksource /zt_pool 端点, timeseries + date_param=compact
  (支持历史回补), 盘中多盘 time_field=__pull_ts__, 仅交易日拉取
- 批次播种 (v2): 老库 (已有 v1 播种标记) 启动时自动创建三池配置;
  用户删除后重启不复活
- 失败不阻断启动
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.ext_data import ExtConfigStore
from app.services.ext_presets import (
    _SEED_BATCH_MARKER,
    _SEED_BATCH_VERSION,
    ensure_builtin_presets,
)

_ZT_IDS = ("ext_zt_pool", "ext_zb_pool", "ext_dt_pool")


def _zt_presets():
    from app.services.ext_presets import _presets
    return {p.id: p for p in _presets() if p.id in _ZT_IDS}


def test_zt_pool_presets_ship_with_complete_pull() -> None:
    """三池出厂自带完整拉取配方: aksource 端点 / timeseries / 历史回补参数。"""
    presets = _zt_presets()
    assert set(presets) == set(_ZT_IDS), f"三池预设缺失: {set(_ZT_IDS) - set(presets)}"
    for cid, preset in presets.items():
        pull = preset.pull
        assert pull is not None, f"{cid} 出厂未带拉取配方"
        assert pull.url.startswith("http://127.0.0.1:3021/zt_pool"), (
            f"{cid} 未指向本地 aksource 涨停池端点"
        )
        assert preset.mode == "timeseries", f"{cid} 必须为 timeseries (保留每日历史)"
        # 历史回补: 东财服务端按日期存档, date_param=compact + date=YYYYMMDD
        assert pull.date_param == "date", f"{cid} 缺少日期参数 (无法历史回补)"
        assert pull.date_format == "compact", f"{cid} 日期格式必须为 compact"
        # 盘中多盘: 拉取时刻注入, 同日多盘按 [symbol, 时刻] 去重
        assert pull.time_field == "__pull_ts__", f"{cid} 缺少拉取时刻字段"
        # 仅交易日拉取 (非交易日东财返回旧日期数据, 不落盘)
        assert pull.schedule_trading_only, f"{cid} 必须仅交易日拉取"
        # 免鉴权
        assert pull.auth.get("type") == "none", f"{cid} 应为免鉴权"
        # 核心字段
        names = {f.name for f in preset.fields}
        for must in ("symbol", "股票代码", "股票简称", "连板数", "首次封板时间", "所属行业"):
            assert must in names, f"{cid} 缺少核心字段 {must}"


def test_zt_batch_seeding_on_fresh_install(tmp_path: Path) -> None:
    """全新数据目录: 启动即创建三池配置 + v1 播种标记 + v2 批次标记。"""
    asyncio.run(ensure_builtin_presets(tmp_path))
    store = ExtConfigStore(tmp_path)
    for cid in _ZT_IDS:
        config = store.get(cid)
        assert config is not None, f"{cid} 配置未创建"
        assert config.pull is not None and config.pull.url, f"{cid} 配方未写入"
    batch = tmp_path / "ext_data" / _SEED_BATCH_MARKER
    assert batch.exists(), "v2 批次标记未写入"
    assert batch.read_text(encoding="utf-8").strip() == str(_SEED_BATCH_VERSION)


def test_zt_batch_seeding_on_existing_v1_library(tmp_path: Path) -> None:
    """老库 (v1 已播种): 启动自动补建三池 (批次标记缺失 = 未送达 v2)。"""
    # 模拟老库: 先跑一次并手动删除批次标记与三池配置 (相当于从 v1 升级)
    asyncio.run(ensure_builtin_presets(tmp_path))
    store = ExtConfigStore(tmp_path)
    for cid in _ZT_IDS:
        store.delete(cid)
    (tmp_path / "ext_data" / _SEED_BATCH_MARKER).unlink()
    # v1 标记还在 (.builtin_presets_seeded), 旧表 ext_gn_ths 也删掉模拟用户删除
    store.delete("ext_gn_ths")

    asyncio.run(ensure_builtin_presets(tmp_path))
    store = ExtConfigStore(tmp_path)
    # 三池作为 v2 批次成员被创建
    for cid in _ZT_IDS:
        assert store.get(cid) is not None, f"{cid} 未随 v2 批次创建"
    # 用户删除过的 v1 表不复活
    assert store.get("ext_gn_ths") is None, "v1 已删除表不应复活"
    # 批次标记已落地
    batch = tmp_path / "ext_data" / _SEED_BATCH_MARKER
    assert batch.exists() and batch.read_text(encoding="utf-8").strip() == str(_SEED_BATCH_VERSION)


def test_zt_deleted_after_batch_seed_not_resurrected(tmp_path: Path) -> None:
    """批次送达后删除三池 → 重启不复活。"""
    asyncio.run(ensure_builtin_presets(tmp_path))
    store = ExtConfigStore(tmp_path)
    store.delete("ext_zt_pool")

    asyncio.run(ensure_builtin_presets(tmp_path))
    assert store.get("ext_zt_pool") is None, "已送达批次后删除的表不应复活"


# ------------------------------------------------------- 资金流向 (v3 批次)

_FF_IDS = ("ext_fund_flow", "ext_fund_flow_board")


def test_fund_flow_presets_ship_with_complete_pull() -> None:
    """资金流两表出厂配方: aksource 端点 / 数值字段类型必须 float (非 number)。"""
    from app.services.ext_presets import _presets
    presets = {p.id: p for p in _presets() if p.id in _FF_IDS}
    assert set(presets) == set(_FF_IDS)
    for cid, preset in presets.items():
        pull = preset.pull
        assert pull is not None, f"{cid} 出厂未带拉取配方"
        assert pull.url.startswith("http://127.0.0.1:3021/fund_flow")
        assert preset.mode == "timeseries"
        assert pull.time_field == "__pull_ts__"
        assert pull.schedule_trading_only
        # 字段类型必须是 float/int/string/bool —— "number" 不在 dtype 映射里会退化成字符串
        for f in preset.fields:
            assert f.dtype in ("string", "int", "float", "bool"), (
                f"{cid} 字段 {f.name} 类型 {f.dtype} 不在 dtype 映射, 会退化成 Utf8"
            )
    # 个股表带 symbol 映射; 板块表是 market_level (无标的关联)
    stock = presets["ext_fund_flow"]
    assert stock.symbol_map.get("col") == "股票代码"
    for must in ("净流入", "特大单净额", "大单净额"):
        assert any(f.name == must for f in stock.fields)
    board = presets["ext_fund_flow_board"]
    assert board.market_level, "板块资金流必须是 market_level 表"


def test_fund_flow_batch_v3_seeded_on_existing_library(tmp_path: Path) -> None:
    """v3 批次 (资金流两表) 送达到已播种的老库。"""
    asyncio.run(ensure_builtin_presets(tmp_path))
    store = ExtConfigStore(tmp_path)
    for cid in _FF_IDS:
        assert store.get(cid) is not None, f"{cid} 未随 v3 批次创建"
