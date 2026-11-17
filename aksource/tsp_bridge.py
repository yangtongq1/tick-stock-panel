#!/usr/bin/env python3
"""TSP Bridge — QUANT TERMINAL 侧数据同步器

把 TSP (tick-stock-panel, 10.2.106.66:3018) 的数据拉到本机,
供 QUANT TERMINAL / 任意消费者使用。零依赖 (纯标准库)。

用法:
  python3 tsp_bridge.py            # 拉一次全量快照 (打印 JSON)
  python3 tsp_bridge.py --watch 30 # 每 30 秒轮询, 变化才输出
  python3 tsp_bridge.py --out snapshot.json   # 落盘到文件

可用数据集:
  accounts   模拟账户列表
  positions  持仓 (默认 quant_system 账户)
  intraday   实时行情快照状态 (全市场 5887 只)
  realtime   实时行情 (单股/全量)
  kline      日K线
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

TSP_BASE = "http://10.2.106.66:3018"
DEFAULT_ACCOUNT = "quant_system"


def api(path: str, timeout: float = 30) -> dict:
    url = f"{TSP_BASE}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_accounts() -> list[dict]:
    return api("/api/paper/accounts").get("accounts", [])


def fetch_positions(account: str = DEFAULT_ACCOUNT) -> list[dict]:
    return api(f"/api/paper/positions?account={account}").get("holdings", [])


def fetch_intraday_status() -> dict:
    return api("/api/intraday/status")


def fetch_realtime(symbol: str | None = None, timeout: float = 60) -> list[dict]:
    """symbol 为空 = 全市场快照 (约 5887 只, 内存缓存秒回)"""
    q = f"?symbols={symbol}" if symbol else ""
    return api(f"/api/intraday/quotes{q}", timeout=timeout).get("rows", [])


def fetch_kline(symbol: str, limit: int = 120) -> list[dict]:
    return api(f"/api/kline/daily?symbol={symbol}&limit={limit}").get("rows", [])


def snapshot(account: str = DEFAULT_ACCOUNT) -> dict:
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "accounts": fetch_accounts(),
        "positions": fetch_positions(account),
        "intraday_status": fetch_intraday_status(),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--account", default=DEFAULT_ACCOUNT)
    p.add_argument("--watch", type=float, default=0, help="轮询间隔秒, 0=单次")
    p.add_argument("--out", help="输出到文件 (JSON)")
    args = p.parse_args()

    def emit(data: dict) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)

    last_hash: int | None = None
    while True:
        try:
            snap = snapshot(args.account)
            h = hash(json.dumps(snap, sort_keys=True))
            if h != last_hash:
                emit(snap)
                last_hash = h
        except Exception as e:  # noqa: BLE001
            print(f"[tsp-bridge] fetch failed: {e}", file=sys.stderr)
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
