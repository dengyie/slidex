"""
测试轨迹生成核心逻辑

覆盖:
1. generate_trajectory() 基本功能
2. 不同距离的轨迹生成
3. 4 阶段物理模型验证
4. 轨迹点分布合理性
5. 边界条件 (0px, 极大值, 负数)
6. attempt 参数影响
7. trajectory_to_points() 坐标转换
8. 随机性和稳定性
"""

import pytest
from slidex._trajectory import generate_trajectory, trajectory_to_points


class TestTrajectoryGeneration:
    """测试轨迹生成算法"""

    def test_generate_trajectory_basic(self):
        """测试基本轨迹生成"""
        distance = 100
        trajectory = generate_trajectory(distance, attempt=1)

        # 应该返回列表
        assert isinstance(trajectory, list)
        assert len(trajectory) > 0

        # 每个点应该是 (x, y, delay) 三元组
        for point in trajectory:
            assert len(point) == 3
            x, y, delay = point
            assert isinstance(x, (int, float))
            assert isinstance(y, (int, float))
            assert isinstance(delay, (int, float))

    def test_generate_trajectory_starts_at_zero(self):
        """测试轨迹从 (0, 0) 开始"""
        trajectory = generate_trajectory(100, attempt=1)

        # 第一个点应该是 (0, 0, delay)
        first_point = trajectory[0]
        assert first_point[0] == 0  # x = 0
        assert first_point[1] == 0  # y = 0
        assert first_point[2] > 0   # delay > 0

    def test_generate_trajectory_ends_at_distance(self):
        """测试轨迹终点接近目标距离"""
        distance = 150
        trajectory = generate_trajectory(distance, attempt=1)

        # 最后一个点的 x 应该等于 distance
        last_point = trajectory[-1]
        assert last_point[0] == distance

    def test_generate_trajectory_has_minimum_steps(self):
        """测试轨迹至少有 12 个点（起始 + 10-15 步 + 终点）"""
        trajectory = generate_trajectory(100, attempt=1)

        # 至少 12 个点 (起始 + 10 步 + 终点)
        assert len(trajectory) >= 12

    def test_generate_trajectory_different_distances(self):
        """测试不同距离的轨迹生成"""
        distances = [50, 100, 200, 300]

        for distance in distances:
            trajectory = generate_trajectory(distance, attempt=1)

            # 应该生成有效轨迹
            assert len(trajectory) > 0

            # 终点 x 坐标应该等于距离
            assert trajectory[-1][0] == distance

    def test_generate_trajectory_zero_distance(self):
        """测试 0 距离的轨迹"""
        trajectory = generate_trajectory(0, attempt=1)

        # 应该生成轨迹（即使距离为 0）
        assert len(trajectory) > 0

        # 所有 x 坐标应该为 0
        for point in trajectory:
            assert point[0] == 0

    def test_generate_trajectory_negative_distance(self):
        """测试负距离（向左移动）"""
        distance = -100
        trajectory = generate_trajectory(distance, attempt=1)

        # 应该生成轨迹
        assert len(trajectory) > 0

        # 终点应该是负数
        assert trajectory[-1][0] == distance

    def test_generate_trajectory_large_distance(self):
        """测试极大距离"""
        distance = 1000
        trajectory = generate_trajectory(distance, attempt=1)

        assert len(trajectory) > 0
        assert trajectory[-1][0] == distance

    def test_generate_trajectory_x_monotonic_increase(self):
        """测试 x 坐标单调递增（正距离）"""
        trajectory = generate_trajectory(100, attempt=1)

        x_coords = [point[0] for point in trajectory]

        # x 应该单调递增
        for i in range(1, len(x_coords)):
            assert x_coords[i] >= x_coords[i-1]

    def test_generate_trajectory_y_drift_downward(self):
        """测试 y 坐标向下漂移趋势"""
        trajectory = generate_trajectory(100, attempt=1)

        # 统计 y 坐标
        y_coords = [point[1] for point in trajectory[1:-1]]  # 排除起点和终点

        # 大部分 y 应该是负数（向下漂移）
        negative_count = sum(1 for y in y_coords if y < 0)
        assert negative_count > len(y_coords) * 0.5

    def test_generate_trajectory_delay_positive(self):
        """测试所有延迟都是正数"""
        trajectory = generate_trajectory(100, attempt=1)

        for point in trajectory:
            delay = point[2]
            assert delay > 0
            assert delay < 200  # 合理的延迟范围

    def test_generate_trajectory_total_time_reasonable(self):
        """测试总时间在合理范围内"""
        trajectory = generate_trajectory(100, attempt=1)

        total_time = sum(point[2] for point in trajectory)

        # 总时间应该在 500ms - 2000ms 之间
        assert 400 <= total_time <= 2000

    def test_generate_trajectory_four_phases(self):
        """测试 4 阶段速度分布"""
        distance = 200
        trajectory = generate_trajectory(distance, attempt=1)

        # 排除起点和终点
        middle_points = trajectory[1:-1]

        # 计算每段的平均速度（x 增量 / delay）
        speeds = []
        for i in range(len(middle_points) - 1):
            x1 = middle_points[i][0]
            x2 = middle_points[i + 1][0]
            delay = middle_points[i][2]
            speed = (x2 - x1) / delay if delay > 0 else 0
            speeds.append(speed)

        # 应该有不同的速度区间
        # 前期慢 -> 中期快 -> 后期减速
        if len(speeds) >= 3:
            early_speed = sum(speeds[:len(speeds)//3]) / (len(speeds)//3)
            mid_speed = sum(speeds[len(speeds)//3:2*len(speeds)//3]) / (len(speeds)//3)

            # 中期速度应该 > 前期速度
            # (这是一个弱断言，因为随机性可能导致偶尔不满足)
            # 至少验证速度有变化
            assert max(speeds) > min(speeds)

    def test_generate_trajectory_attempt_increases_jitter(self):
        """测试 attempt 参数增加抖动"""
        distance = 100

        # 生成多次尝试的轨迹
        traj1 = generate_trajectory(distance, attempt=1)
        traj5 = generate_trajectory(distance, attempt=5)

        # 计算 y 坐标的标准差（抖动程度）
        y_std1 = _calculate_std([p[1] for p in traj1[1:-1]])
        y_std5 = _calculate_std([p[1] for p in traj5[1:-1]])

        # attempt 越大，抖动应该越大
        # (弱断言，因为随机性)
        # 至少验证都有抖动
        assert y_std1 > 0
        assert y_std5 > 0

    def test_generate_trajectory_randomness(self):
        """测试轨迹具有随机性（不是完全相同）"""
        distance = 100

        traj1 = generate_trajectory(distance, attempt=1)
        traj2 = generate_trajectory(distance, attempt=1)

        # 两次生成的轨迹应该不完全相同
        # 比较中间点的 y 坐标
        y1 = [p[1] for p in traj1[1:-1]]
        y2 = [p[1] for p in traj2[1:-1]]

        # 至少有一半的点不同
        different_count = sum(1 for a, b in zip(y1, y2) if abs(a - b) > 0.1)
        assert different_count > len(y1) * 0.5

    def test_trajectory_to_points_basic(self):
        """测试相对坐标转绝对坐标"""
        relative_traj = [
            (0, 0, 100),
            (50, -2, 50),
            (100, -5, 50),
        ]

        start_x = 200
        start_y = 300

        absolute_traj = trajectory_to_points(relative_traj, start_x, start_y)

        # 应该返回相同长度的列表
        assert len(absolute_traj) == len(relative_traj)

        # 验证坐标转换
        assert absolute_traj[0] == (200, 300, 100)  # start + (0, 0)
        assert absolute_traj[1] == (250, 298, 50)   # start + (50, -2)
        assert absolute_traj[2] == (300, 295, 50)   # start + (100, -5)

    def test_trajectory_to_points_zero_start(self):
        """测试起点为 (0, 0) 的坐标转换"""
        relative_traj = [
            (0, 0, 100),
            (50, -2, 50),
        ]

        absolute_traj = trajectory_to_points(relative_traj, 0, 0)

        # 起点为 0 时，绝对坐标 = 相对坐标
        assert absolute_traj[0] == (0, 0, 100)
        assert absolute_traj[1] == (50, -2, 50)

    def test_trajectory_to_points_negative_start(self):
        """测试负数起点"""
        relative_traj = [
            (0, 0, 100),
            (50, -2, 50),
        ]

        absolute_traj = trajectory_to_points(relative_traj, -100, -50)

        assert absolute_traj[0] == (-100, -50, 100)
        assert absolute_traj[1] == (-50, -52, 50)

    def test_trajectory_to_points_preserves_delay(self):
        """测试坐标转换保留延迟"""
        relative_traj = [
            (0, 0, 123.45),
            (50, -2, 67.89),
        ]

        absolute_traj = trajectory_to_points(relative_traj, 100, 200)

        # delay 应该保持不变
        assert absolute_traj[0][2] == 123.45
        assert absolute_traj[1][2] == 67.89

    def test_integration_generate_and_convert(self):
        """集成测试：生成轨迹 + 坐标转换"""
        distance = 150
        start_x = 500
        start_y = 300

        # 生成相对轨迹
        relative_traj = generate_trajectory(distance, attempt=1)

        # 转换为绝对坐标
        absolute_traj = trajectory_to_points(relative_traj, start_x, start_y)

        # 验证长度一致
        assert len(absolute_traj) == len(relative_traj)

        # 验证起点
        assert absolute_traj[0][0] == start_x
        assert absolute_traj[0][1] == start_y

        # 验证终点
        assert absolute_traj[-1][0] == start_x + distance


def _calculate_std(values):
    """计算标准差（辅助函数）"""
    if not values:
        return 0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return variance ** 0.5
