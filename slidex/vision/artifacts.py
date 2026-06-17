from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from slidex.vision.models import redact_sensitive


def _sanitize_component(value: str, field_name: str) -> str:
    cleaned = str(value).replace("\\", "/").split("/")[-1].strip()
    if cleaned in {"", ".", ".."}:
        raise ValueError(f"invalid {field_name}")
    return cleaned.replace(" ", "_")


def build_artifact_path(root: Path, run_id: str, artifact_type: str, name: str) -> Path:
    return (
        root
        / _sanitize_component(run_id, "run_id")
        / _sanitize_component(artifact_type, "artifact_type")
        / _sanitize_component(name, "artifact name")
    )


def safe_artifact_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return redact_sensitive(metadata)
