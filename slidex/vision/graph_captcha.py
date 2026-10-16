"""字符图形验证码：识别 → 归一 → 填入 → 提交。

形态（阿里云 baxia 字符验证码，实测样本 150×50、4 位大写字母彩色字符）：
图里只有待识别字符本身，**没有点选目标、没有顺序语义**——识别出来填进输入框
即可。因此执行器只需"填 + 提交"，与文字点选（``point_select``）的执行器完全
不同，但**共用同一个识别引擎**（``ocr/llm.py``），这是两段式解耦的收益。

与点选的关键差异：
- 识别正确性是**离散**的：`o`/`0`、`l`/`1`、`Z`/`2` 混淆填错即整体失败，
  不像坐标偏差有容错——所以长度与字符集的校验比置信度更关键。
- 但**纠错方向必须由字符集决定**（见 ``_DIGIT_FORMS`` 处的实测教训）：
  在字母数字混合的字符集上把字母"纠"成数字，是把对的改成错的。
- 挑战生命周期是**一次性**的：填错就换图重来，不存在"点了一半"的状态。
  因此重试必须**换新截图**，绝不能拿同一个猜测重复提交（0.6.28 纪律）。
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from slidex._slide_result import is_punish_slide_code

# baxia 字符验证码常见长度。未指定长度时接受 4~6 位。
DEFAULT_EXPECTED_LENGTH = 4
LENGTH_RANGE = (4, 6)

# 易混字符。**绝不能无条件套用**——实测教训：baxia 的字符集是字母数字混合，
# Q 与 0、Z 与 2 都是合法字符。把字母"纠"成数字是一次抛硬币，字母本来是
# 对的，改完必然错：实测正确答案 AEQZ 被这套映射改写成了 "AE20"，由对变错。
# 正确做法是把折叠**受限于调用方声明的字符集**：只有渠道明确说不含字母时，
# 字母→数字才是纠错；反之若声明只有大写字母，则数字→字母才是纠错。
# 混合字符集（默认）两者都合法，任何折叠都只是破坏，因此一律不动。
_DIGIT_FORMS = {
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
    "G": "6",
}
# 反向折叠只能一对一：一个数字对应多个字母时（D→0 而 O、Q 也→0），反向
# 无解。显式标注出歧义位，不让 dict 构造顺序悄悄决定结果——那会让
# digits_only 与 upper_only 得出互不自洽的结论。
_REVERSE_AMBIGUOUS = {"0"}
_LETTER_FORMS = {
    digit: letter
    for digit, letter in (
        ("0", "O"), ("1", "I"), ("2", "Z"), ("5", "S"), ("6", "G"), ("8", "B"),
    )
}

# 字符集策略。
ALPHABET_ANY = "any"  # 字母数字混合（baxia 实测形态）：不做易混折叠
ALPHABET_DIGITS_ONLY = "digits_only"  # 只有数字：字母 → 数字是纠错
ALPHABET_UPPER_ONLY = "upper_only"  # 只有大写字母：数字 → 字母是纠错

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


@dataclass(frozen=True)
class GraphCaptchaChars:
    """一次字符识别的结果。"""

    text: str
    raw_text: str = ""
    confidence: float = 0.0
    #: 归一化做了什么改动，进遥测供失败对照。
    normalized_from: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.text)


@dataclass
class GraphCaptchaPlan:
    chars: Optional[GraphCaptchaChars] = None
    error_code: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error_code and self.chars is not None


def normalize_chars(text: str, *, alphabet: str = ALPHABET_ANY) -> Tuple[str, Optional[str]]:
    """把模型输出整理成可提交的字符序列。

    返回 ``(归一后, 归一前的原文)``；未做改动时第二项为 None。

    步骤：去分隔符/空白 → 统一大写（渠道字符集为大写）→ 按字符集做易混折叠。
    """
    raw = str(text or "").strip()
    if not raw:
        return "", None
    stripped = _NON_ALNUM.sub("", raw)
    if not stripped:
        return "", raw
    upper = stripped.upper()
    if alphabet == ALPHABET_ANY:
        # 混合字符集下 Q/0 都合法，折叠即破坏。只做大写化。
        return upper, (raw if upper != raw else None)
    if alphabet == ALPHABET_DIGITS_ONLY:
        mapped = "".join(_DIGIT_FORMS.get(ch, ch) for ch in upper)
    elif alphabet == ALPHABET_UPPER_ONLY:
        mapped = "".join(
            ch if ch in _REVERSE_AMBIGUOUS else _LETTER_FORMS.get(ch, ch)
            for ch in upper
        )
    else:
        mapped = upper
    return mapped, (raw if mapped != raw else None)


def plan_graph_chars(
    raw_text: str,
    *,
    expected_length: Optional[int] = None,
    alphabet: str = ALPHABET_ANY,
    min_length: int = LENGTH_RANGE[0],
    max_length: int = LENGTH_RANGE[1],
) -> GraphCaptchaPlan:
    """校验识别结果是否可提交。纯函数。

    **长度不符一律拒判**——长度不对说明漏识或多识了字，填进去必错；
    宁可换新图重识别，也不要提交一个已知可疑的答案。

    ``alphabet`` 决定是否做易混折叠，默认 ``ALPHABET_ANY``（不折叠），理由见
    ``_DIGIT_FORMS`` 上方的实测教训。渠道若能确定字符集再收紧。
    """
    notes: List[str] = []
    text, normalized_from = normalize_chars(raw_text, alphabet=alphabet)
    if not text:
        return GraphCaptchaPlan(error_code="no_chars_recognized", notes=notes)

    if expected_length is None:
        if not (min_length <= len(text) <= max_length):
            notes.append(f"length={len(text)} outside [{min_length},{max_length}]")
            return GraphCaptchaPlan(
                chars=GraphCaptchaChars(text=text, raw_text=raw_text, normalized_from=normalized_from),
                error_code="unexpected_char_count", notes=notes,
            )
    elif len(text) != expected_length:
        notes.append(f"length={len(text)} expected={expected_length}")
        return GraphCaptchaPlan(
            chars=GraphCaptchaChars(text=text, raw_text=raw_text, normalized_from=normalized_from),
            error_code="unexpected_char_count", notes=notes,
        )

    if normalized_from:
        notes.append(f"normalized_from={normalized_from}")
    return GraphCaptchaPlan(
        chars=GraphCaptchaChars(text=text, raw_text=raw_text, normalized_from=normalized_from),
        notes=notes,
    )


class GraphCaptchaExecutor:
    """把识别出的字符填入输入框并提交。

    填入用**逐字键入**（``type`` 带 delay），而不是一次性 ``fill``：
    真实用户是逐字敲的，整块填充在行为特征上与真人差异明显。
    """

    def __init__(
        self,
        *,
        keystroke_delay: Tuple[float, float] = (0.08, 0.22),
        pre_submit_delay_s: float = 0.3,
    ):
        self.keystroke_delay = keystroke_delay
        self.pre_submit_delay_s = pre_submit_delay_s

    async def _fill(self, locator: Any, text: str) -> None:
        await locator.click()
        await locator.fill("")
        for ch in text:
            await locator.type(ch, delay=random.uniform(*self.keystroke_delay) * 1000)

    async def execute(
        self,
        *,
        input_locator: Any,
        submit_locator: Optional[Any] = None,
        text: str = "",
        verdict_reader: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if not text:
            return {"filled": False, "error_code": "no_chars_to_submit"}
        try:
            await self._fill(input_locator, text)
        except Exception as exc:
            logger.warning(f"graph-captcha: fill failed: {exc}")
            return {"filled": False, "error_code": "fill_failed"}

        if submit_locator is not None:
            await asyncio.sleep(self.pre_submit_delay_s)
            try:
                await submit_locator.click()
            except Exception as exc:
                logger.warning(f"graph-captcha: submit failed: {exc}")
                return {"filled": True, "submitted": False, "error_code": "submit_failed"}

        verdict: Dict[str, Any] = {}
        if verdict_reader is not None:
            try:
                verdict = await verdict_reader()
            except Exception as exc:
                logger.warning(f"graph-captcha: verdict read failed: {exc}")
                verdict = {"error_code": "verdict_read_failed"}
        return {"filled": True, "submitted": submit_locator is not None,
                "verdict": verdict, "error_code": None}


def is_punish_graph_code(code: Any) -> bool:
    """字符验证码的惩罚码判定，复用滑块 300 语义（0.6.33 纪律）。"""
    return is_punish_slide_code(code)