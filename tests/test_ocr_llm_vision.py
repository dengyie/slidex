"""多模态识别引擎 —— 响应解析与失败语义的回归锁定。

不发起真实网络请求：全部以假响应体驱动 ``_post``，锁定的是**解析与失败
语义**——模型输出不稳定（markdown 代码块、尾逗号、NaN、缺字段）是这类集成
的主要故障面。
"""
import pytest

from slidex.ocr.llm import (
    LlmVisionConfig,
    LlmVisionExtractor,
    parse_llm_targets,
)


@pytest.mark.parametrize("payload_text", [
    '{"targets":[{"char":"的","x":0.31,"y":0.52,"confidence":0.95}]}',
    '```json\n{"targets":[{"char":"的","x":0.31,"y":0.52,"confidence":0.95}]}\n```',
    '```\n{"targets":[{"char":"的","x":0.31,"y":0.52,"confidence":0.95}]}\n```',
])
def test_parses_plain_and_fenced_json(payload_text):
    found = parse_llm_targets(payload_text)
    assert found == [("的", 0.31, 0.52, 0.95)]


def test_clamps_out_of_range_coordinates():
    """越界坐标夹到边界而不是丢弃——夹边后仍点得到边缘目标。"""
    found = parse_llm_targets('{"targets":[{"char":"的","x":1.8,"y":-0.3,"confidence":2}]}')
    assert found == [("的", 1.0, 0.0, 1.0)]


def test_skips_malformed_entries():
    found = parse_llm_targets(
        '{"targets":[{"char":"的","x":0.1,"y":0.2},{"char":"","x":0.3,"y":0.4},"junk",'
        '{"x":0.5,"y":0.6},{"char":"我","x":"bad","y":0.7,"confidence":0.8}]}'
    )
    assert [c for c, _x, _y, _k in found] == ["的", "我"]
    # 非法坐标回落 0.0 而非抛错
    assert found[1][1] == 0.0


@pytest.mark.parametrize("payload", [
    "", "not json at all", "{}", '{"targets": null}', "[]", '{"targets": ["a"]}',
])
def test_returns_empty_for_unusable_payloads(payload):
    assert parse_llm_targets(payload) == []


class _StubExtractor(LlmVisionExtractor):
    """覆写 _post 返回固定响应体，避免真实网络。"""

    def __init__(self, body, **config_kwargs):
        super().__init__(LlmVisionConfig(
            endpoint="http://stub/v1/chat/completions",
            model="stub-model",
            **config_kwargs,
        ))
        self._body = body
        self.captured_payload = None

    def _post(self, payload):
        self.captured_payload = payload
        return self._body


def _body_with(text):
    return {"choices": [{"message": {"content": text}}]}


def test_locate_targets_returns_normalized_boxes():
    ex = _StubExtractor(
        _body_with('{"targets":[{"char":"的","x":0.2,"y":0.3,"confidence":0.9}]}')
    )
    result = ex.locate_targets(image_bytes=b"png")
    assert result.provider == "llm-vision"
    assert result.text == "的"
    assert len(result.boxes) == 1
    assert result.boxes[0].x == pytest.approx(0.2)


def test_target_mismatch_is_rejected_wholesale():
    """指令已知时模型返回字序列不符 → 整体不可信，不按错字去点。"""
    from slidex.ocr.instruction import Instruction

    ex = _StubExtractor(
        _body_with('{"targets":[{"char":"我","x":0.2,"y":0.3,"confidence":0.9}]}')
    )
    result = ex.locate_targets(
        image_bytes=b"png", instruction=Instruction(targets=("的",), raw="的")
    )
    assert result.metadata["error_code"] == "vision_target_mismatch"
    assert result.metadata["expected"] == ["的"]
    assert result.metadata["got"] == ["我"]


def test_matching_sequence_is_accepted():
    from slidex.ocr.instruction import Instruction

    ex = _StubExtractor(
        _body_with(
            '{"targets":[{"char":"的","x":0.2,"y":0.3,"confidence":0.9},'
            '{"char":"了","x":0.4,"y":0.3,"confidence":0.8}]}'
        )
    )
    result = ex.locate_targets(
        image_bytes=b"png", instruction=Instruction(targets=("的", "了"), raw="的了")
    )
    assert "error_code" not in result.metadata
    assert result.confidence == pytest.approx(0.8)  # 取最低置信
    assert result.metadata["low_confidence_targets"] == []


def test_low_confidence_targets_flagged():
    ex = _StubExtractor(
        _body_with('{"targets":[{"char":"的","x":0.2,"y":0.3,"confidence":0.1}]}'),
        min_confidence=0.35,
    )
    result = ex.locate_targets(image_bytes=b"png")
    assert result.metadata["low_confidence_targets"] == ["的"]
    assert result.confidence == pytest.approx(0.1)


def test_unconfigured_extractor_reports_config_error():
    ex = LlmVisionExtractor(LlmVisionConfig(endpoint="", model=""))
    result = ex.locate_targets(image_bytes=b"png")
    assert result.metadata["error_code"] == "vision_not_configured"


def test_missing_image_input():
    ex = _StubExtractor(_body_with('{"targets":[]}'))
    result = ex.locate_targets(image_bytes=None, image_path=None)
    assert result.metadata["error_code"] == "missing_image_input"


def test_unparseable_model_output_reports_error():
    ex = _StubExtractor(_body_with("I could not find the characters."))
    result = ex.locate_targets(image_bytes=b"png")
    assert result.metadata["error_code"] == "vision_unparseable"
    assert "raw_excerpt" in result.metadata


def test_instruction_text_is_sent_to_model():
    from slidex.ocr.instruction import Instruction

    ex = _StubExtractor(_body_with('{"targets":[]}'))
    ex.locate_targets(image_bytes=b"png", instruction=Instruction(targets=("的",), raw="请点【的】"))
    text_block = ex.captured_payload["messages"][0]["content"][0]["text"]
    assert "请点【的】" in text_block


def test_config_is_not_configured_without_endpoint_or_model():
    assert LlmVisionConfig(endpoint="", model="m").configured is False
    assert LlmVisionConfig(endpoint="http://x", model="").configured is False
    assert LlmVisionConfig(endpoint="http://x", model="m").configured is True


def test_from_config_reads_slidex_config():
    """配置单一事实源：LlmVisionConfig 从 SlidexConfig 取值，不自己读 env。"""
    from slidex.config import SlidexConfig

    cfg = SlidexConfig(
        vision_endpoint="http://cfg", vision_model="cfg-model", vision_timeout_s=7.0
    )
    parsed = LlmVisionConfig.from_config(cfg)
    assert parsed.endpoint == "http://cfg"
    assert parsed.model == "cfg-model"
    assert parsed.timeout_s == pytest.approx(7.0)