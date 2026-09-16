"""CLI for EU Chirp 3 HD conversation synthesis."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from tts_audio_conversation.logic.jobs.models import TERMINAL_STATUSES, JobStatus
from tts_audio_conversation.logic.service import (
    AudioConversationService,
    create_audio_conversation_service_from_adc,
)
from tts_audio_conversation.logic.settings import Settings
from tts_audio_conversation.logic.telemetry import setup_telemetry
from tts_audio_conversation.logic.template import create_script_template

load_dotenv()

console = Console()


def _service(project: str | None = None) -> AudioConversationService:
    """Build the library facade from ADC / ``--project``."""
    return create_audio_conversation_service_from_adc(Settings(), project=project)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable DEBUG logging and OpenTelemetry console traces.",
)
def main(verbose: bool = False) -> None:
    """Create conversation audio with Google Cloud TTS Chirp 3 HD in the EU."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    if verbose or os.environ.get("OTEL_TRACES_EXPORTER"):
        setup_telemetry()


@main.command(name="template")
@click.option(
    "--output",
    "-o",
    type=click.Path(writable=True, path_type=Path),
    help="File to write. Prints to stdout if omitted.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["yaml", "json"], case_sensitive=False),
    default="yaml",
    show_default=True,
    help="Template format.",
)
@click.option(
    "--language",
    "-l",
    default="en-US",
    show_default=True,
    help="BCP-47 language code (e.g. en-US, de-DE).",
)
def template_command(
    output: Path | None,
    output_format: str,
    language: str,
) -> None:
    """Write a starter 2-speaker dialogue script."""
    script = create_script_template(language_code=language)
    serialized = script.to_yaml() if output_format.lower() == "yaml" else script.to_json()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
        console.print(f"[bold green]Template written to[/bold green] [cyan]{output}[/cyan]")
    else:
        click.echo(serialized)


@main.command(name="upload")
@click.option(
    "--script",
    "-s",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Local YAML or JSON dialogue script.",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def upload_command(script: Path, project: str | None) -> None:
    """Upload a local script to staging GCS and print ``script_uri``."""
    try:
        result = _service(project).upload_script(script)
    except Exception as exc:
        console.print(f"[bold red]Upload failed:[/bold red] {exc}")
        sys.exit(1)
    console.print(f"[bold green]Uploaded[/bold green] [cyan]{result.script_uri}[/cyan]")
    console.print(f"  • script_id: {result.script_id}")


@main.command(name="validate")
@click.option(
    "--script-uri",
    required=True,
    help="gs:// URI from upload.",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def validate_command(script_uri: str, project: str | None) -> None:
    """Validate a script stored at a ``gs://`` URI."""
    result = _service(project).validate_script(script_uri)
    if not result.valid:
        console.print(f"[bold red]Script validation failed:[/bold red] {result.error}")
        sys.exit(1)
    est_duration_min = round(result.total_characters / 750, 1)
    console.print(f"[bold green]Script is valid[/bold green] ([cyan]{result.script_uri}[/cyan])")
    console.print(f"  • Title: [bold]{result.title}[/bold]")
    console.print(f"  • Language: [yellow]{result.language_code}[/yellow]")
    console.print(f"  • Speakers ({len(result.speakers)}): {', '.join(result.speakers)}")
    console.print(f"  • Dialogue Turns: {result.turn_count}")
    console.print(f"  • Total Characters: {result.total_characters}")
    console.print(f"  • Estimated Spoken Duration: ~{est_duration_min} minutes")


@main.command(name="voices")
@click.option(
    "--language",
    "-l",
    help="Optional BCP-47 language filter (e.g. de-DE, en-US).",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def voices_command(language: str | None, project: str | None) -> None:
    """List Chirp 3 HD voices from the EU ``list_voices`` API."""
    with console.status("[bold cyan]Listing Chirp 3 HD voices (EU)..."):
        results = _service(project).list_voices(language_code=language)

    table = Table(title=f"Chirp 3 HD voices (EU) — {len(results)} found")
    table.add_column("Voice Name", style="cyan", no_wrap=True)
    table.add_column("Language", style="yellow")
    table.add_column("Gender", style="green")
    for voice in results:
        table.add_row(voice.name, voice.language_code, voice.gender)
    console.print(table)


@main.command(name="translate")
@click.option(
    "--script-uri",
    required=True,
    help="gs:// URI from upload.",
)
@click.option(
    "--to",
    "-t",
    "target_language",
    required=True,
    help="Target BCP-47 language tag (e.g. de-DE).",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def translate_command(
    script_uri: str,
    target_language: str,
    project: str | None,
) -> None:
    """Translate a GCS script via translate-eu; print ``output_uri``."""
    try:
        with console.status(f"[bold cyan]Translating to {target_language} via translate-eu..."):
            result = _service(project).translate_script(script_uri, target_language)
    except Exception as exc:
        console.print(f"[bold red]Translate failed:[/bold red] {exc}")
        sys.exit(1)
    if result.skipped:
        console.print(f"[dim]Already in {result.language_code}; skipped translation.[/dim]")
    console.print(f"[bold green]output_uri[/bold green] [cyan]{result.output_uri}[/cyan]")


@main.command(name="synthesize")
@click.option(
    "--script-uri",
    required=True,
    help="gs:// URI of the script to synthesize.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(writable=True, path_type=Path),
    help="Optional local WAV download path when the job succeeds.",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def synthesize_command(
    script_uri: str,
    output: Path | None,
    project: str | None,
) -> None:
    """Start a synthesis job, poll until terminal, optionally download audio."""
    service = _service(project)

    async def _run() -> None:
        try:
            started = await service.create_audio(script_uri)
        except Exception as exc:
            console.print(f"[bold red]Start failed:[/bold red] {exc}")
            sys.exit(1)
        console.print(f"[bold green]Job[/bold green] {started.job_id} ({started.status})")
        console.print(f"  • audio_uri: [cyan]{started.audio_uri}[/cyan]")
        with console.status("[bold cyan]Waiting for synthesis..."):
            while True:
                record = await service.get_job(started.job_id)
                if record.status in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(0.5)
        if record.status != JobStatus.succeeded:
            console.print(f"[bold red]Job {record.status}[/bold red]: {record.error}")
            sys.exit(1)
        console.print("[bold green]Synthesis complete[/bold green]")
        console.print(f"  • audio_uri: [cyan]{record.audio_uri}[/cyan]")
        if output is not None:
            service.download(record.audio_uri, output)
            console.print(f"  • Local WAV: [cyan]{output}[/cyan]")

    asyncio.run(_run())


@main.command(name="status")
@click.option("--job-id", required=True, help="Job id from synthesize / start_conversation.")
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def status_command(job_id: str, project: str | None) -> None:
    """Print the current status of a synthesis job."""

    async def _run() -> None:
        try:
            record = await _service(project).get_job(job_id)
        except Exception as exc:
            console.print(f"[bold red]{exc}[/bold red]")
            sys.exit(1)
        console.print(f"status: [bold]{record.status}[/bold]")
        console.print(f"  • audio_uri: [cyan]{record.audio_uri}[/cyan]")
        console.print(f"  • script_uri: [cyan]{record.script_uri}[/cyan]")
        if record.error:
            console.print(f"  • error: {record.error}")

    asyncio.run(_run())


@main.command(name="download")
@click.option(
    "--gcs-uri",
    "-g",
    required=True,
    help="GCS URI, e.g. gs://my-bucket/conversation/jobs/.../output/audio.wav",
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(writable=True, path_type=Path),
    help="Local output path.",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def download_command(
    gcs_uri: str,
    output: Path,
    project: str | None,
) -> None:
    """Download a GCS object via the library facade."""
    try:
        with console.status(f"[bold cyan]Downloading {gcs_uri} to {output}..."):
            result = _service(project).download(gcs_uri, output)
    except Exception as exc:
        console.print(f"[bold red]Download failed:[/bold red] {exc}")
        sys.exit(1)
    console.print(f"[bold green]Downloaded to[/bold green] [cyan]{result.local_path}[/cyan]")
