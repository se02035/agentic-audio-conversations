"""ADK adapter for MCP audio overviews (``adk web`` / ``adk api_server``)."""

from . import _syspath as _unshadow_mcp_sdk  # noqa: F401  # isort: skip
from .agent import app, root_agent

__all__ = ["app", "root_agent"]
