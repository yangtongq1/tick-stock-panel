"""批次 (持仓提醒) 测试: 批次→规则映射 + 校验 + sync 一致性 (锁/校验先行/级联删除)。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import lots as lots_api
from app.strategy import lots as lots_domain
from app.strategy import monitor_rules


def _lot(**overrides):
    lot = {
        "id": "lot_test1",
        "symbol": "600519.SH",
        "qty": 100,
        "cost_price": 1500.0,
        "buy_date": "2026-08-01",
        "target_pct": 10,
        "stop_pct": 5,
        "remind_date": "2026-09-01",
        "lead_days": 2,
    }
    lot.update(overrides)
    return lot


class _EngineStub:
    def __init__(self) -> None:
        self.set_calls = 0
        self.rules = []

    def set_rules(self, rules) -> None:
        self.set_calls += 1
        self.rules = rules


def _make_request(tmp_path: Path, engine: _EngineStub):
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        resolve_asset_type=lambda _symbol: "stock",
    )
    state = SimpleNamespace(repo=repo, monitor_engine=engine)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _lot_path(tmp_path: Path, lot_id: str) -> Path:
    return tmp_path / "user_data" / "lots" / f"{lot_id}.json"


def _patch_channels(monkeypatch) -> None:
    monkeypatch.setattr("app.services.preferences.get_webhook_default_channels", lambda: [])


# ── 纯映射: 批次 → 规则 ────────────────────────────────
def test_lot_to_rules_price_and_date():
    price, date_rule = lots_domain.lot_to_rules(_lot())
    assert price is not None and date_rule is not None
    assert price["id"] == "lot_test1_p" and price["type"] == "price"
    assert price["scope"] == "symbols" and price["symbols"] == ["600519.SH"]
    assert price["conditions"] == [
        {"field": "close", "op": ">=", "value": 1650.0},  # 1500*1.10
        {"field": "close", "op": "<=", "value": 1425.0},  # 1500*0.95
    ]
    assert price["logic"] == "or"
    assert price["cooldown_seconds"] == 86400
    assert price["lot_id"] == "lot_test1"
    assert "止盈10%" in price["message"] and "止损5%" in price["message"]
    assert date_rule["id"] == "lot_test1_d" and date_rule["type"] == "date"
    assert date_rule["remind_date"] == "2026-09-01" and date_rule["lead_days"] == 2


def test_lot_to_rules_optional_parts():
    # 只有止盈 (无止损/无到期) → 无 date 规则
    price, date_rule = lots_domain.lot_to_rules(_lot(stop_pct=0, remind_date=None))
    assert price is not None and date_rule is None
    assert len(price["conditions"]) == 1
    # 只有到期 (无止盈/止损) → 无 price 规则
    price, date_rule = lots_domain.lot_to_rules(_lot(target_pct=0, stop_pct=0))
    assert price is None and date_rule is not None


def test_validate_lot_rules_and_errors():
    lots_domain.validate_lot(lots_domain.normalize_lot(_lot()))
    for overrides in (
        {"symbol": " "},
        {"cost_price": 0},
        {"qty": -1},
        {"target_pct": -1},
        {"stop_pct": -1},
        {"lead_days": -1},
        {"remind_date": "2026/09/01"},
        {"buy_date": "bad"},
        {"target_pct": 0, "stop_pct": 0, "remind_date": None},
    ):
        with pytest.raises(ValueError):
            lots_domain.validate_lot({**_lot(), **overrides})


def test_normalize_lot_defaults():
    n = lots_domain.normalize_lot({"id": "lot_x", "symbol": "600519.SH", "cost_price": 10})
    assert n["qty"] == 0 and n["lead_days"] == 1
    assert n["created_at"]
    assert n["symbol"] == "600519.SH"


# ── sync_lot 一致性 (校验先行 / 级联 / 单次重载) ─────────
def test_sync_lot_validates_rules_before_write(tmp_path, monkeypatch):
    engine = _EngineStub()
    request = _make_request(tmp_path, engine)
    _patch_channels(monkeypatch)
    reloaded = []
    monkeypatch.setattr(lots_api, "_reload_engine", lambda r: reloaded.append(1))

    def boom(_rule) -> None:
        raise ValueError("bad rule")

    monkeypatch.setattr("app.strategy.monitor_rules.validate", boom)
    with pytest.raises(HTTPException) as ei:
        lots_api.sync_lot(request, _lot())
    assert ei.value.status_code == 400
    # 批次文件、规则文件、引擎重载 都不该发生 (避免半成品)
    assert not _lot_path(tmp_path, "lot_test1").exists()
    assert reloaded == []
    rules_dir = tmp_path / "user_data" / "monitor_rules"
    assert not rules_dir.exists() or list(rules_dir.glob("*.json")) == []


def test_sync_lot_writes_lot_rules_and_reloads_once(tmp_path, monkeypatch):
    engine = _EngineStub()
    request = _make_request(tmp_path, engine)
    _patch_channels(monkeypatch)
    reloaded = []
    monkeypatch.setattr(lots_api, "_reload_engine", lambda r: reloaded.append(1))

    lots_api.sync_lot(request, _lot())
    assert _lot_path(tmp_path, "lot_test1").exists()
    price = monitor_rules.load_one(tmp_path, "lot_test1_p")
    date_rule = monitor_rules.load_one(tmp_path, "lot_test1_d")
    assert price is not None and price["lot_id"] == "lot_test1"
    assert price["conditions"][0] == {"field": "close", "op": ">=", "value": 1650.0}
    assert date_rule is not None and date_rule["remind_date"] == "2026-09-01"
    assert len(reloaded) == 1


def test_sync_lot_removes_rules_when_monitor_point_removed(tmp_path, monkeypatch):
    engine = _EngineStub()
    request = _make_request(tmp_path, engine)
    _patch_channels(monkeypatch)
    monkeypatch.setattr(lots_api, "_reload_engine", lambda r: None)
    lots_api.sync_lot(request, _lot())
    # 编辑后只剩止盈, 无到期 → date 规则应被级联删除
    lots_api.sync_lot(request, _lot(remind_date=None))
    assert monitor_rules.load_one(tmp_path, "lot_test1_d") is None
    assert monitor_rules.load_one(tmp_path, "lot_test1_p") is not None


def test_delete_lot_removes_lot_and_both_rules(tmp_path, monkeypatch):
    engine = _EngineStub()
    request = _make_request(tmp_path, engine)
    _patch_channels(monkeypatch)
    monkeypatch.setattr(lots_api, "_reload_engine", lambda r: None)
    lots_api.sync_lot(request, _lot())
    lots_api.delete_lot("lot_test1", request)
    assert not _lot_path(tmp_path, "lot_test1").exists()
    assert monitor_rules.load_one(tmp_path, "lot_test1_p") is None
    assert monitor_rules.load_one(tmp_path, "lot_test1_d") is None


def test_sync_lot_etf_resolves_asset_type(tmp_path, monkeypatch):
    engine = _EngineStub()
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        resolve_asset_type=lambda _symbol: "etf",
    )
    state = SimpleNamespace(repo=repo, monitor_engine=engine)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    _patch_channels(monkeypatch)
    monkeypatch.setattr(lots_api, "_reload_engine", lambda r: None)
    lots_api.sync_lot(request, _lot(symbol="510300.SH"))
    # 止盈止损价格规则须走 ETF 监控轮才会触发, 故 asset_type 必须为 etf
    price = monitor_rules.load_one(tmp_path, "lot_test1_p")
    date_rule = monitor_rules.load_one(tmp_path, "lot_test1_d")
    assert price is not None and price["asset_type"] == "etf"
    assert date_rule is not None and date_rule["asset_type"] == "etf"


def test_upsert_lot_invalid_returns_400(tmp_path, monkeypatch):
    engine = _EngineStub()
    request = _make_request(tmp_path, engine)
    _patch_channels(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        lots_api.upsert_lot(lots_api.LotModel(**_lot(cost_price=0)), request)
    assert ei.value.status_code == 400
    assert "cost_price" in str(ei.value.detail)


# ── 模拟盘持仓 → 批次 同步 (lot_pp_* 命名空间) ────────────
def _positions():
    return {
        "600519.SH": {
            "asset_type": "stock", "qty": 300, "avg_cost": 1500.0,
            "lots": [{"date": "2026-11-16", "qty": 300}],
        },
        "000042.SZ": {
            "asset_type": "stock", "qty": 100, "avg_cost": 6.28,
            "lots": [{"date": "2026-11-17", "qty": 100}],
        },
    }


def test_sync_paper_positions_writes_lots(tmp_path):
    r = lots_domain.sync_paper_positions(tmp_path, "quant_system", _positions())
    assert r == {"written": 2, "deleted": 0, "unchanged": 0}
    lot = lots_domain.load_all(tmp_path)
    assert {l["symbol"] for l in lot} == {"600519.SH", "000042.SZ"}
    sym = next(l for l in lot if l["symbol"] == "600519.SH")
    assert sym["id"].startswith("lot_pp_quant_system_")
    assert sym["qty"] == 300 and sym["cost_price"] == pytest.approx(1500.0)
    assert sym["buy_date"] == "2026-11-16"  # 最早批次买入日


def test_sync_paper_positions_idempotent(tmp_path):
    lots_domain.sync_paper_positions(tmp_path, "quant_system", _positions())
    # 同样的持仓再同步 → 全部 unchanged, 不重写文件
    r = lots_domain.sync_paper_positions(tmp_path, "quant_system", _positions())
    assert r == {"written": 0, "deleted": 0, "unchanged": 2}


def test_sync_paper_positions_keeps_user_alert_settings_and_manual_lots(tmp_path):
    lots_domain.sync_paper_positions(tmp_path, "quant_system", _positions())
    # 用户在同步批次上补填止盈止损
    lot_id = lots_domain.paper_lot_id("quant_system", "600519.SH")
    saved = next(l for l in lots_domain.load_all(tmp_path) if l["id"] == lot_id)
    saved.update({"target_pct": 10, "stop_pct": 5})
    lots_domain.save_one(tmp_path, saved)
    # 用户手动登记的批次
    manual = _lot(id="lot_manual1", symbol="300750.SZ")
    lots_domain.save_one(tmp_path, lots_domain.normalize_lot(manual))

    r = lots_domain.sync_paper_positions(tmp_path, "quant_system", _positions())

    assert r["unchanged"] == 2  # 补填的参数保留 → 批次仍视为不变
    kept = next(l for l in lots_domain.load_all(tmp_path) if l["id"] == lot_id)
    assert kept["target_pct"] == 10 and kept["stop_pct"] == 5
    # 手动批次不受影响
    assert any(l["id"] == "lot_manual1" for l in lots_domain.load_all(tmp_path))


def test_sync_paper_positions_removes_closed_and_updates_changed(tmp_path):
    lots_domain.sync_paper_positions(tmp_path, "quant_system", _positions())
    # 000042 清仓, 600519 加仓
    changed = _positions()
    changed["600519.SH"]["qty"] = 600
    del changed["000042.SZ"]

    r = lots_domain.sync_paper_positions(tmp_path, "quant_system", changed)

    assert r == {"written": 1, "deleted": 1, "unchanged": 0}
    symbols = {l["symbol"] for l in lots_domain.load_all(tmp_path)}
    assert symbols == {"600519.SH"}
    updated = next(l for l in lots_domain.load_all(tmp_path) if l["symbol"] == "600519.SH")
    assert updated["qty"] == 600


def test_paper_lot_id_namespace_isolates_accounts(tmp_path):
    a = lots_domain.paper_lot_id("acc_a", "600519.SH")
    b = lots_domain.paper_lot_id("acc_b", "600519.SH")
    assert a != b and a.startswith("lot_pp_acc_a_") and b.startswith("lot_pp_acc_b_")
