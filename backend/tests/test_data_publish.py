"""data_publish (行情 git 自动发布) 测试。

覆盖三个关键安全/幂等属性:
1. 无变更 → 不 commit 不 push (幂等, 手动/cron 重复触发无副作用);
2. add 只走白名单目录 → user_data/secrets 永不入库;
3. 远端超前 (非快进) → pull --rebase 后重推成功。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services import data_publish


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """local repo (含 data/) + bare remote, 首次推送建立 main。"""
    local = tmp_path / "local"
    remote = tmp_path / "remote.git"
    local.mkdir()
    _run(local, "init", "-b", "main")
    _run(local, "config", "user.email", "t@t")
    _run(local, "config", "user.name", "t")
    _run(local, "config", "commit.gpgsign", "false")
    _run(local, "remote", "add", "origin", str(remote))
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, timeout=60)
    # bare 默认 HEAD 可能指向 master, 统一指到 main 否则 clone 拿到空库
    subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
                   capture_output=True, timeout=60)
    (local / "data" / "kline_daily" / "date=2026-01-05").mkdir(parents=True)
    (local / "data" / "kline_daily" / "date=2026-01-05" / "part.parquet").write_bytes(b"prev")
    (local / "data" / "user_data").mkdir()
    (local / "data" / "user_data" / "secrets.json").write_text("{}")
    (local / ".gitignore").write_text(
        "data/**\n!data/.gitkeep\n!data/kline_daily/\n!data/kline_daily/**\n")
    _run(local, "add", "-A")
    _run(local, "commit", "-m", "init")
    _run(local, "push", "origin", "main")
    return local, remote


def test_no_change_is_idempotent(tmp_path, monkeypatch):
    """无变更 → pushed=False, 不产生新 commit。"""
    local, _ = _init_repo_with_remote(tmp_path)
    before = _run(local, "rev-parse", "HEAD").stdout.strip()

    result = data_publish.publish_increment(local / "data")

    after = _run(local, "rev-parse", "HEAD").stdout.strip()
    assert result["ok"] is True
    assert result["pushed"] is False
    assert before == after  # 未产生任何提交


def test_increment_pushes_and_never_touches_user_data(tmp_path, monkeypatch):
    """新增日K分区 → commit+push; user_data/secrets 不入库。"""
    local, remote = _init_repo_with_remote(tmp_path)
    # 白名单外目录即使 gitignore 放行也不会被 add (双保险)
    (local / "data" / "user_data" / "secrets.json").write_text('{"ai_api_key": "sk-x"}')

    new_part = local / "data" / "kline_daily" / "date=2026-01-06" / "part.parquet"
    new_part.parent.mkdir(parents=True)
    new_part.write_bytes(b"new-day")

    # _push_with_fallback 直连即可 (本地 bare 仓库无网络)
    result = data_publish.publish_increment(local / "data")

    assert result["ok"] is True, result
    assert result["pushed"] is True, result
    # 远端 clone 验证: 新分区在, user_data 不在
    clone = tmp_path / "verify"
    subprocess.run(["git", "clone", str(remote), str(clone)], capture_output=True, timeout=60)
    assert (clone / "data" / "kline_daily" / "date=2026-01-06" / "part.parquet").exists()
    assert not (clone / "data" / "user_data").exists()


def test_remote_ahead_rebases_then_pushes(tmp_path):
    """远端有别的机器先推的提交 → 本机 push 被拒 → rebase 后重推成功。"""
    local, remote = _init_repo_with_remote(tmp_path)

    # 另一台"机器"(第二个 clone) 先推了一个新分区
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], capture_output=True, timeout=60)
    _run(other, "config", "user.email", "o@o")
    _run(other, "config", "user.name", "o")
    (other / "data" / "kline_daily" / "date=2026-01-06").mkdir(parents=True)
    (other / "data" / "kline_daily" / "date=2026-01-06" / "part.parquet").write_bytes(b"other-machine")
    _run(other, "add", "-A")
    _run(other, "commit", "-m", "other machine data")
    _run(other, "push", "origin", "main")

    # 本机产生自己的增量 (与 other 不同分区 → rebase 无冲突)
    mine = local / "data" / "kline_daily" / "date=2026-01-07" / "part.parquet"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"my-machine")

    result = data_publish.publish_increment(local / "data")

    assert result["ok"] is True, result
    assert result["pushed"] is True, result
    assert "rebase" in result["detail"], result
    # 远端同时有两台机器的分区
    clone = tmp_path / "verify2"
    subprocess.run(["git", "clone", str(remote), str(clone)], capture_output=True, timeout=60)
    assert (clone / "data" / "kline_daily" / "date=2026-01-06" / "part.parquet").exists()
    assert (clone / "data" / "kline_daily" / "date=2026-01-07" / "part.parquet").exists()


def test_no_git_repo_skips_safely(tmp_path):
    """数据目录不在 git 仓库 → ok=False 但不抛异常 (独立部署场景)。"""
    data_dir = tmp_path / "standalone" / "data"
    data_dir.mkdir(parents=True)
    result = data_publish.publish_increment(data_dir)
    assert result["ok"] is False
    assert "git" in result["detail"]


def test_staged_foreign_changes_not_swept_into_data_commit(tmp_path):
    """开发者手动 add 的代码改动 (暂存区异物) 不会被数据发布 commit 捎带。"""
    local, remote = _init_repo_with_remote(tmp_path)
    # 开发者半成品: 手动 add 的代码改动躺在暂存区
    (local / "src_wip.py").write_text("print('half done')\n")
    _run(local, "add", "src_wip.py")

    # 新数据分区到达
    p = local / "data" / "kline_daily" / "date=2026-01-06" / "part.parquet"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"new-day")

    result = data_publish.publish_increment(local / "data")

    assert result["ok"] is True, result
    assert result["pushed"] is True, result
    # 数据 commit 只包含白名单路径; src_wip.py 留在暂存区原封不动
    show = _run(local, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    assert "src_wip.py" not in show, "数据 commit 不应捎带暂存区的代码改动"
    assert "data/kline_daily/date=2026-01-06/part.parquet" in show
    staged = _run(local, "diff", "--cached", "--name-only").stdout.strip()
    assert "src_wip.py" in staged, "src_wip.py 应仍在暂存区等待开发者自己提交"
    # 远端也不含捎带文件
    clone = tmp_path / "verify3"
    subprocess.run(["git", "clone", str(remote), str(clone)], capture_output=True, timeout=60)
    assert not (clone / "src_wip.py").exists()
