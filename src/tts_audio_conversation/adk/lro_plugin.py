"""Agent client: poll MCP off-request, then resume with a matching FunctionResponse."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps.app import App
from google.adk.events.event import Event
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from tts_audio_conversation.logic.jobs.models import JobStatus

from .artifact_ids import (
    OVERVIEWS_STATE_KEY,
    PENDING_LRO_STATE_KEY,
    audio_artifact_name,
    upsert_audio_overview,
)
from .config import AgentSettings
from .create_audio import CREATE_AUDIO_TOOL_NAME
from .job_poll import (
    JobPollTimeout,
    is_terminal_status,
    terminal_function_response,
    wait_for_terminal_job,
)
from .mcp_client import McpConversationClient

logger = logging.getLogger(__name__)

# Stashed on pending LRO state so ``before_agent_callback`` can copy it onto
# ``EventActions.artifact_delta``. ADK Web's Artifacts tab only lists files
# that appear in a session event's artifactDelta (not raw ``save_artifact``).
_AUDIO_ARTIFACT_VERSION_KEY = "audio_artifact_version"


class ConversationJobClientPlugin(BasePlugin):
    """Poll ``get_conversation_status`` after LRO start; resume the agent run."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        client: McpConversationClient,
    ) -> None:
        """Bind poll knobs and the MCP client used off the request path.

        Args:
            settings: Poll interval/timeout from env.
            client: HTTP client for status + WAV download.
        """
        super().__init__(name="conversation_job_client")
        self._settings = settings
        self._client = client
        self._app: App | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._inflight: set[tuple[str, str]] = set()

    def bind_app(self, app: App) -> None:
        """Record the ``App`` so a resume ``Runner`` can share plugins/root agent."""
        self._app = app

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Stash ``function_call_id`` next to the queued job snapshot."""
        _ = tool_args
        if tool.name != CREATE_AUDIO_TOOL_NAME or not isinstance(result, dict):
            return None
        pending = dict(tool_context.state.get(PENDING_LRO_STATE_KEY) or {})
        pending.update(
            {
                "job_id": result.get("job_id") or pending.get("job_id"),
                "status": result.get("status") or pending.get("status"),
                "script_uri": result.get("script_uri") or pending.get("script_uri"),
                "audio_uri": result.get("audio_uri") or pending.get("audio_uri"),
                "function_call_id": tool_context.function_call_id
                or pending.get("function_call_id"),
                "tool_name": CREATE_AUDIO_TOOL_NAME,
            }
        )
        tool_context.state[PENDING_LRO_STATE_KEY] = pending
        return None

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> types.Content | None:
        """Copy a saved WAV onto ``artifact_delta`` so ADK Web lists it.

        ``save_artifact`` alone writes the file; Dev UI only renders names from
        session ``actions.artifactDelta``. Mutating pending state after the copy
        forces ADK to persist those actions on a session event.

        Args:
            agent: Root agent about to run (unused).
            callback_context: Resume invocation; may include a WAV version.

        Returns:
            Always ``None`` so the model turn continues.
        """
        _ = agent
        pending = dict(callback_context.state.get(PENDING_LRO_STATE_KEY) or {})
        version = pending.pop(_AUDIO_ARTIFACT_VERSION_KEY, None)
        filename = pending.get("audio_artifact")
        if not filename or version is None:
            return None
        callback_context.actions.artifact_delta[str(filename)] = int(version)
        callback_context.state[PENDING_LRO_STATE_KEY] = pending
        return None

    async def on_event_callback(
        self,
        *,
        invocation_context: InvocationContext,
        event: Event,
    ) -> Event | None:
        """Copy long-running function_call ids from the model event into state."""
        lro_ids = event.long_running_tool_ids or set()
        if not lro_ids or event.content is None or not event.content.parts:
            return None
        pending = dict(invocation_context.session.state.get(PENDING_LRO_STATE_KEY) or {})
        for part in event.content.parts:
            call = part.function_call
            if call is None or not call.id or call.id not in lro_ids:
                continue
            if call.name and call.name != CREATE_AUDIO_TOOL_NAME:
                continue
            pending["function_call_id"] = call.id
            pending["tool_name"] = call.name or CREATE_AUDIO_TOOL_NAME
            invocation_context.session.state[PENDING_LRO_STATE_KEY] = pending
        return None

    async def after_run_callback(self, *, invocation_context: InvocationContext) -> None:
        """Start a background poller after the HTTP turn that queued the job."""
        pending = dict(invocation_context.session.state.get(PENDING_LRO_STATE_KEY) or {})
        job_id = str(pending.get("job_id") or "")
        function_call_id = str(pending.get("function_call_id") or "")
        if not job_id or not function_call_id:
            return
        if is_terminal_status(pending.get("status")):
            return
        key = (invocation_context.session.id, job_id)
        if key in self._inflight:
            return
        self._inflight.add(key)
        task = asyncio.create_task(
            self._poll_and_resume(invocation_context, pending),
            name=f"audio-lro-{job_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_inflight(self, timeout_sec: float = 120.0) -> None:
        """Await background resume tasks (tests)."""
        if not self._tasks:
            return
        done, pending = await asyncio.wait(self._tasks, timeout=timeout_sec)
        if pending:
            raise TimeoutError(
                f"{len(pending)} LRO resume task(s) still running after {timeout_sec:g}s"
            )
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc

    async def close(self) -> None:
        """Cancel background pollers when the runner shuts down."""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._inflight.clear()

    async def _poll_and_resume(
        self,
        invocation_context: InvocationContext,
        pending: dict[str, Any],
    ) -> None:
        job_id = str(pending["job_id"])
        session_id = invocation_context.session.id
        key = (session_id, job_id)
        try:
            payload, wav_bytes, audio_name = await self._terminal_payload(pending)
            if wav_bytes is not None and audio_name is not None:
                version = await self._save_wav(invocation_context, audio_name, wav_bytes)
                if version is not None:
                    pending = {
                        **pending,
                        "audio_artifact": audio_name,
                        _AUDIO_ARTIFACT_VERSION_KEY: version,
                    }
            await self._resume_agent(invocation_context, pending, payload)
        except Exception:
            logger.exception("Failed to resume audio job %s", job_id)
            raise
        finally:
            self._inflight.discard(key)

    async def _terminal_payload(
        self,
        pending: dict[str, Any],
    ) -> tuple[dict[str, Any], bytes | None, str | None]:
        job_id = str(pending["job_id"])
        language_code = str(pending.get("language_code") or "und")
        try:
            job = await wait_for_terminal_job(
                self._client.get_conversation_status,
                job_id,
                interval_sec=self._settings.audio_conversation_job_poll_interval_sec,
                timeout_sec=self._settings.audio_conversation_job_poll_timeout_sec,
            )
        except JobPollTimeout as exc:
            failed = {
                "job_id": job_id,
                "status": JobStatus.failed.value,
                "script_uri": pending.get("script_uri") or "",
                "audio_uri": pending.get("audio_uri") or "",
                "error": str(exc),
            }
            return terminal_function_response(failed, poll_error=str(exc)), None, None
        audio_name: str | None = None
        wav_bytes: bytes | None = None
        download_error: str | None = None
        if str(job.get("status")) == JobStatus.succeeded.value:
            audio_uri = str(job.get("audio_uri") or "")
            wav_bytes = self._client.download_audio(audio_uri) if audio_uri else None
            if wav_bytes:
                audio_name = audio_artifact_name(job_id, language_code)
            else:
                download_error = f"WAV object missing at {audio_uri or '(empty audio_uri)'}"
        payload = terminal_function_response(
            job,
            audio_artifact=audio_name,
            download_error=download_error,
        )
        if audio_name:
            payload["script_artifact"] = pending.get("script_artifact") or ""
            payload["language_code"] = language_code
        return payload, wav_bytes, audio_name

    async def _save_wav(
        self,
        invocation_context: InvocationContext,
        filename: str,
        data: bytes,
    ) -> int | None:
        service = invocation_context.artifact_service
        if service is None:
            logger.warning("No artifact service; skipping save of %s", filename)
            return None
        return await service.save_artifact(
            app_name=invocation_context.app_name,
            user_id=invocation_context.user_id,
            session_id=invocation_context.session.id,
            filename=filename,
            artifact=types.Part(
                inline_data=types.Blob(
                    mime_type="audio/wav",
                    data=data,
                    display_name=filename,
                )
            ),
        )

    async def _resume_agent(
        self,
        invocation_context: InvocationContext,
        pending: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        if self._app is None:
            raise RuntimeError("ConversationJobClientPlugin.bind_app() was not called")
        function_call_id = str(pending["function_call_id"])
        tool_name = str(pending.get("tool_name") or CREATE_AUDIO_TOOL_NAME)
        overview = {
            "script_id": invocation_context.session.state.get("latest_script_id"),
            "script_uri": payload.get("script_uri"),
            "script_artifact": pending.get("script_artifact"),
            "job_id": payload.get("job_id"),
            "audio_artifact": payload.get("audio_artifact"),
            "language_code": pending.get("language_code"),
            "status": payload.get("status"),
        }
        overviews = list(invocation_context.session.state.get(OVERVIEWS_STATE_KEY) or [])
        state_delta = {
            PENDING_LRO_STATE_KEY: {**pending, "status": payload.get("status")},
            OVERVIEWS_STATE_KEY: upsert_audio_overview(overviews, overview),
        }
        runner = Runner(
            app=self._app,
            session_service=invocation_context.session_service,
            artifact_service=invocation_context.artifact_service,
            memory_service=invocation_context.memory_service,
            credential_service=invocation_context.credential_service,
        )
        message = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=function_call_id,
                        name=tool_name,
                        response=payload,
                    )
                )
            ],
        )
        async for _event in runner.run_async(
            user_id=invocation_context.user_id,
            session_id=invocation_context.session.id,
            new_message=message,
            state_delta=state_delta,
        ):
            pass
