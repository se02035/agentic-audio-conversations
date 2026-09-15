"""Tests for official Cloud Storage helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tts_podcast_creator.logic.exceptions import GcsResidencyError
from tts_podcast_creator.logic.storage import (
    bucket_name_from_uri,
    delete_file,
    download_bytes,
    download_file,
    is_eu_bucket_location,
    require_eu_bucket,
    upload_bytes,
    upload_file,
)


class TestStorage:
    """Blob.from_uri upload/download/delete wrappers."""

    @patch("tts_podcast_creator.logic.storage.storage.Blob.from_uri")
    def test_download_file(
        self,
        mock_from_uri: MagicMock,
        tmp_path: Path,
    ) -> None:
        """download_file calls Blob.from_uri and download_to_filename."""
        mock_blob = MagicMock()
        mock_from_uri.return_value = mock_blob
        gcs_client = MagicMock()
        out_file = tmp_path / "downloaded.wav"

        download_file(gcs_client, "gs://my-bucket/podcasts/ep1.wav", out_file)

        mock_from_uri.assert_called_once_with("gs://my-bucket/podcasts/ep1.wav", client=gcs_client)
        mock_blob.download_to_filename.assert_called_once_with(str(out_file))

    @patch("tts_podcast_creator.logic.storage.storage.Blob.from_uri")
    def test_upload_file(self, mock_from_uri: MagicMock, tmp_path: Path) -> None:
        """upload_file sends the local WAV with audio/wav content type."""
        mock_blob = MagicMock()
        mock_from_uri.return_value = mock_blob
        gcs_client = MagicMock()
        source = tmp_path / "episode.wav"
        source.write_bytes(b"fake")

        upload_file(gcs_client, "gs://my-bucket/podcasts/ep1.wav", source)

        mock_blob.upload_from_filename.assert_called_once_with(
            str(source), content_type="audio/wav"
        )

    @patch("tts_podcast_creator.logic.storage.storage.Blob.from_uri")
    def test_delete_file(self, mock_from_uri: MagicMock) -> None:
        """delete_file removes the blob when it exists."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_from_uri.return_value = mock_blob

        delete_file(MagicMock(), "gs://my-bucket/podcasts/ep1.wav")
        mock_blob.delete.assert_called_once()

    @patch("tts_podcast_creator.logic.storage.storage.Blob.from_uri")
    def test_upload_and_download_bytes(self, mock_from_uri: MagicMock) -> None:
        """Byte helpers round-trip through Blob.from_uri."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_bytes.return_value = b'{"status":"queued"}'
        mock_from_uri.return_value = mock_blob
        gcs_client = MagicMock()

        upload_bytes(
            gcs_client, "gs://my-bucket/podcasts/job/status.json", b"{}", "application/json"
        )
        mock_blob.upload_from_string.assert_called_once_with(b"{}", content_type="application/json")

        payload = download_bytes(gcs_client, "gs://my-bucket/podcasts/job/status.json")
        assert payload == b'{"status":"queued"}'

    @patch("tts_podcast_creator.logic.storage.storage.Blob.from_uri")
    def test_download_bytes_missing_returns_none(self, mock_from_uri: MagicMock) -> None:
        """Missing objects return None instead of raising."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_from_uri.return_value = mock_blob
        assert download_bytes(MagicMock(), "gs://my-bucket/missing.json") is None

    @patch("tts_podcast_creator.logic.storage.storage.Blob.from_uri")
    def test_invalid_uri_raises(self, mock_from_uri: MagicMock, tmp_path: Path) -> None:
        """Invalid gs:// URIs surface the SDK ValueError."""
        mock_from_uri.side_effect = ValueError("URI pattern must be gs://bucket/object")
        with pytest.raises(ValueError, match="gs://"):
            download_file(MagicMock(), "https://example.com/audio.wav", tmp_path / "x.wav")

    def test_eu_location_allowlist(self) -> None:
        """EU, EUR4, and europe-* are allowed; US/ASIA/AU are not."""
        assert is_eu_bucket_location("EU")
        assert is_eu_bucket_location("eu")
        assert is_eu_bucket_location("EUR4")
        assert is_eu_bucket_location("europe-west3")
        assert is_eu_bucket_location("EUROPE-WEST1")
        assert not is_eu_bucket_location("US")
        assert not is_eu_bucket_location("NAM4")
        assert not is_eu_bucket_location("ASIA")
        assert not is_eu_bucket_location("AU")
        assert not is_eu_bucket_location(None)
        assert not is_eu_bucket_location("")

    def test_bucket_name_from_uri(self) -> None:
        """Parse bucket names from gs:// URIs."""
        assert bucket_name_from_uri("gs://my-eu-bucket/podcasts/ep.wav") == "my-eu-bucket"

    def test_require_eu_bucket_accepts_europe_west3(self) -> None:
        """europe-west3 is accepted."""
        client = MagicMock()
        client.get_bucket.return_value.location = "EUROPE-WEST3"
        assert require_eu_bucket(client, "eu-bucket") == "EUROPE-WEST3"

    def test_require_eu_bucket_rejects_us(self) -> None:
        """US multi-region is rejected."""
        client = MagicMock()
        client.get_bucket.return_value.location = "US"
        with pytest.raises(GcsResidencyError, match="not EU"):
            require_eu_bucket(client, "us-bucket")

    def test_require_eu_bucket_rejects_missing_location(self) -> None:
        """Missing location metadata is rejected."""
        client = MagicMock()
        client.get_bucket.return_value.location = None
        with pytest.raises(GcsResidencyError, match="Could not read location"):
            require_eu_bucket(client, "mystery")
