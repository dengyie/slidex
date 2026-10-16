"""多模态大模型点选识别：一次给出「点哪里」，不做字形拼接。

设计取舍（0.6.34）：点选验证码里"识别出是什么字"和"决定点哪里"由**同一个
模型一次完成**，而不是"先 OCR 出所有字框、再用规则代码做字形匹配聚类"。
后者在字符粘连时字框会碎成无法合并的碎片，规则很难兜住；而前者由模型在
语义层面直接给出目标坐标。

坐标一律用 **0..1 归一化**（相对整张图）返回，而非像素：截图与页面元素之间
可能有 devicePixelRatio 缩放、元素自身 CSS 尺寸与实际渲染尺寸不一致等
偏差，坐标换算只应由执行器按元素 bounding box 做一次（见
``slidex.vision.point_select``）。

传输层用标准库 urllib：slidex 核心依赖刻意保持轻（opencv/numpy/playwright/
psutil），不为单次 HTTP 调用引入 httpx/aiohttp。同步实现是刻意的——本模块
由 ``VisualChallengeSolver`` 的线程池隔离调用，不占事件循环。
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from slidex.ocr.instruction import Instruction
from slidex.ocr.models import OcrBox, OcrResult

# 请求模型的坐标输出。强调归一化与顺序，避免模型回像素或乱序。
_DEFAULT_PROMPT = (
    "你是一个点击验证码识别器。图中有一行提示，要求按顺序点击若干汉字。\n"
    "请找出提示中要求点击的每一个字，并按提示给出的顺序返回它们在图中的位置。\n"
    "严格只输出如下 JSON，不要任何解释或多余文字：\n"
    '{"targets":[{"char":"的","x":0.31,"y":0.52,"confidence":0.95}]}\n'
    "字段说明：char 是该位置上的汉字；x、y 是该字**中心点**在整张图中的归一化"
    "坐标，取值范围 0 到 1，左上角为 (0,0)、右下角为 (1,1)；confidence 是 0 到 1"
    "的置信度。targets 必须与提示要求的顺序完全一致，长度必须与提示要求的字数"
    "相同。若某个字找不到或看不清，把它的 confidence 设为 0，仍要保留该条目，"
    "不要缩短数组。"
)


# 字符图形验证码提示。刻意**不要求 JSON**：这类图只有几个字，纯文本直出比
# 结构化输出更稳——JSON 的括号/引号也要占 token，且模型偶发把内容截在
# "{" 之后（实测 max_tokens=100 时 3 次里 2 次如此）。
_CHARS_PROMPT = (
    "这是一张验证码图片。请识别出图中依次出现的所有字符。\n"
    "只输出这些字符本身，用大写字母和数字表示，不要任何解释、引号、空格或标点。\n"
    "逐个字符确认后再输出，不要漏字也不要多数字。"
)


def _was_truncated(body: Any) -> bool:
    """回答是否因 token 预算耗尽而中断。

    reasoning 模型的典型失败形态：``max_tokens`` 被 reasoning_tokens 吃光，
    正文一个字没吐出来（实测 max_tokens=64 时 reasoning=59、content 空）。
    这与"图没认清"必须分开——前者该重发，后者该换图。
    """
    if not isinstance(body, dict):
        return False
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    reason = choices[0].get("finish_reason")
    if reason == "length":
        return True
    if reason is None:
        return False
    # 网关不回 finish_reason 时（部分自建网关），用 usage 兜底判断。
    usage = _extract_usage(body)
    return bool(usage) and usage.get("completion_tokens", 0) <= 0


def _extract_usage(body: Any) -> Dict[str, int]:
    """取出 token 用量，顺带保留 reasoning_tokens。

    排查截断问题时 reasoning_tokens 是关键证据：只看 completion_tokens 看不出
    预算被谁吃掉了。
    """
    if not isinstance(body, dict):
        return {}
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return {}
    out: Dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            out[key] = value
    return out


@dataclass(frozen=True)
class LlmVisionConfig:
    """多模态通道配置。缺 endpoint 时构造出的 extractor 只会返回配置错误。"""

    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: float = 180.0
    prompt: str = _DEFAULT_PROMPT
    #: 低于该置信度视为「没认清」，交由执行器换通道重截图，而不是硬点。
    min_confidence: float = 0.35
    #: 保守的浏览器 UA。部分网关按 UA 拦非浏览器客户端（Cloudflare 1010）。
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    #: 网关路由会话头。部分 OpenAI 兼容网关要求该头才路由（缺失报
    #: MissingSession/400）。标准端点忽略未知头，留空则不发。
    session_header: str = ""
    session_header_name: str = "x-opencode-session"
    #: 字符识别专用提示（覆盖 point_select 的坐标提示）。
    chars_prompt: str = _CHARS_PROMPT
    #: 输出 token 上限。**刻意给到十万**：截断的代价是「答案一个字没吐」，
    #: 而正常识别只用百余 token，这个上限实际跑不到。调低才是风险——GLM 系列
    #: 是 reasoning 模型，会先花 reasoning_tokens 再给答案（实测 max_tokens=64
    #: 时 reasoning 吃掉 59，正文只剩 5）。
    max_tokens: int = 100_000
    #: 网关层超时（HTTP 524）的重试次数。524 是 Cloudflare 等网关的**超时**，
    #: 不是模型失败——重发通常就命中已预热的连接。给到 3 次：单次响应可能几十秒，
    #: 但重发总比整个挑战失败划算。重试不做退避——退避会让整体超时更早到来。
    retry_on_gateway_timeout: int = 3

    @classmethod
    def from_config(cls, config: Any = None) -> "LlmVisionConfig":
        """从 SlidexConfig 取值——env 解析只有 config 一处事实源。

        0.6.34 修正：本类曾自己读 SLIDEX_VISION_*，与 SlidexConfig 的同名字段
        重复，改一个默认值要改两处。解析统一收口到 SlidexConfig.from_env()。
        """
        if config is None:
            from slidex.config import SlidexConfig

            config = SlidexConfig.from_env()
        return cls(
            endpoint=str(getattr(config, "vision_endpoint", "") or "").strip(),
            api_key=str(getattr(config, "vision_api_key", "") or "").strip(),
            model=str(getattr(config, "vision_model", "") or "").strip(),
            timeout_s=_safe_float(getattr(config, "vision_timeout_s", None), 180.0),
            min_confidence=_safe_float(getattr(config, "vision_min_confidence", None), 0.35),
            max_tokens=_safe_int(getattr(config, "vision_max_tokens", None), 100_000),
            retry_on_gateway_timeout=_safe_int(
                getattr(config, "vision_gateway_retries", None), 3
            ),
        )

    @classmethod
    def from_env(cls) -> "LlmVisionConfig":
        return cls.from_config(None)

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.model)


def _safe_float(raw: Any, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _safe_int(raw: Any, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    # 负数对两个调用方都无意义（max_tokens、重试次数），一律回退默认，
    # 不让一个手滑的 env 值把重试关掉。
    return value if value >= 0 else default


def _load_image_bytes(image_bytes: Optional[bytes], image_path: Optional[Path]) -> Optional[bytes]:
    if image_bytes:
        return image_bytes
    if image_path:
        try:
            return Path(image_path).read_bytes()
        except OSError:
            return None
    return None


def _encode_image(data: bytes) -> str:
    """识别通道主流格式是 base64 data URL。"""
    return base64.b64encode(data).decode("ascii")


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    # 非有限数（模型偶尔回 NaN/Infinity 的 JSON 变体）一律回落默认值。
    return result if result == result and result not in (float("inf"), float("-inf")) else default


def parse_llm_targets(payload: Any) -> List[Tuple[str, float, float, float]]:
    """从模型返回值里抽出 ``[(char, x, y, confidence), ...]``。

    模型返回的 JSON 外形不稳定（包 markdown 代码块、尾逗号、单引号），
    这里做宽松解析。**坐标越界一律夹到 [0,1] 而非丢弃**：夹边后点到的
    位置仍是边缘目标，比丢掉整个条目少点一次字要好。
    """
    data = payload
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("```"):
            # ```json ... ``` → 取代码块内容
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
            if "\n" in text and text.endswith("```"):
                text = text.rsplit("```", 1)[0].strip()
        try:
            data = json.loads(text)
        except ValueError:
            return []

    if not isinstance(data, dict):
        return []
    raw_targets = data.get("targets")
    if not isinstance(raw_targets, list):
        return []

    out: List[Tuple[str, float, float, float]] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        char = str(item.get("char") or "").strip()
        if not char:
            continue
        x = min(1.0, max(0.0, _coerce_float(item.get("x"))))
        y = min(1.0, max(0.0, _coerce_float(item.get("y"))))
        conf = min(1.0, max(0.0, _coerce_float(item.get("confidence"), 0.0)))
        out.append((char, x, y, conf))
    return out


class LlmVisionExtractor:
    """多模态点选识别器，实现 ``PointSelectExtractor`` 协议。

    语义：``instruction`` 为空时让模型自行读图给出目标；有指令时把指令
    文本一并告知，降低模型读错提示的概率。
    """

    def __init__(self, config: Optional[LlmVisionConfig] = None):
        self.config = config or LlmVisionConfig.from_env()

    # ── 传输层：OpenAI 兼容 chat/completions 形状 ──────────────────────
    def _build_payload(self, image_data: bytes, instruction_text: str) -> Dict[str, Any]:
        prompt = self.config.prompt
        if instruction_text:
            prompt = f"{prompt}\n\n当前提示文案：{instruction_text}"
        return self._payload_for(image_data, prompt)

    def _payload_for(self, image_data: bytes, prompt: str) -> Dict[str, Any]:
        return {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{_encode_image(image_data)}"},
                        },
                    ],
                }
            ],
        }

    def _post(self, payload: Dict[str, Any]) -> Any:
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # urllib 的默认 UA 是 "Python-urllib/3.x"，部分网关（如
                # Cloudflare 前置的 api.mangoqwq.com）按 UA 拦截非浏览器客户端，
                # 直接回 1010 Access denied —— 真实端到端联调时踩到：带浏览器
                # UA 同一 key 正常 401/200，不带则 1010。发一个保守的 UA 头。
                "User-Agent": self.config.user_agent,
                "Accept": "application/json",
                **(
                    {self.config.session_header_name: self.config.session_header}
                    if self.config.session_header
                    else {}
                ),
                **({"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}),
            },
            method="POST",
        )
        attempts = max(0, int(self.config.retry_on_gateway_timeout)) + 1
        last_exc: Optional[urllib.error.HTTPError] = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                    body = response.read().decode("utf-8", errors="replace")
                return json.loads(body)
            except urllib.error.HTTPError as exc:
                # 504/524 是网关等不到上游；重发一次有意义。5xx 其余情况
                # （500/502/503）多为上游真故障，重试只是浪费配额，不做。
                if exc.code in (504, 524) and attempt < attempts - 1:
                    logger.warning(
                        f"llm vision: gateway {exc.code}, retrying ({attempt + 1}/{attempts - 1})"
                    )
                    last_exc = exc
                    continue
                raise
        raise last_exc  # pragma: no cover - 循环内必 raise 或 return

    @staticmethod
    def _extract_message_text(body: Any) -> str:
        if not isinstance(body, dict):
            return ""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            # 多模态模型有时回 [{type:text,text:...}] 形状
            if isinstance(content, list):
                return "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if isinstance(content, str):
                return content
        return str(first.get("text") or "")

    # ── 对外接口 ──────────────────────────────────────────────────────
    def read_chars(
        self,
        *,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[Path] = None,
    ) -> OcrResult:
        """识别图形验证码里的字符序列（无坐标、无顺序语义）。

        与 ``locate_targets`` 的关键差异：返回值**不校验长度**。图形验证码的
        正确长度渠道各异（baxia 常见 4 位，也有 5~6 位），长度校验属于策略层，
        放 ``vision.graph_captcha.plan_graph_chars``，那里才知道期望值。
        这里只负责"图里有哪些字"，保持引擎层与渠道无关。
        """
        if not self.config.configured:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="llm-vision",
                metadata={"error_code": "vision_not_configured"},
            )

        data = _load_image_bytes(image_bytes, image_path)
        if not data:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="llm-vision",
                metadata={"error_code": "missing_image_input"},
            )

        started = time.monotonic()
        try:
            body = self._post(self._payload_for(data, self.config.chars_prompt))
        except urllib.error.HTTPError as exc:
            logger.warning(f"llm vision: HTTP {exc.code} from vision endpoint")
            return OcrResult(
                text="", confidence=0.0, provider="llm-vision",
                metadata={"error_code": f"vision_http_{exc.code}"},
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning(f"llm vision: request failed: {exc}")
            return OcrResult(
                text="", confidence=0.0, provider="llm-vision",
                metadata={"error_code": "vision_request_failed"},
            )

        raw = self._extract_message_text(body)
        if not raw.strip():
            return OcrResult(
                text="", confidence=0.0, provider="llm-vision",
                metadata={"error_code": "vision_empty_response"},
            )

        # reasoning 模型会把 budget 花在内部分析上，截断时 finish_reason=length
        # 且 content 为空——那不是"没认清"，是"没说完"，必须能区分开，否则
        # 上层会把它当成识别失败而白白换图重试。
        truncated = _was_truncated(body)
        usage = _extract_usage(body)
        return OcrResult(
            text=raw.strip(),
            confidence=0.0 if truncated else 1.0,
            provider="llm-vision",
            metadata={
                "model": self.config.model,
                "truncated": truncated,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "usage": usage,
            },
        )

    def locate_targets(
        self,
        *,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[Path] = None,
        instruction: Optional[Instruction] = None,
    ) -> OcrResult:
        if not self.config.configured:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="llm-vision",
                metadata={"error_code": "vision_not_configured"},
            )

        data = _load_image_bytes(image_bytes, image_path)
        if not data:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="llm-vision",
                metadata={"error_code": "missing_image_input"},
            )

        instruction_text = ""
        targets_hint: Tuple[str, ...] = ()
        if instruction is not None:
            instruction_text = instruction.raw or "".join(instruction.targets)
            targets_hint = instruction.targets

        try:
            body = self._post(self._build_payload(data, instruction_text))
        except urllib.error.HTTPError as exc:
            logger.warning(f"llm vision: HTTP {exc.code} from vision endpoint")
            return OcrResult(
                text="", confidence=0.0, provider="llm-vision",
                metadata={"error_code": f"vision_http_{exc.code}"},
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning(f"llm vision: request failed: {exc}")
            return OcrResult(
                text="", confidence=0.0, provider="llm-vision",
                metadata={"error_code": "vision_request_failed"},
            )

        text = self._extract_message_text(body)
        found = parse_llm_targets(text)
        if not found:
            return OcrResult(
                text="", confidence=0.0, provider="llm-vision",
                metadata={"error_code": "vision_unparseable", "raw_excerpt": text[:200]},
            )

        # 指令已知时校验模型返回的字序列：顺序或字面不符即整体不可信，
        # 返回低置信度让执行器换通道，而不是按错字去点。
        if targets_hint:
            got = tuple(ch for ch, _x, _y, _c in found)
            if got != targets_hint:
                logger.debug(f"llm vision: target mismatch hint={targets_hint} got={got}")
                return OcrResult(
                    text="".join(got),
                    confidence=0.0,
                    provider="llm-vision",
                    boxes=[
                        OcrBox(text=ch, x=x, y=y, width=0.0, height=0.0, confidence=c)
                        for ch, x, y, c in found
                    ],
                    metadata={
                        "error_code": "vision_target_mismatch",
                        "expected": list(targets_hint),
                        "got": list(got),
                    },
                )

        boxes = [
            OcrBox(text=ch, x=x, y=y, width=0.0, height=0.0, confidence=c)
            for ch, x, y, c in found
        ]
        confidence = min(c for _ch, _x, _y, c in found) if found else 0.0
        below = [ch for ch, _x, _y, c in found if c < self.config.min_confidence]
        return OcrResult(
            text="".join(ch for ch, _x, _y, _c in found),
            confidence=confidence,
            boxes=boxes,
            provider="llm-vision",
            metadata={
                "model": self.config.model,
                "target_count": len(boxes),
                "low_confidence_targets": below,
            },
        )