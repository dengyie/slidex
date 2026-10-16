"""点选指令解析：把「请依次点击【的】【了】【我】」这类提示变成有序字符列表。

阿里云点选验证码的提示语高度模板化，但渠道与版本会在分隔符、括号码、
语气词上摇摆。这里只做**保守**解析：宁可少认一个字符（返回
``error_code="instruction_unparsed"`` 让调用方重试或降级人工），也绝不
猜错一个字符——点选判错一个字符即整体失败，猜错的代价远大于漏认。

``Instruction`` 是有序目标序列。空序列视为解析失败而不是"无需点击"，
否则会把"提示识别失败"静默降级成"通过"，这是最危险的静默假通过路径。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# 指令里需要剥掉的模板噪声：这些是提示语自身的字，不是目标字符。
# 用**字面包含**判定而非逐字删除，避免把目标字（如「我」「你」）误伤。
_NOISE_TOKENS: Tuple[str, ...] = (
    "请依次点击", "请依次点选", "请按顺序点击", "请依次", "请完成", "请选择",
    "请点击", "请按顺序", "按顺序点击", "依次点击", "依次点选", "点选",
    "按顺序", "依次", "点击", "选择", "下面", "图中", "图片", "文字",
    "字符", "顺序", "从左到右", "从左至右", "正确的", "正确", "请先",
    "然后", "的汉字", "汉字", "个字", "请", "验证",
)

# 剥离后的残留语气助词。**不含「的」**——它既是助词也是点选验证码里最
# 高频的目标字（「请点击【的】」），剥掉会造成目标缺失。
_LEADING_FILLER = ("在", "把", "将", "中", "里", "上", "下", "为", "是")

# 单个字符目标：CJK 统一表意文字 / 平假名 / 片假名 / 谚文 / 全角半角字母数字。
# 全角落在 FF01-FF5E（含标点）；这里放宽到全角字母数字段，宁可多交给
# 后续字面匹配去判，也不让含全角的指令整个解析失败。
_ONE_CHAR = re.compile(
    r"["
    r"一-鿿"        # CJK 统一表意文字
    r"぀-ヿ"        # 平假名 / 片假名
    r"가-힯"        # 谚文
    r"A-Za-z"       # 半角字母
    r"０-９Ａ-Ｚａ-ｚ"  # 全角数字与字母
    r"]"
)

# 裸字序列里必须先剥掉的标点分隔符：「、，,;；」等。剥完仍要求全为单字。
_SEPARATORS = "、，,;；:：。．·"

# 裸字路径的**动词证据**：指令句必须含这些词之一，否则整句普通文案
# （「请完成安全验证」）会被误当成目标序列。
_BARE_REQUIRED_VERB = ("点击", "点选", "依次", "按顺序", "选择")

# 成对括号及其变体：捕获组保留内容，剥离括号本身。
_BRACKETED = re.compile(
    r"[\(\（\[【｛{〈《「『]+(.+?)[\)\）\]】｝}〉》」』]+",
    re.S,
)

# 未加括号的裸字序列，例如 "点击的了我" → 的了我。仅在整句剥完噪声后
# 剩下的部分**全部**是单字时才接受，避免误吞语气词。
_BRACKET_CHARS = "()（）[]【】｛｝{}〈〉《》「」『』"

# 指令解析后的最大目标字数。单字点选验证码不会超过 8；留出余量同时
# 挡住解析器把整句提示当成目标序列的情况（那会导致 N 次无意义点击）。
MAX_TARGETS = 8


@dataclass(frozen=True)
class Instruction:
    """一条已解析的点选指令。"""

    targets: Tuple[str, ...]
    #: 原始提示文本，供遥测与失败对照。
    raw: str = ""
    #: 解析路径，遥测用：bracket / bare / mixed / none。
    origin: str = "none"

    def __bool__(self) -> bool:
        return bool(self.targets)

    def __len__(self) -> int:
        return len(self.targets)


def _dedup_consecutive(text: str) -> str:
    """折叠连续重复字：模板常写「点击【的】的【我】」，噪声与目标边界会重复。"""
    out: List[str] = []
    for ch in text:
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)


def _strip_noise(text: str) -> Tuple[str, List[str], bool]:
    """剥模板噪声。

    返回 ``(残留文本, 括号组列表, 是否命中过噪声词)``。第三项是关键：裸字
    路径只在"看起来确实是指令"时才启用——否则「请完成安全验证」这类整句
    会因全为单字而被吞成 7 个目标，把**漏认**变成**猜错**，正是本模块要
    避免的方向。
    """
    # 括号内的内容一律视为目标，即使它恰好包含噪声词（如「请选择」
    # 作为目标字出现概率极低，误伤代价大于漏认）。
    boxed = [m.group(1) for m in _BRACKETED.finditer(text)]
    residual = _BRACKETED.sub(" ", text)
    matched_noise = False
    for token in _NOISE_TOKENS:
        if token in residual:
            matched_noise = True
            residual = residual.replace(token, " ")
    return residual, boxed, matched_noise


def _normalize_target(text: str) -> str:
    """全角 ASCII → 半角。与规划器 ``_normalize_chars`` 同口径，
    否则指令目标字（Ａ）与图中字框（A）对不上，永远匹配不到。"""
    out: List[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _targets_from_bracket_group(group: str) -> List[str]:
    """从一个括号组里取目标字：组内通常只有 1 个字，偶有「的了」这种双字。"""
    cleaned = "".join(ch for ch in group if not ch.isspace())
    if not cleaned:
        return []
    # 组内全为单字时逐字取；否则整组当一个目标（例如「AB」是一个词组）。
    if all(_ONE_CHAR.match(ch) for ch in cleaned):
        return list(cleaned)
    return [cleaned]


def parse_instruction(text: str) -> Optional[Instruction]:
    """解析点选提示。解析不出有序目标返回 None。

    返回 None 而非空 Instruction：调用方据此走"重截图 / 降级人工"，
    绝不能把空序列当成"没有目标因而无需点击"。
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    residual, boxed, matched_noise = _strip_noise(raw)

    targets: List[str] = []
    order: List[str] = []

    # 优先采信括号组（模板里目标字几乎总是被括起来）。
    for group in boxed:
        for ch in _targets_from_bracket_group(group):
            targets.append(ch)
            order.append("bracket")

    # 括号里没有目标时，退回裸字序列。判据**严格**（三条全须满足）：
    #  ①剥过噪声词（证明这确实是指令句而非普通文案）；
    #  ②原文含明确的点选动词（点击/点选/依次/按顺序/选择）——
    #     这一条挡住「请完成安全验证」：它剥掉「请完成」后残留「安全」，
    #     但从未出现点选动词，不能被当成目标序列；
    #  ③残留部分剥掉分隔符与语气助词后全是单字，且不超上限。
    if not targets and matched_noise and any(v in raw for v in _BARE_REQUIRED_VERB):
        bare = "".join(
            ch for ch in residual
            if not ch.isspace() and ch not in _BRACKET_CHARS and ch not in _SEPARATORS
        )
        bare = _dedup_consecutive(bare)
        bare = bare.strip("".join(_LEADING_FILLER) + " ")
        if bare and all(_ONE_CHAR.match(ch) for ch in bare) and len(bare) <= MAX_TARGETS:
            targets = list(bare)
            order = ["bare"] * len(targets)

    if not targets:
        return None
    # 目标过多几乎必然是把整句提示当成了目标序列。拒绝并让调用方
    # 重截图或降级，而不是盲点十几次把账号打上失败标记。
    if len(targets) > MAX_TARGETS:
        return None

    origin = "mixed" if len(set(order)) > 1 else (order[0] if order else "none")
    return Instruction(
        targets=tuple(_normalize_target(t) for t in targets),
        raw=raw,
        origin=origin,
    )