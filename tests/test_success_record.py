"""0.6.29: 成功链路记录（schema v1）— 每次通过风控的指纹链路快照。

钉住：
- build_success_record 的 schema 契约（六大块齐全、v1 版本、指纹/票据/环境字段）
- capture_fingerprint：活页面补采、失败返回 None（绝不影响求解）
- persist_success_record：successes.jsonl append-only、单行合法 JSON
- solver._maybe_record_success：成功挂 success_record + 并入遥测摘要 + 落盘；
  二次调用不重复；失败路径不产记录
- 五个成功出口的 outcome 标记（源码契约，防回归删除）
"""
import json

import pytest

from slidex.config import SlidexConfig
from slidex.solver import SliderSolver
from slidex._success_record import (
    SCHEMA_VERSION,
    FINGERPRINT_AUDIT_JS,
    build_success_record,
    capture_egress_ip,
    capture_fingerprint,
    persist_success_record,
)


FP = {
    "ua": "Mozilla/5.0 X11; Linux x86_64 Chrome/154",
    "brands": "Google Chrome 154",
    "glRenderer": "ANGLE (Mesa LLVMpipe)",
    "fonts": "SimSun|Noto Sans CJK SC",
    "timezone": "Asia/Shanghai",
    "hwConcurrency": 4,
    "webdriver": False,
}


class _FakePage:
    def __init__(self, info=None, raise_eval=False):
        self._info = info if info is not None else dict(FP)
        self._raise = raise_eval
        self.eval_calls = 0

    async def evaluate(self, js, *a, **kw):
        self.eval_calls += 1
        if self._raise:
            raise RuntimeError("page gone")
        return self._info


# ---------- capture_fingerprint ----------

@pytest.mark.asyncio
async def test_capture_fingerprint_returns_dict():
    page = _FakePage()
    info = await capture_fingerprint(page)
    assert info["ua"] == FP["ua"]
    assert page.eval_calls == 1


@pytest.mark.asyncio
async def test_capture_fingerprint_none_on_failure():
    assert await capture_fingerprint(None) is None
    assert await capture_fingerprint(_FakePage(raise_eval=True)) is None
    assert await capture_fingerprint(_FakePage(info="not-a-dict")) is None


# ---------- build_success_record ----------

class _FakeSolver:
    cookie_id = "acct_1"
    pure_user_id = "acct_1"
    _telemetry_run_id = "run-abc"
    _telemetry_summary = {"distance": 258, "distance_source": "scale_full_travel",
                          "provider_name": "aliyun-nocaptcha", "slide_code": 0}
    _telemetry_events = [{"event": "x5sec_settled", "source": "bx_header"}]
    _bx_voucher = "x5sec=abc; Path=/"
    _is_cdp_mode = True
    _solve_mode = "cdp"
    automation_backend = None
    browser_channel = None
    headless = False
    proxy = {"server": "http://x"}
    trajectory_mode = "auto"


def test_build_success_record_schema_v1():
    rec = build_success_record(
        _FakeSolver(), "voucher_harvest", {"unb": "1", "x5sec": "t" * 100},
        dict(FP), duration_s=12.34, extra={"archetype": "pause_hold"},
    )
    assert rec["schema_version"] == SCHEMA_VERSION == 1
    for key in ("run_id", "cookie_id", "ts", "duration_s", "outcome",
                "slide", "voucher", "fingerprint", "environment", "extra"):
        assert key in rec, key
    assert rec["outcome"] == "voucher_harvest"
    assert rec["duration_s"] == 12.34
    assert rec["slide"]["provider_name"] == "aliyun-nocaptcha"
    assert rec["voucher"] == {"bx_voucher_present": True, "x5sec_len": 100,
                              "settle_source": "bx_header"}
    assert rec["fingerprint"]["glRenderer"].startswith("ANGLE")
    env = rec["environment"]
    assert env["mode"] == "cdp"
    assert env["backend"] == "playwright"  # CDP 模式 _init_browser 不跑，回退 playwright
    assert env["proxy_enabled"] is True
    assert env["slidex_version"]
    assert rec["extra"]["archetype"] == "pause_hold"


def test_build_success_record_container_mode_defaults():
    s = _FakeSolver()
    s._is_cdp_mode = False
    s._solve_mode = "browser"
    s.automation_backend = "patchright"
    s.browser_channel = "chrome"
    rec = build_success_record(s, "provider_pass", {"x5sec": "v"}, {})
    env = rec["environment"]
    # mode 直接取 solver 的 _solve_mode：browser=容器自带浏览器 / cdp=用户真机 / playwright_page=调用方页面
    assert env["mode"] == "browser"
    assert env["backend"] == "patchright"
    assert env["channel"] == "chrome"
    assert rec["voucher"]["x5sec_len"] == 1
    assert rec["fingerprint"] == {}


def test_build_success_record_no_cookies():
    rec = build_success_record(_FakeSolver(), "jar_landed", None, dict(FP))
    assert rec["voucher"]["x5sec_len"] == 0


# ---------- 出口 IP 探针 ----------

class _FakeEgressPage:
    def __init__(self, text=None, raise_eval=False):
        self._text = text
        self._raise = raise_eval
        self.calls = []

    async def evaluate(self, js, *args, **kw):
        self.calls.append((js, args))
        if self._raise:
            raise RuntimeError("page gone")
        return self._text


@pytest.mark.asyncio
async def test_capture_egress_ip_parses_json():
    page = _FakeEgressPage(text='{"ip":"183.193.162.101"}\n')
    ip = await capture_egress_ip(page, "https://api.ipify.org?format=json")
    assert ip == "183.193.162.101"
    # URL 经 evaluate 参数传入，不拼进 JS 字符串
    assert page.calls[0][1] == ("https://api.ipify.org?format=json",)


@pytest.mark.asyncio
async def test_capture_egress_ip_plain_text_and_failures():
    assert await capture_egress_ip(_FakeEgressPage(text="1.2.3.4"), "http://x") == "1.2.3.4"
    assert await capture_egress_ip(_FakeEgressPage(raise_eval=True), "http://x") is None
    assert await capture_egress_ip(_FakeEgressPage(text=None), "http://x") is None
    assert await capture_egress_ip(None, "http://x") is None
    assert await capture_egress_ip(_FakeEgressPage(text="1.2.3.4"), "") is None
    assert await capture_egress_ip(_FakeEgressPage(text="   "), "http://x") is None


def test_build_success_record_egress_ip_and_settle_semantics():
    s = _FakeSolver()
    s._telemetry_events = []  # 无结算事件但票据在手 → already_in_jar
    rec = build_success_record(s, "voucher_harvest", {"x5sec": "v" * 10}, dict(FP), egress_ip="1.2.3.4")
    assert rec["environment"]["egress_ip"] == "1.2.3.4"
    assert rec["voucher"]["settle_source"] == "already_in_jar"
    # 有结算事件时不改写
    s2 = _FakeSolver()
    rec2 = build_success_record(s2, "provider_pass", {"x5sec": "v"}, {})
    assert rec2["voucher"]["settle_source"] == "bx_header"
    # 无票据时保持 None
    rec3 = build_success_record(s, "provider_pass", {}, {})
    assert rec3["voucher"]["settle_source"] is None


# ---------- persist_success_record ----------

def test_persist_success_record_appends_jsonl(tmp_path):
    rec = build_success_record(_FakeSolver(), "provider_pass", {"x5sec": "v"}, dict(FP))
    path = persist_success_record(tmp_path, rec)
    assert path and path.endswith("successes.jsonl")
    rec2 = build_success_record(_FakeSolver(), "jar_landed", {"x5sec": "w"}, {})
    persist_success_record(tmp_path, rec2)
    lines = (tmp_path / "successes.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["outcome"] == "provider_pass"
    assert parsed[1]["outcome"] == "jar_landed"
    assert all(p["schema_version"] == 1 for p in parsed)


def test_persist_success_record_swallows_errors(tmp_path, monkeypatch):
    # successes.jsonl 是目录等异常 → 返回 None，绝不抛出
    (tmp_path / "successes.jsonl").mkdir()
    assert persist_success_record(tmp_path, {"schema_version": 1}) is None


# ---------- solver._maybe_record_success ----------

def _mk_solver(tmp_path):
    s = SliderSolver(
        cookie_id="acct_1",
        cookies_str="a=1",
        headless=False,
        config=SlidexConfig(telemetry_dir=str(tmp_path / "telemetry")),
    )
    s.page = None  # 不启动浏览器；指纹走 _fingerprint_at_init 回退
    s._fingerprint_at_init = dict(FP)
    s._success_outcome = "voucher_harvest"
    s._success_extra = {"attempt": 2}
    return s


@pytest.mark.asyncio
async def test_solver_records_success_once(tmp_path):
    s = _mk_solver(tmp_path)
    await s._maybe_record_success(True, {"unb": "1", "x5sec": "v"})
    rec = s.success_record
    assert rec is not None
    assert rec["outcome"] == "voucher_harvest"
    assert rec["fingerprint"]["ua"] == FP["ua"]
    assert rec["extra"] == {"attempt": 2}
    assert s._telemetry_summary["success_record"] is rec
    jsonl = tmp_path / "telemetry" / "successes.jsonl"
    assert jsonl.exists()
    saved = json.loads(jsonl.read_text(encoding="utf-8").strip())
    assert saved["run_id"] == rec["run_id"]

    # 二次调用不重复（一次 solve 只产一条记录）
    await s._maybe_record_success(True, {"x5sec": "v2"})
    lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_summary_record_survives_finalize_telemetry(tmp_path):
    """0.6.30 回归：_finalize_telemetry 用布尔覆盖 summary["success"]，
    记录必须活在独立的 success_record 键下，两个通道共存。"""
    s = _mk_solver(tmp_path)
    await s._maybe_record_success(True, {"x5sec": "v"})
    s._finalize_telemetry(success=True, status="success", cookies={"x5sec": "v"})
    assert isinstance(s._telemetry_summary["success_record"], dict)
    assert s._telemetry_summary["success"] is True
    summary_file = tmp_path / "telemetry" / f"{s._telemetry_run_id}.json"
    saved = json.loads(summary_file.read_text(encoding="utf-8"))
    assert saved["success_record"]["outcome"] == "voucher_harvest"
    assert saved["success"] is True


@pytest.mark.asyncio
async def test_solver_skips_record_on_failure(tmp_path):
    s = _mk_solver(tmp_path)
    await s._maybe_record_success(False, None)
    assert s.success_record is None
    assert not (tmp_path / "telemetry" / "successes.jsonl").exists()


@pytest.mark.asyncio
async def test_solver_record_never_raises(tmp_path):
    s = _mk_solver(tmp_path)
    s._config = type("C", (), {"get_telemetry_dir": lambda self: None})()  # 坏配置
    await s._maybe_record_success(True, {"x5sec": "v"})
    assert s.success_record is not None  # 记录仍组装成功（仅落盘跳过）


@pytest.mark.asyncio
async def test_capture_egress_ip_default_url_when_env_unset(monkeypatch):
    """0.6.31：env 未设时探针默认 api.ipify.org（与主仓一致性门控同源同默认）。"""
    monkeypatch.delenv("XY_OUTBOUND_IP_PROBE_URL", raising=False)
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    solver = SliderSolver(cookie_id="acct_1", config=SlidexConfig(telemetry_dir=str(tmp)))
    page = _FakeEgressPage(text='{"ip":"9.9.9.9"}')
    solver.page = page
    solver._fingerprint_at_init = dict(FP)
    solver._success_outcome = "provider_pass"
    await solver._maybe_record_success(True, {"x5sec": "v"})
    assert solver.success_record["environment"]["egress_ip"] == "9.9.9.9"
    probe_args = [args for _, args in page.calls if args]  # 指纹 evaluate 无参，探针带 URL 参数
    assert probe_args and "api.ipify.org" in probe_args[0][0]


@pytest.mark.asyncio
async def test_capture_egress_ip_env_empty_disables(monkeypatch):
    monkeypatch.setenv("XY_OUTBOUND_IP_PROBE_URL", "")
    assert await capture_egress_ip(_FakeEgressPage(text="1.2.3.4"), "") is None


# ---------- 成功出口 outcome 标记（源码契约） ----------

def test_outcome_markers_present_in_solver():
    import inspect
    from slidex import solver as solver_module
    src = inspect.getsource(solver_module)
    for marker in (
        'self._success_outcome = "provider_pass"',
        'self._success_outcome = "legacy_pass"',
        'self._success_outcome = "voucher_harvest"',
        'self._success_outcome = "manual_pass_wait"',
        'self._success_outcome = "jar_landed"',
    ):
        assert marker in src, marker


def test_fingerprint_js_single_source():
    # solver 类属性与 _success_record 常量同源，防两份 JS 漂移
    assert SliderSolver._FINGERPRINT_AUDIT_JS is FINGERPRINT_AUDIT_JS
