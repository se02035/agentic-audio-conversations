"""Thin GCS helpers around ``google.cloud.storage.Blob.from_uri``."""

from __future__ import annotations

from pathlib import Path

from google.cloud import storage  # type: ignore[attr-defined]

from tts_podcast_creator.logic.exceptions import GcsResidencyError

# GCS ``bucket.location`` values that keep object data in the EU (not UK/CH).
EU_BUCKET_LOCATIONS = frozenset(
    {
        "EU",
        "EUR4",
        "EUROPE-CENTRAL2",
        "EUROPE-NORTH1",
        "EUROPE-NORTH2",
        "EUROPE-SOUTHWEST1",
        "EUROPE-WEST1",
        "EUROPE-WEST3",
        "EUROPE-WEST4",
        "EUROPE-WEST8",
        "EUROPE-WEST9",
        "EUROPE-WEST10",
        "EUROPE-WEST12",
    }
)


def format_eu_bucket_locations() -> str:
    """Return a short, stable allowlist string for errors and docs."""
    return ", ".join(sorted(EU_BUCKET_LOCATIONS))


def upload_file(
    gcs_client: storage.Client,
    gcs_uri: str,
    source_path: Path | str,
    content_type: str = "audio/wav",
) -> None:
    """Upload a local file to ``gs://bucket/object``.

    Args:
        gcs_client: Official Cloud Storage client.
        gcs_uri: Destination URI (``gs://bucket/object``).
        source_path: Local file to upload.
        content_type: Object content type.
    """
    blob = storage.Blob.from_uri(gcs_uri, client=gcs_client)
    blob.upload_from_filename(str(source_path), content_type=content_type)


def upload_bytes(
    gcs_client: storage.Client,
    gcs_uri: str,
    data: bytes,
    content_type: str = "application/json",
) -> None:
    """Upload in-memory bytes to ``gs://bucket/object``.

    Args:
        gcs_client: Official Cloud Storage client.
        gcs_uri: Destination URI (``gs://bucket/object``).
        data: Object payload.
        content_type: Object content type.
    """
    blob = storage.Blob.from_uri(gcs_uri, client=gcs_client)
    blob.upload_from_string(data, content_type=content_type)


def download_file(
    gcs_client: storage.Client,
    gcs_uri: str,
    destination_path: Path | str,
) -> None:
    """Download ``gs://bucket/object`` to a local path.

    Args:
        gcs_client: Official Cloud Storage client.
        gcs_uri: Source URI (``gs://bucket/object``).
        destination_path: Local destination file path.
    """
    target = Path(destination_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = storage.Blob.from_uri(gcs_uri, client=gcs_client)
    blob.download_to_filename(str(target))


def download_bytes(gcs_client: storage.Client, gcs_uri: str) -> bytes | None:
    """Download object bytes, or ``None`` if the object does not exist.

    Args:
        gcs_client: Official Cloud Storage client.
        gcs_uri: Source URI (``gs://bucket/object``).

    Returns:
        Object bytes, or ``None`` when the blob is missing.
    """
    blob = storage.Blob.from_uri(gcs_uri, client=gcs_client)
    if not blob.exists():
        return None
    payload: bytes = blob.download_as_bytes()
    return payload


def delete_file(gcs_client: storage.Client, gcs_uri: str) -> None:
    """Delete a GCS object if it exists.

    Args:
        gcs_client: Official Cloud Storage client.
        gcs_uri: Object URI (``gs://bucket/object``).
    """
    blob = storage.Blob.from_uri(gcs_uri, client=gcs_client)
    if blob.exists():
        blob.delete()


def is_eu_bucket_location(location: str | None) -> bool:
    """Return True when ``location`` is on the EU-only GCS allowlist."""
    if not location or not isinstance(location, str):
        return False
    normalized = location.strip().upper().replace("_", "-")
    return normalized in EU_BUCKET_LOCATIONS


def bucket_name_from_uri(gcs_uri: str) -> str:
    """Return the bucket name from ``gs://bucket/object``."""
    text = gcs_uri.strip()
    if not text.startswith("gs://"):
        raise ValueError(f"GCS URI must start with gs://, got '{gcs_uri}'.")
    bucket = text[5:].split("/", 1)[0].strip()
    if not bucket:
        raise ValueError(f"GCS URI '{gcs_uri}' is missing a bucket name.")
    return bucket


def read_bucket_location(gcs_client: storage.Client, bucket_name: str) -> str:
    """Read ``bucket.location`` from the Storage API.

    Args:
        gcs_client: Official Cloud Storage client.
        bucket_name: Bucket name without ``gs://``.

    Returns:
        Raw location string from GCS (e.g. ``EU``, ``EUROPE-WEST3``).

    Raises:
        GcsResidencyError: When location metadata is missing.
    """
    bucket = gcs_client.get_bucket(bucket_name)
    location = getattr(bucket, "location", None)
    if not isinstance(location, str) or not location.strip():
        raise GcsResidencyError(f"Could not read location for GCS bucket '{bucket_name}'.")
    return location.strip()


def require_eu_bucket(gcs_client: storage.Client, bucket_name: str) -> str:
    """Reject buckets that are not in an EU location.

    Args:
        gcs_client: Official Cloud Storage client.
        bucket_name: Bucket name without ``gs://``.

    Returns:
        The bucket location string.

    Raises:
        GcsResidencyError: When the bucket is not on the EU-only allowlist.
    """
    location = read_bucket_location(gcs_client, bucket_name)
    if not is_eu_bucket_location(location):
        raise GcsResidencyError(
            f"GCS bucket '{bucket_name}' is in '{location}', which is not EU. "
            f"Use one of: {format_eu_bucket_locations()}."
        )
    return location


def require_eu_gcs_uri(gcs_client: storage.Client, gcs_uri: str) -> str:
    """Reject ``gs://`` URIs whose bucket is not in the EU."""
    return require_eu_bucket(gcs_client, bucket_name_from_uri(gcs_uri))


def non_eu_bucket_warning(gcs_client: storage.Client, gcs_uri: str) -> str | None:
    """Return a warning if the URI's bucket is not EU; never block.

    Args:
        gcs_client: Official Cloud Storage client.
        gcs_uri: Object URI.

    Returns:
        Warning text, or ``None`` when the bucket is EU or location cannot be read.
    """
    try:
        bucket_name = bucket_name_from_uri(gcs_uri)
        location = read_bucket_location(gcs_client, bucket_name)
    except Exception:
        return None
    if is_eu_bucket_location(location):
        return None
    return (
        f"GCS bucket '{bucket_name}' is in '{location}', which is not EU. "
        "Download will continue; writes to this bucket are rejected."
    )
