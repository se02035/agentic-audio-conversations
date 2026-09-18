"""ADK A2A (Agent-to-Agent) Starlette application for audio conversations.

Exposes the ADK ``LlmAgent`` over the Agent2Agent (A2A) protocol using
ADK's ``to_a2a`` utility function. Runs as an ASGI Starlette application
via Uvicorn.
"""

from __future__ import annotations

from . import _syspath as _unshadow_mcp_sdk  # noqa: F401  # isort: skip

import argparse
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import uvicorn
from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .agent import BuiltAgent, build_app
from .config import AgentSettings, load_repo_dotenv

__all__ = ["AgentCardBuilder", "build_a2a_app", "get_a2a_app", "main"]


def build_a2a_app(
    settings: AgentSettings | None = None,
    *,
    built_agent: BuiltAgent | None = None,
    download_bytes_fn: Any | None = None,
    agent_card: Any | None = None,
    a2a_agent_endpoint: str | None = None,
) -> Starlette:
    """Wire the ADK agent into an A2A Starlette application.

    Args:
        settings: Override env-backed settings (host, port, rpc_path, endpoint, etc.).
        built_agent: Optional pre-built agent and app. If None, built via ``build_app``.
        download_bytes_fn: Optional ``gs://`` downloader for the resume plugin.
        agent_card: Optional pre-built ``AgentCard`` or card JSON path.
        a2a_agent_endpoint: Optional advertised public RPC endpoint URL (e.g. tunnel address).

    Returns:
        A Starlette ASGI application exposing the A2A endpoints.
    """
    load_repo_dotenv()
    cfg = settings or AgentSettings()
    built = built_agent or build_app(cfg, download_bytes_fn=download_bytes_fn)

    if a2a_agent_endpoint is not None:
        effective_endpoint: str | None = a2a_agent_endpoint
    elif agent_card is not None:
        effective_endpoint = None
    else:
        effective_endpoint = cfg.effective_a2a_agent_endpoint

    runner = Runner(
        app=built.app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
    )

    card_source = agent_card if agent_card is not None else cfg.adk_a2a_agent_card_path

    # If a pre-built AgentCard protobuf was passed, patch its interface URL if an endpoint is set
    if (
        effective_endpoint
        and card_source is not None
        and hasattr(card_source, "supported_interfaces")
    ):
        for iface in card_source.supported_interfaces:
            iface.url = effective_endpoint.rstrip("/")

    @asynccontextmanager
    async def _lifespan(app: Starlette) -> AsyncIterator[None]:
        if effective_endpoint:
            for route in app.routes:
                if "agent-card" in getattr(route, "path", ""):
                    endpoint = getattr(route, "endpoint", None)
                    closure = getattr(endpoint, "__closure__", None)
                    if closure:
                        for cell in closure:
                            obj = getattr(cell, "cell_contents", None)
                            if obj is not None and hasattr(obj, "supported_interfaces"):
                                for iface in obj.supported_interfaces:
                                    iface.url = effective_endpoint.rstrip("/")
        yield

    app = to_a2a(
        agent=built.root_agent,
        host=cfg.adk_a2a_host,
        port=cfg.adk_a2a_port,
        protocol=cfg.adk_a2a_protocol,
        rpc_path=cfg.adk_a2a_rpc_path,
        agent_card=card_source,
        runner=runner,
        lifespan=_lifespan,
    )

    # Legacy /.well-known/agent.json returns A2A v0.3 schema for Gemini Enterprise
    prefix = f"/{cfg.adk_a2a_rpc_path.strip('/')}" if cfg.adk_a2a_rpc_path.strip("/") else ""
    target_card_path = f"{prefix}/.well-known/agent-card.json"
    legacy_card_path = f"{prefix}/.well-known/agent.json"

    async def _legacy_card_handler(request: Request) -> Response:
        for route in app.routes:
            if "agent-card" in getattr(route, "path", ""):
                endpoint = getattr(route, "endpoint", None)
                if callable(endpoint):
                    resp = cast(Response, await endpoint(request))
                    try:
                        raw = json.loads(bytes(resp.body).decode("utf-8"))
                        if isinstance(raw, dict):
                            ifaces = raw.pop("supportedInterfaces", [])
                            default_url = (
                                effective_endpoint.rstrip("/")
                                if effective_endpoint
                                else f"{cfg.adk_a2a_protocol}://{cfg.adk_a2a_host}:{cfg.adk_a2a_port}"
                            )
                            url = ifaces[0].get("url", default_url) if ifaces else default_url
                            raw["url"] = url
                            raw["protocolVersion"] = "0.3.0"
                            raw["preferredTransport"] = (
                                ifaces[0].get("protocolBinding", "JSONRPC") if ifaces else "JSONRPC"
                            )
                            return JSONResponse(raw)
                    except Exception:
                        pass
                    return resp
        return RedirectResponse(url=target_card_path, status_code=307)

    app.routes.append(
        Route(
            path=legacy_card_path,
            endpoint=_legacy_card_handler,
            methods=["GET"],
        )
    )

    return app


_cached_app: Starlette | None = None


def get_a2a_app() -> Starlette:
    """Return the cached or newly built default A2A Starlette app."""
    global _cached_app
    if _cached_app is None:
        _cached_app = build_a2a_app()
    return _cached_app


def __getattr__(name: str) -> Any:
    if name == "a2a_app":
        return get_a2a_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: list[str] | None = None) -> None:
    """Run the A2A agent with Uvicorn."""
    load_repo_dotenv()
    settings = AgentSettings()

    parser = argparse.ArgumentParser(
        description="Run the ADK audio overview agent with A2A protocol support."
    )
    parser.add_argument(
        "--host",
        default=settings.adk_a2a_host,
        help=f"Bind host (default: {settings.adk_a2a_host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.adk_a2a_port,
        help=f"Bind port (default: {settings.adk_a2a_port})",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Public advertised endpoint URL (e.g. https://my-tunnel.ngrok.app)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on file changes",
    )
    args = parser.parse_args(argv)

    updates: dict[str, Any] = {"adk_a2a_host": args.host, "adk_a2a_port": args.port}
    if args.endpoint:
        updates["adk_a2a_agent_endpoint"] = args.endpoint
    cfg = settings.model_copy(update=updates)

    if not args.reload:
        app = build_a2a_app(cfg)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        os.environ["ADK_A2A_HOST"] = args.host
        os.environ["ADK_A2A_PORT"] = str(args.port)
        if args.endpoint:
            os.environ["ADK_A2A_AGENT_ENDPOINT"] = args.endpoint
        uvicorn.run(
            "tts_audio_conversation.adk.a2a:a2a_app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level="info",
        )


if __name__ == "__main__":
    main()
