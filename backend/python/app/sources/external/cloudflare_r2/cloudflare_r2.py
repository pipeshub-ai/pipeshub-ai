from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError

from app.connectors.sources.web.address_guard import GuardedResolver
from app.sources.client.cloudflare_r2.cloudflare_r2 import CloudflareR2Client
from app.sources.client.s3.s3 import S3Response
from app.utils.url_fetcher import private_addresses_blocked

# S3's wording for these names the signing algorithm, not what the admin has to change.
_ACTIONABLE_ERRORS = {
    "InvalidAccessKeyId": "Cloudflare does not know this access key; check the Access Key",
    "SignatureDoesNotMatch": "the secret key does not match the access key; check the Secret Key",
    "RequestTimeTooSkewed": "the clocks of PipesHub and Cloudflare differ by more than 15 minutes",
}


def _client_error_text(e: ClientError) -> str:
    error = e.response.get("Error", {})
    code = error.get("Code", "Unknown")
    message = (error.get("Message") or str(e)).rstrip(".")
    if code in _ACTIONABLE_ERRORS:
        return f"{code}: {_ACTIONABLE_ERRORS[code]} ({message})"
    return f"{code}: {message}"


class CloudflareR2DataSource:
    """The R2 S3 API operations PipesHub uses. Every call answers with an S3Response and does not raise."""

    def __init__(self, client: CloudflareR2Client) -> None:
        self._r2_client = client
        # Per task tree: a call from a request outside shared_client() must not get a client that scope may close.
        self._shared_client: ContextVar[Any] = ContextVar(f"r2_shared_client_{id(self)}", default=None)

    def _create_client(self) -> Any:  # noqa: ANN401 - aiobotocore's client context manager has no public type
        return self._r2_client.get_session().create_client(
            "s3",
            endpoint_url=self._r2_client.get_endpoint_url(),
            # Every connection goes to an address the guard checked when it looked the name up.
            # Merged with the session's default config (the retry budget).
            config=AioConfig(
                connector_args={"resolver": GuardedResolver(allow_private=not private_addresses_blocked())}
            ),
        )

    @asynccontextmanager
    async def shared_client(self) -> AsyncIterator[None]:
        """Make every call in this scope, and in tasks started inside it, reuse one client."""
        if self._shared_client.get() is not None:
            yield
            return
        async with self._create_client() as s3_client:
            token = self._shared_client.set(s3_client)
            try:
                yield
            finally:
                self._shared_client.reset(token)

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        shared = self._shared_client.get()
        if shared is not None:
            yield shared
            return
        async with self._create_client() as s3_client:
            yield s3_client

    async def _call(self, operation: str, **params: object) -> S3Response:
        # Callers pass None for "not set"; botocore refuses it.
        sent = {name: value for name, value in params.items() if value is not None}
        try:
            async with self._client() as s3_client:
                response = await getattr(s3_client, operation)(**sent)
        except ClientError as e:
            return S3Response(
                success=False,
                error=_client_error_text(e),
                status_code=e.response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
            )
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {str(e).rstrip('.')}")
        if response is None:
            return S3Response(success=False, error="Empty response from Cloudflare R2")
        return S3Response(success=True, data=response)

    async def list_buckets(self, **params: object) -> S3Response:
        return await self._call("list_buckets", **params)

    async def list_objects_v2(self, **params: object) -> S3Response:
        return await self._call("list_objects_v2", **params)

    async def get_bucket_location(self, **params: object) -> S3Response:
        return await self._call("get_bucket_location", **params)

    async def head_object(self, **params: object) -> S3Response:
        return await self._call("head_object", **params)

    async def get_object(self, **params: object) -> S3Response:
        """The body stays readable only while the client is open: read it inside shared_client()."""
        return await self._call("get_object", **params)

    async def put_object(self, **params: object) -> S3Response:
        return await self._call("put_object", **params)

    async def delete_object(self, **params: object) -> S3Response:
        return await self._call("delete_object", **params)

    async def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any] | None = None,
        ExpiresIn: int | None = None,
        region_name: str | None = None,
    ) -> S3Response:
        # region_name is ignored: R2 signs for "auto", whatever location a bucket reports.
        return await self._call("generate_presigned_url", ClientMethod=ClientMethod, Params=Params, ExpiresIn=ExpiresIn)
