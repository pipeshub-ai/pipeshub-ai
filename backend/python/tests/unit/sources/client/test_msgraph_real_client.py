"""The real msgraph SDK and kiota, built the way our Microsoft client builds them.

The other Microsoft client tests patch the SDK. This one does not, so a release that renames a
request builder or changes how the authentication provider is built fails here. No request is sent.
"""

import time

from azure.core.credentials import AccessToken
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from kiota_http.httpx_request_adapter import HttpxRequestAdapter
from msgraph import GraphServiceClient

from app.sources.client.microsoft.microsoft import _KeepOpenCredential


class _StubCredential:
    def __init__(self) -> None:
        self.scopes: tuple[str, ...] = ()

    async def get_token(self, *scopes: str, **_kwargs: object) -> AccessToken:
        self.scopes = scopes
        return AccessToken("stub-token", int(time.time()) + 3600)

    async def close(self) -> None:
        return None


def _auth_provider(credential: _StubCredential) -> AzureIdentityAuthenticationProvider:
    return AzureIdentityAuthenticationProvider(
        _KeepOpenCredential(credential), scopes=["https://graph.microsoft.com/.default"]
    )


def _client(credential: _StubCredential) -> GraphServiceClient:
    return GraphServiceClient(request_adapter=HttpxRequestAdapter(_auth_provider(credential)))


async def test_request_builder_chain_builds_the_messages_url() -> None:
    client = _client(_StubCredential())

    request = client.users.by_user_id("user-1").messages.to_get_request_information()

    assert request.url == "/users/user-1/messages"
    assert request.http_method.value == "GET"
    assert client.request_adapter.base_url == "https://graph.microsoft.com/v1.0"


async def test_nested_chain_keeps_every_path_segment() -> None:
    client = _client(_StubCredential())

    request = client.drives.by_drive_id("d1").items.by_drive_item_id("i1").content.to_get_request_information()

    assert request.url == "/drives/d1/items/i1/content"


async def test_the_authentication_provider_signs_a_request_offline() -> None:
    credential = _StubCredential()
    request = _client(credential).users.by_user_id("user-1").messages.to_get_request_information()
    request.url = "https://graph.microsoft.com/v1.0/users/user-1/messages"

    await _auth_provider(credential).authenticate_request(request)

    assert request.request_headers.get("authorization") == "Bearer stub-token"
    assert credential.scopes == ("https://graph.microsoft.com/.default",)
