"""CLI for EU Chirp 3 HD podcast synthesis."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from google.cloud import storage  # type: ignore[attr-defined]
from rich.console import Console
from rich.table import Table

from tts_podcast_creator.logic.auth import get_credentials_and_project
from tts_podcast_creator.logic.client import (
    EU_TTS_ENDPOINT,
    assert_voices_in_catalog,
    eu_tts_client,
    list_chirp3_voices,
    synthesize_script,
)
from tts_podcast_creator.logic.exceptions import GcsResidencyError, VoiceCatalogError
from tts_podcast_creator.logic.models import PodcastScript
from tts_podcast_creator.logic.storage import (
    download_file,
    non_eu_bucket_warning,
    require_eu_gcs_uri,
)
from tts_podcast_creator.logic.telemetry import setup_telemetry
from tts_podcast_creator.logic.template import create_script_template
from tts_podcast_creator.logic.translator import PodcastTranslator

load_dotenv()

console = Console()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable DEBUG logging and OpenTelemetry console traces.",
)
def main(verbose: bool = False) -> None:
    """Create podcast audio with Google Cloud TTS Chirp 3 HD in the EU."""
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


@main.command(name="validate")
@click.option(
    "--script",
    "-s",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="YAML or JSON dialogue script.",
)
def validate_command(script: Path) -> None:
    """Check script schema, speaker aliases, and size metrics."""
    try:
        podcast_script = PodcastScript.from_path(script)
    except Exception as exc:
        console.print(f"[bold red]Script validation failed:[/bold red] {exc}")
        sys.exit(1)

    chars = podcast_script.total_character_count
    est_duration_min = round(chars / 750, 1)
    console.print(f"[bold green]Script is valid[/bold green] ([cyan]{script}[/cyan])")
    console.print(f"  • Title: [bold]{podcast_script.metadata.title}[/bold]")
    console.print(f"  • Language: [yellow]{podcast_script.metadata.language_code}[/yellow]")
    console.print(
        f"  • Audio Encoding: [magenta]{podcast_script.metadata.audio_encoding.value}[/magenta]"
    )
    console.print(
        f"  • Speakers ({len(podcast_script.voices)}): {', '.join(podcast_script.voices)}"
    )
    console.print(f"  • Dialogue Turns: {podcast_script.turn_count}")
    console.print(f"  • Total Characters: {chars}")
    console.print(f"  • Estimated Spoken Duration: ~{est_duration_min} minutes")
    if podcast_script.metadata.audio_encoding.value != "LINEAR16":
        console.print(
            "[yellow]Note:[/yellow] synthesis always writes LINEAR16 WAV "
            f"(script declares {podcast_script.metadata.audio_encoding.value})."
        )
    long_turns = [
        i for i, turn in enumerate(podcast_script.turns, start=1) if len(turn.text) > 1500
    ]
    if long_turns:
        console.print(
            f"[yellow]Note:[/yellow] turn(s) {long_turns} exceed 1500 characters "
            "and will be split across TTS requests."
        )


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
    credentials, _project_id = get_credentials_and_project(project)
    client = eu_tts_client(credentials)
    with console.status(f"[bold cyan]Listing voices via {EU_TTS_ENDPOINT}..."):
        results = list_chirp3_voices(client, language_code=language)

    table = Table(title=f"Chirp 3 HD voices (EU) — {len(results)} found")
    table.add_column("Voice Name", style="cyan", no_wrap=True)
    table.add_column("Language", style="yellow")
    table.add_column("Gender", style="green")
    for voice in results:
        table.add_row(voice["name"], voice["language_code"], voice["gender"])
    console.print(table)


@main.command(name="translate")
@click.option(
    "--script",
    "-s",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Source YAML or JSON script.",
)
@click.option(
    "--to",
    "-t",
    "target_language",
    required=True,
    help="Target BCP-47 language tag (e.g. de-DE).",
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(writable=True, path_type=Path),
    help="Destination script path.",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def translate_command(
    script: Path,
    target_language: str,
    output: Path,
    project: str | None,
) -> None:
    """Translate turns via translate-eu and remap Chirp 3 HD voices."""
    credentials, project_id = get_credentials_and_project(project)
    source_script = PodcastScript.from_path(script)
    translator = PodcastTranslator(project_id=project_id, credentials=credentials)
    with console.status(f"[bold cyan]Translating to {target_language} via translate-eu..."):
        translated_script = translator.translate_script(source_script, target_language)

    serialized = (
        translated_script.to_yaml()
        if output.suffix.lower() in {".yaml", ".yml"}
        else translated_script.to_json()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")
    console.print(f"[bold green]Translated script saved to[/bold green] [cyan]{output}[/cyan]")


@main.command(name="synthesize")
@click.option(
    "--script",
    "-s",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="YAML or JSON dialogue script.",
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(writable=True, path_type=Path),
    help="Local WAV path for the stitched LINEAR16 audio.",
)
@click.option(
    "--gcs-uri",
    "-g",
    help="Optional GCS upload URI, e.g. gs://my-eu-bucket/podcasts/episode.wav",
)
@click.option(
    "--translate-to",
    "-t",
    help="Optional translation to a target language before synthesis.",
)
@click.option(
    "--language",
    "-l",
    help="Optional override for the script language code.",
)
@click.option(
    "--project",
    "-p",
    help="GCP project ID (falls back to GOOGLE_CLOUD_PROJECT or ADC).",
)
def synthesize_command(
    script: Path,
    output: Path,
    gcs_uri: str | None,
    translate_to: str | None,
    language: str | None,
    project: str | None,
) -> None:
    """Synthesize a local WAV via batched EU ``synthesize_speech``."""
    credentials, project_id = get_credentials_and_project(project)
    podcast_script = PodcastScript.from_path(script)
    tts_client = eu_tts_client(credentials)
    gcs_client = storage.Client(project=project_id, credentials=credentials) if gcs_uri else None

    if gcs_uri:
        assert gcs_client is not None
        try:
            require_eu_gcs_uri(gcs_client, gcs_uri)
        except GcsResidencyError as exc:
            console.print(f"[bold red]{exc}[/bold red]")
            sys.exit(1)

    try:
        assert_voices_in_catalog(tts_client, podcast_script)
    except VoiceCatalogError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        sys.exit(1)

    if translate_to:
        current_lang = podcast_script.metadata.language_code.lower()
        target_lang = translate_to.lower()
        if current_lang == target_lang or current_lang.startswith(f"{target_lang}-"):
            console.print(f"[dim]Script is already in {translate_to}; skipping translation.[/dim]")
        else:
            with console.status(
                f"[bold cyan]Translating dialogue to {translate_to} (translate-eu)..."
            ):
                translator = PodcastTranslator(project_id=project_id, credentials=credentials)
                podcast_script = translator.translate_script(podcast_script, translate_to)
            try:
                assert_voices_in_catalog(tts_client, podcast_script)
            except VoiceCatalogError as exc:
                console.print(f"[bold red]{exc}[/bold red]")
                sys.exit(1)

    if language:
        podcast_script.metadata.language_code = language

    with console.status(f"[bold cyan]Synthesizing via {EU_TTS_ENDPOINT}..."):
        dest = synthesize_script(
            tts_client,
            podcast_script,
            output,
            output_gcs_uri=gcs_uri,
            gcs_client=gcs_client,
        )

    console.print("[bold green]Synthesis complete[/bold green]")
    console.print(f"  • Local WAV: [cyan]{dest}[/cyan]")
    if gcs_uri:
        console.print(f"  • GCS: [cyan]{gcs_uri}[/cyan]")
    console.print(f"  • Endpoint: [magenta]{EU_TTS_ENDPOINT}[/magenta]")


@main.command(name="download")
@click.option(
    "--gcs-uri",
    "-g",
    required=True,
    help="GCS URI, e.g. gs://my-bucket/podcasts/ep1.wav",
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
    """Download a GCS object with the official Storage client."""
    credentials, project_id = get_credentials_and_project(project)
    gcs_client = storage.Client(project=project_id, credentials=credentials)
    warning = non_eu_bucket_warning(gcs_client, gcs_uri)
    if warning:
        console.print(f"[yellow]{warning}[/yellow]")
    try:
        with console.status(f"[bold cyan]Downloading {gcs_uri} to {output}..."):
            download_file(gcs_client, gcs_uri, output)
    except Exception as exc:
        console.print(f"[bold red]Download failed:[/bold red] {exc}")
        sys.exit(1)
    console.print(f"[bold green]Downloaded to[/bold green] [cyan]{output}[/cyan]")
