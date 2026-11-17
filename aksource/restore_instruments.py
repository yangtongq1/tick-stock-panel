#!/usr/bin/env python3
"""恢复 instruments.parquet: SZ 3068 只被今早 09:10 定时任务写丢 (TickFlow SZ 503)。
从新浪深市 A 股列表接口全量拉回, 与现有 2824 只合并。一次性脚本。
"""
import os
import time

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
       "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=sz_a")
PARQUET = "data/instruments/instruments.parquet"

rows = []
page = 1
while True:
    for attempt in range(3):
        try:
            r = requests.get(URL.format(page=page), headers=H, timeout=10)
            if r.status_code == 200:
                batch = r.json() if r.text.strip() else []
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        batch = []
    if not batch:
        break
    rows.extend(batch)
    print(f"page {page}: +{len(batch)} (total {len(rows)})", flush=True)
    page += 1
    time.sleep(0.3)

print(f"sina sz_a total: {len(rows)}")

# 合并: 新浪拿回的 SZ 全量 + 现有表 (SH 2469 + BJ 355)
old = pd.read_parquet(PARQUET)
print(f"current parquet: {len(old)}")

new_sz = pd.DataFrame([{
    "symbol": f"{r['code']}.SZ",
    "name": r["name"],
    "code": r["code"],
    "exchange": "SZ",
} for r in rows])
new_sz = new_sz.drop_duplicates(subset="symbol")
print(f"sina SZ unique: {len(new_sz)}")

# 现有表里已有的 SZ (0 只) 保留; 缺的列从旧表同 symbol 补 (无则 null)
merged = pd.concat([old, new_sz[~new_sz["symbol"].isin(old["symbol"])]], ignore_index=True)
# 继承字段: 同 symbol 从旧表补不了的 (SZ 全是新行), 用 null → 但 total_shares 等列保留
merged = merged.sort_values("symbol").reset_index(drop=True)
print(f"merged total: {len(merged)}")
assert len(merged) > 5800, "merged rows look wrong"

merged.to_parquet(PARQUET, index=False)
print(f"written: {PARQUET}")

# 校验
chk = pd.read_parquet(PARQUET)
print(chk["exchange"].value_counts().to_dict())
