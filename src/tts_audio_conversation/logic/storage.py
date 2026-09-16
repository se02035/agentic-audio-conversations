"""GCS object I/O via ``StorageService`` (no bucket residency checks)."""

from __future__ import annotations

from pathlib import Path

from google.cloud import storage  # type: ignore[attr-defined]


class StorageService:
    """Thin wrappers around ``google.cloud.storage.Blob.from_uri``."""

    def __init__(self, gcs_client: storage.Client) -> None:
        """Bind an official Cloud Storage client."""
        self._gcs_client = gcs_client

    def upload_file(
        self,
        gcs_uri: str,
        source_path: Path | str,
        content_type: str = "audio/wav",
    ) -> None:
        """Upload a local file to ``gs://bucket/object``."""
        blob = storage.Blob.from_uri(gcs_uri, client=self._gcs_client)
        blob.upload_from_filename(str(source_path), content_type=content_type)

    def upload_bytes(
        self,
        gcs_uri: str,
        data: bytes,
        content_type: str = "application/json",
    ) -> None:
        """Upload in-memory bytes to ``gs://bucket/object``."""
        blob = storage.Blob.from_uri(gcs_uri, client=self._gcs_client)
        blob.upload_from_string(data, content_type=content_type)

    def download_file(self, gcs_uri: str, destination_path: Path | str) -> None:
        """Download ``gs://bucket/object`` to a local path."""
        target = Path(destination_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = storage.Blob.from_uri(gcs_uri, client=self._gcs_client)
        blob.download_to_filename(str(target))

    def download_bytes(self, gcs_uri: str) -> bytes | None:
        """Download object bytes, or ``None`` if the object does not exist."""
        blob = storage.Blob.from_uri(gcs_uri, client=self._gcs_client)
        if not blob.exists():
            return None
        payload: bytes = blob.download_as_bytes()
        return payload

    def delete_file(self, gcs_uri: str) -> None:
        """Delete a GCS object if it exists."""
        blob = storage.Blob.from_uri(gcs_uri, client=self._gcs_client)
        if blob.exists():
            blob.delete()


def upload_file(
    gcs_client: storage.Client,
    gcs_uri: str,
    source_path: Path | str,
    content_type: str = "audio/wav",
) -> None:
    """Upload a local file (module-level shim for existing callers)."""
    StorageService(gcs_client).upload_file(gcs_uri, source_path, content_type=content_type)


def upload_bytes(
    gcs_client: storage.Client,
    gcs_uri: str,
    data: bytes,
    content_type: str = "application/json",
) -> None:
    """Upload bytes (module-level shim for existing callers)."""
    StorageService(gcs_client).upload_bytes(gcs_uri, data, content_type=content_type)


def download_file(
    gcs_client: storage.Client,
    gcs_uri: str,
    destination_path: Path | str,
) -> None:
    """Download a file (module-level shim for existing callers)."""
    StorageService(gcs_client).download_file(gcs_uri, destination_path)


def download_bytes(gcs_client: storage.Client, gcs_uri: str) -> bytes | None:
    """Download bytes (module-level shim for existing callers)."""
    return StorageService(gcs_client).download_bytes(gcs_uri)


def delete_file(gcs_client: storage.Client, gcs_uri: str) -> None:
    """Delete an object (module-level shim for existing callers)."""
    StorageService(gcs_client).delete_file(gcs_uri)


def bucket_name_from_uri(gcs_uri: str) -> str:
    """Return the bucket name from ``gs://bucket/object``."""
    text = gcs_uri.strip()
    if not text.startswith("gs://"):
        raise ValueError(f"GCS URI must start with gs://, got '{gcs_uri}'.")
    bucket = text[5:].split("/", 1)[0].strip()
    if not bucket:
        raise ValueError(f"GCS URI '{gcs_uri}' is missing a bucket name.")
    return bucket
