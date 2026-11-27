import os
from uuid import uuid4

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError


@pytest.mark.skipif("STUDIO_S3_ENDPOINT" not in os.environ, reason="requires the development S3 endpoint")
def test_private_development_bucket():
    endpoint = os.environ["STUDIO_S3_ENDPOINT"]
    bucket = os.environ["STUDIO_MEDIA_BUCKET"]
    key = f"tests/{uuid4()}"
    body = b"storage smoke test"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    anonymous = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}),
    )
    client.put_object(Bucket=bucket, Key=key, Body=body)
    try:
        with client.get_object(Bucket=bucket, Key=key)["Body"] as result:
            assert result.read() == body
        with pytest.raises(ClientError) as error:
            anonymous.get_object(Bucket=bucket, Key=key)
        assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    finally:
        client.delete_object(Bucket=bucket, Key=key)
