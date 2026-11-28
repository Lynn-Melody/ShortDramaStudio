from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import APIException


class StorageUnavailable(APIException):
    status_code = 503
    default_detail = "Private media storage is unavailable."


class UploadConflict(APIException):
    status_code = 409


UPLOAD_TTL_SECONDS = 300


def storage_client():
    if not settings.STUDIO_MEDIA_BUCKET:
        raise StorageUnavailable()
    try:
        return boto3.client(
            "s3",
            endpoint_url=settings.STUDIO_S3_ENDPOINT or None,
            region_name=settings.STUDIO_S3_REGION,
            config=Config(
                signature_version="s3v4",
                connect_timeout=2,
                read_timeout=2,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise StorageUnavailable() from exc


def sign_upload(upload, deadline_at):
    headers = {
        "Content-Type": upload.content_type,
        "Content-Length": str(upload.size_bytes),
        "If-None-Match": "*",
    }
    try:
        with closing(storage_client()) as client:
            ttl = min(UPLOAD_TTL_SECONDS, int((deadline_at - timezone.now()).total_seconds()))
            if ttl < 1:
                raise UploadConflict("The upload deadline is too close to issue a URL.")
            url = client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": upload.bucket,
                    "Key": upload.object_key,
                    "ContentType": upload.content_type,
                    "ContentLength": upload.size_bytes,
                    "IfNoneMatch": "*",
                },
                ExpiresIn=ttl,
                HttpMethod="PUT",
            )
        signed_at = parse_qs(urlsplit(url).query)["X-Amz-Date"][0]
        expires_at = datetime.strptime(signed_at, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) + timedelta(seconds=ttl)
    except (BotoCoreError, ClientError, KeyError, ValueError) as exc:
        raise StorageUnavailable() from exc
    if expires_at > deadline_at or expires_at <= timezone.now():
        raise UploadConflict("The signed URL falls outside the upload window.")
    return url, headers, expires_at


def verify_uploaded_object(client, upload):
    try:
        info = client.head_object(Bucket=upload.bucket, Key=upload.object_key)
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
            raise UploadConflict("The declared upload object is missing.") from exc
        raise StorageUnavailable() from exc
    except BotoCoreError as exc:
        raise StorageUnavailable() from exc

    if info["ContentLength"] != upload.size_bytes or info.get("ContentType") != upload.content_type:
        raise UploadConflict("The stored object does not match its authorized size and content type.")
    etag = info.get("ETag")
    if not etag:
        raise StorageUnavailable()
    return etag
