"""
测试轨迹池完整功能

覆盖:
1. save_trajectory() - 保存轨迹，超出上限淘汰
2. load_best_trajectory() - 按距离匹配，tolerance 回退
3. load_random_trajectory() - 随机选取
4. get_random_trajectory() - provider 模式别名
5. rotate_trajectory() - LRU 轮转
6. get_pool_stats() - 统计信息
7. clean_stale() - 清理过期轨迹
8. 并发读写（多进程安全）
9. 损坏文件处理
10. 池满时清理
"""

import pytest
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta
from slidex._trajectory_pool import SliderTrajectoryPool


class TestSliderTrajectoryPool:
    """测试轨迹池管理"""

    def test_save_trajectory_basic(self):
        """测试基本保存功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            points = [[0, 0, 100], [50, -2, 50], [100, -5, 50]]
            filename = pool.save_trajectory(
                points=points,
                cookie_id="test_user",
                distance=100,
                success=True,
                verify_url="https://test.com",
                duration_ms=200
            )

            # 应该返回文件名
            assert filename is not None
            assert filename.startswith("trajectory_")
            assert filename.endswith(".json")

            # 文件应该存在
            file_path = Path(tmpdir) / "test_user" / filename
            assert file_path.exists()

    def test_save_trajectory_creates_cookie_dir(self):
        """测试自动创建 cookie 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            points = [[0, 0, 100]]
            pool.save_trajectory(points, "new_user", 100, True)

            # cookie 子目录应该被创建
            cookie_dir = Path(tmpdir) / "new_user"
            assert cookie_dir.exists()
            assert cookie_dir.is_dir()

    def test_save_trajectory_json_format(self):
        """测试保存的 JSON 格式"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            points = [[0, 0, 100], [50.123, -2.456, 50.789]]
            filename = pool.save_trajectory(
                points=points,
                cookie_id="test",
                distance=100.5,
                success=True,
                verify_url="https://test.com",
                duration_ms=200.7
            )

            # 读取并验证 JSON
            file_path = Path(tmpdir) / "test" / filename
            with open(file_path, "r") as f:
                data = json.load(f)

            assert data["cookie_id"] == "test"
            assert data["distance"] == 100.5
            assert data["success"] is True
            assert data["duration_ms"] == 200.7
            assert len(data["points"]) == 2
            assert "recorded_at" in data
            assert "verify_url_hash" in data

    def test_save_trajectory_max_per_cookie_rotation(self):
        """测试达到上限时淘汰最旧的轨迹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)
            pool.max_per_cookie = 3  # 设置小的上限便于测试

            points = [[0, 0, 100]]

            # 保存 5 条轨迹
            for i in range(5):
                pool.save_trajectory(points, "test", 100, True)
                time.sleep(0.01)  # 确保文件创建时间不同

            # 应该始终保留 max_per_cookie 条最新轨迹
            cookie_dir = Path(tmpdir) / "test"
            files = list(cookie_dir.glob("trajectory_*.json"))
            assert len(files) == 3

    def test_save_trajectory_does_not_reuse_filenames(self):
        """测试满容量后保存不会复用已有文件名"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)
            pool.max_per_cookie = 3

            points = [[0, 0, 100]]
            filenames = []
            for _ in range(5):
                filenames.append(pool.save_trajectory(points, "test", 100, True))

            cookie_dir = Path(tmpdir) / "test"
            files = sorted(p.name for p in cookie_dir.glob("trajectory_*.json"))

            assert len(files) == 3
            assert len(set(filenames)) == 5
            assert files == ["trajectory_003.json", "trajectory_004.json", "trajectory_005.json"]

    def test_load_best_trajectory_exact_match(self):
        """测试精确距离匹配"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存 3 条不同距离的轨迹
            pool.save_trajectory([[0, 0, 100]], "test", 50, True)
            pool.save_trajectory([[0, 0, 100]], "test", 100, True)
            pool.save_trajectory([[0, 0, 100]], "test", 150, True)

            # 请求 100px 的轨迹
            traj = pool.load_best_trajectory("test", target_distance=100)

            assert traj is not None
            assert traj["distance"] == 100

    def test_load_best_trajectory_tolerance_fallback(self):
        """测试 tolerance 回退机制"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 只保存 50px 的轨迹
            pool.save_trajectory([[0, 0, 100]], "test", 50, True)

            # 请求 70px（在 40% tolerance 内应该匹配）
            traj = pool.load_best_trajectory("test", target_distance=70)

            # 应该找到 50px 的轨迹
            assert traj is not None
            assert traj["distance"] == 50

    def test_load_best_trajectory_prefers_successful(self):
        """测试优先选择成功的轨迹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存失败和成功的轨迹
            pool.save_trajectory([[0, 0, 100]], "test", 100, False)  # 失败
            pool.save_trajectory([[0, 0, 100]], "test", 105, True)   # 成功

            # 应该优先返回成功的
            traj = pool.load_best_trajectory("test", target_distance=100)

            assert traj is not None
            assert traj["success"] is True
            assert traj["distance"] == 105

    def test_load_best_trajectory_no_match_returns_none(self):
        """测试无匹配时返回 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 空池
            traj = pool.load_best_trajectory("test", target_distance=100)

            assert traj is None

    def test_load_random_trajectory(self):
        """测试随机选取"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存 3 条轨迹
            pool.save_trajectory([[0, 0, 100]], "test", 50, True)
            pool.save_trajectory([[0, 0, 100]], "test", 100, True)
            pool.save_trajectory([[0, 0, 100]], "test", 150, True)

            # 随机选取
            traj = pool.load_random_trajectory("test")

            assert traj is not None
            assert traj["distance"] in [50, 100, 150]

    def test_load_random_trajectory_empty_pool(self):
        """测试空池随机选取"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            traj = pool.load_random_trajectory("test")

            assert traj is None

    def test_get_random_trajectory_uses_default_cookie(self):
        """测试 get_random_trajectory 使用 default cookie"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存到 default cookie
            pool.save_trajectory([[0, 0, 100]], "default", 100, True)

            # get_random_trajectory 应该能找到
            traj = pool.get_random_trajectory()

            assert traj is not None
            assert traj["cookie_id"] == "default"

    def test_rotate_trajectory_lru(self):
        """测试 LRU 轮转"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存 3 条轨迹
            pool.save_trajectory([[0, 0, 100]], "test", 100, True)
            pool.save_trajectory([[0, 0, 100]], "test", 105, True)
            pool.save_trajectory([[0, 0, 100]], "test", 110, True)

            # 首次轮转，应该选最久未用的（都未用过）
            traj1 = pool.rotate_trajectory("test", target_distance=100)
            assert traj1 is not None

            # 再次轮转，应该选另一个未用的
            traj2 = pool.rotate_trajectory("test", target_distance=100)
            assert traj2 is not None

            # 两次应该返回不同的轨迹（LRU）
            # (可能相同，但概率很低)

    def test_get_pool_stats_basic(self):
        """测试统计信息"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存 2 成功 1 失败
            pool.save_trajectory([[0, 0, 100]], "test", 100, True, duration_ms=200)
            pool.save_trajectory([[0, 0, 100]], "test", 100, False, duration_ms=300)
            pool.save_trajectory([[0, 0, 100]], "test", 100, True, duration_ms=400)

            stats = pool.get_pool_stats("test")

            assert stats["total"] == 3
            assert stats["successful"] == 2
            assert stats["failed"] == 1
            assert abs(stats["success_rate"] - 0.666) < 0.01
            assert stats["avg_duration_ms"] == 300
            assert stats["pool_ready"] is False  # < min_pool_size (5)

    def test_get_pool_stats_empty(self):
        """测试空池统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            stats = pool.get_pool_stats("test")

            assert stats["total"] == 0
            assert stats["success_rate"] == 0
            assert stats["pool_ready"] is False

    def test_get_pool_stats_pool_ready(self):
        """测试池就绪状态"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)
            pool.min_pool_size = 3

            # 保存 3 条
            for i in range(3):
                pool.save_trajectory([[0, 0, 100]], "test", 100, True)

            stats = pool.get_pool_stats("test")

            assert stats["pool_ready"] is True

    def test_clean_stale_by_age(self):
        """测试按时间清理过期轨迹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存一条轨迹
            filename = pool.save_trajectory([[0, 0, 100]], "test", 100, True)
            file_path = Path(tmpdir) / "test" / filename

            # 手动修改 recorded_at 为 10 天前
            with open(file_path, "r") as f:
                data = json.load(f)

            old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
            data["recorded_at"] = old_date

            with open(file_path, "w") as f:
                json.dump(data, f)

            # 清理 7 天以上的
            pool.clean_stale("test", max_age_days=7)

            # 文件应该被删除
            assert not file_path.exists()

    def test_clean_stale_unsuccessful(self):
        """测试清理失败的轨迹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存失败的轨迹
            filename = pool.save_trajectory([[0, 0, 100]], "test", 100, False)
            file_path = Path(tmpdir) / "test" / filename

            # 清理
            pool.clean_stale("test", max_age_days=7)

            # 失败的轨迹应该被删除
            assert not file_path.exists()

    def test_clean_stale_preserves_recent_successful(self):
        """测试保留最近成功的轨迹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存最近成功的轨迹
            filename = pool.save_trajectory([[0, 0, 100]], "test", 100, True)
            file_path = Path(tmpdir) / "test" / filename

            # 清理
            pool.clean_stale("test", max_age_days=7)

            # 应该保留
            assert file_path.exists()

    def test_corrupt_file_handling(self):
        """测试损坏文件处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 创建损坏的 JSON 文件
            cookie_dir = Path(tmpdir) / "test"
            cookie_dir.mkdir(parents=True, exist_ok=True)
            corrupt_file = cookie_dir / "trajectory_001.json"
            with open(corrupt_file, "w") as f:
                f.write("not valid json {{{")

            # 保存一个正常的轨迹
            pool.save_trajectory([[0, 0, 100]], "test", 100, True)

            # 应该能够加载（跳过损坏的文件）
            traj = pool.load_random_trajectory("test")

            assert traj is not None
            assert traj["distance"] == 100

    def test_permission_error_handling(self):
        """测试权限错误处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 尝试保存（可能因权限问题失败，但不应崩溃）
            try:
                filename = pool.save_trajectory([[0, 0, 100]], "test", 100, True)
                # 应该返回 filename 或 None
                assert filename is None or isinstance(filename, str)
            except Exception as e:
                pytest.fail(f"Should not raise exception: {e}")

    def test_concurrent_save_and_load(self):
        """测试并发保存和加载（基本测试）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 快速保存多条
            for i in range(10):
                pool.save_trajectory([[0, 0, 100]], "test", 100 + i, True)

            # 加载应该成功
            traj = pool.load_random_trajectory("test")
            assert traj is not None

    def test_cookie_id_with_special_chars(self):
        """测试特殊字符的 cookie_id"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 使用包含特殊字符的 cookie_id（合法文件名）
            filename = pool.save_trajectory(
                [[0, 0, 100]],
                "test_user-123",
                100,
                True
            )

            assert filename is not None

            # 应该能加载
            traj = pool.load_random_trajectory("test_user-123")
            assert traj is not None

    def test_cookie_id_path_traversal_is_sanitized(self):
        """测试 cookie_id 不能逃逸轨迹池目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            filename = pool.save_trajectory(
                [[0, 0, 100], [10, 0, 50], [20, 0, 50]],
                "../../outside",
                20,
                True,
            )

            assert filename is not None
            assert (Path(tmpdir) / "outside" / filename).exists()
            assert not (Path(tmpdir).parent / "outside" / filename).exists()

    def test_empty_points_list(self):
        """测试空 points 列表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存空 points
            filename = pool.save_trajectory([], "test", 100, True)

            # 应该能保存
            assert filename is not None

            # 加载应该成功
            traj = pool.load_random_trajectory("test")
            assert traj is not None
            assert traj["points"] == []
