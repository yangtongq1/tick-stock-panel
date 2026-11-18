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


# ---------------------------------------------------------------------------
# 预设定义: 字段结构 + 完整拉取配方
# ---------------------------------------------------------------------------

def _aksource_boards_url(kind: str) -> str:
    """本地 aksource 板块聚合端点 (东方财富源, 免鉴权)。"""
    return f"http://127.0.0.1:3021/boards?kind={kind}"


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


def _presets() -> list[ExtConfig]:
    return [_concept_preset(), _industry_preset()]


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
    for config in _presets():
        try:
            existing = store.get(config.id)
            if existing is None:
                if first_install:
                    store.upsert(config)
                    logger.info("内置扩展表 %s 配置已就绪 (含出厂拉取配方)", config.id)
                continue  # 已播种后缺失 = 用户删除过, 不复活

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
