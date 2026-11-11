"""SLIDEX_TRAJ_POOL_STRATEGY=lru 接线：选轨跨会话按 last_used 轮换（0.11.7）。

2026-11-12 生产实证（旧实现，strategy 一直默认 lru 却无人消费）：
dist=258 的候选里 trajectory_959 两天被选 18 次（每个会话都从它开始），
同家族的 951/953/955/956 各只放过 2 次——同一条人类轨迹反复重放正是池
要避免的指纹。接线后：同距离候选按 (距离差, last_used 升序, 编号) 取
最久未用；未用过（last_used 无记录）先于用过的；其余策略维持旧行为。
peek（0.11.6）不写 last_used，探测不会打乱轮换节奏。
"""
import json
from pathlib import Path
import tempfile

from slidex._trajectory_pool import SliderTrajectoryPool

from tests.test_trajectory_pool_peek import _human_points


def _pool(specs, cookie="u"):
    """specs: [(distance, dy)]。dy 必须互不相同——同 distance 同 dy 的 payload
    会被 0.11.3 的幂等去重并成同一个文件，fixture 就退化了。"""
    tmp = tempfile.TemporaryDirectory()
    pool = SliderTrajectoryPool(tmp.name)
    files = []
    for d, dy in specs:
        fname = pool.save_trajectory(
            _human_points(d, dy=dy), cookie_id=cookie, distance=d,
            success=True, source="human_cdp",
        )
        assert fname, f"fixture 被几何闸门拒收: {(d, dy)}"
        files.append(fname)
    assert len(set(files)) == len(files), "fixture 撞上去重，请换 dy"
    return tmp, pool, files


def _name(rec):
    return Path(rec["_file"]).name


def test_lru_rotates_across_sessions():
    """三个同距离候选：连选三次不回头，第四次才轮回第一条。"""
    tmp, pool, files = _pool([(258, 0), (258, 1), (258, 2)])
    with tmp:
        picks = [_name(pool.load_unused_human("u", 258.0)) for _ in range(4)]
        assert picks[:3] == files, picks  # 互不重复，按编号起步
        assert picks[3] == files[0]  # 全部用过一轮后，最久未用的重回首选


def test_lru_prefers_never_used_over_used():
    tmp, pool, files = _pool([(258, 0), (258, 1)])
    with tmp:
        first = pool.load_unused_human("u", 258.0)  # 消费 files[0]，写 last_used
        assert _name(first) == files[0]
        second = pool.load_unused_human("u", 258.0)  # 新会话，不带 exclude
        assert _name(second) == files[1], "未用过的必须先于刚用过的"


def test_distance_still_dominates_over_lru():
    """LRU 只在距离匹配的候选内部轮换，不能为了轮换选远距离的。"""
    tmp, pool, files = _pool([(258, 0), (260, 0)])
    with tmp:
        for _ in range(3):
            rec = pool.load_unused_human("u", 258.0)
            assert _name(rec) == files[0], "距离差必须压过 last_used"


def test_non_lru_strategy_keeps_index_order():
    """显式非 lru 策略维持旧行为：同距离永远取最旧编号（确定性首选）。"""
    tmp, pool, files = _pool([(258, 0), (258, 1)])
    with tmp:
        pool.rotation_strategy = "oldest"
        assert _name(pool.load_unused_human("u", 258.0)) == files[0]
        # 消费过 files[0] 后（无 exclude），旧实现仍选它——这正是 959×18 的根源
        assert _name(pool.load_unused_human("u", 258.0)) == files[0]


def test_lru_without_last_used_file_behaves_deterministically():
    """last_used.json 缺失（新池）时退化为编号序，不抛异常。"""
    tmp, pool, files = _pool([(258, 0), (258, 1)])
    with tmp:
        pool._last_used_path("u").unlink(missing_ok=True)
        picks = [_name(pool.load_unused_human("u", 258.0)) for _ in range(2)]
        assert picks == files


def test_default_pool_strategy_is_lru():
    """旋钮默认值就是 lru：0.11.6 之前它从未被消费，这里钉住接线后的行为来源。"""
    with tempfile.TemporaryDirectory() as tmp:
        assert SliderTrajectoryPool(tmp).rotation_strategy == "lru"


# ── 0.11.8：策略校验 / 淘汰对账 / 原子写 ──

def test_apply_config_rejects_unknown_strategy():
    """拼错的策略值必须告警并回落 lru——0.11.7 起 strategy 是承重旋钮，
    静默走 legacy 分支会让「以为在轮换、实际重复回放」无日志可查。"""
    from types import SimpleNamespace
    from loguru import logger

    with tempfile.TemporaryDirectory() as tmp:
        pool = SliderTrajectoryPool(tmp)
        cap = []
        h = logger.add(lambda m: cap.append(m), level="WARNING")
        try:
            pool.apply_config(SimpleNamespace(
                trajectory_pool_rotation_strategy="lur",
                trajectory_pool_max_per_cookie=50,
                trajectory_pool_min_pool_size=5,
                trajectory_pool_recorded_only_mode=False,
                trajectory_pool_enabled=True,
            ))
        finally:
            logger.remove(h)
        assert pool.rotation_strategy == "lru"
        text = "".join(str(m) for m in cap)
        assert "SLIDEX_TRAJ_POOL_STRATEGY" in text and "lur" in text, text


def test_apply_config_normalizes_case():
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as tmp:
        pool = SliderTrajectoryPool(tmp)
        pool.apply_config(SimpleNamespace(trajectory_pool_rotation_strategy="  LRU "))
        assert pool.rotation_strategy == "lru"


def test_eviction_prunes_dead_last_used_keys():
    """淘汰后 last_used 不得残留指向已删文件的死键（含历史死键自愈）。

    注意淘汰时机：``_evict_overflow`` 在新录制落盘**之前**执行（幸存者 =
    max-1 条旧文件 + 新文件），所以新文件没有 last_used 键是正确的——
    last_used 只记「被消费过」的。不变式是 ``lu keys ⊆ live``。
    """
    tmp, pool, files = _pool([(258, i) for i in range(4)])
    with tmp:
        pool.max_per_cookie = 2
        # 逐条消费制造 last_used 账目
        for _ in range(4):
            pool.load_unused_human("u", 258.0)
        lu_path = Path(tmp.name) / "u" / "last_used.json"
        assert set(json.loads(lu_path.read_text())) == set(files)
        # 注入历史死键（指向不存在的 trajectory_999.json）
        lu = json.loads(lu_path.read_text())
        lu["trajectory_999.json"] = 1.0
        lu_path.write_text(json.dumps(lu))
        # 池满触发淘汰（新录制用不同 dy，避开幂等去重）
        pool.save_trajectory(_human_points(258, dy=99), "u", 258, True,
                             source="human_cdp")
        live = {p.name for p in Path(tmp.name, "u").glob("trajectory_*.json")}
        lu_after = json.loads(lu_path.read_text())
        assert not (set(lu_after) - live), (sorted(lu_after), sorted(live))
        assert "trajectory_999.json" not in lu_after
        assert lu_after  # 至少还有幸存者的账


def test_touch_leaves_no_tmp_file():
    """原子写不留 .tmp 残骸（半截 JSON 等于丢整本账）。"""
    tmp, pool, files = _pool([(258, 0)])
    with tmp:
        pool.load_unused_human("u", 258.0)
        leftovers = list(Path(tmp.name, "u").glob("*.tmp"))
        assert leftovers == []
        assert Path(tmp.name, "u", "last_used.json").exists()
