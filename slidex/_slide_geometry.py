"""滑动距离与录制轨迹的单一语义：相对位移像素。"""

from __future__ import annotations

import math

from typing import Any, Dict, List, Optional, Sequence


# ── AliyunCaptcha.js 2.0 拼图几何（zchat.z.ai 实测 2026-11-02）───────────────────
# 新一代 AliyunCaptcha.js 弹窗拼图（DOM 前缀 aliyunCaptcha-*，场景如 z.ai 登录
# 1nl1cfkp）的拼图块 CSS left 不是滑块位移的 1:1 投影，而是一条二次曲线：
#
#     piece_left = A·knob² + B·knob
#
# 其中 knob 为 `#aliyunCaptcha-sliding-slider` 的 computed left（= 指针相对位移，
# 0..260 夹紧，轨道 300 − 滑块 40）。两次独立标定 g(200)=157.396 完全吻合，
# 并经 5 组观测与一次成功验证（knob=235 → piece=214.142）复核。
# 服务器判定用的是释放时刻拼图块的 left（非滑块位置），因此把缺口位置换算成
# 滑块行程必须用下面的反函数；滑块在大幅/回退跳变时会滞后，终点要以小步逼近。
ALIYUN_JIGSAW_PIECE_A = 0.00355029
ALIYUN_JIGSAW_PIECE_B = 0.076924


def aliyun_jigsaw_piece_left(knob: float) -> float:
    """滑块位移（computed left, px）→ 拼图块 CSS left（px）。"""
    k = max(0.0, float(knob))
    return ALIYUN_JIGSAW_PIECE_A * k * k + ALIYUN_JIGSAW_PIECE_B * k


def aliyun_jigsaw_knob_for_piece(piece_left: float) -> float:
    """拼图块目标 CSS left（px）→ 所需滑块位移（px）。

    二次方程 A·k² + B·k = L 的正根；L<=0 返回 0。调用方需再按
    轨道行程（track − 滑块宽度，实测 260）夹紧。
    """
    target = float(piece_left)
    if target <= 0:
        return 0.0
    a = ALIYUN_JIGSAW_PIECE_A
    b = ALIYUN_JIGSAW_PIECE_B
    return (-b + math.sqrt(b * b + 4.0 * a * target)) / (2.0 * a)


def clamp_travel(distance: float, max_travel: Optional[float]) -> float:
    """缺口行程用 JS 轨道可滑动最大值夹紧，JS 本身不是缺口位置。"""
    if distance is None:
        return 0.0
    value = float(distance)
    if value <= 0:
        return 0.0
    if max_travel is not None and max_travel > 0:
        return min(value, float(max_travel))
    return value


def scale_recorded_points(
    points: Sequence[Sequence[float]],
    recorded_distance: float,
    target_distance: float,
    tolerance: float = 0.10,
) -> List[List[float]]:
    """录制轨迹是相对位移。距离差超过 tolerance 时按比例缩放 x（保留 y 与 delay）。"""
    scaled: List[List[float]] = []
    rec = float(recorded_distance or 0.0)
    target = float(target_distance or 0.0)
    factor = 1.0
    if rec > 0 and target > 0 and abs(rec - target) / max(target, 1.0) > tolerance:
        factor = target / rec
    for point in points:
        if not point:
            continue
        x = float(point[0]) * factor
        y = float(point[1]) if len(point) > 1 else 0.0
        delay = float(point[2]) if len(point) > 2 else 0.0
        scaled.append([x, y, delay])
    return scaled


def points_from_recorded(
    recorded: Optional[Dict[str, Any]],
    target_distance: float,
    tolerance: float = 0.10,
) -> Optional[List[List[float]]]:
    if not recorded:
        return None
    raw = recorded.get("points")
    if not raw:
        return None
    rec_dist = recorded.get("distance", target_distance)
    return scale_recorded_points(raw, rec_dist, target_distance, tolerance)


def _x_monotonic_forward(points: Sequence[Sequence[float]]) -> bool:
    """x 是否单调不降（允许 <=1px 的采样抖动）。人类单向拖动必然如此。"""
    prev = float(points[0][0])
    for point in points[1:]:
        x = float(point[0])
        if x < prev - 1.0:
            return False
        prev = x
    return True


def upgrade_points_to_cumulative(
    points: Sequence[Sequence[float]],
    distance: float,
    tolerance: float = 2.0,
) -> List[List[float]]:
    """Lift legacy adjacent-diff traces to origin-relative cumulative x/y.

    Decision uses only last-x vs ``distance`` and sum(x) vs ``distance``.
    Mid-trace ``|x|`` is never a classifier (those values collide for both
    encodings) — for exactly that reason a corrupted absolute-coordinate
    trace can slip through here judged as cumulative. Callers that replay
    pool recordings must gate on ``recorded_geometry_verdict`` first; the
    pool does so via ``recorded_trace_verdict``, which decodes first so a
    legacy delta file is judged on the geometry it actually replays.
    """
    if not points:
        return []
    parsed: List[List[float]] = []
    for point in points:
        if not point:
            continue
        x = float(point[0])
        y = float(point[1]) if len(point) > 1 else 0.0
        delay = float(point[2]) if len(point) > 2 else 0.0
        parsed.append([x, y, delay])
    if not parsed:
        return []
    dist = float(distance or 0.0)
    tol = max(float(tolerance), 0.02 * abs(dist) if dist else float(tolerance))
    # 末点 x 已对上 distance 且 x 轴单调 → 不必再看 sum(x)。回退是人类的常态
    # （到点后小幅回校），而 sum(x)≈distance 恰是绝对坐标轨迹的典型特征：线上
    # trajectory_1001 的 sum=73636、dist=258，若用 sum 判增量编码，回放时会把它
    # 当位移累加成一串飞点。两个判据并存时，优先信"末点对得上且单调"。
    if abs(parsed[-1][0] - dist) <= tol and _x_monotonic_forward(parsed):
        return parsed
    last_x = parsed[-1][0]
    sum_x = sum(p[0] for p in parsed)
    if abs(last_x - dist) <= tol:
        return parsed
    if abs(sum_x - dist) <= tol:
        cx = 0.0
        cy = 0.0
        out: List[List[float]] = []
        for x, y, delay in parsed:
            cx += x
            cy += y
            out.append([cx, cy, delay])
        return out
    return parsed


def recorded_trace_verdict(
    points: Sequence[Sequence[float]],
    distance: Optional[float] = None,
) -> Optional[str]:
    """池内录制判定：先按编码解码，再判几何。

    池里同时存在历史增量编码（旧版本写入）与累计编码（0.10.x 起所有生产
    者）。直接判原始点列会把一条健康的历史增量录制误判成
    ``distance_mismatch``/``jump``；而只解码不判又会放过绝对坐标垃圾
    （这一类 sum(x) 与 distance 差得很远，解码是恒等变换）。所以两步都做。
    """
    try:
        decoded = upgrade_points_to_cumulative(points or [], distance)
    except (TypeError, ValueError, IndexError):
        return "invalid_points"
    return recorded_geometry_verdict(decoded, distance)


# ── 录制轨迹的时序归一化（0.10.2，密度上限 0.11.0）─────────────────────
# 录制器记的是「用户从按下到松开的世界时钟」：CDP 真机模式下人是在远端画面里
# 拖的，一次手动救场拖动跨 13-48s 很常见（线上池 50 条全部如此，单点间隙最大
# 1.24s）。回放侧只缩放 x、原样保留 delay，于是自动拖动变成十几秒的走走停停
# ——用户侧看到的就是"轨迹很卡"。
#
# 几何（形状、终点、过冲）是录制最值钱的部分，必须原样保留；只有时间轴需要
# 回到生成器同一设计带（~0.6-0.8s 的纯位移段）。规则：
#   1. 死停顿折叠：连续同坐标之间的间隙不是"移动"，压到 ≤ DEAD_HOLD_MAX_MS
#      （保留停顿存在，去掉落体感冻结的 1s+ 空档）
#   2. 慢录制压缩：纯位移段超出设计带时整体按比例压缩（只压缩不放慢）
#   3. 单点间隙上限：归一化后仍超过 GAP_MAX_MS 的一律夹紧
#   4. 采样密度上限（0.11.0）：压缩把整条时间轴等比缩到 650ms 而点数不变，
#      采样率被同比例放大——线上 234 点 / 48.4s 的录制压缩后是 360 events/s
#      （真人约 5Hz，池内其余 49 条 p50 70Hz）。压缩后按 GAP_MIN_MS
#      （8ms ⇒ ≤125Hz）抽稀：保首点（press-hold）与末点（释放位），被丢点的
#      间隙累加到下一个保留点，总位移时长与终点不变。
# 归一化幂等：二次调用不再改动。
#
# 阈值可经 env 覆盖（按站点节奏微调，无需发版）：
#   SLIDEX_RECORDED_MOTION_MS / _TOLERANCE / _GAP_MAX_MS / _DEAD_HOLD_MS /
#   _PRESS_HOLD_MS / _GAP_MIN_MS
RECORDED_MOTION_TARGET_MS = 650.0
RECORDED_MOTION_BAND_TOLERANCE = 1.15
RECORDED_GAP_MAX_MS = 120.0
RECORDED_DEAD_HOLD_MAX_MS = 40.0
RECORDED_PRESS_HOLD_MAX_MS = 1200.0
RECORDED_GAP_MIN_MS = 8.0


def _recorded_knob(name: str, default: float) -> float:
    """读 ``SLIDEX_RECORDED_<NAME>`` 覆盖；非法值回落默认（_env 统一容错）。"""
    from slidex._env import env_float

    return env_float(f"SLIDEX_RECORDED_{name}", default, minimum=0.0)


def recorded_motion_ms(points: Sequence[Sequence[float]]) -> float:
    """纯位移段时间：只计"位置真的变了"的点所带的间隙（死停顿不计）。

    首点是按下到第一次采样的间隔（press-hold），不是位移，不计入。
    """
    total = 0.0
    prev_x = prev_y = None
    for idx, point in enumerate(points):
        if not point:
            continue
        x = float(point[0])
        y = float(point[1]) if len(point) > 1 else 0.0
        gap = float(point[2]) if len(point) > 2 else 0.0
        moved = prev_x is None or abs(x - prev_x) > 1e-6 or abs(y - prev_y) > 1e-6
        if idx > 0 and moved:
            total += gap
        prev_x, prev_y = x, y
    return total


def normalize_recorded_delays(
    points: Sequence[Sequence[float]],
    target_total_ms: Optional[float] = None,
    tolerance: Optional[float] = None,
    gap_max_ms: Optional[float] = None,
    dead_hold_max_ms: Optional[float] = None,
    press_hold_max_ms: Optional[float] = None,
    gap_min_ms: Optional[float] = None,
) -> List[List[float]]:
    """把录制轨迹的时间轴拉回设计带（几何不动）。返回新列表，不改入参。

    只有纯位移段参与压缩判据：首点（按下停顿）单独夹到 press-hold 上限，
    同坐标的死停顿夹到 dead-hold 上限。录制已经够快（纯位移段 <=
    target*tolerance）时保持原节奏，只做间隙夹紧；慢录制整体压缩。
    没有真实位移时返回空列表（调用方当次弃用该录制）。

    压缩后按 ``gap_min_ms`` 抽稀采样密度：保首末点，丢点间隙累加到下一个
    保留点，总位移时长与终点不变。

    各阈值参数为 ``None`` 时读 ``SLIDEX_RECORDED_*`` env，仍无则用模块默认。

    归一化幂等：二次调用不再改动。
    """
    if not points:
        return []
    parsed: List[List[float]] = []
    for point in points:
        if not point:
            continue
        parsed.append([
            float(point[0]),
            float(point[1]) if len(point) > 1 else 0.0,
            max(0.0, float(point[2]) if len(point) > 2 else 0.0),
        ])
    if not parsed:
        return []

    # 阈值解析：显式实参优先（严格 > 0），其次 env 覆盖，最后模块默认。
    def _pick(value: Optional[float], name: str, default: float, minimum: float) -> float:
        if value is not None and float(value) > 0:
            return float(value)
        return max(minimum, _recorded_knob(name, default))

    def _pick_opt(value: Optional[float], name: str, default: float) -> float:
        if value is not None:
            return max(0.0, float(value))
        return max(0.0, _recorded_knob(name, default))

    target = _pick(target_total_ms, "MOTION_MS", RECORDED_MOTION_TARGET_MS, 1.0)
    tol = _pick(tolerance, "TOLERANCE", RECORDED_MOTION_BAND_TOLERANCE, 1.0)
    cap = _pick(gap_max_ms, "GAP_MAX_MS", RECORDED_GAP_MAX_MS, 1.0)
    dead = _pick_opt(dead_hold_max_ms, "DEAD_HOLD_MS", RECORDED_DEAD_HOLD_MAX_MS)
    press = _pick(press_hold_max_ms, "PRESS_HOLD_MS", RECORDED_PRESS_HOLD_MAX_MS, 1.0)
    floor = _pick_opt(gap_min_ms, "GAP_MIN_MS", RECORDED_GAP_MIN_MS)

    # 1) 首点 = 按下停顿；其余点里同坐标的间隙是死停顿
    parsed[0][2] = min(parsed[0][2], press)
    prev_x, prev_y = parsed[0][0], parsed[0][1]
    for idx in range(1, len(parsed)):
        x, y, gap = parsed[idx]
        if abs(x - prev_x) <= 1e-6 and abs(y - prev_y) <= 1e-6:
            parsed[idx][2] = min(gap, dead)
        prev_x, prev_y = x, y

    motion = recorded_motion_ms(parsed)
    if motion <= 0:
        return []

    # 2) 慢录制压缩到设计带（只压缩，不放慢）
    if motion > target * tol:
        scale = target / motion
        for idx in range(1, len(parsed)):
            parsed[idx][2] *= scale

    # 3) 单点间隙上限（首点的 press-hold 不受这道夹）
    for idx in range(1, len(parsed)):
        parsed[idx][2] = min(parsed[idx][2], cap)

    # 4) 采样密度上限：按 floor 换算该时长能容纳的点数，超出则贪心合并——
    #    相邻保留点的时间间隔 >= floor（丢点间隙累加到下一个保留点），
    #    首点（press-hold）与末点（释放位）必留。末尾不足 floor 的残余并回
    #    最后一个保留点：它原本是死停顿被压掉的碎间隙，若单独成点既会拉出
    #    一个 < floor 的位移间隙、也会让点数比上限多一。总位移时长、终点
    #    几何都不变（残余点与末点同坐标时，并回末点不增加位移时长）。
    if floor > 0 and len(parsed) > 2:
        span = recorded_motion_ms(parsed)
        if span > 0 and int(span / floor) + 1 < len(parsed):
            kept: List[List[float]] = [parsed[0]]
            acc = 0.0
            last = len(parsed) - 1
            for idx in range(1, len(parsed)):
                acc += parsed[idx][2]
                if idx != last and acc < floor - 1e-9:
                    continue
                x, y = parsed[idx][0], parsed[idx][1]
                if abs(x - kept[-1][0]) <= 1e-6 and abs(y - kept[-1][1]) <= 1e-6:
                    # 被抽掉的点与上一保留点同坐标 = 死停顿（用户中途停手）。
                    # 它的时间累加后可能超过 dead，二次归一化会再夹一次——
                    # 这里就按同一条规则收口，保持幂等。
                    kept[-1][2] = min(kept[-1][2] + acc, dead)
                else:
                    if idx == last and acc < floor - 1e-9 and len(kept) > 1:
                        # 尾部残余不足 floor：并回上一个保留点，避免碎帧。
                        acc += kept.pop()[2]
                    kept.append([x, y, acc])
                acc = 0.0
            parsed = kept

    # 5) 抽稀改变不了纯位移总时长（丢点的间隙被保留点接手），但若尾部残余被
    #    并回末点，位移间隙可能超过设计带——再压一次（只压缩，不改点数/端点）。
    motion = recorded_motion_ms(parsed)
    if motion > target * tol:
        scale = target / motion
        for idx in range(1, len(parsed)):
            parsed[idx][2] *= scale
    return parsed


# ── 录制轨迹的坐标几何校验（0.11.1）─────────────────────────────────────
# 线上实证（1926782908，2026-11-11）：50 条池内录制里 10 条坐标轴坏掉——
#   trajectory_1000: 146 点 0→608，末点才 258（distance=258，绝对坐标混入）
#   trajectory_1001: 234 点、单点间隙 48400ms（墙钟）混进 650ms 位移段
#   957/958/989-992: 峰值回退 131-254px（双次编码震荡）
#   997/998: 单次跳变 220px（漏采样，回放=瞬移）
# 回放侧 upgrade_points_to_cumulative 用 sum(x)≈distance 判"增量编码"。这两条
# 录制实测 sum(x)=46892(1000)/73636(1001)，离 distance 很远，legacy 分类器靠
# last_x==distance 就放行了——坏的不是编码方式，是几何本身（0→258 的行程里
# 混着冲到 608/545 的绝对坐标再弹回）。归一化只调时间轴不碰几何，这类录制必须
# **在写池时**拒收：宁可少一条，不可让一条坏录制毒化后续所有自动拖动。
# 加载侧复用同一条规则挡存量脏文件。
#
# 阈值来自线上 50 条盲测（见 tests/test_recorded_geometry_verdict.py）：
#   健康 40 条：max 单步跳变 62px/258 = 24%，回退 <=1px，max|x| <= 259
#   脏 10 条：  max 单步跳变 >=131px = 51%，回退 131-350px，max|x| >= 270
# 分界取 35% 与 1.15，两边都有 10% 以上的安全余量。
RECORDED_JUMP_FRACTION = 0.35           # 单步跳变（以及峰值回退）/ 行程；> 判 jump / non_monotonic
RECORDED_RETRO_RATIO = RECORDED_JUMP_FRACTION   # 别名：回退与跳变共用此线，保留旧名字给外部引用
RECORDED_SPAN_RATIO = 1.15              # max|x| / distance；> 判 overshoot


def recorded_geometry_verdict(
    points: Sequence[Sequence[float]],
    distance: Optional[float] = None,
) -> Optional[str]:
    """一串录制点是否真是"一次人类滑动"：合法返回 None，否则返回原因码。

    点语义：``[dx, dy, delay_ms]``，相对按下的位置；末点是松键位置。

    - ``no_points`` / ``no_motion``：空、或全部同坐标（点一下就松手）。
    - ``distance_mismatch``：末点 x 与声明的 distance 对不上。说明 points 与
      distance 来自两套坐标系——远端面板上报绝对坐标时，客户端会把事件 x
      直接写进 points，却把"松手 x − 按下 x"写成 distance。
    - ``overshoot``：max|x| 超过 distance 的 RECORDED_SPAN_RATIO。人类按在
      0、松在 distance，过冲只有几像素。
    - ``non_monotonic``：从某处峰值回退超过 distance * RECORDED_RETRO_RATIO
      （混入了绝对坐标 / 面板坐标，轨迹先冲出去再跳回来）。
    - ``jump``：相邻两点位移超过 distance * RECORDED_JUMP_FRACTION，是拼接
      出来的轨迹（漏采样 / 双次编码）。
    - ``invalid_clock``：负间隙的位移时间戳，录制时钟不可信。
    - ``invalid_points``：坐标/时间戳不是有限数值（``null``、字符串、NaN、
      Infinity）。上游 JS 用 ``JSON.stringify`` 序列化，NaN 会变成 ``null``，
      所以这一码在真实录制里是能出现的——必须拒收，而不是让 NaN 穿过
      全部比较（NaN 参与的大小判断恒为 False）污染池子。

    只看 x 轴：滑块是水平拖动，y 的抖动是手臂自然颤动，不约束。
    """
    rows: List[List[float]] = []
    for point in points or []:
        if not point:
            continue
        try:
            x = float(point[0])
            y = float(point[1]) if len(point) > 1 else 0.0
            t = float(point[2]) if len(point) > 2 else 0.0
        except (TypeError, ValueError, IndexError):
            return "invalid_points"
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(t)):
            return "invalid_points"
        rows.append([x, y, t])
    if len(rows) < 2:
        return "no_points"
    xs = [row[0] for row in rows]
    span = max(xs) - min(xs)
    if span <= 1e-6:
        return "no_motion"
    for idx in range(1, len(rows)):
        if rows[idx][2] < 0:
            return "invalid_clock"
    limit = span
    if distance is not None:
        try:
            dist = abs(float(distance))
        except (TypeError, ValueError):
            return "invalid_points"
        if not math.isfinite(dist):
            # NaN 参与任何比较恒为 False，若在这里放行，这个文件会永久
            # 绕过全部几何检查（实测：整条 NaN 轨迹被判为健康并入库）。
            return "invalid_points"
        if dist > 1e-6:
            if abs(xs[-1] - dist) > max(1.5, dist * 0.15):
                return "distance_mismatch"
            if span > dist * RECORDED_SPAN_RATIO:
                return "overshoot"
            limit = max(dist, span) * RECORDED_JUMP_FRACTION
    peak = xs[0]
    retro = 0.0
    for x in xs[1:]:
        if x >= peak:
            peak = x
        else:
            retro = max(retro, peak - x)
    if retro > limit:
        return "non_monotonic"
    biggest = max(abs(xs[i] - xs[i - 1]) for i in range(1, len(xs)))
    if biggest > limit:
        return "jump"
    return None
