"""Check of an R2 account, using the calls the connector makes.

Run from backend/python:

    export R2_ACCOUNT_ID=...          # 32 hexadecimal characters
    export R2_ACCESS_KEY_ID=...
    export R2_SECRET_ACCESS_KEY=...
    export R2_BUCKET_NAME=...         # needed when the token cannot list buckets
    export R2_JURISDICTION=eu         # only for a bucket in a jurisdiction: eu, fedramp, us
    python -m app.sources.external.cloudflare_r2.example [path/to/connected-apps.html]

Without a file this only reads. With a file it also uploads the file to R2_BUCKET_NAME under a
new key, reads it back, and deletes that one object; the token then needs Object Read & Write.

The exit code is the number of checks that failed.
"""

import asyncio
import mimetypes
import os
import sys
import uuid
from pathlib import Path

import httpx

from app.sources.client.cloudflare_r2.cloudflare_r2 import (
    CloudflareR2AccessKeyConfig,
    CloudflareR2Client,
)
from app.sources.external.cloudflare_r2.cloudflare_r2 import CloudflareR2DataSource

HTTP_FORBIDDEN = 403
PREVIEW_BYTES = 100


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def report(self, name: str, passed: bool, detail: object) -> None:  # noqa: FBT001
        self.failed += not passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")


async def check_buckets(data_source: CloudflareR2DataSource, checks: Checks, bucket_named: str | None) -> list[str]:
    """ListBuckets with the paging arguments the connector sends."""
    response = await data_source.list_buckets(MaxBuckets=1000)
    if not response.success:
        # An Object Read only token cannot list buckets, and syncing a named bucket does not need to.
        if bucket_named and response.status_code == HTTP_FORBIDDEN:
            print(f"SKIP  ListBuckets: this token cannot list buckets ({response.error})")
        else:
            checks.report("ListBuckets", False, response.error)
        return []
    names = [bucket["Name"] for bucket in response.data.get("Buckets", [])]
    checks.report("ListBuckets", True, names)
    return names


async def check_object(data_source: CloudflareR2DataSource, bucket: str, key: str, checks: Checks) -> None:
    head = await data_source.head_object(Bucket=bucket, Key=key)
    checks.report(
        "HeadObject",
        head.success,
        f"{head.data.get('ContentLength')} bytes, ETag {head.data.get('ETag')}" if head.success else head.error,
    )

    # The body is read from the open connection, so this has to run inside shared_client().
    got = await data_source.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{PREVIEW_BYTES - 1}")
    if got.success:
        async with got.data["Body"] as body:
            checks.report("GetObject", True, f"read {len(await body.read())} bytes")
    else:
        checks.report("GetObject", False, got.error)

    # How the connector serves a file. The URL is a credential, so it is never printed.
    signed = await data_source.generate_presigned_url(
        ClientMethod="get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60,
    )
    if not signed.success:
        checks.report("Presigned URL", False, signed.error)
        return
    try:
        async with httpx.AsyncClient() as http:
            download = await http.get(signed.data, headers={"Range": f"bytes=0-{PREVIEW_BYTES - 1}"})
    except httpx.HTTPError as e:
        checks.report("Presigned URL", False, type(e).__name__)
        return
    checks.report("Presigned URL", download.is_success, f"HTTP {download.status_code}, {len(download.content)} bytes")


async def check_upload(data_source: CloudflareR2DataSource, bucket: str, path: Path, checks: Checks) -> None:
    """Upload a file, read it back and delete it. The new key means nothing existing is overwritten."""
    content = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    key = f"pipeshub-r2-example/{uuid.uuid4().hex}/{path.name}"

    # A failed answer can still mean R2 stored the object, so the delete runs either way.
    try:
        uploaded = await data_source.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
        checks.report("PutObject", uploaded.success, f"{key}, {len(content)} bytes" if uploaded.success else f"{uploaded.error}; {key}")
        if not uploaded.success:
            return
        got = await data_source.get_object(Bucket=bucket, Key=key)
        if got.success:
            async with got.data["Body"] as body:
                same = await body.read() == content
            checks.report("Same bytes read back", same, got.data.get("ContentType"))
        else:
            checks.report("Same bytes read back", False, got.error)
        await check_object(data_source, bucket, key, checks)
    finally:
        deleted = await data_source.delete_object(Bucket=bucket, Key=key)
        checks.report("DeleteObject", deleted.success, key if deleted.success else f"{deleted.error}; remove {key} by hand")


async def main() -> int:
    missing = [n for n in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY") if not os.getenv(n)]
    if missing:
        print(f"Set these environment variables: {', '.join(missing)}")
        return 1

    sample = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if sample and not sample.is_file():
        print(f"No such file: {sample}")
        return 1
    # A write goes only to a bucket someone chose, never to whichever bucket is listed first.
    if sample and not os.getenv("R2_BUCKET_NAME"):
        print("Set R2_BUCKET_NAME to the bucket the file may be uploaded to.")
        return 1

    try:
        client = CloudflareR2Client.build_with_config(
            CloudflareR2AccessKeyConfig(
                access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                account_id=os.environ["R2_ACCOUNT_ID"],
                jurisdiction=os.getenv("R2_JURISDICTION"),
            )
        )
    except ValueError as e:
        print(e)
        return 1
    data_source = CloudflareR2DataSource(client)
    checks = Checks()
    print(f"Endpoint: {client.get_endpoint_url()}")

    async with data_source.shared_client():
        named = os.getenv("R2_BUCKET_NAME")
        buckets = await check_buckets(data_source, checks, named)
        bucket = named or next(iter(buckets), None)
        if not bucket:
            print("No bucket to read. Set R2_BUCKET_NAME.")
            return checks.failed + 1

        # A location R2 reports is not a signing region: requests are always signed for "auto".
        location = await data_source.get_bucket_location(Bucket=bucket)
        checks.report(
            "GetBucketLocation",
            location.success,
            location.data.get("LocationConstraint") if location.success else location.error,
        )

        listing = await data_source.list_objects_v2(Bucket=bucket, MaxKeys=5)
        objects = (listing.data.get("Contents") or []) if listing.success else []
        checks.report(
            f"ListObjectsV2 in {bucket}", listing.success, [o["Key"] for o in objects] if listing.success else listing.error,
        )
        if sample:
            await check_upload(data_source, bucket, sample, checks)
        elif objects:
            await check_object(data_source, bucket, objects[0]["Key"], checks)
        elif listing.success:
            print(f"{bucket} is empty, so the object checks were skipped. Add a file and run this again.")

    print(f"\n{checks.failed} check(s) failed")
    return checks.failed


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
