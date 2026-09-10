"""Stealth 稳定目录回归：浏览器 profile / 失败快照目录不得随 CWD 漂移。

对应 0.5.6：browser_data 默认不再写 CWD（历史 CWD 登录态目录沿用不丢
登录），失败现场快照从 CWD 相对 logs/slider_debug 迁到
SlidexConfig.get_debug_screenshot_dir()。
"""

import os
import uuid

import pytest

from slidex._concurrency import concurrency_manager
from slidex.config import SlidexConfig
from slidex.stealth import XianyuSliderStealth


@pytest.fixture
def stealth():
    """构造 XianyuSliderStealth（其 __init__ 会占用并发槽位），测试结束释放。

    每个实例用唯一 user_id，避免同账号排队等待槽位（60s 超时）。
    """
    built = []

    def _build(user_id=None, cfg=None):
        s = XianyuSliderStealth(
            user_id=user_id or f"u{uuid.uuid4().hex[:8]}",
            slidex_config=cfg if cfg is not None else SlidexConfig(),
        )
        # 确保未显式指定持久目录，走默认解析路径
        s.account_persistent_profile_dir = ""
        built.append(s)
        return s

    yield _build

    for s in built:
        try:
            concurrency_manager.unregister_instance(s.user_id, s)
        except Exception:
            pass


class TestAccountProfileDir:

    def test_explicit_dir_wins_and_created(self, tmp_path, stealth):
        s = stealth()
        explicit = tmp_path / "explicit_profile"
        s.account_persistent_profile_dir = str(explicit)
        assert s._resolve_account_profile_dir() == str(explicit)
        assert explicit.is_dir()

    def test_explicit_config_dir_used(self, tmp_path, monkeypatch, stealth):
        cfg = SlidexConfig(browser_data_dir=str(tmp_path / "stable"))
        s = stealth(cfg=cfg)
        monkeypatch.chdir(tmp_path)  # CWD 无历史目录
        d = s._resolve_account_profile_dir()
        assert d == os.path.join(str(tmp_path / "stable"), f"user_{s.pure_user_id}")
        assert os.path.isdir(d)

    def test_explicit_config_beats_legacy_dir(self, tmp_path, monkeypatch, stealth):
        """显式配置（SLIDEX_BROWSER_DATA_DIR / browser_data_dir）优先于历史沿用：
        即使 CWD 下存在旧登录态目录，也按显式目录走。"""
        cfg = SlidexConfig(browser_data_dir=str(tmp_path / "newbase"))
        s = stealth(cfg=cfg)
        legacy = tmp_path / "browser_data" / f"user_{s.pure_user_id}"
        legacy.mkdir(parents=True)  # 模拟升级前遗留目录
        monkeypatch.chdir(tmp_path)
        d = s._resolve_account_profile_dir()
        assert d == os.path.join(str(tmp_path / "newbase"), f"user_{s.pure_user_id}")
        assert os.path.isdir(d)

    def test_honors_legacy_cwd_profile_dir(self, tmp_path, monkeypatch, stealth):
        """无显式配置时，账号在 CWD browser_data/ 已有登录态目录 → 沿用不搬，
        防静默掉登录。"""
        cfg = SlidexConfig()
        s = stealth(cfg=cfg, user_id="legacy_acct")
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))
        legacy = tmp_path / "browser_data" / f"user_{s.pure_user_id}"
        legacy.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        d = s._resolve_account_profile_dir()
        assert d == str(legacy)
        # 稳定目录不落盘：登录态继续留在历史位置
        assert not (tmp_path / ".slidex").exists()

    def test_default_no_legacy_uses_stable_home_dir(self, tmp_path, monkeypatch, stealth):
        """无显式配置、无历史目录 → 稳定目录 ~/.slidex/browser_data。"""
        cfg = SlidexConfig()
        s = stealth(cfg=cfg)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))
        monkeypatch.chdir(tmp_path)
        d = s._resolve_account_profile_dir()
        assert d == os.path.join(
            str(tmp_path), ".slidex", "browser_data", f"user_{s.pure_user_id}"
        )
        assert os.path.isdir(d)

    def test_fallback_without_config(self, tmp_path, monkeypatch, stealth):
        s = stealth(cfg=SlidexConfig())
        s._slidex_config = None
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))
        d = s._resolve_account_profile_dir()
        assert d == os.path.join(
            str(tmp_path), ".slidex", "browser_data", f"user_{s.pure_user_id}"
        )
        assert os.path.isdir(d)


class TestDebugSnapshotDir:

    def test_uses_stable_config_dir(self, tmp_path, stealth):
        cfg = SlidexConfig(debug_screenshot_dir=str(tmp_path / "shots"))
        s = stealth(cfg=cfg)
        assert s._debug_snapshot_dir() == str(tmp_path / "shots")

    def test_fallback_without_config(self, tmp_path, monkeypatch, stealth):
        s = stealth(cfg=SlidexConfig())
        s._slidex_config = None
        monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))
        assert s._debug_snapshot_dir() == os.path.join(
            str(tmp_path), ".slidex", "debug_screenshots"
        )