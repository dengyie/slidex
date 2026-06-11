#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""滑块验证并发管理器 — 控制同时运行的滑块求解实例数量"""

import threading
import time
from loguru import logger

# 默认值，可被 SlidexConfig 覆盖
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_WAIT_TIMEOUT = 60


class SliderConcurrencyManager:
    """滑块验证并发管理器（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_concurrent=None, wait_timeout=None):
        if not self._initialized:
            self.max_concurrent = max_concurrent if max_concurrent is not None else DEFAULT_MAX_CONCURRENT
            self.wait_timeout = wait_timeout if wait_timeout is not None else DEFAULT_WAIT_TIMEOUT
            self.active_instances = {}
            self.waiting_queue = []
            self.instance_lock = threading.Lock()
            self._initialized = True
            logger.info(f"滑块验证并发管理器初始化: 最大并发数={self.max_concurrent}, 等待超时={self.wait_timeout}秒")

    def can_start_instance(self, user_id: str) -> bool:
        with self.instance_lock:
            return self._can_start_locked(user_id)

    def _find_same_account_active_locked(self, user_id: str):
        pure_user_id = self._extract_pure_user_id(user_id)
        for active_user_id in self.active_instances:
            if self._extract_pure_user_id(active_user_id) == pure_user_id:
                return active_user_id
        return None

    def _can_start_locked(self, user_id: str) -> bool:
        same_account_active = self._find_same_account_active_locked(user_id)
        return len(self.active_instances) < self.max_concurrent and same_account_active is None

    def wait_for_slot(self, user_id: str, timeout: int = None) -> bool:
        if timeout is None:
            timeout = self.wait_timeout

        start_time = time.time()

        while time.time() - start_time < timeout:
            with self.instance_lock:
                same_account_active = self._find_same_account_active_locked(user_id)
                if len(self.active_instances) < self.max_concurrent and same_account_active is None:
                    return True

            with self.instance_lock:
                if user_id not in self.waiting_queue:
                    self.waiting_queue.append(user_id)
                    pure_user_id = self._extract_pure_user_id(user_id)
                    same_account_active = self._find_same_account_active_locked(user_id)
                    if same_account_active:
                        logger.warning(
                            f"【{pure_user_id}】同账号滑块任务正在执行({same_account_active})，进入等待队列，当前队列长度: {len(self.waiting_queue)}"
                        )
                    else:
                        logger.info(f"【{pure_user_id}】进入等待队列，当前队列长度: {len(self.waiting_queue)}")

            time.sleep(1)

        with self.instance_lock:
            if user_id in self.waiting_queue:
                self.waiting_queue.remove(user_id)
                pure_user_id = self._extract_pure_user_id(user_id)
                logger.warning(f"【{pure_user_id}】等待超时，从队列中移除")

        return False

    def register_instance(self, user_id: str, instance):
        with self.instance_lock:
            if not self._can_start_locked(user_id):
                return False
            self.active_instances[user_id] = {
                'instance': instance,
                'start_time': time.time()
            }
            if user_id in self.waiting_queue:
                self.waiting_queue.remove(user_id)
            return True

    def unregister_instance(self, user_id: str, instance=None):
        with self.instance_lock:
            active_entry = self.active_instances.get(user_id)
            if not active_entry:
                return False

            if instance is not None and active_entry.get('instance') is not instance:
                pure_user_id = self._extract_pure_user_id(user_id)
                logger.debug(f"【{pure_user_id}】跳过注销实例：当前活跃实例已切换，避免误释放新槽位")
                return False

            del self.active_instances[user_id]
            pure_user_id = self._extract_pure_user_id(user_id)
            logger.info(f"【{pure_user_id}】实例已注销，当前活跃: {len(self.active_instances)}")
            return True

    def _extract_pure_user_id(self, user_id: str) -> str:
        if '_' in user_id:
            parts = user_id.split('_')
            if len(parts) >= 2 and parts[-1].isdigit() and len(parts[-1]) >= 10:
                return '_'.join(parts[:-1])
            else:
                return user_id
        else:
            return user_id

    def get_stats(self):
        with self.instance_lock:
            return {
                'active_count': len(self.active_instances),
                'max_concurrent': self.max_concurrent,
                'available_slots': self.max_concurrent - len(self.active_instances),
                'queue_length': len(self.waiting_queue),
                'waiting_users': self.waiting_queue.copy()
            }


# 全局单例
concurrency_manager = SliderConcurrencyManager()
