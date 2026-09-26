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
    extra_overshoot: provider 路径在点序之外追加过冲/回拖/手抖（与顺序路径
    对齐）；legacy 的轨迹已由 generate_trajectory(overshoot_back=True) 烘焙
    进点序，传 False 避免双重过冲。

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


async def dispatch_drag_timeline(session: Any, timeline: List[Tuple[float, Dict[str, Any]]]) -> None:
    """按时间线 gap 流水线派发 Input 事件（不等每个 ack）。

    顺序保证：task 依创建序启动，playwright 连接层同步入队写帧 →
    驱动层对 CDP 的 send 支持多飞行（pipelining）→ 浏览器按序、按
    设计间隔收到事件。
    """
    loop = asyncio.get_running_loop()
    tasks = []
    for gap_ms, params in timeline:
        if gap_ms > 0:
            await asyncio.sleep(gap_ms / 1000.0)
        tasks.append(asyncio.create_task(session.send("Input.dispatchMouseEvent", params)))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failed = [r for r in results if isinstance(r, BaseException)]
    if failed:
        # 页面中途关闭等场景：让 solve 流程按既有失败路径收尾，不在拖动层炸出
        logger.warning(f"drag timeline: {len(failed)}/{len(tasks)} dispatches failed: {failed[0]}")
