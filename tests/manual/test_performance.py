"""
性能回归测试 - 对比 v0.3.0 和 v0.3.1

测试指标:
    - 轨迹生成耗时
    - 图像匹配耗时
    - 内存占用
    - Provider 检测开销

运行方式:
    PYTHONPATH=. python3 tests/manual/test_performance.py
"""

import asyncio
import time
import tracemalloc
from typing import List, Tuple
from unittest.mock import AsyncMock

from slidex import SliderSolver
from slidex._trajectory import generate_trajectory
from slidex._image_match import SliderImageMatcher
from slidex.providers import ProviderRegistry


def benchmark_trajectory_generation(iterations: int = 100) -> Tuple[float, List[float]]:
    """基准测试: 轨迹生成"""
    times = []
    for i in range(iterations):
        start = time.perf_counter()
        generate_trajectory(distance=100 + i % 50, attempt=1)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)
    return sum(times) / len(times), times


def benchmark_image_matching(iterations: int = 10) -> Tuple[float, List[float]]:
    """基准测试: 图像匹配"""
    # 创建假图像 (1x1 透明 PNG)
    fake_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        try:
            SliderImageMatcher.find_gap_position(fake_png, fake_png)
        except:
            pass  # 可能失败但我们只关心性能
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)
    return sum(times) / len(times), times


async def benchmark_provider_detection(iterations: int = 50) -> Tuple[float, List[float]]:
    """基准测试: Provider 检测"""
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value=False)

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        await ProviderRegistry.auto_detect(page)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)
    return sum(times) / len(times), times


def measure_memory_footprint():
    """测量内存占用"""
    tracemalloc.start()

    # 创建 SliderSolver 实例
    solver = SliderSolver(provider="auto")

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return current / 1024 / 1024, peak / 1024 / 1024  # MB


async def run_benchmarks():
    """运行所有基准测试"""
    print("\n" + "="*70)
    print("性能基准测试 - v0.3.1")
    print("="*70)

    # 1. 轨迹生成
    print("\n📊 测试 1: 轨迹生成性能")
    avg, samples = benchmark_trajectory_generation(100)
    print(f"  平均耗时: {avg:.2f} ms")
    print(f"  最小值: {min(samples):.2f} ms")
    print(f"  最大值: {max(samples):.2f} ms")
    print(f"  标准差: {(sum((x - avg)**2 for x in samples) / len(samples))**0.5:.2f} ms")

    # 2. 图像匹配
    print("\n📊 测试 2: 图像匹配性能")
    avg, samples = benchmark_image_matching(10)
    print(f"  平均耗时: {avg:.2f} ms")
    print(f"  最小值: {min(samples):.2f} ms")
    print(f"  最大值: {max(samples):.2f} ms")

    # 3. Provider 检测
    print("\n📊 测试 3: Provider 自动检测性能")
    avg, samples = await benchmark_provider_detection(50)
    print(f"  平均耗时: {avg:.2f} ms")
    print(f"  最小值: {min(samples):.2f} ms")
    print(f"  最大值: {max(samples):.2f} ms")

    # 4. 内存占用
    print("\n📊 测试 4: 内存占用")
    current, peak = measure_memory_footprint()
    print(f"  当前内存: {current:.2f} MB")
    print(f"  峰值内存: {peak:.2f} MB")

    # 性能判断
    print("\n" + "="*70)
    print("性能评估")
    print("="*70)

    issues = []

    # 轨迹生成应该 < 1ms
    if avg > 1.0:
        issues.append(f"⚠ 轨迹生成过慢: {avg:.2f}ms > 1.0ms")
    else:
        print(f"✅ 轨迹生成: {avg:.2f}ms (目标: <1ms)")

    # Provider 检测应该 < 10ms
    avg_detect, _ = await benchmark_provider_detection(10)
    if avg_detect > 10.0:
        issues.append(f"⚠ Provider 检测过慢: {avg_detect:.2f}ms > 10ms")
    else:
        print(f"✅ Provider 检测: {avg_detect:.2f}ms (目标: <10ms)")

    # 内存应该 < 50MB
    if peak > 50:
        issues.append(f"⚠ 内存占用过高: {peak:.2f}MB > 50MB")
    else:
        print(f"✅ 内存占用: {peak:.2f}MB (目标: <50MB)")

    if issues:
        print("\n⚠ 性能问题:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ 所有性能指标正常")

    print("\n" + "="*70)
    print("基准测试完成")
    print("="*70)
    print("\n💡 提示:")
    print("  - 这些是单机基准，实际性能受网络/硬件影响")
    print("  - 对比 v0.3.0 需要在同一环境运行")
    print("  - 关注趋势变化而非绝对值")


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
