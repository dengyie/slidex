"""点选指令解析 —— 保守语义的回归锁定。

核心不变量：**解析不出就返回 None，绝不猜**。点选判错一个字即整体失败，
漏认（返回 None → 上层重截图或降级人工）的代价远小于猜错（点上去直接判负）。
"""
import pytest

from slidex.ocr.instruction import MAX_TARGETS, parse_instruction


@pytest.mark.parametrize("text,expected", [
    ("请依次点击【的】【了】【我】", ("的", "了", "我")),
    ("依次点击的了我", ("的", "了", "我")),
    ("请依次点击：的、了、我", ("的", "了", "我")),
    ("请依次点击(的)(了)(我)", ("的", "了", "我")),
    ("请按顺序点击【中】【国】", ("中", "国")),
    ("点击【我】", ("我",)),
    ("请依次点击【Ａ】【Ｂ】", ("A", "B")),
    ("请依次点击【1】【2】", ("1", "2")),
])
def test_parses_expected_targets(text, expected):
    instruction = parse_instruction(text)
    assert instruction is not None, f"failed to parse: {text!r}"
    assert instruction.targets == expected


def test_residual_noise_is_not_treated_as_target():
    """剥完模板噪声后不应把「验证」「正确」等残留字吞成目标。"""
    instruction = parse_instruction("请依次点击【的】【了】【我】")
    assert instruction is not None
    for noise in ("请", "依次", "点击", "下面", "正确"):
        assert noise not in instruction.targets


def test_returns_none_on_garbage():
    """没有可解析目标时返回 None（不是空 Instruction）。"""
    for text in ("", "   ", "请完成安全验证", "验证成功"):
        assert parse_instruction(text) is None, text


def test_rejects_oversized_target_run():
    """目标数超上限视为「整句提示被当成目标序列」→ 拒绝，避免盲点。

    上限 MAX_TARGETS=8 是**合法**的（恰好 8 个必须放行），故用 9 个触发拒绝。
    """
    text = "".join("【%s】" % c for c in "的一是不了人我在有")
    assert len(text) > MAX_TARGETS * 2
    assert parse_instruction(text) is None


def test_accepts_exactly_max_targets():
    """恰好等于上限的目标数必须放行（off-by-one 回归锁定）。"""
    text = "".join("【%s】" % c for c in "的一是不了人我在")
    instruction = parse_instruction(text)
    assert instruction is not None, "8 targets must be accepted"
    assert len(instruction.targets) == MAX_TARGETS


def test_empty_instruction_is_falsy():
    """空目标不可为真——否则「解析失败」会被静默当成「无需点击」。"""
    from slidex.ocr.instruction import Instruction

    assert not Instruction(targets=())
    assert Instruction(targets=("的",))


def test_raw_and_origin_are_recorded():
    instruction = parse_instruction("请依次点击【的】【了】【我】")
    assert instruction is not None
    assert instruction.raw == "请依次点击【的】【了】【我】"
    assert instruction.origin in {"bracket", "bare", "mixed"}


def test_whitespace_and_separator_variants():
    for text in (
        "请依次点击 【的】 【了】 【我】",
        "请依次点击【的】，【了】，【我】",
        "请依次点击【的】、【了】、【我】。",
    ):
        instruction = parse_instruction(text)
        assert instruction is not None, text
        assert instruction.targets == ("的", "了", "我"), text