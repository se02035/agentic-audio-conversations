"""Application Default Credentials and GCP project resolution."""

from __future__ import annotations

import os

import google.auth
from google.auth.credentials import Credentials


def resolve_project_id(
    project_override: str | None = None,
    *,
    adc_project: str | None = None,
) -> str:
    """Resolve the GCP project ID: CLI flag, then env, then ADC project.

    Args:
        project_override: Explicit project ID (e.g. from ``--project``).
        adc_project: Project ID returned by ``google.auth.default()``.

    Returns:
        Non-empty GCP project ID.

    Raises:
        ValueError: If no project ID could be discovered.
    """
    if project_override and project_override.strip():
        return project_override.strip()

    env_project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if env_project and env_project.strip():
        return env_project.strip()

    if adc_project and adc_project.strip():
        return adc_project.strip()

    raise ValueError(
        "GCP Project ID could not be determined. Please supply --project, "
        "set the GOOGLE_CLOUD_PROJECT environment variable, or configure a "
        "default project via 'gcloud config set project <PROJECT_ID>'."
    )


def get_credentials_and_project(
    project_override: str | None = None,
) -> tuple[Credentials, str]:
    """Return ADC credentials and project ID from a single ``google.auth.default()`` call.

    Args:
        project_override: Explicit project ID (e.g. from ``--project``).

    Returns:
        Tuple of (credentials, project_id).

    Raises:
        DefaultCredentialsError: If Application Default Credentials are missing.
        ValueError: If a project ID cannot be resolved.
    """
    credentials, adc_project = google.auth.default(
        scopes=("https://www.googleapis.com/auth/cloud-platform",)
    )
    project_id = resolve_project_id(project_override, adc_project=adc_project)
    return credentials, project_id
