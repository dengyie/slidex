from slidex.ocr import FakeOcrExtractor, OcrResult, OcrTextExtractor


def test_fake_ocr_extracts_from_image_bytes():
    extractor = FakeOcrExtractor(text="大麦", confidence=0.98, language="zh-CN")

    result = extractor.extract(image_bytes=b"fake-png")

    assert isinstance(extractor, OcrTextExtractor)
    assert isinstance(result, OcrResult)
    assert result.text == "大麦"
    assert result.confidence == 0.98
    assert result.language == "zh-CN"
    assert result.provider == "fake"
    assert result.metadata["input"] == "image_bytes"


def test_fake_ocr_extracts_from_image_path(tmp_path):
    image_path = tmp_path / "captcha.png"
    image_path.write_bytes(b"fake-png")
    extractor = FakeOcrExtractor(text="验票")

    result = extractor.extract(image_path=image_path)

    assert result.text == "验票"
    assert result.metadata["input"] == "image_path"
    assert result.metadata["image_path"] == str(image_path)


def test_fake_ocr_accepts_roi():
    extractor = FakeOcrExtractor(text="A12")

    result = extractor.extract(
        image_bytes=b"fake-png",
        roi={"x": 10, "y": 20, "width": 30, "height": 40},
    )

    assert result.boxes[0].text == "A12"
    assert result.boxes[0].x == 10
    assert result.boxes[0].width == 30


def test_ocr_requires_image_input():
    extractor = FakeOcrExtractor(text="unused")

    result = extractor.extract()

    assert result.text == ""
    assert result.confidence == 0.0
    assert result.metadata["error_code"] == "missing_image_input"
