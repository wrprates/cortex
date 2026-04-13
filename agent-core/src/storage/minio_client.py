from __future__ import annotations

import io
from functools import lru_cache

import boto3
from botocore.client import Config

from ..config import get_settings


@lru_cache
def get_s3_client():
    settings = get_settings()
    endpoint = settings.minio_endpoint
    url = endpoint if endpoint.startswith("http") else f"http://{endpoint}"
    client = boto3.client(
        "s3",
        endpoint_url=url,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    _ensure_bucket(client, settings.minio_bucket)
    return client


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    settings = get_settings()
    client = get_s3_client()
    client.put_object(
        Bucket=settings.minio_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return f"s3://{settings.minio_bucket}/{key}"


def get_bytes(key: str) -> bytes:
    settings = get_settings()
    client = get_s3_client()
    response = client.get_object(Bucket=settings.minio_bucket, Key=key)
    return response["Body"].read()


def presigned_get(key: str, expires: int = 3600) -> str:
    settings = get_settings()
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": key},
        ExpiresIn=expires,
    )


def upload_fileobj(key: str, fileobj: io.IOBase, content_type: str = "application/octet-stream") -> str:
    settings = get_settings()
    client = get_s3_client()
    client.upload_fileobj(
        fileobj,
        settings.minio_bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"s3://{settings.minio_bucket}/{key}"
