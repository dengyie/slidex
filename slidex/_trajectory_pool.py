#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""滑块轨迹池管理 — 录制真实人类拖拽轨迹并回放，突破 WASM ML 检测"""

import hashlib
import json
import math
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from slidex._sanitize import sanitize_pure_user_id
from slidex._slide_geometry import recorded_geometry_verdict, recorded_trace_verdict


_TRAJECTORY_INDEX_RE = re.compile(r"trajectory_(\d+)\.json$")

# 每个脏文件只告警一次（进程内）：_load_all 在每次拖动里至少被调用一次，
# 逐次刷 WARNING 会让线上日志被存量脏文件淹没（实测占约 9%），而原因码在
# 首次出现后就不再是新信息。
_DIRTY_WARNED: set = set()


def _dirty_geometry(record: Dict[str, Any]) -> Optional[str]:
    """池内一条录制坐标轴是否坏了；坏了返回原因码，健康返回 None。

    加载侧判据与写入侧同一条（``recorded_geometry_verdict``），只是参数
    来源是文件本身；多一步解码，因为池里可能有旧版本的增量编码。存量脏
    文件挡在回放之前，避免一条坏录制毒化之后每一次自动拖动。
    """
    reason = recorded_trace_verdict(record.get("points") or [], record.get("distance"))
    if reason:
        name = os.path.basename(str(record.get("_file") or ""))
        if name not in _DIRTY_WARNED:
            _DIRTY_WARNED.add(name)
            logger.warning(f"[TrajectoryPool] skip dirty recording {name}: {reason}")
        else:
            logger.debug(f"[TrajectoryPool] skip dirty recording {name}: {reason}")
    return reason


def _trajectory_index(path) -> int:
    """trajectory_<n>.json 的数值编号；不匹配的名字返回 -1（排最前，优先淘汰）。"""
    m = _TRAJECTORY_INDEX_RE.search(os.path.basename(str(path)))
    return int(m.group(1)) if m else -1


def _sorted_trajectories(cdir: Path) -> List[Path]:
    """按**数值**编号排序，不用字典序。

    字典序把 ``trajectory_1000.json`` 排在 ``trajectory_951.json`` 前面
    （'1' < '9'）。池满 ``max_per_cookie`` 时"淘汰最旧"取的就是这个序列的
    头部——于是淘汰掉的是**最新**的一条，新录制还复用同一个编号把它覆盖：
    线上 2026-11-10 实证（1926782908），22:27 的 48s 真人录制被 23:17 的
    新录制静默吃掉。
    """
    return sorted(cdir.glob("trajectory_*.json"), key=_trajectory_index)


def _recorded_at_value(path) -> Optional[datetime]:
    """读文件的 recorded_at（ISO 秒级）；读不到返回 None（按"未知"处理）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f).get("recorded_at")
        return datetime.strptime(str(raw), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _eviction_order(paths: List[Path]) -> List[Path]:
    """淘汰序：真实录制时间最早在前，无时间戳的排最前；同时间按数值编号。

    0.10.3 只按数值编号（修的是字典序把 1000 排在 951 前的问题）。编号仍是
    主键时的次要风险：跨机器/备份恢复会复用编号，于是"编号小但刚录"的条目
    会被当成最旧删掉。线上池里同时存在 951/1001/1002 这类号段就是这种痕迹。
    """
    def _key(fp: Path):
        ts = _recorded_at_value(fp)
        return (ts is None, ts or datetime.min, _trajectory_index(fp))

    return sorted(paths, key=_key)


class SliderTrajectoryPool:
    """管理真机滑块轨迹的录制、存储、回放与轮转"""

    #: 本类认得的构造参数名（供 solver/api 只挑出池相关的配置项传进来）。
    _CONFIG_KEYS = (
        "max_per_cookie",
        "min_pool_size",
        "rotation_strategy",
        "recorded_only_mode",
    )

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.expanduser("~"), ".slidex", "trajectories")
        self.base_dir = Path(base_dir)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"Cannot create trajectory pool dir {self.base_dir}: {e}")
            # Pool will return None for all get/load calls, solver falls back to generated
        self.max_per_cookie = 50
        self.min_pool_size = 5
        self.rotation_strategy = "lru"
        self.recorded_only_mode = False
        self.enabled = True

    @classmethod
    def from_config(cls, config: Any, base_dir: str = None) -> "SliderTrajectoryPool":
        """按 SlidexConfig 的 trajectory_pool_* 字段构造池（0.11.0）。

        0.10.x 起 config 暴露了 ``SLIDEX_TRAJ_POOL_MAX/MIN/STRATEGY/
        POOL_RECORDED_ONLY`` 等旋钮，但构造方一律 ``SliderTrajectoryPool(base_dir=...)``，
        字段从未下沉到池实例——线上设了 env 也不生效。这里统一收口。
        """
        pool = cls(base_dir=base_dir)
        if config is None:
            return pool
        pool.apply_config(config)
        return pool

    def apply_config(self, config: Any) -> "SliderTrajectoryPool":
        """把 SlidexConfig 上的池字段写到实例（缺字段/非法值保持当前值）。"""
        def _int(name: str, current: int, floor: int) -> int:
            raw = getattr(config, name, None)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return current
            return value if value >= floor else current

        self.max_per_cookie = _int("trajectory_pool_max_per_cookie", self.max_per_cookie, 1)
        self.min_pool_size = _int("trajectory_pool_min_pool_size", self.min_pool_size, 0)
        strategy = getattr(config, "trajectory_pool_rotation_strategy", None)
        if isinstance(strategy, str) and strategy.strip():
            self.rotation_strategy = strategy.strip().lower()
        mode = getattr(config, "trajectory_pool_recorded_only_mode", None)
        if mode is not None:
            self.recorded_only_mode = bool(mode)
        enabled = getattr(config, "trajectory_pool_enabled", None)
        if enabled is not None:
            self.enabled = bool(enabled)
        return self

    def serve_pooled(self) -> bool:
        """是否允许从池里取轨迹回放（``SLIDEX_TRAJ_POOL_ENABLED=0`` 可整体关闭）。

        关闭只是不回放池内录制；solver 仍会采集真人轨迹并自动生成手势，
        随时可再打开（池内容不丢）。
        """
        return bool(self.enabled)

    # ── cookie 子目录 ──────────────────────────────────────────
    @staticmethod
    def _sanitize_cookie_id(cookie_id: str) -> str:
        """委托共享清洗：保留名转义、.. 收敛、分隔符剔除"""
        return sanitize_pure_user_id(cookie_id)

    def _cookie_dir(self, cookie_id: str) -> Path:
        base = self.base_dir.resolve()
        safe_cookie_id = self._sanitize_cookie_id(cookie_id)
        d = (base / safe_cookie_id).resolve()
        if not d.is_relative_to(base):
            raise ValueError("cookie_id escapes trajectory pool directory")
        try:
            d.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.debug(f"Cannot create cookie trajectory dir: {e}")
        return d

    # ── 保存 ──────────────────────────────────────────────────
    def save_trajectory(self, points: List[List[float]], cookie_id: str,
                        distance: float, success: bool, verify_url: str = "",
                        duration_ms: float = 0, source: str = "auto") -> Optional[str]:
        """持久化一条轨迹，返回文件名，超出上限时淘汰最旧的

        落盘前过 ``recorded_geometry_verdict``：坐标轴坏的录制（绝对坐标 /
        双次编码回退 / 漏采样跳变）一旦进池就会被优先抽中回放，比没有录制更
        糟。拒收时返回 None，调用方照常降级到生成器轨迹。
        """
        cookie_id = self._sanitize_cookie_id(cookie_id)
        if not points:
            return None
        reason = recorded_geometry_verdict(points, distance)
        try:
            dist_label = f"{float(distance or 0):.0f}px" if math.isfinite(float(distance or 0)) else "-"
        except (TypeError, ValueError):
            dist_label = "?"
        if reason:
            logger.warning(
                f"[TrajectoryPool] reject recording for {cookie_id} "
                f"(dist={dist_label}, {len(points or [])} pts): {reason}"
            )
            return None
        # 非数值坐标（null / 字符串 / 嵌套 list / NaN）在 round 或 verdict 里
        # 都可能抛：写池是旁路能力，绝不能把调用方的成功路径变成异常。
        # 与 verdict 一样容忍缺省 y/delay（老上报方可能只给 [x] 或 [x, y]）。
        try:
            rows = [
                [float(p[0]),
                 float(p[1]) if len(p) > 1 else 0.0,
                 float(p[2]) if len(p) > 2 else 0.0]
                for p in points
            ]
            if not all(math.isfinite(v) for row in rows for v in row):
                raise ValueError("non-finite coordinate")
        except (TypeError, ValueError, IndexError) as e:
            logger.warning(
                f"[TrajectoryPool] reject recording for {cookie_id}: invalid_points ({e})"
            )
            return None
        cdir = self._cookie_dir(cookie_id)
        new_points = [[round(x, 2), round(y, 2), round(t, 1)] for x, y, t in rows]
        new_distance = round(float(distance or 0), 1)
        # 幂等去重：同一份 payload 反复落盘只会挤占池位，并让这条几何被抽中的
        # 概率翻倍（线上 2026-10-12..14 实证：50 个文件里 20 对完全相同的录制，
        # 同秒双写）。命中时复用已存在的文件名，调用方无感。
        # 只在存量文件本身健康时复用——脏文件不能顶替一条新录制，否则新录制被
        # "去重"成永远加载不出来的废条目。
        for fp in _sorted_trajectories(cdir):
            try:
                with open(fp, encoding="utf-8") as f:
                    existing = json.load(f)
            except (OSError, ValueError):
                continue
            if (
                existing.get("points") == new_points
                and existing.get("distance") == new_distance
            ):
                existing["_file"] = str(fp)
                if _dirty_geometry(existing):
                    continue
                logger.debug(
                    f"[TrajectoryPool] duplicate recording for {cookie_id} — reuse {fp.name}"
                )
                return fp.name
        self._evict_overflow(cdir)

        remaining = _sorted_trajectories(cdir)
        idx = 1
        for fp in remaining:
            try:
                idx = max(idx, int(fp.stem.split("_")[-1]) + 1)
            except ValueError:
                continue
        fname = f"trajectory_{idx:03d}.json"
        fpath = cdir / fname
        data = {
            "cookie_id": cookie_id,
            "recorded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "verify_url_hash": hashlib.md5(str(verify_url or "").encode()).hexdigest()[:8],
            "distance": round(float(distance or 0), 1),
            "success": bool(success),
            "source": str(source or "auto"),
            "duration_ms": round(float(duration_ms), 1),
            "duration_kind": "motion",
            "points": new_points,
        }
        stub = fname[: -len(".json")]
        for attempt in range(3):
            try:
                with open(fpath, "x", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"[TrajectoryPool] saved {fname} for {cookie_id} (dist={distance:.0f}px, ok={success})")
                return fname
            except FileExistsError:
                # 并发进程抢到同一编号：让位重试，不覆盖对方已写好的录制
                idx += 1
                stub = f"trajectory_{idx:03d}"
                fname = f"{stub}.json"
                fpath = cdir / fname
                continue
            except (OSError, IOError, PermissionError) as e:
                logger.debug(f"[TrajectoryPool] cannot save trajectory: {e}")
                return None
        logger.debug(f"[TrajectoryPool] cannot save trajectory: {stub} kept colliding")
        return None

    def _evict_overflow(self, cdir: Path) -> None:
        """池满时淘汰最旧的。

        0.10.3 按 numeric 编号升序淘汰；0.11.0 起改为 ``recorded_at`` 优先、
        编号作并列决胜——编号可能被并发/人工提交跨机器复用（例如备份恢复
        后新录制沿用了 1001/1002 号），按编号淘汰会把"编号小但刚录"
        的条目误删。
        """
        existing = _sorted_trajectories(cdir)
        if len(existing) < self.max_per_cookie:
            return
        victims = existing[: len(existing) - self.max_per_cookie + 1]
        for old in _eviction_order(victims):
            try:
                os.remove(str(old))
            except Exception:
                pass

    # ── 加载 ──────────────────────────────────────────────────
    def _load_all(self, cookie_id: str) -> List[dict]:
        cdir = self._cookie_dir(cookie_id)
        records = []
        for fp in _sorted_trajectories(cdir):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    d = json.load(f)
                d["_file"] = str(fp)
            except Exception as e:
                logger.debug(f"[TrajectoryPool] skip corrupt {fp}: {e}")
                continue
            # 存量脏录制（0.11.1 之前写入、写池时还没有几何校验）：坐标轴坏了
            # 就不参与回放，否则一条脏文件会毒化之后每一次自动拖动。判定本身
            # 也包在 try 里——它是防御性代码，抛异常时最坏结果是跳过这一条，
            # 而不是让整个池子对这个 cookie 全部不可读（一个坏文件拖垮 49 条
            # 好录制）。
            try:
                if _dirty_geometry(d):
                    continue
            except Exception as e:
                logger.warning(
                    f"[TrajectoryPool] geometry check failed on "
                    f"{os.path.basename(str(fp))}, skipping: {e}"
                )
                continue
            records.append(d)
        return records

    def load_unused_human(
        self,
        cookie_id: str,
        target_distance: float,
        exclude_files: Optional[set] = None,
        tolerance: float = 0.10,
    ) -> Optional[dict]:
        """Next unused human recording within ``tolerance`` of ``target_distance``.

        Unlike ``load_best_trajectory`` this never widens past the given
        tolerance, never falls back to failed/auto traces, and skips files
        already consumed by the current ``GestureSession``.
        """
        cookie_id = self._sanitize_cookie_id(cookie_id)
        skip = set()
        for p in (exclude_files or set()):
            skip.add(str(p))
            skip.add(os.path.basename(str(p)))
        records = []
        for r in self._load_all(cookie_id):
            source = str(r.get("source") or "")
            if not r.get("success"):
                continue
            if not source.startswith("human"):
                continue
            fname = os.path.basename(str(r.get("_file") or ""))
            if fname in skip or str(r.get("_file") or "") in skip:
                continue
            d = abs(float(r.get("distance") or 0.0) - float(target_distance))
            ratio = d / max(float(target_distance), 1.0)
            if ratio <= float(tolerance):
                records.append((d, r))
        if not records:
            return None
        # 同距离时按数值编号取最旧的一条：避免 1000 号（字典序最小）被反复
        # 抽中而 951 号永远轮不到。
        records.sort(key=lambda item: (item[0], _trajectory_index(item[1].get("_file") or "")))
        best = records[0][1]
        logger.info(
            f"[TrajectoryPool] unused human for dist={target_distance:.0f}: "
            f"loaded dist={best['distance']:.0f}px file={os.path.basename(best.get('_file', ''))}"
        )
        self._touch_last_used(cookie_id, best["_file"])
        return best

    def load_best_trajectory(self, cookie_id: str, target_distance: float,
                             distance_tolerance: float = 0.10) -> Optional[dict]:
        """按距离匹配最佳轨迹（优先成功过的），支持回退放宽 tolerance"""
        records = [r for r in self._load_all(cookie_id) if r.get("success")]
        if not records:
            records = self._load_all(cookie_id)
        if not records:
            return None

        def _best(tol):
            best, best_diff = None, float("inf")
            for r in records:
                d = abs(r["distance"] - target_distance)
                ratio = d / max(target_distance, 1.0)
                if ratio <= tol and d < best_diff:
                    best, best_diff = r, d
            return best

        for tol in (distance_tolerance, 0.15, 0.25, 0.40):
            best = _best(tol)
            if best:
                logger.info(f"[TrajectoryPool] best match for dist={target_distance:.0f}: "
                            f"loaded dist={best['distance']:.0f}px (tol={tol})")
                self._touch_last_used(cookie_id, best["_file"])
                return best
        return None

    def load_random_trajectory(self, cookie_id: str) -> Optional[dict]:
        """随机选取（防重放检测）"""
        records = self._load_all(cookie_id)
        if not records:
            return None
        r = random.choice(records)
        self._touch_last_used(cookie_id, r["_file"])
        return r

    def get_random_trajectory(self) -> Optional[dict]:
        """
        获取随机轨迹（用于 provider 模式）

        这是 load_random_trajectory() 的便捷别名，使用 "default" 作为 cookie_id。
        Provider 模式下不需要区分不同用户的轨迹池。
        """
        return self.load_random_trajectory("default")

    # ── 轮转 ──────────────────────────────────────────────────
    def _last_used_path(self, cookie_id: str) -> Path:
        return self._cookie_dir(cookie_id) / "last_used.json"

    def _read_last_used(self, cookie_id: str) -> Dict[str, float]:
        p = self._last_used_path(cookie_id)
        if p.exists():
            try:
                with open(p, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _touch_last_used(self, cookie_id: str, filepath: str):
        try:
            lu = self._read_last_used(cookie_id)
            lu[os.path.basename(filepath)] = time.time()
            with open(self._last_used_path(cookie_id), "w") as f:
                json.dump(lu, f)
        except (OSError, IOError, PermissionError) as e:
            logger.debug(f"[TrajectoryPool] cannot update last_used: {e}")

    def rotate_trajectory(self, cookie_id: str, target_distance: float) -> Optional[dict]:
        """LRU 轮转：选最久未用 + 距离匹配"""
        records = [r for r in self._load_all(cookie_id) if r.get("success")]
        if not records:
            return self.load_best_trajectory(cookie_id, target_distance)

        lu = self._read_last_used(cookie_id)
        scored = []
        for r in records:
            fname = os.path.basename(r["_file"])
            age = time.time() - lu.get(fname, 0)
            dist_err = abs(r["distance"] - target_distance) / max(target_distance, 1.0)
            score = age * 0.6 - dist_err * 0.4
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            best = scored[0][1]
            self._touch_last_used(cookie_id, best["_file"])
            return best
        return None

    # ── 统计 ──────────────────────────────────────────────────
    def get_pool_stats(self, cookie_id: str) -> dict:
        records = self._load_all(cookie_id)
        successful = [r for r in records if r.get("success")]
        # 只对声明为 "motion"（归一化后的纯位移段）的时长求均值：0.10.1 及更早
        # 落盘的是录制墙钟（13-48s），混进来会把均值拉到一个不存在的量级。
        motion = [r for r in records if r.get("duration_kind") == "motion"]
        return {
            "total": len(records),
            "successful": len(successful),
            "failed": len(records) - len(successful),
            "success_rate": len(successful) / len(records) if records else 0,
            "avg_duration_ms": (sum(float(r.get("duration_ms") or 0.0) for r in motion) / len(motion)
                                if motion else 0),
            "durations_sampled": len(motion),
            "pool_ready": len(records) >= self.min_pool_size,
        }

    # ── 清理 ──────────────────────────────────────────────────
    def clean_stale(self, cookie_id: str = None, max_age_days: int = 7):
        """清理过期 / 无效轨迹"""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        targets = [cookie_id] if cookie_id else [
            d.name for d in self.base_dir.iterdir() if d.is_dir()]
        for cid in targets:
            cdir = self._cookie_dir(cid)
            for fp in cdir.glob("trajectory_*.json"):
                try:
                    with open(fp, "r") as f:
                        d = json.load(f)
                    ts = datetime.strptime(d.get("recorded_at", "2000-01-01T00:00:00"),
                                           "%Y-%m-%dT%H:%M:%S")
                    if ts < cutoff or not d.get("success"):
                        os.remove(str(fp))
                        logger.info(f"[TrajectoryPool] cleaned: {fp}")
                except Exception:
                    pass
