#!/usr/bin/env python3
"""恢复 instruments.parquet 的 SZ 部分 (不依赖新浪列表接口, DNS 不稳)。

方案: 枚举深市代码段 (000xxx-005xxx 主板, 001xxx 主板新, 002xxx 中小板,
300xxx/301xxx 创业板), 腾讯 qt.gtimg.cn 批量探测, 有效代码返回名称。
B 股 (200xxx) TSP 的行情链路本就不覆盖, 跳过。
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
QT = "http://qt.gtimg.cn/q="
PARQUET = "data/instruments/instruments.parquet"


def probe_batch(codes: list[str]) -> dict[str, str]:
    """探测一批深市代码, 返回 {code: name}。无效代码腾讯返回 v_pv_none_none 或空名。"""
    qs = ",".join(f"sz{c}" for c in codes)
    for attempt in range(3):
        try:
            r = requests.get(QT + qs, headers=H, timeout=8)
            if r.status_code != 200:
                continue
            body = r.content.decode("gbk", errors="replace")
            out = {}
            import re
            for m in re.finditer(r'v_sz(\d{6})="([^"]*)"', body):
                code, payload = m.group(1), m.group(2)
                parts = payload.split("~")
                # 有效: 字段>=40 且名称非空; 无效代码返回 ~1 行或空名
                if len(parts) >= 40 and parts[1].strip():
                    out[code] = parts[1]
            return out
        except requests.RequestException:
            time.sleep(0.5)
    return {}


def main() -> None:
    # 深市代码段: 000000-004999 (主板+中小板), 300000-301999 (创业板)
    candidates = [f"{i:06d}" for i in range(0, 5000)] + \
                 [f"{i:06d}" for i in range(300000, 302000)]
    # 过滤: 000xxx 上市实体从 000001 起; 200xxx B股跳过 (不在段内)
    batches = [candidates[i:i + 50] for i in range(0, len(candidates), 50)]
    print(f"probing {len(candidates)} codes in {len(batches)} batches")

    found: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for result in ex.map(probe_batch, batches):
            found.update(result)
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(batches)} batches, found {len(found)}", flush=True)
    print(f"total valid SZ codes: {len(found)}")

    old = pd.read_parquet(PARQUET)
    print(f"current parquet: {len(old)} ({old['exchange'].value_counts().to_dict()})")

    existing_sz = set(old.loc[old['exchange'] == 'SZ', 'symbol'])
    new_rows = [
        {"symbol": f"{c}.SZ", "name": n, "code": c, "exchange": "SZ"}
        for c, n in sorted(found.items())
        if f"{c}.SZ" not in existing_sz
    ]
    print(f"new SZ rows to add: {len(new_rows)}")

    merged = pd.concat([old, pd.DataFrame(new_rows)], ignore_index=True)
    merged = merged.sort_values("symbol").reset_index(drop=True)
    print(f"merged total: {len(merged)}")

    merged.to_parquet(PARQUET, index=False)
    chk = pd.read_parquet(PARQUET)
    print("final:", chk["exchange"].value_counts().to_dict())


if __name__ == "__main__":
    main()
