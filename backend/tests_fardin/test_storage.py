from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from src.storage import download_file, file_exists, build_storage_path
from botocore.exceptions import ClientError

class TestDownloadFile:
    @patch("src.storage.get_config")
    @patch("src.storage._get_client")
    def test_success(self, mock_client, mock_get_config):
        mock_get_config.return_value.r2_bucket_name = "test"
        mock_s3 = MagicMock()
        mock_response = {"Body": MagicMock()}
        mock_response["Body"].read.return_value = b"data"
        mock_s3.get_object.return_value = mock_response
        mock_client.return_value = mock_s3
        
        assert download_file("path") == b"data"

    @patch("src.storage.get_config")
    @patch("src.storage._get_client")
    def test_file_not_found(self, mock_client, mock_get_config):
        mock_get_config.return_value.r2_bucket_name = "test"
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "get_object")
        mock_client.return_value = mock_s3
        
        with pytest.raises(FileNotFoundError):
            download_file("path")

class TestFileExists:
    @patch("src.storage.get_config")
    @patch("src.storage._get_client")
    def test_exists(self, mock_client, mock_get_config):
        mock_get_config.return_value.r2_bucket_name = "test"
        mock_client.return_value.head_object.return_value = {}
        assert file_exists("path") is True

    @patch("src.storage.get_config")
    @patch("src.storage._get_client")
    def test_not_exists(self, mock_client, mock_get_config):
        mock_get_config.return_value.r2_bucket_name = "test"
        mock_s3 = MagicMock()
        mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "head_object")
        mock_client.return_value = mock_s3
        assert file_exists("path") is False

class TestBuildStoragePath:
    def test_standard_path(self):
        p = build_storage_path("c1", "p1", "doc.pdf")
        assert p == "companies/c1/products/p1/doc.pdf"
