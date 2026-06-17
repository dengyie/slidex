import pytest

from slidex.vision.artifacts import build_artifact_path, safe_artifact_metadata


def test_build_artifact_path_sanitizes_components(tmp_path):
    path = build_artifact_path(
        root=tmp_path,
        run_id="../run 1",
        artifact_type="../telemetry",
        name="../events.jsonl",
    )

    assert path == tmp_path / "run_1" / "telemetry" / "events.jsonl"


def test_build_artifact_path_rejects_empty_components(tmp_path):
    with pytest.raises(ValueError, match="invalid run_id"):
        build_artifact_path(
            root=tmp_path,
            run_id="..",
            artifact_type="telemetry",
            name="events.jsonl",
        )


def test_safe_artifact_metadata_redacts_sensitive_keys():
    metadata = safe_artifact_metadata(
        {
            "source": "unit",
            "token": "secret",
            "nested": {"cookie": "abc"},
        }
    )

    assert metadata["source"] == "unit"
    assert metadata["token"] == "[redacted]"
    assert metadata["nested"]["cookie"] == "[redacted]"
