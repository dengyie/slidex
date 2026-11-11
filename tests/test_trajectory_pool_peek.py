"""peek 语义：只读探测不得按消费记账（0.11.6）。

2026-11-11 23:09 生产日志实证：一次求解打了两条一模一样的
``[TrajectoryPool] unused human ... loaded trajectory_959.json`` ——
``GestureSession._first_available`` 的可用性探测与 ``_build`` 的真正消费
各调一次 ``load_unused_human``，旧实现两次都打 INFO 并写 ``last_used.json``。

后果有二：

* 日志说谎：一条求解被记成两次「loaded」，排障时无法区分探测与消费；
* 只读探测推进 LRU 时钟：``last_used`` 是 0.11.7 起 ``load_unused_human``
  选轨与轮换的输入，
  只读审计跑一遍就刷新两个文件的 last_used（2026-11-11 22:41 的线上审计
  实际改写了生产池的记账）。

``peek=True`` 之后：DEBUG 日志、不写 last_used。选轨按 (距离差, 文件序号)
排序、与 last_used 无关，所以 peek 跳过 touch 不改变选到哪条——这一点也
在本文件里钉住。
"""
import json
from pathlib import Path
import tempfile

from loguru import logger

from slidex._gestures import GestureSession
from slidex._trajectory_pool import SliderTrajectoryPool


def _human_points(distance, frames=2, steps=7, dy=0.0, t0=100.0, gap=50.0):
    """与 test_trajectory_pool 同款的几何合法录制形状（写池闸门能收）。"""
    pts = []
    for f in range(frames):
        start = distance * f / frames
        end = distance * (f + 1) / frames
        for s in range(1, steps + 1):
            pts.append([start + (end - start) * s / steps, dy, t0 + gap * len(pts)])
    return pts


class _Capture:
    """按级别收集 loguru 记录，便于断言「INFO 没有 / DEBUG 有」。"""

    def __init__(self):
        self.records = []

    def __call__(self, message):
        self.records.append(message.record)

    def text(self, level=None):
        return "".join(
            r["message"] + "\n"
            for r in self.records
            if level is None or r["level"].name == level
        )


def _pool_with_human(distances=(258, 260), cookie_id="u"):
    tmp = tempfile.TemporaryDirectory()
    pool = SliderTrajectoryPool(tmp.name)
    files = []
    for d in distances:
        fname = pool.save_trajectory(
            _human_points(d),
            cookie_id=cookie_id,
            distance=d,
            success=True,
            source="human_cdp",
        )
        assert fname, f"fixture 录制被几何闸门拒收: dist={d}"
        files.append(fname)
    return tmp, pool, files


def test_peek_returns_record_without_touching_last_used():
    tmp, pool, files = _pool_with_human()
    with tmp:
        lu = Path(tmp.name) / "u" / "last_used.json"
        rec = pool.load_unused_human("u", 258.0, peek=True)
        assert rec is not None
        assert Path(rec["_file"]).name == files[0]  # 仍取最旧编号那条
        assert not lu.exists(), "peek 不得写 last_used.json"


def test_consume_touches_last_used_once():
    tmp, pool, files = _pool_with_human()
    with tmp:
        pool.load_unused_human("u", 258.0)
        lu = json.loads((Path(tmp.name) / "u" / "last_used.json").read_text())
        assert set(lu) == {files[0]}


def test_peek_logs_debug_only_consume_logs_info():
    tmp, pool, files = _pool_with_human()
    cap = _Capture()
    handler = logger.add(cap, level="DEBUG")
    try:
        pool.load_unused_human("u", 258.0, peek=True)
        assert "unused human peek" in cap.text("DEBUG")
        assert cap.text("INFO") == "", "peek 不得打 INFO"
        pool.load_unused_human("u", 260.0, exclude_files={files[0]})
        info = cap.text("INFO")
        assert "unused human for dist=260" in info
        assert "loaded dist=260px file=%s" % files[1] in info
    finally:
        logger.remove(handler)


def test_selection_identical_with_or_without_peek():
    """peek 跳过 touch 后，选轨仍是最旧编号那条（排序与 last_used 无关）。"""
    tmp, pool, files = _pool_with_human()
    with tmp:
        peeked = pool.load_unused_human("u", 258.0, peek=True)
        consumed = pool.load_unused_human("u", 258.0)
        assert Path(peeked["_file"]).name == files[0]
        assert Path(consumed["_file"]).name == files[0]


def test_gesture_session_solve_flow_touches_exactly_once():
    """整条求解链（探测 + 消费）只允许一次 last_used 落盘与一条 INFO。"""
    tmp, pool, files = _pool_with_human()
    cap = _Capture()
    handler = logger.add(cap, level="DEBUG")
    touches = []
    original = pool._touch_last_used

    def _spy(cookie_id, filepath):
        touches.append(Path(filepath).name)
        original(cookie_id, filepath)

    pool._touch_last_used = _spy
    try:
        session = GestureSession(pool, "u", "auto")
        plan = session.next(258.0, None)
        assert plan is not None, "human 轨迹应可用并被选为首选 archetype"
        assert touches == [files[0]], touches
        info_lines = [ln for ln in cap.text("INFO").splitlines() if "unused human" in ln]
        assert len(info_lines) == 1, cap.text("INFO")
    finally:
        logger.remove(handler)
