"""行情数据 Git 自动发布 — 盘后管道成功后把当日增量 commit+push 到 GitHub。

设计目标 (与 QUICKSTART「clone 即得完整行情」配套):
- 各机器盘后管道 (15:35) 落盘当日 parquet 增量 → 本模块把增量推上 GitHub;
- 任何机器 git clone 即得全部历史行情, 之后各自每日 aksource 增量不受影响;
- 历史分区只追加不覆盖 → 多机各自增量天然无冲突, 偶发同日重写由 rebase 合并。

安全边界:
- 只 add data/ 白名单目录 (由 .gitignore 控制), 显式排除 user_data/secrets;
- git 凭据走系统 credential store, 绝不落盘;
- 失败只记日志, 绝不影响盘后管道结果。

推送链路: 直连 → 失败自动切本地代理 (127.0.0.1:7897, Clash 常用端口) → 再失败放弃等次日。
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# 与 .gitignore 白名单一致的目录 (防未来 ignore 规则变动误提交运行时数据)
_ALLOWED_DIRS = (
    "kline_daily",
    "kline_daily_enriched",
    "kline_index_daily",
    "kline_minute",
    "financials",
    "adj_factor",
    "instruments",
    "instruments_etf",
    "instruments_index",
    "data_sources",
)

# 本地代理候选端口 (Clash Verge 默认 7897, Clash 经典 7890), 逐个探测
_PROXY_CANDIDATES = ("127.0.0.1:7897", "127.0.0.1:7890")


def _git(repo_root: Path, *args: str, timeout_s: int = 120, env_proxy: str | None = None) -> subprocess.CompletedProcess:
    """在 repo_root 执行 git 命令。env_proxy 传入时设置 https_proxy/HTTPS_PROXY。"""
    import os

    env = os.environ.copy()
    if env_proxy:
        env["https_proxy"] = f"http://{env_proxy}"
        env["HTTPS_PROXY"] = f"http://{env_proxy}"
    else:
        env.pop("https_proxy", None)
        env.pop("HTTPS_PROXY", None)
        env.pop("http_proxy", None)
        env.pop("http_proxy".upper(), None)
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        env=env,
    )


def _find_git_root(data_dir: Path) -> Path | None:
    """从 data_dir 向上找 .git; data 目录在 repo 内则命中, 独立部署无 git 返回 None。"""
    cur = data_dir.resolve()
    for _ in range(6):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _proxy_alive(proxy: str, timeout_s: float = 3.0) -> bool:
    """探测本地代理端口是否有 TCP 监听。"""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(f"//{proxy}")
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 7897
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def publish_increment(data_dir: Path) -> dict:
    """把 data/ 下新增/变更的行情文件 commit + push。幂等: 无变更时不动 git。

    返回 {"ok": bool, "pushed": bool, "detail": str} — 调用方 (盘后管道钩子)
    只记日志, 不因失败中断管道。
    """
    repo_root = _find_git_root(data_dir)
    if repo_root is None:
        return {"ok": False, "pushed": False,
                "detail": "数据目录不在 git 仓库内, 跳过发布 (独立部署无 git 属正常)"}

    # 1. add 白名单目录 (不存在的跳过 — git add 对不存在的 pathspec 会 fatal)
    targets = [f"data/{d}" for d in _ALLOWED_DIRS if (repo_root / "data" / d).exists()]
    if not targets:
        return {"ok": True, "pushed": False, "detail": "data/ 无白名单数据目录, 跳过"}
    r = _git(repo_root, "add", "-A", "--", *targets)
    if r.returncode != 0:
        return {"ok": False, "pushed": False, "detail": f"git add 失败: {r.stderr.strip()[:200]}"}

    # 2. 无变更则直接返回 (幂等, 日常多数手动触发场景)
    r = _git(repo_root, "diff", "--cached", "--quiet", "--", *targets)
    if r.returncode == 0:
        # 白名单无新增但暂存区可能有别人的改动(如代码): 不动它, 只说明本模块无事可做
        return {"ok": True, "pushed": False, "detail": "无新增数据, 跳过 commit"}

    # 3. commit 只圈定白名单路径 — 暂存区里其他内容 (代码改动等) 一概不带走,
    #    否则开发者手动 add 的半成品会被数据发布捎带上 GitHub。
    r = _git(repo_root, "commit", "-m", "data: 盘后管道增量行情", "--", *targets)
    if r.returncode != 0:
        return {"ok": False, "pushed": False, "detail": f"git commit 失败: {r.stderr.strip()[:200]}"}
    n = r.stdout.count("\n")
    logger.info("data publish: committed incremental data (%d files)", n)

    # 4. push (直连 → 代理), 被拒(远端超前)则 rebase 后重推一次
    detail = _push_with_fallback(repo_root)
    return {"ok": detail.startswith("pushed"), "pushed": detail.startswith("pushed"), "detail": detail}


def _push_with_fallback(repo_root: Path) -> str:
    """直连推送; 失败切代理; 仍被拒(非快进)则 pull --rebase 后重推。

    返回人类可读结果串, 以 "pushed" 开头表示成功。
    """
    for label, proxy in (("direct", None), *[(f"proxy {p}", p) for p in _PROXY_CANDIDATES if _proxy_alive(p)]):
        r = _git(repo_root, "push", "origin", "main", timeout_s=300, env_proxy=proxy)
        if r.returncode == 0:
            return f"pushed ({label})"
        err = (r.stderr or r.stdout or "").strip()
        # 非快进 = 远端有其他机器已推的提交 → rebase 到远端再推
        if "fetch first" in err or "non-fast-forward" in err or "behind" in err:
            rp = _git(repo_root, "pull", "--rebase", "--autostash", "origin", "main", timeout_s=300, env_proxy=proxy)
            if rp.returncode != 0:
                logger.warning("data publish: pull --rebase 失败: %s", rp.stderr.strip()[:200])
                continue
            r2 = _git(repo_root, "push", "origin", "main", timeout_s=300, env_proxy=proxy)
            if r2.returncode == 0:
                return f"pushed (rebase, {label})"
            err = (r2.stderr or r2.stdout or "").strip()
        logger.warning("data publish: push via %s 失败: %s", label, err[:200])
    return "push failed (direct + proxy 均未成功, 留待下次盘后再试)"


def run_after_pipeline(data_dir: Path) -> dict:
    """盘后管道成功钩子入口 — publish_increment 的安全包装 (吞掉一切异常)。"""
    try:
        result = publish_increment(data_dir)
        (logger.info if result.get("ok") else logger.warning)(
            "data publish after pipeline: %s", result.get("detail"))
        return result
    except Exception:  # noqa: BLE001
        logger.exception("data publish after pipeline: 未预期异常 (不影响管道结果)")
        return {"ok": False, "pushed": False, "detail": "unexpected error (见日志)"}
