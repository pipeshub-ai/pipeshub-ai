"""TG-16: the matrix's fake Slack, Google, Microsoft and image model answer the product's own clients, as the
toolsets build them (the client factories, the vendors' SDKs routed by ``vendors.routed``), and refuse a request
without the toolset's credential. No Docker: the lane's cells (``test_tg16_toolset_categories``) rely on this."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from tests.support.config_service import FakeConfigurationService
from tests.unit.harness.engines.matrix import vendors as v

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.timeout(120), pytest.mark.asyncio(loop_scope="module")]

LOG = logging.getLogger("tg16.vendors")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def fake() -> AsyncIterator[v.FakeVendors]:
    async with v.serving() as vendors:
        with v.routed(vendors):
            yield vendors


def _config() -> FakeConfigurationService:
    return FakeConfigurationService({
        f"/services/oauths/toolsets/{kind}": [
            {"_id": f"oauth-{kind}", "config": {"clientId": "cid", "clientSecret": "secret", "tenantId": "tenant-tg16"}}
        ]
        for kind in ("gmail", "drive", "outlook", "onedrive")
    })


def _record(kind: str, token: str) -> dict[str, Any]:
    return {"toolsetType": kind, "authType": "OAUTH", "oauthConfigId": f"oauth-{kind}", "isAuthenticated": True,
            "credentials": {"access_token": token}}


async def _client(kind: str, token: str) -> Any:  # noqa: ANN401 - each vendor's client
    from app.agents.tools.factories.registry import ClientFactoryRegistry

    ClientFactoryRegistry.initialize_default_factories()
    factory = ClientFactoryRegistry.get_factory(kind)
    assert factory is not None, kind
    return await factory.create_client(_config(), LOG, _record(kind, token))


async def test_tg16_vendors_slack_channel_history(fake: v.FakeVendors) -> None:
    from app.agents.actions.slack.slack import Slack

    ok, out = await Slack(await _client("slack", v.SLACK_TOKEN)).get_channel_history(v.SLACK_CHANNEL)
    assert ok and v.SLACK_MARKER in out and "alice@tg16.example" in out, out
    assert {a for *_, a in fake.requests("slack")} == {f"Bearer {v.SLACK_TOKEN}"}


async def test_tg16_vendors_slack_refuses_another_token(fake: v.FakeVendors) -> None:
    from app.agents.actions.slack.slack import Slack

    ok, out = await Slack(await _client("slack", "xoxp-someone-else-0123456789")).get_channel_history(v.SLACK_CHANNEL)
    assert not ok and v.SLACK_MARKER not in out and "invalid_auth" in out, out


async def test_tg16_vendors_gmail_and_drive(fake: v.FakeVendors) -> None:
    from app.agents.actions.google.drive.drive import GoogleDrive
    from app.agents.actions.google.gmail.gmail import Gmail

    ok, mail = await Gmail(await _client("gmail", v.GOOGLE_TOKEN)).search_emails("invoice")
    assert ok and v.GMAIL_MARKER in mail, mail
    ok, files = await GoogleDrive(await _client("drive", v.GOOGLE_TOKEN)).get_files_list()
    assert ok and v.DRIVE_MARKER in files, files
    paths = {p for _, p, _ in fake.requests("google")}
    assert {"/google/gmail/gmail/v1/users/me/messages", "/google/www/drive/v3/files"} <= paths, paths


async def test_tg16_vendors_google_refuses_another_token(fake: v.FakeVendors) -> None:
    from app.agents.actions.google.gmail.gmail import Gmail

    ok, mail = await Gmail(await _client("gmail", "ya29.someone-else")).search_emails("invoice")
    assert not ok and v.GMAIL_MARKER not in mail, mail


async def test_tg16_vendors_outlook_and_onedrive(fake: v.FakeVendors) -> None:
    from app.agents.actions.microsoft.one_drive.one_drive import OneDrive
    from app.agents.actions.microsoft.outlook.outlook import Outlook

    ok, message = await Outlook(await _client("outlook", v.GRAPH_TOKEN), {}).get_message(v.OUTLOOK_ID)
    assert ok and v.OUTLOOK_MARKER in message, message
    ok, drives = await OneDrive(await _client("onedrive", v.GRAPH_TOKEN)).get_drives()
    assert ok and v.ONEDRIVE_MARKER in drives, drives
    graph = [(p, a) for _, p, a in fake.requests("microsoft") if p.startswith("/microsoft/graph/")]
    # Graph is asked for the token's own user, with the token as the bearer: the allowed-hosts check passed.
    assert graph and all(f"/users/{v.GRAPH_OID}/" in p and a == f"Bearer {v.GRAPH_TOKEN}" for p, a in graph), graph


async def test_tg16_vendors_graph_refuses_another_token(fake: v.FakeVendors) -> None:
    from app.agents.actions.microsoft.outlook.outlook import Outlook

    other = v._jwt({"oid": v.GRAPH_OID})  # pyright: ignore[reportPrivateUsage]
    ok, message = await Outlook(await _client("outlook", other), {}).get_message(v.OUTLOOK_ID)
    assert not ok and v.OUTLOOK_MARKER not in message, message


async def test_tg16_vendors_image_model(fake: v.FakeVendors) -> None:
    from app.utils.aimodels import get_image_generation_model

    config = {"configuration": {"model": "pxh-image-tg16", "endpoint": f"{fake.base}/images/v1", "apiKey": v.IMAGE_KEY}}
    images = await get_image_generation_model("openAICompatible", config).generate("a lighthouse", n=1)
    assert images == [v.PNG]
    assert fake.requests("images")[-1] == ("POST", "/images/v1/images/generations", f"Bearer {v.IMAGE_KEY}")
    refused = {"configuration": {**config["configuration"], "apiKey": "sk-someone-else"}}
    with pytest.raises(Exception, match="401|Incorrect API key"):
        _ = await get_image_generation_model("openAICompatible", refused).generate("a lighthouse", n=1)


def test_tg16_vendors_reroute_only_vendor_hosts() -> None:
    base = "http://127.0.0.1:1"
    assert v.rerouted("https://graph.microsoft.com/v1.0/me?$top=1", base) == "http://127.0.0.1:1/microsoft/graph/v1.0/me?$top=1"
    assert v.rerouted("https://example.com/x", base) == "https://example.com/x"
