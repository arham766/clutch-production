"""
Clutch Cloudflare R2 Storage layer — handle document uploads and downloads.

Path convention: ``companies/{company_id}/products/{product_id}/{filename}``
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

from src.api.deps import get_config

logger = logging.getLogger(__name__)


_s3_client = None

def _get_client() -> Any:
    """Get the initialized boto3 client for Cloudflare R2."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
        
    cfg = get_config()
    _s3_client = boto3.client(
        "s3",
        endpoint_url=f"https://{cfg.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg.r2_access_key_id,
        aws_secret_access_key=cfg.r2_secret_access_key,
        region_name="auto",
    )
    return _s3_client


def upload_file(storage_path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Upload a file directly to R2 via the backend.
    
    Returns:
        The storage path.
    """
    s3 = _get_client()
    cfg = get_config()
    
    s3.put_object(
        Bucket=cfg.r2_bucket_name,
        Key=storage_path,
        Body=data,
        ContentType=content_type,
    )
    logger.info("Uploaded %d bytes to %s", len(data), storage_path)
    return storage_path


def download_file(storage_path: str) -> bytes:
    """Download a file from R2.

    Returns:
        The raw file bytes.

    Raises:
        FileNotFoundError: If the file does not exist in Storage.
    """
    s3 = _get_client()
    cfg = get_config()
    
    try:
        response = s3.get_object(Bucket=cfg.r2_bucket_name, Key=storage_path)
        data = response["Body"].read()
        logger.info("Downloaded %d bytes from %s", len(data), storage_path)
        return data
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            raise FileNotFoundError(f"File not found in Storage: {storage_path}")
        raise


def get_download_url(storage_path: str, expiration: int = 3600) -> str:
    """Generate a signed download URL for a file."""
    s3 = _get_client()
    cfg = get_config()
    
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg.r2_bucket_name, "Key": storage_path},
        ExpiresIn=expiration,
    )
    return url


def file_exists(storage_path: str) -> bool:
    """Check if a file exists in R2."""
    s3 = _get_client()
    cfg = get_config()
    
    try:
        s3.head_object(Bucket=cfg.r2_bucket_name, Key=storage_path)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        # R2 might also return 'NoSuchKey' on head_object if not found depending on compat layer
        return False


def build_storage_path(
    company_id: str,
    product_id: str,
    filename: str,
) -> str:
    """Build the canonical storage path for a file.

    Returns:
        Path like ``companies/{company_id}/products/{product_id}/{filename}``.
    """
    return f"companies/{company_id}/products/{product_id}/{filename}"
