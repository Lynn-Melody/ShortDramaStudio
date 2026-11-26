import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError


def ensure_bucket(client, bucket):
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    client.head_bucket(Bucket=bucket)


if __name__ == "__main__":
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["STUDIO_S3_ENDPOINT"],
        region_name="us-east-1",
        config=Config(
            connect_timeout=2,
            read_timeout=2,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )
    for attempt in range(30):
        try:
            ensure_bucket(client, os.environ["STUDIO_MEDIA_BUCKET"])
            break
        except EndpointConnectionError:
            if attempt == 29:
                raise
            time.sleep(2)
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 503 or attempt == 29:
                raise
            time.sleep(2)
