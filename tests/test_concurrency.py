"""
测试并发管理器

覆盖:
1. 单例模式
2. 并发数量限制
3. 同账号互斥（不能同时运行）
4. 等待队列和超时
5. 实例注册/注销
6. user_id 提取（带时间戳）
7. 多线程并发场景
8. 统计信息
"""

import pytest
import threading
import time
from slidex._concurrency import SliderConcurrencyManager, concurrency_manager


class TestSliderConcurrencyManager:
    """测试并发管理器"""

    def setup_method(self):
        """每个测试前重置单例状态"""
        # 重置单例
        SliderConcurrencyManager._instance = None
        SliderConcurrencyManager._lock = threading.Lock()

    def test_singleton_pattern(self):
        """测试单例模式"""
        manager1 = SliderConcurrencyManager()
        manager2 = SliderConcurrencyManager()

        # 应该是同一个实例
        assert manager1 is manager2

    def test_initialization_with_defaults(self):
        """测试默认参数初始化"""
        manager = SliderConcurrencyManager()

        assert manager.max_concurrent == 3
        assert manager.wait_timeout == 60
        assert len(manager.active_instances) == 0
        assert len(manager.waiting_queue) == 0

    def test_initialization_with_custom_params(self):
        """测试自定义参数初始化"""
        manager = SliderConcurrencyManager()

        # 手动设置参数（单例初始化后）
        manager.max_concurrent = 5
        manager.wait_timeout = 30

        assert manager.max_concurrent == 5
        assert manager.wait_timeout == 30

    def test_can_start_instance_when_empty(self):
        """测试空闲时可以启动实例"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        result = manager.can_start_instance("user1")

        assert result is True

    def test_can_start_instance_when_full(self):
        """测试达到上限时不能启动"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 2

        # 注册 2 个实例
        manager.register_instance("user1", "instance1")
        manager.register_instance("user2", "instance2")

        # 第 3 个应该不能启动
        result = manager.can_start_instance("user3")

        assert result is False

    def test_register_instance_success(self):
        """测试成功注册实例"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        result = manager.register_instance("user1", "instance1")

        assert result is True
        assert "user1" in manager.active_instances
        assert manager.active_instances["user1"]["instance"] == "instance1"

    def test_register_instance_when_full(self):
        """测试满时注册失败"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 1

        manager.register_instance("user1", "instance1")
        result = manager.register_instance("user2", "instance2")

        assert result is False
        assert "user2" not in manager.active_instances

    def test_unregister_instance_success(self):
        """测试成功注销实例"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        manager.register_instance("user1", "instance1")
        result = manager.unregister_instance("user1", "instance1")

        assert result is True
        assert "user1" not in manager.active_instances

    def test_unregister_instance_nonexistent(self):
        """测试注销不存在的实例"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        result = manager.unregister_instance("user1")

        assert result is False

    def test_unregister_instance_wrong_instance(self):
        """测试注销错误的实例对象"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        manager.register_instance("user1", "instance1")
        result = manager.unregister_instance("user1", "wrong_instance")

        # 应该失败（实例对象不匹配）
        assert result is False
        assert "user1" in manager.active_instances

    def test_same_account_mutual_exclusion(self):
        """测试同账号互斥（不能同时运行）"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 5

        # 注册 user1_123456
        manager.register_instance("user1_1234567890", "instance1")

        # 尝试注册 user1_654321（同一个用户）
        result = manager.can_start_instance("user1_9876543210")

        # 应该不能启动（同账号互斥）
        assert result is False

    def test_different_accounts_can_run_concurrently(self):
        """测试不同账号可以并发"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        # 注册 user1 和 user2
        manager.register_instance("user1_1234567890", "instance1")
        result = manager.can_start_instance("user2_1234567890")

        # user2 应该可以启动
        assert result is True

    def test_extract_pure_user_id_with_timestamp(self):
        """测试提取纯用户 ID（带时间戳）"""
        manager = SliderConcurrencyManager()

        # 带 10 位以上数字后缀的应该被去掉
        pure_id = manager._extract_pure_user_id("user123_1234567890")
        assert pure_id == "user123"

        # 带下划线但后缀不是长数字的应该保留
        pure_id = manager._extract_pure_user_id("user_abc")
        assert pure_id == "user_abc"

        # 没有下划线的应该原样返回
        pure_id = manager._extract_pure_user_id("user123")
        assert pure_id == "user123"

    def test_wait_for_slot_success(self):
        """测试等待成功获取槽位"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 1
        manager.wait_timeout = 5

        # 注册一个实例
        manager.register_instance("user1", "instance1")

        # 在另一个线程中等待并注销
        def unregister_after_delay():
            time.sleep(0.5)
            manager.unregister_instance("user1", "instance1")

        thread = threading.Thread(target=unregister_after_delay)
        thread.start()

        # 等待槽位
        start = time.time()
        result = manager.wait_for_slot("user2", timeout=3)
        elapsed = time.time() - start

        thread.join()

        # 应该成功获取槽位
        assert result is True
        assert elapsed < 3  # 应该在 0.5 秒左右

    def test_wait_for_slot_timeout(self):
        """测试等待超时"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 1
        manager.wait_timeout = 2

        # 注册一个实例（不释放）
        manager.register_instance("user1", "instance1")

        # 等待槽位（应该超时）
        start = time.time()
        result = manager.wait_for_slot("user2", timeout=2)
        elapsed = time.time() - start

        # 应该超时
        assert result is False
        assert elapsed >= 2

    def test_waiting_queue_management(self):
        """测试等待队列管理"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 1

        manager.register_instance("user1", "instance1")

        # 在另一个线程中等待
        def wait_in_thread():
            manager.wait_for_slot("user2", timeout=1)

        thread = threading.Thread(target=wait_in_thread)
        thread.start()

        time.sleep(0.2)  # 等待线程进入队列

        # 队列中应该有 user2
        stats = manager.get_stats()
        assert "user2" in stats["waiting_users"]

        thread.join()

        # 超时后队列应该清空
        stats = manager.get_stats()
        assert "user2" not in stats["waiting_users"]

    def test_get_stats_basic(self):
        """测试统计信息"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        manager.register_instance("user1", "instance1")
        manager.register_instance("user2", "instance2")

        stats = manager.get_stats()

        assert stats["active_count"] == 2
        assert stats["max_concurrent"] == 3
        assert stats["available_slots"] == 1
        assert stats["queue_length"] == 0

    def test_get_stats_with_queue(self):
        """测试带队列的统计信息"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 1

        manager.register_instance("user1", "instance1")

        # 模拟一个等待中的用户
        with manager.instance_lock:
            manager.waiting_queue.append("user2")

        stats = manager.get_stats()

        assert stats["active_count"] == 1
        assert stats["queue_length"] == 1
        assert "user2" in stats["waiting_users"]

    def test_concurrent_registration_thread_safe(self):
        """测试并发注册的线程安全性"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 5

        results = []

        def register_instance(user_id):
            result = manager.register_instance(user_id, f"instance_{user_id}")
            results.append((user_id, result))

        # 启动 10 个线程同时注册
        threads = []
        for i in range(10):
            thread = threading.Thread(target=register_instance, args=(f"user{i}",))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # 应该只有 5 个成功（max_concurrent=5）
        successful = [r for r in results if r[1] is True]
        assert len(successful) == 5

        # 活跃实例数应该是 5
        assert len(manager.active_instances) == 5

    def test_register_removes_from_waiting_queue(self):
        """测试注册时从等待队列移除"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 2

        # 手动加入等待队列
        with manager.instance_lock:
            manager.waiting_queue.append("user1")

        # 注册实例
        manager.register_instance("user1", "instance1")

        # 应该从队列中移除
        assert "user1" not in manager.waiting_queue

    def test_multiple_instances_lifecycle(self):
        """测试多实例完整生命周期"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        # 注册 3 个
        manager.register_instance("user1", "i1")
        manager.register_instance("user2", "i2")
        manager.register_instance("user3", "i3")

        assert len(manager.active_instances) == 3

        # 注销 1 个
        manager.unregister_instance("user2", "i2")

        assert len(manager.active_instances) == 2
        assert "user2" not in manager.active_instances

        # 可以注册新的
        result = manager.can_start_instance("user4")
        assert result is True

    def test_global_singleton_instance(self):
        """测试全局单例实例"""
        from slidex._concurrency import concurrency_manager

        # 应该是 SliderConcurrencyManager 的实例
        assert isinstance(concurrency_manager, SliderConcurrencyManager)

    def test_same_account_detection_with_complex_ids(self):
        """测试复杂 ID 的同账号检测"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 5

        # 注册 user_abc_1234567890
        manager.register_instance("user_abc_1234567890", "i1")

        # user_abc_9876543210 应该被识别为同账号
        result = manager.can_start_instance("user_abc_9876543210")

        assert result is False

    def test_empty_user_id(self):
        """测试空用户 ID"""
        manager = SliderConcurrencyManager()
        manager.max_concurrent = 3

        result = manager.register_instance("", "instance")

        # 应该能注册（空字符串也是有效 ID）
        assert result is True
