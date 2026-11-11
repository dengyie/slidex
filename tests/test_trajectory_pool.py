"""
测试轨迹池完整功能

覆盖:
1. save_trajectory() - 保存轨迹，幂等去重，超出上限淘汰
2. get_pool_stats() - 统计信息
3. clean_stale() - 清理过期轨迹
4. 并发读写（多进程安全）
5. 损坏文件处理
6. cookie_id 净化 / 边界
加载语义（peek / LRU / 淘汰对账）见 tests/test_trajectory_pool_lru.py 与
tests/test_trajectory_pool_peek.py ——生产唯一加载入口是 load_unused_human
（load_best/load_random/get_random/rotate 四个死方法已随 0.11.8 删除）。
"""

import pytest
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta
from slidex._trajectory_pool import SliderTrajectoryPool


def _human_points(distance, frames=1, steps=14, dy=0.0, t0=100.0, gap=50.0):
    """Healthy drag point list: `frames` linear legs of `steps` small steps each.

    These pool tests cover bookkeeping (numbering, eviction, LRU, file format),
    not geometry -- but since 0.11.1 a recording whose coordinate axis is broken
    is refused at write time, so a one-shot [[0, 0, 100], [50, ...], [100, ...]]
    fixture could no longer exercise any of it. Step size stays at
    distance / (frames*steps) (far below the 35% jump line) and the last point is
    exactly ``distance``: the shape a real recording has.
    """
    pts = []
    for f in range(frames):
        start = distance * f / frames
        end = distance * (f + 1) / frames
        for s in range(1, steps + 1):
            pts.append([start + (end - start) * s / steps, dy, t0 + gap * len(pts)])
    return pts


class TestSliderTrajectoryPool:
    """测试轨迹池管理"""

    def test_save_trajectory_basic(self):
        """测试基本保存功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            points = _human_points(100, frames=2, steps=7)
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

    def test_save_trajectory_same_payload_is_idempotent(self):
        """同一份 payload 二次落盘必须复用首条文件，不再新增。

        线上 2026-10-12..14 实证：50 个文件里 20 对完全相同（同秒双写），
        池位被挤占、该几何被抽中的概率翻倍。去重后文件名不变、文件数不变。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)
            points = _human_points(100, frames=2, steps=7)

            first = pool.save_trajectory(points, "dup_user", 100, True, duration_ms=200)
            second = pool.save_trajectory(points, "dup_user", 100, True, duration_ms=200)

            assert first == second
            files = list((Path(tmpdir) / "dup_user").glob("trajectory_*.json"))
            assert len(files) == 1

    def test_save_trajectory_different_payload_still_written(self):
        """不同距离（几何不同）不能被去重吞掉。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            a = pool.save_trajectory(_human_points(100, frames=2, steps=7), "u", 100, True)
            b = pool.save_trajectory(_human_points(120, frames=2, steps=7), "u", 120, True)

            assert a != b
            assert len(list((Path(tmpdir) / "u").glob("trajectory_*.json"))) == 2

    def test_dedupe_ignores_dirty_existing_recording(self):
        """存量同 payload 文件若加载侧判脏，不能顶替新录制（否则新录制被
        "去重"成永远加载不出来的废条目）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)
            points = _human_points(100, frames=2, steps=7)
            first = pool.save_trajectory(points, "dirty_user", 100, True)

            # 手动把已存文件改成 load 侧判脏的形状（非单调折返），但保留相同 payload 数值
            path = Path(tmpdir) / "dirty_user" / first
            data = json.loads(path.read_text(encoding="utf-8"))
            data["points"] = [[0.0, 0.0, 0.0], [50.0, 0.0, 100.0], [10.0, 0.0, 300.0]]
            path.write_text(json.dumps(data), encoding="utf-8")

            restored = pool.save_trajectory(points, "dirty_user", 100, True)
            assert restored != first
            assert (Path(tmpdir) / "dirty_user" / restored).exists()

    def test_save_trajectory_creates_cookie_dir(self):
        """测试自动创建 cookie 子目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            points = _human_points(100)
            pool.save_trajectory(points, "new_user", 100, True)

            # cookie 子目录应该被创建
            cookie_dir = Path(tmpdir) / "new_user"
            assert cookie_dir.exists()
            assert cookie_dir.is_dir()

    def test_save_trajectory_json_format(self):
        """测试保存的 JSON 格式"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 两行点列（首行按下、末行=distance）也要能原样落盘：断言的是 JSON 格式本身
            points = [[80.4, -2.456, 100.0], [100.5, -5.0, 50.0]]
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

            # 5 条**几何不同**的录制：同 payload 会被幂等去重合并（见
            # test_save_trajectory_same_payload_is_idempotent），而这里要测的是
            # 淘汰序本身。
            for i in range(5):
                pool.save_trajectory(_human_points(100 + i), "test", 100 + i, True,
                                     source="human_cdp")
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

            points = _human_points(100)
            filenames = []
            for i in range(5):
                # 每次几何不同（同 payload 会被去重，见上一条测试）
                filenames.append(pool.save_trajectory(_human_points(100 + i), "test", 100 + i, True,
                                     source="human_cdp"))

            cookie_dir = Path(tmpdir) / "test"
            files = sorted(p.name for p in cookie_dir.glob("trajectory_*.json"))

            assert len(files) == 3
            assert len(set(filenames)) == 5
            assert files == ["trajectory_003.json", "trajectory_004.json", "trajectory_005.json"]

    def test_save_trajectory_rotation_at_four_digit_indices(self):
        """编号进 4 位后淘汰仍按数值序：不能删掉最新的 1000、也不能复用它的名字。

        线上实证（1926782908，2026-11-10）：字典序把 trajectory_1000 排在
        trajectory_951 前面，池满 50 条时"淘汰最旧"删的是最新录制，新录制再
        复用 1000 这个名字把它覆盖——一条 48s 真人轨迹就这么静默消失了。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)
            pool.max_per_cookie = 3
            cdir = Path(tmpdir) / "test"
            cdir.mkdir(parents=True, exist_ok=True)
            for idx in (998, 999, 1000):
                (cdir / f"trajectory_{idx}.json").write_text(
                    json.dumps({"distance": 100, "success": True, "points": _human_points(100)}),
                    encoding="utf-8",
                )

            name = pool.save_trajectory(_human_points(100), "test", 100, True)

            files = {p.name for p in cdir.glob("trajectory_*.json")}
            assert "trajectory_998.json" not in files      # 最旧编号被淘汰
            assert {"trajectory_999.json", "trajectory_1000.json"} <= files
            assert name == "trajectory_1001.json"          # 不复用 1000

            # 两次应该返回不同的轨迹（LRU）
            # (可能相同，但概率很低)

    def test_get_pool_stats_basic(self):
        """测试统计信息"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存 2 成功 1 失败（距离各不相同：同 payload 会被幂等去重）
            pool.save_trajectory(_human_points(100), "test", 100, True, duration_ms=200)
            pool.save_trajectory(_human_points(104), "test", 104, False, duration_ms=300)
            pool.save_trajectory(_human_points(108), "test", 108, True, duration_ms=400)

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

            # 保存 3 条（距离各不相同：同 payload 会被幂等去重）
            for i in range(3):
                pool.save_trajectory(_human_points(100 + i), "test", 100 + i, True,
                                     source="human_cdp")

            stats = pool.get_pool_stats("test")

            assert stats["pool_ready"] is True

    def test_clean_stale_by_age(self):
        """测试按时间清理过期轨迹"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 保存一条轨迹
            filename = pool.save_trajectory(_human_points(100), "test", 100, True)
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
            filename = pool.save_trajectory(_human_points(100), "test", 100, False)
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
            filename = pool.save_trajectory(_human_points(100), "test", 100, True)
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
            pool.save_trajectory(_human_points(100), "test", 100, True,
                                 source="human_cdp")

            # 应该能够加载（跳过损坏的文件）
            traj = pool.load_unused_human("test", 100.0)

            assert traj is not None
            assert traj["distance"] == 100

    def test_permission_error_handling(self):
        """测试权限错误处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 尝试保存（可能因权限问题失败，但不应崩溃）
            try:
                filename = pool.save_trajectory(_human_points(100), "test", 100, True)
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
                pool.save_trajectory(_human_points(100 + i), "test", 100 + i, True,
                                     source="human_cdp")

            # 加载应该成功
            traj = pool.load_unused_human("test", 100.0)
            assert traj is not None

    def test_cookie_id_with_special_chars(self):
        """测试特殊字符的 cookie_id"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 使用包含特殊字符的 cookie_id（合法文件名）
            filename = pool.save_trajectory(
                _human_points(100),
                "test_user-123",
                100,
                True,
                source="human_cdp",
            )

            assert filename is not None

            # 应该能加载
            traj = pool.load_unused_human("test_user-123", 100.0)
            assert traj is not None

    def test_cookie_id_path_traversal_is_sanitized(self):
        """测试 cookie_id 不能逃逸轨迹池目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            filename = pool.save_trajectory(
                _human_points(20, frames=1, steps=10),
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

            # 保存空 points：不写文件（0.11.0 几何校验一并拒收，no_points）
            filename = pool.save_trajectory([], "test", 100, True)

            assert filename is None

            # 池里不应出现空录制
            traj = pool.load_unused_human("test", 100.0)
            assert traj is None
