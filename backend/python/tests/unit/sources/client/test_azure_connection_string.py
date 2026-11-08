"""An Azure storage connection string is refused when the SDK could not sign with its AccountKey."""

import base64

import pytest
from azure.storage.blob import BlobServiceClient
from azure.storage.fileshare import ShareServiceClient

from app.sources.client.azure.connection_string import (
    INVALID_ACCOUNT_KEY,
    account_key_problem,
)

GOOD_KEY = base64.b64encode(bytes(range(64))).decode()
AZURITE_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="


def _account(key: str) -> str:
    return f"DefaultEndpointsProtocol=https;AccountName=acct;AccountKey={key};EndpointSuffix=core.windows.net"


CLIENTS = pytest.mark.parametrize("client_cls", [BlobServiceClient, ShareServiceClient])


@CLIENTS
@pytest.mark.parametrize(
    "connection_string",
    [
        _account(GOOD_KEY),
        _account(AZURITE_KEY),
        f"defaultendpointsprotocol=https;accountname=acct;accountkey={GOOD_KEY}",
        _account(GOOD_KEY) + "\n",
        # The SDK signs with the SAS and ignores a key without an AccountName.
        "BlobEndpoint=https://acct.blob.core.windows.net;FileEndpoint=https://acct.file.core.windows.net;"
        "SharedAccessSignature=sv=2024-01-01&sig=x;AccountKey=abc",
        "BlobEndpoint=https://acct.blob.core.windows.net;FileEndpoint=https://acct.file.core.windows.net;"
        "SharedAccessSignature=sv=2024-01-01&sig=x",
        # Not ours to judge: the client build reports a malformed string.
        "",
        "not a connection string",
    ],
    ids=["real-key", "azurite-key", "lowercase-names", "trailing-newline", "sas-with-stray-key", "sas", "empty", "malformed"],
)
def test_a_key_the_sdk_can_sign_with_passes(client_cls: type, connection_string: str) -> None:
    assert account_key_problem(connection_string, client_cls) is None


def test_development_storage_passes_for_blob() -> None:
    assert account_key_problem("UseDevelopmentStorage=true", BlobServiceClient) is None


@CLIENTS
@pytest.mark.parametrize(
    "key",
    ["abc", "not-a-real-key", GOOD_KEY[:-1], "", "abcé" + GOOD_KEY],
    ids=["too-short", "not-base64", "lost-padding", "empty", "non-ascii"],
)
def test_a_key_the_sdk_cannot_sign_with_is_refused(client_cls: type, key: str) -> None:
    assert account_key_problem(_account(key), client_cls) == INVALID_ACCOUNT_KEY


@CLIENTS
def test_lowercase_accountkey_is_checked_too(client_cls: type) -> None:
    assert account_key_problem("accountname=acct;accountkey=abc", client_cls) == INVALID_ACCOUNT_KEY


@CLIENTS
def test_a_bad_key_beside_a_sas_is_refused_when_the_account_name_is_given(client_cls: type) -> None:
    # With an AccountName the SDK signs with the key and ignores the SAS.
    connection_string = (
        "BlobEndpoint=https://acct.blob.core.windows.net;FileEndpoint=https://acct.file.core.windows.net;"
        "AccountName=acct;AccountKey=abc;SharedAccessSignature=sv=2024-01-01&sig=x"
    )

    assert account_key_problem(connection_string, client_cls) == INVALID_ACCOUNT_KEY
