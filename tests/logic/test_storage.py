"""Tests for official Cloud Storage helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tts_audio_conversation.logic.storage import (
    StorageService,
    bucket_name_from_uri,
    delete_file,
    download_bytes,
    download_file,
    upload_bytes,
    upload_file,
)


class TestStorage:
    """Blob.from_uri upload/download/delete wrappers."""

    @patch("tts_audio_conversation.logic.storage.storage.Blob.from_uri")
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

        download_file(gcs_client, "gs://my-bucket/conversation/ep1.wav", out_file)

        mock_from_uri.assert_called_once_with(
            "gs://my-bucket/conversation/ep1.wav", client=gcs_client
        )
        mock_blob.download_to_filename.assert_called_once_with(str(out_file))

    @patch("tts_audio_conversation.logic.storage.storage.Blob.from_uri")
    def test_upload_file(self, mock_from_uri: MagicMock, tmp_path: Path) -> None:
        """upload_file sends the local WAV with audio/wav content type."""
        mock_blob = MagicMock()
        mock_from_uri.return_value = mock_blob
        gcs_client = MagicMock()
        source = tmp_path / "source.wav"
        source.write_bytes(b"RIFF")

        upload_file(gcs_client, "gs://my-bucket/conversation/ep1.wav", source)

        mock_blob.upload_from_filename.assert_called_once_with(
            str(source), content_type="audio/wav"
        )

    @patch("tts_audio_conversation.logic.storage.storage.Blob.from_uri")
    def test_delete_file(self, mock_from_uri: MagicMock) -> None:
        """delete_file deletes when the blob exists."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_from_uri.return_value = mock_blob

        delete_file(MagicMock(), "gs://my-bucket/conversation/ep1.wav")

        mock_blob.delete.assert_called_once_with()

    @patch("tts_audio_conversation.logic.storage.storage.Blob.from_uri")
    def test_upload_and_download_bytes(self, mock_from_uri: MagicMock) -> None:
        """upload_bytes / download_bytes round-trip through Blob helpers."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_bytes.return_value = b"{}"
        mock_from_uri.return_value = mock_blob
        gcs_client = MagicMock()

        upload_bytes(
            gcs_client, "gs://my-bucket/conversation/job/status.json", b"{}", "application/json"
        )
        mock_blob.upload_from_string.assert_called_once_with(b"{}", content_type="application/json")

        payload = download_bytes(gcs_client, "gs://my-bucket/conversation/job/status.json")
        assert payload == b"{}"

    @patch("tts_audio_conversation.logic.storage.storage.Blob.from_uri")
    def test_download_bytes_missing(self, mock_from_uri: MagicMock) -> None:
        """Missing objects return None."""
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        mock_from_uri.return_value = mock_blob

        assert download_bytes(MagicMock(), "gs://b/missing") is None

    def test_bucket_name_from_uri(self) -> None:
        """Parse bucket names from gs:// URIs."""
        assert bucket_name_from_uri("gs://my-eu-bucket/conversation/ep.wav") == "my-eu-bucket"
        with pytest.raises(ValueError, match="gs://"):
            bucket_name_from_uri("https://example.com/x")

    @patch("tts_audio_conversation.logic.storage.storage.Blob.from_uri")
    def test_storage_service_delegates(self, mock_from_uri: MagicMock, tmp_path: Path) -> None:
        """StorageService methods use the bound client."""
        mock_blob = MagicMock()
        mock_from_uri.return_value = mock_blob
        gcs_client = MagicMock()
        service = StorageService(gcs_client)
        source = tmp_path / "a.wav"
        source.write_bytes(b"x")
        service.upload_file("gs://b/a.wav", source)
        mock_from_uri.assert_called_with("gs://b/a.wav", client=gcs_client)
