"""0.6.24: 拖动事件流水线派发 — 把网络 RTT 从事件间隔里剥出去。

问题（用户实测：CDP 模式下拖动很卡顿、不连贯）：轨迹生成器设计的是
~15 个点、25-75ms 间隔、总时长 ~600ms 的平滑人形时间线；但执行侧每个
事件都是 `await page.mouse.move()` / `wait_for_timeout` 的完整往返——
CDP 模式下一次往返 = VPS → 反向 SSH 隧道 → 用户 PC Chrome（实测
30-150ms/次），且 RTT 抖动直接污染事件节奏。设计 600ms 的拖动被撕成
2-6 秒的台阶，阿里前端看到的是低频大间隔 mousemove 串。

方案：CDP 会话可用时（CDP 真机模式 = plain playwright，允许 CDP session；
容器 patchright 模式禁 CDP 会话——那里本地 RTT ~1ms 无此问题），把整条
拖动 choreography 编译成 [(gap_ms, Input.dispatchMouseEvent params)]
时间线，按设计间隔 fire-and-forget 派发（不等每个 ack，最后统一
gather）。浏览器收到的事件间隔 = 设计间隔 ± 毫秒级调度抖动，与 RTT
无关；首个 ack 的往返只发生在开头，不插入事件之间。

isTrusted 保证：Input domain 派发与 playwright mouse 同源，均为可信
输入事件。按下期间 mousemove 携带 buttons=1（真实拖拽的按钮态）。
"""
import asyncio
import random
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from slidex._trajectory import slide_end_hold_range


def build_drag_events(
    start_x: float,
    start_y: float,
    points: List[Tuple[float, float, float]],
    extra_overshoot: bool = True,
) -> List[Tuple[float, Dict[str, Any]]]:
    """把拖动 choreography 编译为 [(gap_ms, Input.dispatchMouseEvent 参数)]。

    points: 绝对坐标点 [(x, y, delay_ms)]；首点可为 (start_x, start_y, hold_ms)
    表示按下后按住不动的停顿（对 start 的 no-op move，仅吃掉 gap）。
    extra_overshoot: 点序之外追加过冲/回拖/手抖。human 回放与已烘焙过冲
    的家族必须传 False；由 GesturePlan.extra_overshoot 决定，禁止默认 True
    盖掉 plan。

    事件序：接近移动 ×2 → 按下 → 位移点（buttons=1）→ [过冲/回拖/手抖] →
    末端握持 → 松键。
    """
    ev: List[Tuple[float, Dict[str, Any]]] = []

    def add(gap_ms: float, **params: Any) -> None:
        ev.append((max(0.0, float(gap_ms)), params))

    add(random.uniform(20, 60), type="mouseMoved",
        x=start_x + random.uniform(-8, -3), y=start_y + random.uniform(2, 6),
        buttons=0, pointerType="mouse")
    add(random.uniform(20, 60), type="mouseMoved",
        x=start_x, y=start_y, buttons=0, pointerType="mouse")
    add(0, type="mousePressed",
        x=start_x, y=start_y, button="left", buttons=1, clickCount=1,
        pointerType="mouse")

    last_x, last_y = start_x, start_y
    for (x, y, delay) in points:
        gap = float(delay) if delay else 0.0
        add(gap, type="mouseMoved", x=float(x), y=float(y),
            buttons=1, pointerType="mouse")
        last_x, last_y = float(x), float(y)

    if extra_overshoot and points:
        overshoot = random.uniform(3.0, 6.0)
        back = random.uniform(2.0, 3.5)
        add(random.uniform(60, 110), type="mouseMoved",
            x=last_x + overshoot, y=last_y + random.uniform(-1.5, 1.5),
            buttons=1, pointerType="mouse")
        add(random.uniform(50, 90), type="mouseMoved",
            x=last_x + overshoot - back, y=last_y + random.uniform(-1.0, 1.0),
            buttons=1, pointerType="mouse")
        add(random.uniform(30, 60), type="mouseMoved",
            x=last_x + random.uniform(-1.0, 1.0), y=last_y,
            buttons=1, pointerType="mouse")
        last_x = last_x + random.uniform(-1.0, 1.0)

    lo, hi = slide_end_hold_range()
    add(random.uniform(lo, hi) * 1000.0, type="mouseReleased",
        x=last_x, y=last_y, button="left", buttons=0, clickCount=1,
        pointerType="mouse")
    return ev


def apply_end_hold_scale(
    timeline: List[Tuple[float, Dict[str, Any]]],
    scale: float,
) -> List[Tuple[float, Dict[str, Any]]]:
    """Multiply the pre-mouseup gap (last timeline item) by ``scale``. Mutates in place."""
    factor = max(0.0, float(scale or 1.0))
    if factor == 1.0 or not timeline:
        return timeline
    gap, params = timeline[-1]
    timeline[-1] = (gap * factor, params)
    return timeline


class DragDispatchError(RuntimeError):
    """CDP Input.dispatchMouseEvent failed mid-timeline. Not a packet-miss (-1)."""


async def dispatch_drag_timeline(session: Any, timeline: List[Tuple[float, Dict[str, Any]]]) -> None:
    """按时间线 gap 流水线派发 Input 事件（不等每个 ack）。

    顺序保证：task 依创建序启动，playwright 连接层同步入队写帧 →
    驱动层对 CDP 的 send 支持多飞行（pipelining）→ 浏览器按序、按
    设计间隔收到事件。

    任一次 send 失败会 raise ``DragDispatchError``：调用方不得把它折成
    code=-1（那会锁死同一手势家族重试）。会话已死时应中止，而不是再拖一遍。

    0.10.2：派发后打一条 design vs wall 计时（wall 含末次 ack 的一次往返，
    所以良性的 wall ≈ design + RTT）。这是"轨迹不依赖网络"的线上可观测证据：
    出现 wall 远大于 design 时日志里能直接看到，不必再靠用户手感反馈。
    """
    design_ms = sum(gap for gap, _ in timeline)
    started = asyncio.get_running_loop().time()
    tasks = []
    for gap_ms, params in timeline:
        if gap_ms > 0:
            await asyncio.sleep(gap_ms / 1000.0)
        tasks.append(asyncio.create_task(session.send("Input.dispatchMouseEvent", params)))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    wall_ms = (asyncio.get_running_loop().time() - started) * 1000.0
    logger.info(
        f"drag timeline: events={len(tasks)} design={design_ms:.0f}ms wall={wall_ms:.0f}ms "
        f"(wall-design={wall_ms - design_ms:+.0f}ms ≈ 一次 ack 往返)"
    )
    failed = [r for r in results if isinstance(r, BaseException)]
    if failed:
        logger.warning(f"drag timeline: {len(failed)}/{len(tasks)} dispatches failed: {failed[0]}")
        raise DragDispatchError(
            f"{len(failed)}/{len(tasks)} Input.dispatchMouseEvent failed"
        ) from failed[0]


# ═══ 人类拖动轨迹学习 ═══
# 用户在 CDP 标签页里手动拖动时录制其真实动作（press-hold 时序、速度曲线、
# 过冲回拖全是原生的），成功后入轨迹池；此后自动拖动优先回放人类轨迹
# （GestureSession 经 load_unused_human 按距离+lru 选轨，0.6.25 起流水线平滑回放）。

DRAG_RECORDER_JS = r"""
(() => {
  if (window.__slidexDragRecorder) return;
  const rec = {active: false, t0: 0, events: []};
  window.__slidexDragRecorder = rec;
  document.addEventListener('mousedown', (e) => {
    if (!e.isTrusted || rec.active) return;
    rec.active = true;
    rec.t0 = performance.now();
    rec.events = [{dt: 0, x: e.clientX, y: e.clientY, buttons: e.buttons}];
  }, true);
  document.addEventListener('mousemove', (e) => {
    if (!rec.active || !e.isTrusted) return;
    rec.events.push({dt: performance.now() - rec.t0, x: e.clientX, y: e.clientY, buttons: e.buttons});
  }, true);
  document.addEventListener('mouseup', (e) => {
    if (!rec.active || !e.isTrusted) return;
    rec.active = false;
    rec.events.push({dt: performance.now() - rec.t0, x: e.clientX, y: e.clientY, buttons: 0});
    try {
      sessionStorage.setItem('__slidexLastDrag', JSON.stringify({
        done: true, events: rec.events, t_end: performance.now(),
      }));
    } catch (err) {}
  }, true);
})();
"""


def human_events_to_points(events: List[Dict[str, Any]]) -> Tuple[List[List[float]], float, float]:
    """把录制的人类拖动事件序列转为轨迹池格式（0.10.2：时序已归一化）。

    events: [{dt, x, y, buttons}]（首事件=按下位置，末事件=松键位置）。
    返回 (points, distance, duration_ms)：points 是相对 events[0] 的累计
    位移 [[dx, dy, delay_ms]]（与 remote.py 录制同一套语义），末点即释放位。
    duration_ms 是**归一化后**的纯位移段时长，不是录制墙钟时长。

    0.10.2 根因：录制墙钟时长不可信——CDP 真机模式里人是在远端画面里拖的，
    线上轨迹池 50 条 human 全部 13.8-17.3s、单点间隙最大 1.24s。回放侧原样
    保留 delay，于是每次自动拖动都是 16.7s 的走走停停（用户侧"轨迹很卡"）。
    几何（形状/终点）是录制最有价值的部分，必须保留；只把时间轴走
    ``normalize_recorded_delays`` 回到生成器同一设计带。

    非真实拖动（位移 < 50px 或事件 < 5 个）返回 ([], 0, 0)。
    """
    if not events or len(events) < 5:
        return [], 0.0, 0.0
    origin = events[0]
    ox = float(origin["x"])
    oy = float(origin["y"])
    pts = []
    prev_dt = float(origin["dt"])
    for ev in events[1:]:
        dt = float(ev["dt"])
        pts.append([
            float(ev["x"]) - ox,
            float(ev["y"]) - oy,
            max(0.0, dt - prev_dt),
        ])
        prev_dt = dt
    distance = float(events[-1]["x"]) - ox
    if abs(distance) < 50:
        return [], 0.0, 0.0

    from slidex._slide_geometry import normalize_recorded_delays, recorded_motion_ms

    normalized = normalize_recorded_delays(pts)
    if not normalized:
        return [], 0.0, 0.0
    duration = recorded_motion_ms(normalized)
    logger.info(
        f"human drag normalized: {len(pts)} pts, recorded wall {float(events[-1]['dt']):.0f}ms "
        f"→ motion {duration:.0f}ms"
    )
    return normalized, distance, duration
