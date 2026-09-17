"""Contract tests for the installable audio-conversation skill (no FastMCP client)."""

from __future__ import annotations

from pathlib import Path

from tts_audio_conversation.logic.models import ConversationScript

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = REPO_ROOT / "skills" / "audio-conversation"
SKILL_MD = SKILL_ROOT / "SKILL.md"
LOCAL_DESKTOP = SKILL_ROOT / "references" / "local-desktop.md"
GEMINI_ENTERPRISE = SKILL_ROOT / "references" / "gemini-enterprise.md"

_FINGERPRINT = (
    "upload_script",
    "validate_script",
    "translate_script",
    "start_conversation",
    "get_conversation_status",
    "cancel_conversation",
)


def test_skill_md_has_playbook_and_stays_short() -> None:
    """SKILL.md is the MCP playbook and stays under the Agent Skills size hint."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.count("\n") < 500
    assert "name: audio-conversation" in text
    for name in _FINGERPRINT:
        assert name in text
    assert "tts-audio-conversation" in text
    assert "Never start" in text or "never start" in text
    assert "npx skills add se02035/agentic-audio-conversations" in text
    assert "local-desktop.md" in text
    assert "gemini-enterprise.md" in text


def test_skill_md_does_not_ask_for_mcp_url() -> None:
    """The harness already has MCP; the skill must not prompt for a URL."""
    text = SKILL_MD.read_text(encoding="utf-8").lower()
    assert "ask for the mcp streamable" not in text
    assert "ask mcp url" not in text
    assert "--mcp-url" not in text
    assert "mcp_audio.py" not in text
    assert "never collect an mcp url" in text


def test_skill_md_selects_server_by_fingerprint() -> None:
    """Prefer configured name tts-audio-conversation; match all six tools."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "all six" in text.lower() or "exactly six" in text.lower()
    assert "prefer" in text.lower()
    assert "tts-audio-conversation" in text
    assert "server name" in text.lower()
    assert "gcloud storage" not in text


def test_skill_md_forbids_python_cli_fallback() -> None:
    """Create/status/cancel must use MCP tools, not the package CLI or library."""
    text = SKILL_MD.read_text(encoding="utf-8")
    lower = text.lower()
    assert "uv run tts-audio-conversation" in text
    assert "python cli" in lower
    assert "never run `uv run tts-audio-conversation`" in lower
    assert "start_conversation" in text
    assert "if the six mcp tools are missing" in lower
    assert "do not substitute the cli" in lower
    assert "not a shell" in lower or "not bash" in lower


def test_skill_tree_does_not_import_this_package() -> None:
    """The installable skill must not import tts_audio_conversation."""
    hits: list[str] = []
    for path in SKILL_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md"}:
            continue
        body = path.read_text(encoding="utf-8")
        if "tts_audio_conversation" in body:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_skill_has_no_scripts_directory() -> None:
    """MCP calls go through host tools; no bundled FastMCP client."""
    assert not (SKILL_ROOT / "scripts").exists()
    assert (SKILL_ROOT / "references" / "mcp-tools.md").is_file()
    assert (SKILL_ROOT / "references" / "script-schema.md").is_file()
    assert LOCAL_DESKTOP.is_file()
    assert GEMINI_ENTERPRISE.is_file()
    for path in SKILL_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md"}:
            continue
        body = path.read_text(encoding="utf-8")
        assert "mcp_audio.py" not in body
        assert "--mcp-url" not in body


def test_local_desktop_host_file_keeps_gcloud_and_tasks() -> None:
    """Desktop wait/download stays in the host file, not SKILL.md."""
    text = LOCAL_DESKTOP.read_text(encoding="utf-8")
    assert "gcloud storage objects describe" in text
    assert "gcloud storage cp" in text
    assert "/tasks" in text
    assert "local WAV path" in text.lower() or "local wav path" in text.lower()


def test_gemini_enterprise_host_file_skips_gcloud_and_local_wav() -> None:
    """GE assistant finishes on gs:// and must not treat missing gcloud as a stop."""
    text = GEMINI_ENTERPRISE.read_text(encoding="utf-8")
    lower = text.lower()
    plain = lower.replace("*", "")
    assert "do not run `gcloud`" in plain or "do not run gcloud" in plain
    assert "not a reason to refuse" in plain
    assert "audio_uri" in text
    assert "gs://" in text
    assert "local wav" in lower or "local path" in lower
    assert "zip root" in lower


def test_skill_sample_scripts_are_schema_valid() -> None:
    """Bundled assets parse as ConversationScript and are linked from SKILL.md."""
    dialogue_path = SKILL_ROOT / "assets" / "sample-dialogue.yaml"
    narration_path = SKILL_ROOT / "assets" / "sample-narration.yaml"
    assert dialogue_path.is_file()
    assert narration_path.is_file()

    dialogue = ConversationScript.from_path(dialogue_path)
    narration = ConversationScript.from_path(narration_path)
    assert len(dialogue.voices) == 2
    assert len(narration.voices) == 1
    assert len(dialogue.turns) >= 1
    assert len(narration.turns) >= 1

    skill_md = SKILL_MD.read_text(encoding="utf-8")
    assert "sample-dialogue.yaml" in skill_md
    assert "sample-narration.yaml" in skill_md
