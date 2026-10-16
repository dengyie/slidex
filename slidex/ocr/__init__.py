from slidex.ocr.extractors import FakeOcrExtractor, OcrTextExtractor
from slidex.ocr.instruction import Instruction, parse_instruction
from slidex.ocr.models import OcrBox, OcrInput, OcrResult

__all__ = [
    "FakeOcrExtractor",
    "Instruction",
    "OcrBox",
    "OcrInput",
    "OcrResult",
    "OcrTextExtractor",
    "parse_instruction",
]


def __getattr__(name):
    # 延迟导出：llm/local 依赖 loguru 与可选的 rapidocr，导入期不该为用不到
    # 点选的调用方付出代价（llm 走 urllib，local 的引擎更是可选依赖）。
    if name in ("LlmVisionConfig", "LlmVisionExtractor"):
        from slidex.ocr import llm

        return getattr(llm, name)
    if name == "RapidOcrExtractor":
        from slidex.ocr.local import RapidOcrExtractor

        return RapidOcrExtractor
    raise AttributeError(name)
