"""批次登记域 — 薄"批次"页 (持仓提醒): 只生成监控规则, 不做会计。

每行一个买入批次 → 派生两条规则: lot_{id}_p (price 止盈止损) / lot_{id}_d (date 到期提醒)。
记账/加减仓属"交易口径", 不在本模块 (issue #230)。纯函数 + 文件存储 (统一契约 Store,
收口见 app/definitions.py), 不做 API、不做引擎重载。
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path

from app.definitions import JsonDefinitionStore
from app.strategy import monitor_rules
from app.strategy.monitor import MonitorRuleEngine  # 复用条件文本拼装 (静态方法)

# id 需满足规则 id 同款正则, 且为后缀留位: 派生规则 {id}_p/_d 不得超过 40 字符
_ID = monitor_rules.ID_RE
_MAX_ID_LEN = 40 - 2  # 派生规则 id 后缀 "_p" / "_d"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── 校验与归一化 ────────────────────────────────────────
def validate_lot(lot: dict) -> None:
    """校验批次字段, 非法抛 ValueError (中文信息)。"""
    lot_id = lot.get("id")
    if lot_id is not None and (
        not isinstance(lot_id, str) or not _ID.match(lot_id) or len(lot_id) > _MAX_ID_LEN
    ):
        raise ValueError(f"批次 id 非法 (仅小写字母数字下划线, 且需为派生规则 id 留位): {lot_id!r}")
    if not (lot.get("symbol") or "").strip():
        raise ValueError("symbol 不能为空")
    cost = lot.get("cost_price")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost <= 0:
        raise ValueError("cost_price 必须是正数")
    for key, label in (("qty", "数量"), ("target_pct", "止盈%"), ("stop_pct", "止损%")):
        v = lot.get(key, 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"{label} 不能为负数")
    lead = lot.get("lead_days", 0)
    if isinstance(lead, bool) or not isinstance(lead, int) or lead < 0:
        raise ValueError("lead_days 必须是非负整数")
    for key, label in (("buy_date", "买入日期"), ("remind_date", "到期日")):
        raw = lot.get(key)
        if raw not in (None, ""):
            try:
                _date.fromisoformat(raw)
            except ValueError:
                raise ValueError(f"{label} 必须是 YYYY-MM-DD: {raw!r}") from None
    if not (lot.get("target_pct", 0) > 0 or lot.get("stop_pct", 0) > 0 or lot.get("remind_date")):
        raise ValueError("止盈% / 止损% / 到期日 至少设置一项 (否则无监控点)")


def normalize_lot(lot: dict) -> dict:
    """补全默认字段, 返回规范化后的批次 (不校验)。"""
    d = dict(lot)
    d["symbol"] = (d.get("symbol") or "").strip()
    d.setdefault("qty", 0)
    d.setdefault("cost_price", 0)
    d.setdefault("buy_date", None)
    d.setdefault("target_pct", 0)
    d.setdefault("stop_pct", 0)
    d.setdefault("remind_date", None)
    d.setdefault("lead_days", 1)
    d.setdefault("created_at", _now_iso())
    return d


# ── 持久化 (统一契约 Store; normalize_lot 在上方定义) ────
# 只认 lot_*.json — 目录里的其他文件 (误放的导出等) 不进读取面。
_LOT_STORE = JsonDefinitionStore(
    "user_data/lots", glob_pattern="lot_*.json", label="lot", normalize=normalize_lot
)


def load_all(data_dir: Path) -> list[dict]:
    """读取全部批次。损坏的文件被跳过。"""
    return _LOT_STORE.load_all(data_dir)


def save_one(data_dir: Path, lot: dict) -> None:
    _LOT_STORE.save_one(data_dir, lot)


def delete_one(data_dir: Path, lot_id: str) -> bool:
    return _LOT_STORE.delete_one(data_dir, lot_id)


# ── 批次 → 监控规则 (纯映射) ───────────────────────────
def lot_to_rules(lot: dict) -> tuple[dict | None, dict | None]:
    """批次 → (price 止盈止损规则, date 到期规则); 无对应监控点时返回 None。

    纯映射不做 I/O; 规则 id 派生自批次 id ({lot_id}_p/_d), 保证稳定可级联。
    """
    symbol = lot["symbol"]
    lot_id = lot["id"]
    cost = float(lot["cost_price"])
    target = float(lot.get("target_pct", 0))
    stop = float(lot.get("stop_pct", 0))
    qty = float(lot.get("qty", 0) or 0)
    qty_text = f" · {qty:g}股" if qty > 0 else ""

    conds: list[dict] = []
    if target > 0:
        conds.append({"field": "close", "op": ">=", "value": round(cost * (1 + target / 100), 4)})
    if stop > 0:
        conds.append({"field": "close", "op": "<=", "value": round(cost * (1 - stop / 100), 4)})
    price_rule = None
    if conds:
        msg = f"批次止盈止损 · 成本{cost:g}"
        if target > 0:
            msg += f" · 止盈{target:g}%"
        if stop > 0:
            msg += f" · 止损{stop:g}%"
        msg += qty_text
        cond_text = MonitorRuleEngine._format_conditions_text({"logic": "or"}, conds)
        if cond_text:
            msg += f" · {cond_text}"
        price_rule = {
            "id": f"{lot_id}_p",
            "name": f"批次止盈止损 · {symbol}",
            "type": "price",
            "asset_type": "stock",
            "scope": "symbols",
            "symbols": [symbol],
            "conditions": conds,
            "logic": "or",
            "cooldown_seconds": 86400,
            "severity": "warn",
            "message": msg,
            "enabled": True,
            "lot_id": lot_id,
        }

    date_rule = None
    if lot.get("remind_date"):
        lead = int(lot.get("lead_days", 1))
        date_rule = {
            "id": f"{lot_id}_d",
            "name": f"批次到期 · {symbol}",
            "type": "date",
            "asset_type": "stock",
            "scope": "symbols",
            "symbols": [symbol],
            "remind_date": lot["remind_date"],
            "lead_days": lead,
            "cooldown_seconds": 86400,
            "severity": "info",
            # 提前天数由引擎 evaluate_date_rules 统一追加, 这里只放静态部分
            "message": f"批次到期提醒 · {lot['remind_date']}{qty_text}",
            "enabled": True,
            "lot_id": lot_id,
        }
    return price_rule, date_rule


# ── 模拟盘持仓 → 批次 同步 (单向: paper → lots) ─────────
# 模拟盘持仓 (paper.py positions.json) 与手动登记批次 (lots) 是两个独立
# 账本: 前者是策略自动交易的持仓, 后者是提醒规则的来源。同步把模拟盘
# 当前持仓镜像成批次, 让止盈止损/到期提醒与实际持仓保持一致。

# 同步批次的稳定 id: lot_pp_{account}_{code} — lot_ 前缀匹配 _LOT_STORE 的
# glob (lot_*.json); account 为账户 id 的小写化, code 为 symbol 去交易所后缀
# (000042.SZ → 000042)。连同派生规则后缀 _p/_d 不得超过 40 字符。
def paper_lot_id(account_id: str, symbol: str) -> str:
    code = symbol.split(".")[0]
    acc = re.sub(r"[^a-z0-9_]", "", account_id.lower())
    lot_id = f"lot_pp_{acc}_{code}"
    if not _ID.match(lot_id) or len(lot_id) > _MAX_ID_LEN:
        raise ValueError(f"账户 {account_id!r} + 标的 {symbol!r} 生成的批次 id 超长: {lot_id!r}")
    return lot_id


def diff_paper_positions(
    data_dir: Path, account_id: str, positions: dict[str, dict]
) -> tuple[list[dict], list[str], list[dict]]:
    """对比模拟盘持仓与现有同步批次, 返回 (需写入的批次, 需删除的批次 id, 不变批次)。

    只管理 lot_pp_* 命名空间的批次 (模拟盘同步): 用户手动登记的批次绝不触碰;
    账户清仓后对应同步批次自动移除。数量/成本变化重写批次。
    """
    existing = {l["id"]: l for l in load_all(data_dir) if l["id"].startswith("lot_pp_")}
    wanted: dict[str, dict] = {}
    for symbol, pos in positions.items():
        qty = int(pos.get("qty", 0) or 0)
        cost = float(pos.get("avg_cost", 0) or 0)
        if qty <= 0 or cost <= 0:
            continue
        # 最早批次买入日 (多批合并持仓取最早, 提醒口径与持有天数一致)
        dates = [str(l.get("date") or "")[:10] for l in pos.get("lots", []) if l.get("qty", 0) > 0]
        buy_date = min(dates) if dates else None
        lot_id = paper_lot_id(account_id, symbol)
        wanted[lot_id] = {
            "id": lot_id,
            "symbol": symbol,
            "qty": qty,
            "cost_price": cost,
            "buy_date": buy_date,
            # 止盈止损默认 0: 模拟盘持仓的提醒点由用户按需在批次页补填,
            # 同步只保证「持仓在批次页可见且随持仓增删」, 不替用户做止盈止损决策。
            "target_pct": 0,
            "stop_pct": 0,
            "remind_date": None,
            "lead_days": 1,
            "note": f"模拟盘 {account_id} 同步",
        }
    # 保留用户在同步批次上补填的止盈止损/到期设置 (同步覆盖数量/成本/日期,
    # 不抹掉提醒参数)
    for lot_id, lot in wanted.items():
        old = existing.get(lot_id)
        if old:
            for key in ("target_pct", "stop_pct", "remind_date", "lead_days"):
                if old.get(key):
                    lot[key] = old[key]
            if old.get("created_at"):
                lot["created_at"] = old["created_at"]
    unchanged = [existing[i] for i in existing if i in wanted and existing[i] == wanted.get(i)]
    to_write = [wanted[i] for i in wanted if i not in existing or existing[i] != wanted[i]]
    to_delete = [i for i in existing if i not in wanted]
    return to_write, to_delete, unchanged


def sync_paper_positions(
    data_dir: Path, account_id: str, positions: dict[str, dict]
) -> dict:
    """把模拟盘持仓镜像为 lot_pp_* 批次 (纯数据层: 写批次文件, 不建派生规则)。

    派生规则 (止盈止损/到期) 与引擎重载由 API 层 sync_lot 统一处理 —
    同步批次 target/stop 默认 0, 无派生价格规则; 用户补填后由批次页
    正常编辑链路生成规则。
    """
    to_write, to_delete, unchanged = diff_paper_positions(data_dir, account_id, positions)
    for lot in to_write:
        save_one(data_dir, normalize_lot(lot))
    for lot_id in to_delete:
        delete_one(data_dir, lot_id)
        # 同步批次默认无派生规则 (target/stop=0), 但用户补填过再清仓的情形要级联删
        monitor_rules.delete_one(data_dir, f"{lot_id}_p")
        monitor_rules.delete_one(data_dir, f"{lot_id}_d")
    return {"written": len(to_write), "deleted": len(to_delete), "unchanged": len(unchanged)}
