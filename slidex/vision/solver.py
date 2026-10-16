from __future__ import annotations

import inspect
import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loguru import logger

from slidex._async_budget import await_with_budget, wait_abandoned
from slidex.ocr import FakeOcrExtractor, OcrTextExtractor
from slidex.ocr.instruction import parse_instruction
from slidex.solver import SliderSolver
from slidex.vision.models import (
    ChallengeType,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
)
from slidex.vision.graph_captcha import (
    ALPHABET_ANY,
    GraphCaptchaExecutor,
    is_punish_graph_code,
    plan_graph_chars,
)
from slidex.vision.point_select import (
    PointSelectExecutor,
    is_punish_point_code,
    plan_click_points,
)
from slidex.vision.slider_image import SliderImageResult, SliderImageSolver

# slider_captcha 走纯图片求解（无浏览器）的上下文：只拿得到图片 bytes/path。
# ANDROID_SCREENSHOT_BYTES 复用 image_bytes 字段承载整屏截图。
_IMAGE_SLIDER_CONTEXTS = {
    VisionContext.IMAGE_BYTES,
    VisionContext.IMAGE_PATH,
    VisionContext.ANDROID_SCREENSHOT_BYTES,
}

_VISION_WORKER_CAP = max(2, min(8, (os.cpu_count() or 4)))
_VISION_QUEUE_CAP = _VISION_WORKER_CAP * 2
_VISION_CLOSE_BUDGET_S = 2.0
_vision_executor: Optional[ThreadPoolExecutor] = None
_vision_executor_guard = threading.Lock()
# 视觉识别引擎缓存（_default_vision_extractor 使用）；测试可置 _cached_vision_extractor
# 注入假引擎。
_cached_vision_extractor: Any = None
_cached_vision_configured: bool = False
_vision_extractor_guard = threading.Lock()
_vision_slots = threading.BoundedSemaphore(_VISION_QUEUE_CAP)
_vision_hung = 0
_vision_generation = 0


def _default_vision_extractor() -> Any:
    """默认点选识别引擎：多模态视觉（由 SLIDEX_VISION_* env 配置）。

    未配置通道时返回 None，调用方转成 ``vision_not_configured``（不可重试）
    ——通道没配是部署问题，重试只会空耗预算。构造结果按进程缓存：LlmVisionConfig
    每次都会读env，逐次求解重建既浪费又让测试无法在中途注入配置。
    """
    global _cached_vision_extractor, _cached_vision_configured
    with _vision_extractor_guard:
        if _cached_vision_extractor is None:
            try:
                from slidex.ocr.llm import LlmVisionConfig, LlmVisionExtractor
            except Exception as exc:  # pragma: no cover - 依赖缺失保护
                logger.warning(f"point-select: vision engine unavailable: {exc}")
                return None
            config = LlmVisionConfig.from_env()
            _cached_vision_configured = config.configured
            _cached_vision_extractor = LlmVisionExtractor(config)
        return _cached_vision_extractor


def _get_vision_executor() -> ThreadPoolExecutor:
    global _vision_executor
    with _vision_executor_guard:
        if _vision_executor is None:
            _vision_executor = ThreadPoolExecutor(
                max_workers=_VISION_WORKER_CAP,
                thread_name_prefix="slidex-vision",
            )
        return _vision_executor


def _retire_hung_vision_executor() -> None:
    """超时取消不了工作线程；挂死数达到 worker 上限时换池，避免槽位被占死。"""
    global _vision_executor, _vision_slots, _vision_hung, _vision_generation
    with _vision_executor_guard:
        _vision_hung += 1
        if _vision_hung < _VISION_WORKER_CAP:
            return
        old = _vision_executor
        _vision_executor = ThreadPoolExecutor(
            max_workers=_VISION_WORKER_CAP,
            thread_name_prefix="slidex-vision",
        )
        _vision_slots = threading.BoundedSemaphore(_VISION_QUEUE_CAP)
        _vision_hung = 0
        _vision_generation += 1
        if old is not None:
            old.shutdown(wait=False)


class VisualChallengeSolver:
    def __init__(
        self,
        *,
        ocr_extractor: Optional[OcrTextExtractor] = None,
        slider_solver_factory: Optional[Callable[..., SliderSolver]] = None,
        slider_image_solver: Optional[SliderImageSolver] = None,
        vision_extractor: Optional[Any] = None,
        point_select_executor: Optional[PointSelectExecutor] = None,
        graph_captcha_executor: Optional[GraphCaptchaExecutor] = None,
    ):
        self.ocr_extractor = ocr_extractor or FakeOcrExtractor()
        self.slider_solver_factory = slider_solver_factory or SliderSolver
        self.slider_image_solver = slider_image_solver or SliderImageSolver()
        # point_select（0.6.34）/ graph_chars（0.6.35）：识别引擎可注入，默认走多模态
        # 视觉。注入一个实现了 locate_targets()（点选）或 read_chars()（字符）的
        # 对象即可换引擎——规划器与执行器不变，这是"通用能力"的关键。
        self.vision_extractor = vision_extractor
        self.point_select_executor = point_select_executor or PointSelectExecutor()
        self.graph_captcha_executor = graph_captcha_executor or GraphCaptchaExecutor()

    async def solve(self, request: VisualChallengeRequest) -> VisualChallengeResult:
        started = time.time()
        if request.challenge_type == ChallengeType.POINT_SELECT:
            # 点选要在活页面上截图 + 依序点击，不能进 CPU 线程池（截图/点击都
            # 需要事件循环与 CDP 连接）。识别那段单独的阻塞调用由识别引擎
            # 自身控制超时。
            return await self._await_with_timeout(
                self._solve_point_select(request, started),
                request,
                started,
            )
        if request.challenge_type == ChallengeType.GRAPH_CHARS:
            # 字符图形验证码：截图 → 识别 → 填入 → 提交。与点选同样需要在活
            # 页面上取截图，填字/提交也需要事件循环，故不进 CPU 线程池。
            return await self._await_with_timeout(
                self._solve_graph_chars(request, started),
                request,
                started,
            )
        if request.challenge_type in {ChallengeType.OCR_TEXT, ChallengeType.IMAGE_TEXT}:
            # OCR extractors are synchronous/CPU-bound. Isolate them so the
            # caller event loop remains responsive under Provider V2 runtime.
            # timeout_ms 与滑块路径同一套：超时返回 error_code=timeout。
            return await self._await_cpu_with_timeout(
                self._solve_ocr,
                request,
                started,
            )
        if request.challenge_type == ChallengeType.SLIDER_CAPTCHA:
            if request.context in _IMAGE_SLIDER_CONTEXTS:
                # 纯图片求解是 CPU 密集型，隔离到线程避免阻塞事件循环。
                # ANDROID_SCREENSHOT_BYTES 也走这里：安卓截图无 CDP/Page，
                # 只有整屏 bytes，走无块图缺口检测（解 dianping REQ-004）。
                return await self._await_cpu_with_timeout(
                    self._solve_slider_image,
                    request,
                    started,
                )
            return await self._await_with_timeout(
                self._solve_slider(request, started),
                request,
                started,
            )
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=request.provider,
            duration_ms=self._duration_ms(started),
            error_code="unsupported_challenge_type",
            retryable=False,
            artifacts=[],
            metadata={"context": request.context.value},
        )

    def _solve_ocr(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        result = self.ocr_extractor.extract(
            image_bytes=request.image_bytes,
            image_path=request.image_path,
            roi=request.roi,
            language=request.metadata.get("language"),
        )
        success = bool(result.text)
        return VisualChallengeResult(
            success=success,
            challenge_type=request.challenge_type,
            provider=result.provider,
            confidence=result.confidence,
            duration_ms=self._duration_ms(started),
            error_code=None if success else result.metadata.get("error_code", "ocr_failed"),
            retryable=not success,
            cookies=None,
            artifacts=[],
            metadata={
                "text": result.text,
                "language": result.language,
                "boxes": [box.__dict__ for box in result.boxes],
                **result.metadata,
            },
        )

    def _point_select_error(
        self,
        request: VisualChallengeRequest,
        started: float,
        error_code: str,
        *,
        retryable: bool = True,
        metadata: Optional[dict] = None,
    ) -> VisualChallengeResult:
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=str(getattr(self, "_last_vision_provider", "") or request.provider),
            duration_ms=self._duration_ms(started),
            error_code=error_code,
            # 识别失败是「换通道重截图」的信号，可重试；惩罚码与配置缺失
            # 不是——重试只会加深惩罚，必须停手转被动恢复（同 0.6.33）。
            retryable=retryable,
            cookies=None,
            artifacts=[],
            metadata={"context": request.context.value, **(metadata or {})},
        )

    async def _solve_point_select(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        """文字点选：重试编排层。

        重试纪律（0.6.28 教训）：识别不可信时**换通道重新截图**再试，而不是
        拿同一个猜测在本页连点——点选判错一个字即整体失败，连点只会加深风控
        确认样本。因此循环**只包住「截图 + 识别 + 规划」**：一旦进入点击阶段
        就不再重试（半截/重复点击比点错更易触发判罚），惩罚码立即停手。
        """
        max_attempts = self._resolve_max_attempts(request)
        last_error: Optional[str] = None
        for attempt in range(1, max_attempts + 1):
            outcome = await self._point_select_attempt(request, started, attempt)
            # 点击已开始 → 无论成败都不再重试。
            if outcome.metadata.get("click_attempted"):
                return outcome
            if outcome.success or not outcome.retryable:
                return outcome
            last_error = outcome.error_code
            if attempt < max_attempts:
                logger.info(
                    f"point-select: attempt {attempt}/{max_attempts} failed "
                    f"({last_error}) — 重新截图换通道，不在同一猜测上连点"
                )
        return self._point_select_error(
            request, started, last_error or "point_select_attempts_exhausted",
            retryable=True,
            metadata={"attempts": max_attempts},
        )

    @staticmethod
    def _resolve_max_attempts(request: VisualChallengeRequest) -> int:
        """识别阶段的重试上限，默认 3，硬顶 5。

        设上限是因为每次重试都会向风控追加一个样本；无上限重试等于反复撞
        风控，比认不出来更糟。
        """
        try:
            value = int(request.metadata.get("max_attempts", 3))
        except (TypeError, ValueError):
            return 3
        return max(1, min(5, value))

    async def _point_select_attempt(
        self, request: VisualChallengeRequest, started: float, attempt: int
    ) -> VisualChallengeResult:
        if request.context not in {VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP}:
            return self._point_select_error(
                request, started, "unsupported_point_select_context", retryable=False
            )

        extractor = self.vision_extractor or _default_vision_extractor()
        if extractor is None:
            return self._point_select_error(
                request, started, "vision_not_configured", retryable=False
            )

        target = request.target_element
        if target is None and request.page is not None:
            # 未显式给承载元素时退化为整页视口——坐标仍走 bounding_box
            # 换算，只是识别视野更大。
            target = request.page
        if target is None:
            return self._point_select_error(request, started, "missing_target_element")

        # 1) 截图。优先走承载元素的局部截图（挑战通常在一个 iframe 内）。
        try:
            image_bytes = await target.screenshot()
        except Exception as exc:
            logger.warning(f"point-select: screenshot failed: {exc}")
            return self._point_select_error(request, started, "screenshot_failed")

        if not image_bytes:
            return self._point_select_error(request, started, "empty_screenshot")

        # 2) 指令。给文案则按它定目标序列；没给就让引擎自行给出。
        instruction = parse_instruction(request.instruction_text) if request.instruction_text else None
        if request.instruction_text and instruction is None:
            return self._point_select_error(
                request, started, "instruction_unparsed", retryable=True,
                metadata={"instruction_text": request.instruction_text[:120]},
            )

        # 3) 识别。引擎是阻塞 HTTP/推理，直接在事件循环里调会把循环卡住
        # 到引擎自身超时为止——引擎侧已各自带 timeout（HTTP 20s / 本地推理），
        # 这里的 timeout_ms 兜底由外层 _await_with_timeout 负责。
        try:
            detected = await asyncio.to_thread(
                extractor.locate_targets,
                image_bytes=image_bytes,
                instruction=instruction,
            )
        except Exception as exc:
            logger.warning(f"point-select: recognition failed: {exc}")
            return self._point_select_error(request, started, "recognition_failed")

        provider_name = str(getattr(detected, "provider", "") or "vision")
        self._last_vision_provider = provider_name

        engine_error = detected.metadata.get("error_code") if detected.metadata else None
        if engine_error:
            return self._point_select_error(
                request, started, str(engine_error),
                metadata={"engine_provider": provider_name},
            )

        # 4) 规划。引擎没给指令时的兜底：用识别到的字序列当目标。
        plan = plan_click_points(detected, instruction) if instruction else None
        if plan is None and not instruction:
            return self._point_select_error(
                request, started, "instruction_required", retryable=False,
                metadata={"engine_provider": provider_name,
                          "detected_text": getattr(detected, "text", "")[:120]},
            )
        if plan is None or not plan.ok:
            return self._point_select_error(
                request, started, (plan.error_code if plan else "plan_failed"),
                metadata={"engine_provider": provider_name, "notes": plan.notes if plan else []},
            )

        # 5) 执行。序列一旦开始必须点完——半截点击比点错更易触发判罚。
        # 判决读取器由调用方按渠道注入（各家响应形状不同，slidex 不猜）；
        # 不注入时结果停在 point_select_verdict_pending，调用方自行判读。
        verdict_reader = request.metadata.get("verdict_reader")
        if verdict_reader is not None and not callable(verdict_reader):
            verdict_reader = None
        outcome = await self.point_select_executor.execute(
            page=request.page,
            target_element=target,
            plan=plan,
            verdict_reader=verdict_reader,
        )
        if outcome.get("error_code"):
            return self._point_select_error(
                request, started, str(outcome["error_code"]),
                metadata={"engine_provider": provider_name,
                          "click_attempted": True, "attempt": attempt},
            )

        # 6) 判决。点选挑战没有统一响应形状（各家渠道不同），因此这里
        # 默认**不**声称成功：把判决码交回调用方判定，避免把"点完了"
        # 误报成"过了"。惩罚码显式标注，交由调用方停手。
        verdict = outcome.get("verdict") or {}
        code = verdict.get("code") if isinstance(verdict, dict) else None
        punished = is_punish_point_code(code)

        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=provider_name,
            confidence=plan.confidence,
            duration_ms=self._duration_ms(started),
            error_code="point_select_punished" if punished else "point_select_verdict_pending",
            # 惩罚码不可重试：换通道再点只会加深确认样本（0.6.33 纪律）。
            # 非惩罚的判决待定也不重试——点击已发生，重来一遍等于重复点击。
            retryable=False,
            cookies=None,
            artifacts=[],
            metadata={
                "context": request.context.value,
                "targets": list(instruction.targets) if instruction else [],
                "clicks": outcome.get("clicked", []),
                "notes": plan.notes,
                "verdict": verdict,
                "engine_provider": provider_name,
                "click_attempted": True,
                "attempt": attempt,
            },
        )

    async def _solve_graph_chars(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        """字符图形验证码：重试编排层。

        与点选同一套纪律，但有一处关键差异：**提交后不可重试**。填错字符提交
        就是一次判罚，重发同一个答案只会加深确认样本。所以循环同样只包住
        「截图 + 识别 + 规划」，``submit_attempted`` 一旦置位就停手。

        另一处差异：识别被**截断**（reasoning 模型吃光 token 预算、正文为空）
        时该**重发同一次请求**而不是换图——同一个模型对同一张图的答案不变，
        换图才需要重新推理。这里由引擎侧的 max_tokens 预算 + 网关超时重试
        兜底，编排层只看 error_code 决定是否再取新图。
        """
        max_attempts = self._resolve_max_attempts(request)
        last_error: Optional[str] = None
        for attempt in range(1, max_attempts + 1):
            outcome = await self._graph_chars_attempt(request, started, attempt)
            if outcome.metadata.get("submit_attempted"):
                return outcome
            if outcome.success or not outcome.retryable:
                return outcome
            last_error = outcome.error_code
            if attempt < max_attempts:
                logger.info(
                    f"graph-chars: attempt {attempt}/{max_attempts} failed "
                    f"({last_error}) — 重新截图重识别，不重复提交同一猜测"
                )
        return self._graph_chars_error(
            request, started, last_error or "graph_chars_attempts_exhausted",
            retryable=True,
            metadata={"attempts": max_attempts},
        )

    async def _graph_chars_attempt(
        self, request: VisualChallengeRequest, started: float, attempt: int
    ) -> VisualChallengeResult:
        # 纯识别场景（给了图片、没给页面元素）不需要页面上下文。
        has_page = request.context in {VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP}
        if not has_page and request.image_bytes is None and request.image_path is None:
            return self._graph_chars_error(
                request, started, "unsupported_graph_chars_context", retryable=False
            )

        extractor = self.vision_extractor or _default_vision_extractor()
        if extractor is None:
            return self._graph_chars_error(
                request, started, "vision_not_configured", retryable=False
            )
        # 引擎须实现 read_chars；不实现时明确报错，而不是退化成点选路径——
        # 两者的输出语义不同（有序带坐标 vs 纯字符），混用会填错。
        reader = getattr(extractor, "read_chars", None)
        if not callable(reader):
            return self._graph_chars_error(
                request, started, "engine_lacks_read_chars", retryable=False,
                metadata={"engine_provider": str(getattr(extractor, "provider", "vision"))},
            )

        # 1) 取图。给了 target_element 就截它的局部（挑战多在 iframe 内），
        # 否则用调用方直接给的图片字节（纯识别场景）。
        #
        # 重试纪律的前提是「每次重试拿到的是**新图**」：这里每轮都重新截图，
        # 所以长度不符时换一次图重识别是有意义的。反过来，如果调用方传的是
        # 静态 image_bytes/image_path，重试只会把同一张图再识别一遍——同一张
        # 图对同一模型的答案不会变（截断除外，那是重发而非换图）。这种场景
        # 应由调用方自己换图再调一次，故这里显式标注，避免把无效重试
        # 误读成「重试机制在工作」。
        image_bytes = request.image_bytes
        image_path = request.image_path
        from_live_page = image_bytes is None and image_path is None
        if from_live_page:
            target = request.target_element or request.page
            if target is None:
                return self._graph_chars_error(request, started, "missing_image_input")
            try:
                image_bytes = await target.screenshot()
            except Exception as exc:
                logger.warning(f"graph-chars: screenshot failed: {exc}")
                return self._graph_chars_error(request, started, "screenshot_failed")
            if not image_bytes:
                return self._graph_chars_error(request, started, "empty_screenshot")

        # 2) 识别。
        try:
            detected = await asyncio.to_thread(
                reader, image_bytes=image_bytes, image_path=image_path
            )
        except Exception as exc:
            logger.warning(f"graph-chars: recognition failed: {exc}")
            return self._graph_chars_error(request, started, "recognition_failed")

        provider_name = str(getattr(detected, "provider", "") or "vision")
        self._last_vision_provider = provider_name
        engine_meta = dict(getattr(detected, "metadata", {}) or {})
        engine_error = engine_meta.get("error_code")
        if engine_error:
            # 除"截断"外一律不重试：通道未配置、401/403、网关故障重发同一张图
            # 都换不来新信息，只是空耗配额与配额账单。截断是唯一例外——
            # 那是"预算被 reasoning 吃光、正文没吐完"，模型对同一张图的答案
            # 不变，重发是合理的（引擎侧也已按 504/524 做过一次网关重试）。
            truncated = bool(engine_meta.get("truncated"))
            return self._graph_chars_error(
                request, started, str(engine_error),
                retryable=truncated,
                metadata={"engine_provider": provider_name, "truncated": truncated,
                          "attempt": attempt},
            )

        # 3) 规划：归一 + 长度校验。纯函数，无页面依赖。
        plan = plan_graph_chars(
            getattr(detected, "text", "") or "",
            expected_length=request.expected_char_count,
            alphabet=str(request.metadata.get("alphabet", ALPHABET_ANY)),
        )
        if not plan.ok or plan.chars is None:
            # 长度不符说明这张图没认清。**只有活页面重截才能换图**，静态图片
            # 重试等于把同一张图再认一遍，必然得到同一个错误答案。
            return self._graph_chars_error(
                request, started, str(plan.error_code or "plan_failed"),
                retryable=(plan.error_code == "unexpected_char_count" and from_live_page),
                metadata={"engine_provider": provider_name, "notes": plan.notes,
                          "attempt": attempt, "from_live_page": from_live_page},
            )

        recognition = plan.chars
        chars = recognition.text
        confidence = float(getattr(detected, "confidence", 0.0) or 0.0)

        # 4) 纯识别：调用方只要字符，自己填。slidex 不碰页面。
        if request.input_element is None:
            return VisualChallengeResult(
                success=True,
                challenge_type=request.challenge_type,
                provider=provider_name,
                confidence=confidence,
                duration_ms=self._duration_ms(started),
                error_code=None,
                retryable=False,
                cookies=None,
                artifacts=[],
                metadata={
                    "context": request.context.value,
                    "chars": chars,
                    "recognition_only": True,
                    "notes": plan.notes,
                    "engine_provider": provider_name,
                    "engine_meta": engine_meta,
                    "attempt": attempt,
                },
            )

        # 5) 填 + 提交。
        outcome = await self.graph_captcha_executor.execute(
            input_locator=request.input_element,
            submit_locator=request.submit_element,
            text=chars,
            verdict_reader=request.metadata.get("verdict_reader")
            if callable(request.metadata.get("verdict_reader")) else None,
        )
        submitted = bool(outcome.get("submitted"))
        if outcome.get("error_code"):
            return self._graph_chars_error(
                request, started, str(outcome["error_code"]),
                metadata={"engine_provider": provider_name,
                          "chars": chars,
                          # 填了但没提交成功 → 仍可重来（没产生判罚样本）。
                          "submit_attempted": submitted,
                          "attempt": attempt},
            )

        # 6) 判决。同样不擅自判成功：响应形状各家不同，由调用方判读。
        verdict = outcome.get("verdict") or {}
        code = verdict.get("code") if isinstance(verdict, dict) else None
        punished = is_punish_graph_code(code)
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=provider_name,
            confidence=confidence,
            duration_ms=self._duration_ms(started),
            error_code="graph_chars_punished" if punished else "graph_chars_verdict_pending",
            retryable=False,
            cookies=None,
            artifacts=[],
            metadata={
                "context": request.context.value,
                "chars": chars,
                "raw_chars": recognition.raw_text,
                "normalized_from": recognition.normalized_from,
                "notes": plan.notes,
                "verdict": verdict,
                "engine_provider": provider_name,
                "engine_meta": engine_meta,
                "submit_attempted": True,
                "attempt": attempt,
            },
        )

    def _graph_chars_error(
        self,
        request: VisualChallengeRequest,
        started: float,
        error_code: str,
        *,
        retryable: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VisualChallengeResult:
        payload = {"context": request.context.value}
        payload.update(metadata or {})
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            # 同 _point_select_error：属性可能尚未被任何一次识别写过。
            provider=str(getattr(self, "_last_vision_provider", "") or request.provider),
            duration_ms=self._duration_ms(started),
            error_code=error_code,
            retryable=retryable,
            cookies=None,
            artifacts=[],
            metadata=payload,
        )

    def _solve_slider_image(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        """纯图片滑块求解（IMAGE_BYTES / IMAGE_PATH 上下文），同步 CPU-bound。"""
        use_path = request.context == VisionContext.IMAGE_PATH
        if use_path and not request.image_path:
            return self._slider_image_error(request, started, "missing_image_path")
        if not use_path and not request.image_bytes:
            # IMAGE_BYTES 与 ANDROID_SCREENSHOT_BYTES 都从 image_bytes 承载
            return self._slider_image_error(request, started, "missing_image_bytes")

        background: Any = request.image_path if use_path else request.image_bytes
        piece: Optional[Any] = None
        if request.piece_image_bytes is not None:
            piece = request.piece_image_bytes
        elif request.piece_image_path is not None:
            piece = request.piece_image_path

        distance_scale = request.metadata.get("distance_scale")
        # bool 是 int 子类，需显式排除，否则 True/False 会被当 1.0/0.0
        if distance_scale is not None and (
            isinstance(distance_scale, bool)
            or not isinstance(distance_scale, (int, float))
        ):
            return self._slider_image_error(request, started, "invalid_distance_scale")

        result = self.slider_image_solver.solve(
            background,
            piece,
            distance_scale=float(distance_scale) if distance_scale is not None else None,
            roi=request.roi,
        )
        return VisualChallengeResult(
            success=result.success,
            challenge_type=request.challenge_type,
            provider="slidex-image",
            confidence=result.confidence,
            duration_ms=self._duration_ms(started),
            error_code=result.error_code,
            retryable=not result.success,
            cookies=None,
            artifacts=[],
            metadata={
                "context": request.context.value,
                "gap_x": result.gap_x,
                "distance_px": result.distance_px,
                "method": result.method,
                "gap_box": result.gap_box,
                "candidates": result.candidates,
                **result.metadata,
            },
        )

    @staticmethod
    def _slider_image_error(
        request: VisualChallengeRequest,
        started: float,
        error_code: str,
    ) -> VisualChallengeResult:
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider="slidex-image",
            duration_ms=VisualChallengeSolver._duration_ms(started),
            error_code=error_code,
            retryable=True,
            cookies=None,
            artifacts=[],
            metadata={"context": request.context.value},
        )

    async def _await_cpu_with_timeout(
        self,
        worker: Callable[[VisualChallengeRequest, float], VisualChallengeResult],
        request: VisualChallengeRequest,
        started: float,
    ) -> VisualChallengeResult:
        timeout_ms = getattr(request, "timeout_ms", None) or 0
        slot = _vision_slots
        if not slot.acquire(blocking=False):
            return self._timeout_result(request, started, timeout_ms, error_code="executor_busy")
        try:
            cfut = _get_vision_executor().submit(worker, request, started)
        except Exception:
            slot.release()
            raise

        def _release_slot(_done) -> None:
            try:
                slot.release()
            except ValueError:
                pass

        cfut.add_done_callback(_release_slot)
        result = await self._await_with_timeout(
            asyncio.wrap_future(cfut),
            request,
            started,
            wait_cancelled=False,
        )
        if result.error_code == "timeout" and not cfut.done():
            _retire_hung_vision_executor()
        return result

    async def _await_with_timeout(
        self,
        awaitable,
        request: VisualChallengeRequest,
        started: float,
        *,
        wait_cancelled: bool = True,
    ) -> VisualChallengeResult:
        timeout_ms = getattr(request, "timeout_ms", None) or 0
        if timeout_ms <= 0:
            return await awaitable
        task = asyncio.ensure_future(awaitable)
        timeout_waiter = asyncio.ensure_future(asyncio.sleep(timeout_ms / 1000.0))
        try:
            done, _pending = await asyncio.wait(
                {task, timeout_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            await self._finish_timeout_waiter(timeout_waiter)
            await self._cancel_work(task, wait_cancelled=wait_cancelled)
            raise

        await self._finish_timeout_waiter(timeout_waiter)
        if task.done() and not task.cancelled():
            return task.result()
        await self._cancel_work(task, wait_cancelled=wait_cancelled)
        return self._timeout_result(request, started, timeout_ms)

    def _timeout_result(
        self,
        request: VisualChallengeRequest,
        started: float,
        timeout_ms: int,
        error_code: str = "timeout",
    ) -> VisualChallengeResult:
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=request.provider,
            duration_ms=self._duration_ms(started),
            error_code=error_code,
            retryable=True,
            cookies=None,
            artifacts=[],
            metadata={"context": request.context.value, "timeout_ms": timeout_ms},
        )

    @staticmethod
    async def _finish_timeout_waiter(fut: asyncio.Future) -> None:
        if not fut.done():
            fut.cancel()
        try:
            await fut
        except (asyncio.CancelledError, Exception):
            pass

    @staticmethod
    async def _cancel_work(task: asyncio.Future, *, wait_cancelled: bool) -> None:
        if task.done():
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):
                pass
            return
        task.cancel()
        if not wait_cancelled:
            def _discard(done: asyncio.Future) -> None:
                try:
                    done.exception()
                except (asyncio.CancelledError, Exception):
                    pass

            task.add_done_callback(_discard)
            return
        await wait_abandoned(task, _VISION_CLOSE_BUDGET_S)

    async def _solve_slider(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        slider = self.slider_solver_factory(
            cookie_id=str(request.metadata.get("cookie_id", "default")),
            provider=request.provider,
        )
        try:
            if request.context == VisionContext.CDP:
                success, cookies = await slider.solve_on_existing_page(
                    cdp_endpoint=request.cdp_endpoint or "",
                    page_url=request.page_url,
                )
            elif request.context == VisionContext.PLAYWRIGHT_PAGE:
                success, cookies = await slider.solve_on_page(request.page, page_url=request.page_url)
            else:
                return VisualChallengeResult(
                    success=False,
                    challenge_type=request.challenge_type,
                    provider=request.provider,
                    duration_ms=self._duration_ms(started),
                    error_code="unsupported_slider_context",
                    retryable=False,
                )

            telemetry = slider.get_telemetry_summary()
            return VisualChallengeResult(
                success=success,
                challenge_type=request.challenge_type,
                provider=str(telemetry.get("provider_name") or request.provider),
                confidence=float(telemetry.get("confidence") or 0.0),
                duration_ms=self._duration_ms(started),
                error_code=None if success else str(telemetry.get("failure_reason") or "solve_failed"),
                retryable=not success,
                cookies=cookies,
                artifacts=[
                    VisionArtifact(
                        artifact_type="telemetry",
                        path=Path(slider.get_telemetry_dir()) / f"{telemetry.get('run_id', 'unknown')}.json",
                        metadata={"run_id": str(telemetry.get("run_id", ""))},
                    )
                ],
                metadata={"telemetry": telemetry},
            )
        finally:
            close = getattr(slider, "close", None)
            if close:
                try:
                    close_result = close()
                    if inspect.isawaitable(close_result):
                        await await_with_budget(close_result, _VISION_CLOSE_BUDGET_S)
                except Exception:
                    pass

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round(max(0.0, (time.time() - started) * 1000), 1)
