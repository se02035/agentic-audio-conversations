"""Minimal ``ToolContext`` double for ingest/create unit tests (not an API-server fake)."""

from __future__ import annotations

from typing import Any

from google.genai import types


class FakeToolContext:
    """Minimal ``ToolContext`` stand-in for ingest/create unit tests."""

    def __init__(
        self,
        artifacts: dict[str, types.Part],
        function_call_id: str = "fc-1",
        *,
        listed: list[str] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._listed = listed
        self.state: dict[str, Any] = {}
        self.function_call_id = function_call_id
        self.saved: dict[str, types.Part] = {}

    async def list_artifacts(self) -> list[str]:
        """Return current artifact filenames."""
        if self._listed is not None:
            return list(self._listed)
        return list(self._artifacts)

    async def load_artifact(self, filename: str, version: int | None = None) -> types.Part | None:
        """Load one artifact part."""
        _ = version
        return self._artifacts.get(filename)

    async def save_artifact(
        self,
        filename: str,
        artifact: types.Part,
        custom_metadata: dict[str, Any] | None = None,
    ) -> int:
        """Persist a new artifact version."""
        _ = custom_metadata
        self._artifacts[filename] = artifact
        self.saved[filename] = artifact
        return 0


def yaml_part(data: bytes, filename: str = "script.yaml") -> types.Part:
    """Wrap YAML bytes as an ADK inline artifact part."""
    return types.Part(
        inline_data=types.Blob(mime_type="application/yaml", data=data, display_name=filename)
    )
