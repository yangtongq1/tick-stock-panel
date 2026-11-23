"""ext_pull 失败退避重试 — 「后端先起、数据源未就绪」场景。

旧实现: 拉取失败后按正常间隔重试 (zt_pool 30min / 概念行业 1440min),
新机器「没有实时数据/板块」的体感根因。新实现: 失败后 30s 起指数退避
(封顶 5min), 成功恢复正常间隔。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import ext_pull
from app.services.ext_data import ExtConfig, PullConfig


def _config(interval_min: int = 30) -> ExtConfig:
    from app.services.ext_data import ExtField

    return ExtConfig(
        id="ext_test",
        label="测试表",
        mode="timeseries",
        fields=[ExtField(name="symbol", dtype="string", label="代码")],
        pull=PullConfig(
            url="http://127.0.0.1:3021/test",
            schedule_minutes=interval_min,
            enabled=True,
        ),
    )


class _Store:
    """ExtConfigStore 桩: 内存态, upsert 持久化到 get。

    注意真实 ExtConfigStore.get/upsert 是同步方法 (经 run_ext_data_io 丢线程池),
    桩也用同步 def。
    """

    def __init__(self, cfg: ExtConfig):
        self._cfg = cfg

    def get(self, cid: str) -> ExtConfig | None:
        return self._cfg if cid == self._cfg.id else None

    def upsert(self, cfg: ExtConfig, **kwargs) -> None:  # noqa: ARG002
        self._cfg = cfg


def _scheduler(data_dir) -> ext_pull.PullScheduler:
    s = ext_pull.PullScheduler()
    s._running = True
    s._data_dir = data_dir
    return s


@pytest.mark.asyncio
async def test_execute_pull_returns_true_on_success(monkeypatch, tmp_path):
    """拉取成功 → 返回 True (调度循环据此恢复正常间隔)。"""
    calls = {"n": 0}

    async def fake_fetch(fresh, data_dir, **kwargs):  # noqa: ARG001
        calls["n"] += 1
        return 10, "2026-11-23"

    monkeypatch.setattr(ext_pull, "fetch_and_ingest", fake_fetch)
    sched = _scheduler(str(tmp_path))
    cfg = _config()
    ok = await sched._execute_pull(cfg, _Store(cfg))
    assert ok is True
    assert calls["n"] == 1
    assert cfg.pull.last_status == "success"


@pytest.mark.asyncio
async def test_execute_pull_returns_false_on_failure(monkeypatch, tmp_path):
    """拉取失败 (数据源未就绪) → 返回 False (调度循环据此进入退避)。"""
    async def fake_fetch(fresh, data_dir, **kwargs):  # noqa: ARG001
        raise ConnectionError("data source not ready")

    monkeypatch.setattr(ext_pull, "fetch_and_ingest", fake_fetch)
    sched = _scheduler(str(tmp_path))
    cfg = _config()
    ok = await sched._execute_pull(cfg, _Store(cfg))
    assert ok is False
    assert cfg.pull.last_status == "error"
    assert "not ready" in cfg.pull.last_message


@pytest.mark.asyncio
async def test_run_loop_backoff_recovers_when_source_comes_up(monkeypatch, tmp_path):
    """集成: 首拉失败 → 30s 退避 → 数据源起来 → 拉成 → 恢复正常间隔。

    真实 ExtConfigStore (tmp_path): _run_loop 每轮重建 store 读盘, 桩会漏状态。
    假 sleep 秒过退避等待; 第 3 次拉取成功。
    等待方式: 轮询 store 直到 success 落盘 — run_ext_data_io 是真线程池
    往返, 需要真实时间推进, 数事件循环圈数 (sleep(0) 空转) 会在 I/O
    完成前耗尽, 把退避循环掐断在成功之前。
    """
    from app.services.ext_data import ExtConfigStore

    pull_results = ["fail", "fail", "ok"]
    sleeps: list[float] = []

    async def fake_fetch(fresh, data_dir, **kwargs):  # noqa: ARG001
        outcome = pull_results.pop(0) if pull_results else "ok"
        if outcome == "fail":
            raise ConnectionError("not ready")
        return 42, "2026-11-23"

    orig_sleep = asyncio.sleep

    async def fake_sleep(s):
        if s and s > 0:
            sleeps.append(s)
        await orig_sleep(0)  # 真实让出控制权, 被调度的 _run_loop 任务才能推进

    monkeypatch.setattr(ext_pull, "fetch_and_ingest", fake_fetch)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    sched = _scheduler(tmp_path)   # Path: ExtConfigStore 内部做路径拼接
    cfg = _config()
    store = ExtConfigStore(tmp_path)
    store.upsert(cfg)

    task = asyncio.create_task(sched._run_loop(cfg))
    # 真实 50ms 轮询成功态 (store.get 同步直读, 不占线程池), 上限 10s
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0
    ok_seen = False
    while loop.time() < deadline:
        cur = store.get(cfg.id)
        if cur and cur.pull and cur.pull.last_status == "success":
            ok_seen = True
            break
        await orig_sleep(0.05)
    sched._running = False
    await asyncio.wait_for(task, timeout=10)

    assert ok_seen, f"pull_results={pull_results} sleeps={sleeps}"
    fresh = store.get(cfg.id)
    assert fresh is not None and fresh.pull is not None
    assert fresh.pull.last_status == "success", f"sleeps={sleeps}"
    assert fresh.pull.last_rows == 42
    # 退避序列: 首拉失败 → sleep 30 → 失败 → sleep 60 → 成功
    assert 30.0 in sleeps, f"expected 30s backoff in {sleeps}"
