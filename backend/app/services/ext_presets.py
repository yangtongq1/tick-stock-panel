"""内置扩展数据预设 — 概念/行业。

设计原则:
  - 出厂即写【完整可用的拉取配置】(URL / 鉴权 / 两级请求配方), 不是空壳。
    这样「数据」页展开表 → 拉取 就能看到一份真实可跑的配方, 照抄即可接入
    自有源; 也免去"点获取按钮才拉"的隐藏路径。
  - 扩展数据通用逻辑零改动 (ExtConfig / fetch_and_ingest / API / 前端均不动):
    概念/行业所需的「目录 → 逐条目」形态由 PullConfig.fanout 声明式表达,
    通用引擎 (ext_fanout) 执行, 无按 id 的特例分支。
  - 「只播种一次」: 预设仅在首次安装 (ext_data 下无播种标记) 时创建;
    之后缺失 = 用户删除过, 重启不复活 (用户可能已接自有概念/行业源,
    复活会叠加每日重复拉取)。标记独立于表目录, 删表不会误清。
  - 「已存在则跳过」: 绝不覆盖用户已有配置与数据, 老用户零影响。
  - 出厂值变更时定向迁移: 仅当现有配置的 pull 精确命中历代出厂值时整体替换,
    用户自定义过的一律不碰 (见 _RETIRED_FACTORY_PULLS)。迁移不受播种标记
    限制 — 存活表的出厂配方升级必须继续送达。
  - 拉取失败只记 warning, 不阻断启动 (保持「没数据也能跑」)。

数据来源 (概念/行业共用 fuyao 同花顺指数接口):
  - 板块清单: /api/a-share-index/catalog/ths-index-list (tag=cn_concept / industry)
  - 板块成分: /api/a-share-index/constituents/ths-stock-list (单板块一次)
  接口方向为「板块→股票」, fanout 反转为「股票→板块名清单」。
  鉴权复用 fuyao 行情插件同一凭据 (auth.key_ref = fuyao_api_key), 用户只需
  在一处填 Key。全量约 390+320 次成分股调用, 8 并发可承受。

接入点: app.main.lifespan → ensure_builtin_presets(store.data_dir)
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.services.ext_data import (
    ExtConfig,
    ExtConfigStore,
    ExtField,
    PullConfig,
)
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

# 历代出厂源指纹 (仅 _RETIRED_FACTORY_PULLS 引用): fuyao 同花顺指数接口,
# 鉴权 X-api-key, 信封 {code, message, data:{item:[...]}}。2026-11-18 起出厂
# 配方改走本地 aksource 聚合端点 (免鉴权), fuyao 配方仅留作旧库迁移指纹。
_FUYAO_BASE = "https://fuyao.aicubes.cn"
_FUYAO_CATALOG_PATH = "/api/a-share-index/catalog/ths-index-list"

_PRESET_IDS = ("ext_gn_ths", "ext_hy_ths")

# 播种标记: 出厂预设只在标记缺失 (首次安装/老库首次升级到播种机制) 时创建。
_SEED_MARKER = ".builtin_presets_seeded"

# 出厂批次标记: 播种机制上线后新增的内置表, 用独立批次标记送达到老库
# (既不复活用户删过的旧表, 也让新批次表正常创建)。值为批次号, 逐批递增。
# v2 = 2026-11-19 涨停板三池 (ext_zt_pool / ext_zb_pool / ext_dt_pool)。
# v3 = 2026-11-19 资金流向 (ext_fund_flow / ext_fund_flow_board)。
_SEED_BATCH_MARKER = ".builtin_presets_batch"
_SEED_BATCH_VERSION = 3
_SEED_BATCH_MEMBERS = {
    2: ("ext_zt_pool", "ext_zb_pool", "ext_dt_pool"),
    3: ("ext_fund_flow", "ext_fund_flow_board"),
}


# ---------------------------------------------------------------------------
# 预设定义: 字段结构 + 完整拉取配方
# ---------------------------------------------------------------------------

def _aksource_boards_url(kind: str) -> str:
    """本地 aksource 板块聚合端点 (东方财富源, 免鉴权)。"""
    return f"http://127.0.0.1:3021/boards?kind={kind}"


def _aksource_zt_url(pool: str) -> str:
    """本地 aksource 涨停板三池端点 (东方财富 push2ex, 免鉴权)。"""
    return f"http://127.0.0.1:3021/zt_pool?pool={pool}"


_AKSOURCE_DESC = "板块成分 (东方财富 push2, 经本地 aksource 聚合; 拉取配方见「拉取」页)"
_AKSOURCE_TIMEOUT_S = 180.0  # 首轮全量聚合 (1050 板块) 20-60s, 留足余量


def _concept_preset() -> ExtConfig:
    """扩展概念 (ext_gn_ths)。

    本地 aksource 聚合端点: 一次请求返回「股票 → 所属概念清单」全量
    (东财 554 个概念板块逐板块拉成分后反转聚合, 20s 内完成)。
    输出 schema: 股票代码 / 股票简称 / 所属概念 / symbol / code
    """
    return ExtConfig(
        id="ext_gn_ths",
        label="扩展概念",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            ExtField("股票简称", "string", "股票简称"),
            ExtField("所属概念", "string", "所属概念"),
        ],
        description=_AKSOURCE_DESC,
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url=_aksource_boards_url("concept"),
            method="GET",
            response_path="data",
            field_map={"board_list": "所属概念"},
            auth={"type": "none"},
            timeout_seconds=int(_AKSOURCE_TIMEOUT_S),
            schedule_minutes=1440,
            enabled=True,
        ),
    )


def _industry_preset() -> ExtConfig:
    """扩展行业 (ext_hy_ths)。

    本地 aksource 聚合端点。东财行业板块天然带层级 (小家电;家用电器),
    ";" 分隔保留全部层级标签。
    输出 schema: 股票代码 / 股票简称 / 所属同花顺行业 / symbol / code
    """
    return ExtConfig(
        id="ext_hy_ths",
        label="扩展行业",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            ExtField("股票简称", "string", "股票简称"),
            ExtField("所属同花顺行业", "string", "所属同花顺行业"),
        ],
        description=_AKSOURCE_DESC,
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url=_aksource_boards_url("industry"),
            method="GET",
            response_path="data",
            field_map={"board_list": "所属同花顺行业"},
            auth={"type": "none"},
            timeout_seconds=int(_AKSOURCE_TIMEOUT_S),
            schedule_minutes=1440,
            enabled=True,
        ),
    )


_ZT_FIELDS_COMMON = [
    ("股票简称", "string", "股票简称"),
    ("交易所", "string", "交易所"),
    ("最新价", "float", "最新价"),
    ("涨跌幅", "float", "涨跌幅 %"),
    ("成交额", "float", "成交额 元"),
    ("流通市值", "float", "流通市值 元"),
    ("总市值", "float", "总市值 元"),
    ("换手率", "float", "换手率 %"),
    ("所属行业", "string", "所属行业"),
    ("首次封板时间", "string", "首次封板时间"),
    ("最后封板时间", "string", "最后封板时间"),
    ("炸板次数", "float", "炸板次数"),
    ("封板资金", "float", "封板资金 元"),
    ("连板数", "float", "连板数"),
    ("涨停统计", "string", "涨停统计 (次数/天数)"),
]

_ZT_POOLS_DEF = (
    ("zt", "ext_zt_pool", "涨停股池", [("涨停价", "float", "涨停价")]),
    ("zb", "ext_zb_pool", "炸板股池", [("涨停价", "float", "涨停价"), ("振幅", "float", "振幅 %"), ("涨速", "float", "涨速")]),
    ("dt", "ext_dt_pool", "跌停股池", [("封单资金", "float", "封单资金 元")]),
)


def _zt_pool_preset(pool: str, config_id: str, label: str, extra_fields: list[tuple]) -> ExtConfig:
    """涨停板三池预设 (东方财富 push2ex, 经本地 aksource 聚合)。

    timeseries + date_param=compact: 每日分区, 支持历史回补
    (东财服务端保留历史日期数据, backfill_history 可按本地交易日逐日补齐)。
    盘中多盘: time_field=PLATE_TS_FIELD 注入拉取时刻, 同日多盘去重。
    """
    return ExtConfig(
        id=config_id,
        label=label,
        mode="timeseries",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            *[ExtField(*f) for f in _ZT_FIELDS_COMMON],
            *[ExtField(*f) for f in extra_fields],
        ],
        description="涨停板池 (东方财富 push2ex, 经本地 aksource 聚合; 含连板数/封板资金/炸板次数)",
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url=_aksource_zt_url(pool),
            method="GET",
            response_path="data",
            field_map={},
            auth={"type": "none"},
            timeout_seconds=60,
            schedule_minutes=30,
            schedule_trading_only=True,
            time_field="__pull_ts__",
            date_param="date",
            date_format="compact",
            enabled=True,
        ),
    )


def _fund_flow_preset() -> ExtConfig:
    """个股资金流排行 (新浪源, 经本地 aksource 聚合) — timeseries 每日累积。

    新浪接口为当日快照 (无历史参数), 盘中多盘 time_field 去重;
    每日一分区自然形成历史序列 (从启用日起累积)。
    主力口径: 净流入 = 全单净额; 另带特大/大/中/小单净额四档。
    ETF/指数行会随 symbol 关联自然过滤 (instruments 维表无此标的)。
    """
    return ExtConfig(
        id="ext_fund_flow",
        label="个股资金流",
        mode="timeseries",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("code", "string", "代码"),
            ExtField("股票代码", "string", "股票代码"),
            ExtField("股票简称", "string", "股票简称"),
            ExtField("最新价", "float", "最新价"),
            ExtField("涨跌幅", "float", "涨跌幅 %"),
            ExtField("成交额", "float", "成交额 元"),
            ExtField("流入额", "float", "流入额 元"),
            ExtField("流出额", "float", "流出额 元"),
            ExtField("净流入", "float", "净流入 元"),
            ExtField("净流入率", "float", "净流入率 %"),
            ExtField("特大单净额", "float", "特大单净额 元"),
            ExtField("大单净额", "float", "大单净额 元"),
            ExtField("中单净额", "float", "中单净额 元"),
            ExtField("小单净额", "float", "小单净额 元"),
            ExtField("换手率", "float", "换手率 %"),
        ],
        description="个股资金流排行 (新浪源, 经本地 aksource 聚合; 净流入/特大/大/中/小单五档)",
        symbol_map={"type": "mapped", "col": "股票代码"},
        code_map={"type": "computed", "from": "symbol", "method": "strip_exchange"},
        pull=PullConfig(
            url="http://127.0.0.1:3021/fund_flow?kind=stock&num=1000",
            method="GET",
            response_path="data",
            field_map={},
            auth={"type": "none"},
            timeout_seconds=60,
            schedule_minutes=30,
            schedule_trading_only=True,
            time_field="__pull_ts__",
            enabled=True,
        ),
    )


def _fund_flow_board_preset() -> ExtConfig:
    """行业板块资金流 (新浪源) — market_level 表 (行=板块, 无 symbol 关联)。"""
    return ExtConfig(
        id="ext_fund_flow_board",
        label="板块资金流",
        mode="timeseries",
        market_level=True,
        fields=[
            ExtField("板块代码", "string", "板块代码"),
            ExtField("板块名称", "string", "板块名称"),
            ExtField("平均价", "float", "平均价"),
            ExtField("平均涨跌幅", "float", "平均涨跌幅 %"),
            ExtField("换手率", "float", "换手率 %"),
            ExtField("成交额", "float", "成交额 元"),
            ExtField("流入额", "float", "流入额 元"),
            ExtField("流出额", "float", "流出额 元"),
            ExtField("净流入", "float", "净流入 元"),
            ExtField("净流入率", "float", "净流入率 %"),
        ],
        description="行业板块资金流排行 (新浪源, 经本地 aksource 聚合; 板块级无标的关联)",
        pull=PullConfig(
            url="http://127.0.0.1:3021/fund_flow?kind=hangye",
            method="GET",
            response_path="data",
            field_map={},
            auth={"type": "none"},
            timeout_seconds=60,
            schedule_minutes=30,
            schedule_trading_only=True,
            time_field="__pull_ts__",
            enabled=True,
        ),
    )


def _presets() -> list[ExtConfig]:
    return [_concept_preset(), _industry_preset(), _fund_flow_preset(), _fund_flow_board_preset()] + [
        _zt_pool_preset(pool, cid, label, extra)
        for pool, cid, label, extra in _ZT_POOLS_DEF
    ]


def get_preset(config_id: str) -> ExtConfig | None:
    """按 id 取预设定义 (供 API 层校验 id 合法性)。"""
    for c in _presets():
        if c.id == config_id:
            return c
    return None


# 历代出厂 pull 配方指纹 — 仅比对 url。命中即视为未改过的出厂值 (无论有无
# fanout: fuyao 两级形态与早期目录标记同源), 启动时整体替换为当前配方;
# 用户自己填过 URL 的不动。
_RETIRED_FACTORY_PULLS: dict[str, tuple[dict, ...]] = {
    "ext_gn_ths": (
        # 换源前的自维护种子源
        {"url": "https://shy313.com/api/plugins/market_flow/exports/ths-concepts"},
        # fuyao 目录/两级形态 (依赖付费 fuyao_api_key, 未配时恒失败)
        {"url": f"{_FUYAO_BASE}{_FUYAO_CATALOG_PATH}?tag=cn_concept"},
    ),
    "ext_hy_ths": (
        {"url": "https://shy313.com/api/plugins/market_flow/exports/ths-industries"},
        {"url": f"{_FUYAO_BASE}{_FUYAO_CATALOG_PATH}?tag=industry"},
    ),
}
# 出厂 description 前缀 (历代文案) — 命中则同步为当前文案, 用户自定义描述不动
_FACTORY_DESC_PREFIXES = (
    "同花顺概念分类",
    "同花顺行业分类",
)


def _is_retired_factory_pull(config_id: str, pull: PullConfig | None) -> bool:
    """现有 pull 是否为历代出厂值 (不含当前配方), 需要补齐/替换为当前配方。

    True 的情形:
      - pull 为 None / 空 URL: 出厂空壳 (#199 之后、本次改动之前的形态)
      - URL 命中历代出厂源: 无 fanout (种子源/目录标记) 或 fuyao fanout 形态
        (依赖付费 Key, 未配 Key 时恒失败, 与空壳等价) 均视为出厂值
    用户自定义过 URL 的配置一律 False (绝不覆盖)。
    """
    if pull is None:
        return True
    url = (pull.url or "").strip()
    if not url:
        return True   # 空 URL 的空壳配置: 由出厂空壳演进而来, 需补齐
    urls = {retired["url"] for retired in _RETIRED_FACTORY_PULLS.get(config_id, ())}
    if url not in urls:
        return False  # 用户自定义 URL, 绝不覆盖
    # 同一 url 的出厂形态要么带 fanout (fuyao 两级), 要么不带 (种子源/目录标记):
    # 只要是出厂 url 且非当前配方, 一律视为出厂值待迁移。
    return True


async def ensure_builtin_presets(data_dir: Path) -> None:
    """启动时: 首次安装创建预设配置, 并把历代出厂空壳/旧配方补齐为当前配方。

    安全保证:
      - 创建仅发生在播种标记缺失时 (全新数据目录, 或老库首次升级到本机制);
        已播种后预设缺失 = 用户删除过, 绝不复活 (用户可能已接自有源,
        复活会叠加每日 710 次成分股调用)
      - 已存在且是用户自定义配置 → 跳过, 绝不覆盖用户数据
      - 仅当 pull 为空 / 命中历代出厂配方指纹时, 用当前完整配方替换
        (配方迁移不受播种标记限制, 存活表的出厂配方升级必须送达)
      - description 仅当命中出厂前缀时同步, 用户自定义描述不动
      - 只写 config.json (不拉数据), 失败只记 warning 不阻断启动

    数据获取: 由拉取调度器按 schedule_minutes 执行, 或用户在数据页点「拉取 → 执行」。
    """
    store = ExtConfigStore(data_dir)
    marker = data_dir / "ext_data" / _SEED_MARKER
    first_install = not marker.exists()

    # 批次播种: 老库 (已有 v1 标记) 升级到含新出厂表的版本时, 只创建
    # 本批次新增的表 —— 用户此前删除过的 v1 表不会被复活 (不在批次成员里)。
    batch_marker = data_dir / "ext_data" / _SEED_BATCH_MARKER
    def _read_batch() -> int:
        try:
            return int((batch_marker.read_text(encoding="utf-8") or "0").strip() or 0)
        except Exception:  # noqa: BLE001
            return 0
    batch_now = _read_batch()
    new_ids: set[str] = set()
    if batch_now < _SEED_BATCH_VERSION:
        for v in range(batch_now + 1, _SEED_BATCH_VERSION + 1):
            new_ids.update(_SEED_BATCH_MEMBERS.get(v, ()))

    for config in _presets():
        try:
            existing = store.get(config.id)
            if existing is None:
                # 全新安装 (任意批次表都创建) 或本批次新增表 → 创建;
                # 老批次表在已播种库上缺失 = 用户删除过, 不复活。
                if first_install or config.id in new_ids:
                    store.upsert(config)
                    logger.info("内置扩展表 %s 配置已就绪 (含出厂拉取配方)", config.id)
                continue

            changed = False
            if _is_retired_factory_pull(config.id, existing.pull):
                existing.pull = config.pull
                changed = True
            if not (existing.description or "").strip() or \
                    (existing.description or "").startswith(_FACTORY_DESC_PREFIXES):
                if existing.description != config.description:
                    existing.description = config.description
                    changed = True

            if changed:
                store.upsert(existing)
                logger.info("内置扩展表 %s 出厂配方已更新为当前版本", config.id)
        except Exception as e:  # noqa: BLE001 — 启动契约: 预设写入失败不阻断
            logger.warning("内置扩展表 %s 配置写入失败 (不影响启动): %s", config.id, e)
    if first_install:
        # 先建配置后落标记: 中途失败下次启动幂等重试 (已建的命中「已存在跳过」)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(marker, "")
        except Exception as e:  # noqa: BLE001 — 启动契约: 标记失败不阻断
            logger.warning("内置预设播种标记写入失败 (下次启动会重试创建): %s", e)
    if batch_now < _SEED_BATCH_VERSION:
        # 批次标记在表创建后落地; 失败下次启动重试 (已创建的命中「已存在跳过」)
        try:
            batch_marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(batch_marker, str(_SEED_BATCH_VERSION))
        except Exception as e:  # noqa: BLE001
            logger.warning("内置预设批次标记写入失败 (下次启动会重试): %s", e)
