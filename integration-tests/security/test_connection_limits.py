"""What a connected app may see (PP-7), checked against a live stack.

The creator of an OAuth app, or the owner of a personal access token, picks
"everything" or only some sources, spaces, folders or files. The pick is signed
into every token of that app or token, never widens what the connecting user
can see, and every data path keeps to it: search, chat, a record read by id,
a download, and browsing. These tests drive each path the way an outside app
does, mostly through PipesHub's own MCP server, and check both sides: what is
inside the pick is reachable, and what is outside it is not.

The corpus is three small documents in two collections:

- ``inside``   in collection IN, folder IN/picked
- ``sibling``  in collection IN, folder IN/other   (same collection, other folder)
- ``outside``  in collection OUT

An unlimited token must reach all three; otherwise a "not found" from a limited
token would prove nothing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import requests
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pipeshub_client import PipeshubClient

from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_finished
from helper.local_auth import obtain_user_session_token
from helper.mcp_client import mcp_url
from helper.mcp_oauth import (
    authorization_code_token,
    client_credentials_token,
    create_oauth_app,
    delete_oauth_app,
    refresh_token_grant,
)

logger = logging.getLogger("connection-limits-it")

pytestmark = [pytest.mark.integration, pytest.mark.security, pytest.mark.mcp]

PATS = "/api/v1/personal-access-tokens"
OUTSIDE_MESSAGE = "not shared with this connected app"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _claims(token: str) -> dict[str, Any]:
    raw = token.removeprefix("phpat_").split(".")[1]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def _folder_id(payload: dict[str, Any]) -> str:
    for container in (payload, payload.get("folder") or {}, payload.get("data") or {}):
        if isinstance(container, dict):
            for key in ("id", "folderId", "_key"):
                if container.get(key):
                    return str(container[key])
    raise AssertionError(f"No folder id in the create response: {payload}")


def _rest_search(base_url: str, token: str, query: str, filters: dict | None = None) -> str:
    """The search response as text, to look for record ids in. Not the query: it may be echoed."""
    resp = requests.post(
        f"{base_url}/api/v1/search",
        headers=_bearer(token),
        json={"query": query, "limit": 10, **({"filters": filters} if filters else {})},
        timeout=120,
    )
    assert resp.status_code in (200, 404), f"search failed: {resp.status_code} {resp.text[:400]}"
    return resp.text


async def _call_tools(url: str, token: str, calls: list[tuple[str, dict]]) -> list[tuple[str, bool]]:
    async with Client(StreamableHttpTransport(url, auth=token), timeout=300) as client:
        results = []
        for name, args in calls:
            result = await client.call_tool_mcp(name, args)
            text = "\n".join(getattr(c, "text", "") for c in result.content)
            results.append((text + json.dumps(result.structuredContent or {}), bool(result.isError)))
        return results


def mcp(base_url: str, token: str, *calls: tuple[str, dict]) -> list[tuple[str, bool]]:
    return asyncio.run(_call_tools(mcp_url(base_url), token, list(calls)))


def _tool_calls(base_url: str, user_jwt: str, answer: str) -> list[dict[str, Any]]:
    """The tool calls a chat turn made, with their results, as the conversation stored them."""
    found = re.search(r'"conversationId":\s*"([0-9a-f]{24})"', answer)
    assert found, f"no conversationId in the chat answer: {answer[:500]}"
    resp = requests.get(f"{base_url}/api/v1/conversations/{found.group(1)}", headers=_bearer(user_jwt), timeout=30)
    assert resp.status_code == 200, f"reading the conversation: {resp.status_code} {resp.text[:300]}"
    messages = resp.json()["conversation"]["messages"]
    return [
        part for message in messages if message.get("messageType") == "bot_response"
        for part in message.get("parts") or [] if part.get("type") == "tool_call"
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_url(pipeshub_client: PipeshubClient) -> str:
    return pipeshub_client.base_url.rstrip("/")


@pytest.fixture(scope="module")
def user_jwt(base_url: str) -> str:
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        pytest.skip("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set")
    return obtain_user_session_token(base_url)


@pytest.fixture(scope="module")
def corpus(kb_client: KBClient, ai_models_configured) -> Iterator[dict[str, str]]:
    del ai_models_configured
    tag = uuid.uuid4().hex[:8]
    kb_in = kb_client.create_kb(f"pp7-in-{tag}")["id"]
    kb_out = kb_client.create_kb(f"pp7-out-{tag}")["id"]
    try:
        picked = _folder_id(kb_client.create_folder(kb_in, f"picked-{tag}"))
        other = _folder_id(kb_client.create_folder(kb_in, f"other-{tag}"))
        needles = {key: f"quillfern {key} {tag} ledger" for key in ("inside", "sibling", "outside")}

        def upload(kb_id: str, key: str, folder_id: str | None) -> str:
            body = (
                f"# {key} {tag}\n\nThe {needles[key]} records how the {key} team closes "
                f"its books each month. The {needles[key]} is kept by finance.\n"
            ).encode()
            upload = kb_client.upload_file(kb_id, f"pp7-{key}-{tag}.md", body, folder_id=folder_id,
                                           mimetype="text/markdown")
            assert upload["summary"]["failed"] == 0, upload
            return str(upload["records"][0]["recordId"])

        records = {
            "inside": upload(kb_in, "inside", picked),
            "sibling": upload(kb_in, "sibling", other),
            "outside": upload(kb_out, "outside", None),
        }
        final = asyncio.run(wait_until_finished(kb_client, list(records.values())))
        assert all(final.get(r) == "COMPLETED" for r in records.values()), final
        yield {
            "tag": tag, "kb_in": kb_in, "kb_out": kb_out, "picked_folder": picked, "other_folder": other,
            **{f"{k}_record": v for k, v in records.items()},
            **{f"{k}_needle": v for k, v in needles.items()},
        }
    finally:
        for kb_id in (kb_in, kb_out):
            try:
                kb_client.delete_kb(kb_id)
            except Exception as e:  # noqa: BLE001 - teardown must not mask test results
                logger.warning("could not delete KB %s: %s", kb_id, e)


@pytest.fixture(scope="module")
def mint_pat(base_url: str, user_jwt: str) -> Iterator[Callable[..., requests.Response]]:
    """POST a PAT with an optional knowledge limit; every minted token is revoked at the end."""
    minted: list[str] = []

    def mint(knowledge_limit: dict | None = None, *, name: str | None = None) -> requests.Response:
        body: dict[str, Any] = {"name": name or f"pp7-it-{uuid.uuid4().hex[:8]}", "expiryDays": 30}
        if knowledge_limit is not None:
            body["knowledgeLimit"] = knowledge_limit
        resp = requests.post(f"{base_url}{PATS}", json=body, headers=_bearer(user_jwt), timeout=30)
        if resp.status_code == 201:
            minted.append(resp.json()["token"]["id"])
        return resp

    yield mint
    for token_id in minted:
        try:
            requests.delete(f"{base_url}{PATS}/{token_id}", headers=_bearer(user_jwt), timeout=30)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not revoke PAT %s", token_id)


def _token(resp: requests.Response) -> str:
    assert resp.status_code == 201, f"minting a PAT: {resp.status_code} {resp.text[:300]}"
    return resp.json()["token"]["accessToken"]


@pytest.fixture(scope="module")
def unlimited_pat(mint_pat) -> str:
    return _token(mint_pat())


@pytest.fixture(scope="module")
def collection_pat(mint_pat, corpus) -> str:
    return _token(mint_pat({"apps": [corpus["kb_in"]]}))


@pytest.fixture(scope="module")
def folder_pat(mint_pat, corpus) -> str:
    return _token(mint_pat({"records": [corpus["picked_folder"]]}))


@pytest.fixture(scope="module")
def file_pat(mint_pat, corpus) -> str:
    """Limited to the one file in the picked folder, not the folder itself."""
    return _token(mint_pat({"records": [corpus["inside_record"]]}))


# ---------------------------------------------------------------------------
# Creating a limited connection
# ---------------------------------------------------------------------------

class TestCreatingALimit:
    def test_a_limit_must_name_something(self, mint_pat) -> None:
        resp = mint_pat({"apps": [], "recordGroups": [], "records": []})
        assert resp.status_code == 400, resp.text[:300]

    def test_a_limit_holds_at_most_fifty_ids(self, mint_pat) -> None:
        resp = mint_pat({"records": [str(uuid.uuid4()) for _ in range(51)]})
        assert resp.status_code == 400, resp.text[:300]

    def test_a_limit_takes_only_known_keys_and_uuids(self, mint_pat) -> None:
        assert mint_pat({"apps": ["not-a-uuid"]}).status_code == 400
        assert mint_pat({"apps": [str(uuid.uuid4())], "recordsExact": [str(uuid.uuid4())]}).status_code == 400

    def test_the_limit_is_signed_into_the_token_and_listed(
        self, base_url, user_jwt, mint_pat, corpus, collection_pat, unlimited_pat,
    ) -> None:
        assert _claims(collection_pat)["knowledgeLimit"] == {
            "apps": [corpus["kb_in"]], "recordGroups": [], "records": [],
        }
        assert "knowledgeLimit" not in _claims(unlimited_pat)
        listed = requests.get(f"{base_url}{PATS}", headers=_bearer(user_jwt), timeout=30).json()["tokens"]
        limits = [t.get("knowledgeLimit") for t in listed]
        assert {"apps": [corpus["kb_in"]], "recordGroups": [], "records": []} in limits
        assert None in limits


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_an_unlimited_token_reaches_all_three(self, base_url, unlimited_pat, corpus) -> None:
        for key in ("inside", "sibling", "outside"):
            text = _rest_search(base_url, unlimited_pat, corpus[f"{key}_needle"])
            assert corpus[f"{key}_record"] in text, f"{key} not found by an unlimited token: {text[:500]}"

    def test_a_collection_limit_hides_the_other_collection(self, base_url, collection_pat, corpus) -> None:
        assert corpus["inside_record"] in _rest_search(base_url, collection_pat, corpus["inside_needle"])
        assert corpus["sibling_record"] in _rest_search(base_url, collection_pat, corpus["sibling_needle"])
        assert corpus["outside_record"] not in _rest_search(base_url, collection_pat, corpus["outside_needle"])

    def test_a_folder_limit_hides_the_rest_of_its_collection(self, base_url, folder_pat, corpus) -> None:
        assert corpus["inside_record"] in _rest_search(base_url, folder_pat, corpus["inside_needle"])
        assert corpus["sibling_record"] not in _rest_search(base_url, folder_pat, corpus["sibling_needle"])
        assert corpus["outside_record"] not in _rest_search(base_url, folder_pat, corpus["outside_needle"])

    def test_asking_for_the_other_collection_does_not_widen_the_limit(
        self, base_url, collection_pat, corpus,
    ) -> None:
        text = _rest_search(base_url, collection_pat, corpus["outside_needle"], {"kb": [corpus["kb_out"]]})
        assert corpus["outside_record"] not in text

    @pytest.mark.parametrize("key", ["connectionApps", "connectionRecordGroups", "connectionRecords"])
    def test_a_client_cannot_send_the_servers_own_limit_keys(
        self, base_url, unlimited_pat, collection_pat, corpus, key,
    ) -> None:
        """The gateway refuses the keys the server sets the limit with, so a
        limited token cannot widen its limit with one, nor an unlimited one
        narrow itself. Behind the gateway the query service drops them too."""
        for token, ids in ((collection_pat, [corpus["kb_out"]]), (unlimited_pat, [corpus["kb_in"]])):
            resp = requests.post(
                f"{base_url}/api/v1/search",
                headers=_bearer(token),
                json={"query": corpus["outside_needle"], "limit": 10, "filters": {key: ids}},
                timeout=120,
            )
            assert resp.status_code == 400, f"{resp.status_code} {resp.text[:400]}"
            assert key in resp.text and corpus["outside_record"] not in resp.text, resp.text[:400]

    def test_the_website_session_is_not_limited(self, base_url, user_jwt, corpus) -> None:
        assert corpus["outside_record"] in _rest_search(base_url, user_jwt, corpus["outside_needle"])


# ---------------------------------------------------------------------------
# MCP: every tool an outside app uses
# ---------------------------------------------------------------------------

class TestMcp:
    def test_search_keeps_to_the_limit(self, base_url, collection_pat, corpus) -> None:
        (inside, err_in), (outside, err_out) = mcp(
            base_url, collection_pat,
            ("pipeshub_search", {"query": corpus["inside_needle"]}),
            ("pipeshub_search", {"query": corpus["outside_needle"], "kb": [corpus["kb_out"]]}),
        )
        assert not err_in and corpus["inside_record"] in inside, inside[:1000]
        assert corpus["outside_record"] not in outside, outside[:1000]

    def test_reading_a_record_outside_the_limit_is_refused_with_a_reason(
        self, base_url, collection_pat, corpus,
    ) -> None:
        (inside, err_in), (outside, err_out) = mcp(
            base_url, collection_pat,
            ("pipeshub_get_record_content", {"mode": "content", "recordId": corpus["inside_record"]}),
            ("pipeshub_get_record_content", {"mode": "content", "recordId": corpus["outside_record"]}),
        )
        assert not err_in and corpus["inside_needle"] in inside, inside[:1000]
        assert corpus["outside_needle"] not in outside, outside[:1000]
        assert err_out or OUTSIDE_MESSAGE in outside, outside[:1000]

    def test_a_budgeted_read_outside_the_limit_is_refused(self, base_url, collection_pat, corpus) -> None:
        """A read with a character budget goes to the query service, not the connector route."""
        def read(record_id: str) -> requests.Response:
            return requests.get(
                f"{base_url}/api/v1/connectors/record/{record_id}/content",
                params={"maxChars": 4000}, headers=_bearer(collection_pat), timeout=60,
            )
        inside, outside = read(corpus["inside_record"]), read(corpus["outside_record"])
        assert inside.status_code == 200 and corpus["inside_needle"] in inside.text, inside.text[:500]
        assert outside.status_code == 403, f"{outside.status_code} {outside.text[:300]}"
        assert corpus["outside_needle"] not in outside.text

    def test_a_folder_limit_refuses_a_sibling_file_by_id(self, base_url, folder_pat, corpus) -> None:
        (inside, err_in), (sibling, err_sib) = mcp(
            base_url, folder_pat,
            ("pipeshub_get_record_content", {"mode": "content", "recordId": corpus["inside_record"]}),
            ("pipeshub_get_record_content", {"mode": "content", "recordId": corpus["sibling_record"]}),
        )
        assert not err_in and corpus["inside_needle"] in inside, inside[:1000]
        assert corpus["sibling_needle"] not in sibling, sibling[:1000]

    def test_downloading_a_record_outside_the_limit_is_refused(self, base_url, collection_pat, corpus) -> None:
        (inside, err_in), (outside, err_out) = mcp(
            base_url, collection_pat,
            ("pipeshub_download_record", {"recordId": corpus["inside_record"]}),
            ("pipeshub_download_record", {"recordId": corpus["outside_record"]}),
        )
        assert not err_in, inside[:1000]
        assert err_out or OUTSIDE_MESSAGE in outside, outside[:1000]
        assert corpus["outside_needle"] not in outside

    def test_browsing_shows_only_what_is_shared(self, base_url, collection_pat, unlimited_pat, corpus) -> None:
        # Depth 3 lists records across the root's apps (app, folder, file),
        # whatever a depth-1 root lists: the limit has to hold over records.
        records = {"mode": "navigate", "depth": 3, "limit": 200}
        (root, _), (root_records, _), (opened_out, err_out) = mcp(
            base_url, collection_pat,
            ("pipeshub_get_record_content", {"mode": "navigate"}),
            ("pipeshub_get_record_content", records),
            ("pipeshub_get_record_content", {"mode": "navigate", "nodeId": corpus["kb_out"]}),
        )
        (control_records, _), = mcp(base_url, unlimited_pat, ("pipeshub_get_record_content", records))
        assert corpus["outside_record"] in control_records, "the unlimited token should list both collections' records"
        assert corpus["inside_record"] in root_records, root_records[:1500]
        assert corpus["outside_record"] not in root_records, root_records[:1500]
        assert corpus["kb_out"] not in root and corpus["outside_record"] not in root, root[:1500]
        assert err_out or OUTSIDE_MESSAGE in opened_out, opened_out[:1000]
        assert corpus["outside_record"] not in opened_out

    def test_a_folder_limit_browses_from_its_folder(self, base_url, folder_pat, corpus) -> None:
        (root, _), = mcp(base_url, folder_pat, ("pipeshub_get_record_content", {"mode": "navigate"}))
        assert corpus["picked_folder"] in root, root[:1500]
        assert corpus["other_folder"] not in root and corpus["sibling_record"] not in root, root[:1500]

    def test_the_source_list_names_only_what_is_shared(self, base_url, collection_pat, unlimited_pat, corpus) -> None:
        (limited, _), = mcp(base_url, collection_pat, ("pipeshub_sources", {"include": ["sources"]}))
        (control, _), = mcp(base_url, unlimited_pat, ("pipeshub_sources", {"include": ["sources"]}))
        assert corpus["kb_out"] in control, "the unlimited token should list both collections"
        assert corpus["kb_in"] in limited and corpus["kb_out"] not in limited, limited[:1500]

    @pytest.mark.slow
    def test_chat_cites_only_what_is_shared(self, base_url, user_jwt, collection_pat, corpus) -> None:
        """Chat goes through its own path (the turn scope), not the search route.

        The answer alone catches a leak only when the model repeats it, so every
        tool result of the turn is checked too: a name search once listed the
        outside file by name and id, and the answer mentioned it only sometimes.
        """
        (answer, err), = mcp(
            base_url, collection_pat,
            ("pipeshub_chat", {"query": f"Who keeps the {corpus['outside_needle']}? Quote the document."}),
        )
        assert not err, answer[:2000]
        assert corpus["outside_record"] not in answer, answer[:2000]
        assert "outside team closes its books" not in answer, answer[:2000]

        calls = _tool_calls(base_url, user_jwt, answer)
        assert calls, "the stored turn records no tool calls, so nothing below was checked"
        outside_name = f"pp7-outside-{corpus['tag']}"
        for call in calls:
            result = f"{call.get('resultPreview') or ''}\n{call.get('resultSummary') or ''}"
            seen = f"{call.get('toolName')}({call.get('args')}) -> {result[:1500]}"
            assert corpus["outside_record"] not in result, seen
            assert corpus["kb_out"] not in result, seen
            # A result may echo what the model asked for; only a name it was not given is a leak.
            if outside_name not in str(call.get("args") or ""):
                assert outside_name not in result, seen


# ---------------------------------------------------------------------------
# OAuth apps
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def oauth_app(base_url: str, user_jwt: str):
    app = create_oauth_app(base_url, user_jwt, name=f"pp7-it-{uuid.uuid4().hex[:10]}")
    try:
        yield app
    finally:
        try:
            delete_oauth_app(base_url, user_jwt, app.id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            pass


class TestOAuthApps:
    """The limit is the OAuth app's, picked by its creator, and holds for every token of the app."""

    def test_the_limit_picked_on_the_app_holds_for_every_token_and_after_a_refresh(
        self, base_url, user_jwt, corpus,
    ) -> None:
        app = create_oauth_app(base_url, user_jwt, name=f"pp7-it-{uuid.uuid4().hex[:10]}",
                               knowledge_limit={"apps": [corpus["kb_in"]]})
        try:
            first = authorization_code_token(base_url, user_jwt, app)
            assert _claims(first["access_token"])["knowledgeLimit"]["apps"] == [corpus["kb_in"]]
            assert corpus["outside_record"] not in _rest_search(
                base_url, first["access_token"], corpus["outside_needle"],
            )

            assert first.get("refresh_token"), "the app has offline_access, so a refresh token is expected"
            refreshed = refresh_token_grant(base_url, app, first["refresh_token"])
            assert _claims(refreshed["access_token"])["knowledgeLimit"]["apps"] == [corpus["kb_in"]]
            assert corpus["inside_record"] in _rest_search(
                base_url, refreshed["access_token"], corpus["inside_needle"],
            )

            machine = client_credentials_token(base_url, app)["access_token"]
            assert _claims(machine)["knowledgeLimit"]["apps"] == [corpus["kb_in"]]
        finally:
            delete_oauth_app(base_url, user_jwt, app.id)

    def test_an_app_without_a_limit_sees_everything(self, base_url, user_jwt, oauth_app, corpus) -> None:
        token = authorization_code_token(base_url, user_jwt, oauth_app)["access_token"]
        assert "knowledgeLimit" not in _claims(token)
        assert corpus["outside_record"] in _rest_search(base_url, token, corpus["outside_needle"])

    def test_an_app_with_a_limit_that_names_nothing_is_refused(self, base_url, user_jwt) -> None:
        with pytest.raises(RuntimeError, match="400"):
            create_oauth_app(base_url, user_jwt, name=f"pp7-it-{uuid.uuid4().hex[:10]}",
                             knowledge_limit={"apps": []})

    def test_a_new_limit_revokes_the_apps_tokens_and_a_lifted_one_keeps_them_at_their_limit(
        self, base_url, user_jwt, corpus,
    ) -> None:
        app = create_oauth_app(base_url, user_jwt, name=f"pp7-it-{uuid.uuid4().hex[:10]}")
        edit = f"{base_url}/api/v1/oauth-clients/{app.id}"
        try:
            before = authorization_code_token(base_url, user_jwt, app)
            limited = requests.put(edit, headers=_bearer(user_jwt), timeout=30,
                                   json={"knowledgeLimit": {"apps": [corpus["kb_in"]]}})
            assert limited.status_code == 200, f"{limited.status_code} {limited.text[:300]}"
            stale = requests.post(f"{base_url}/api/v1/search", headers=_bearer(before["access_token"]),
                                  json={"query": corpus["outside_needle"]}, timeout=60)
            assert stale.status_code == 401, f"a token from before the limit still works: {stale.status_code}"

            after = authorization_code_token(base_url, user_jwt, app)
            assert _claims(after["access_token"])["knowledgeLimit"]["apps"] == [corpus["kb_in"]]

            # Lifting widens what the app may reach, so connected clients stay connected, each at the
            # limit its user agreed to; the new reach comes with the next connection.
            lifted = requests.put(edit, headers=_bearer(user_jwt), timeout=30, json={"knowledgeLimit": None})
            assert lifted.status_code == 200, f"{lifted.status_code} {lifted.text[:300]}"
            # The access token issued under the limit is not revoked either.
            assert corpus["inside_record"] in _rest_search(
                base_url, after["access_token"], corpus["inside_needle"],
            )
            kept = refresh_token_grant(base_url, app, after["refresh_token"])
            assert _claims(kept["access_token"])["knowledgeLimit"]["apps"] == [corpus["kb_in"]]
            assert corpus["outside_record"] not in _rest_search(
                base_url, kept["access_token"], corpus["outside_needle"],
            )
            again = authorization_code_token(base_url, user_jwt, app)
            assert "knowledgeLimit" not in _claims(again["access_token"])
        finally:
            delete_oauth_app(base_url, user_jwt, app.id)


class TestKnowledgeBaseRoutes:
    """The collection routes an SDK uses directly, outside search and MCP."""

    def test_a_collection_outside_the_limit_cannot_be_opened_or_changed(
        self, base_url, collection_pat, unlimited_pat, corpus,
    ) -> None:
        kb_api = f"{base_url}/api/v1/knowledgeBase"
        assert requests.get(f"{kb_api}/{corpus['kb_out']}", headers=_bearer(unlimited_pat), timeout=30).status_code == 200
        opened = requests.get(f"{kb_api}/{corpus['kb_out']}", headers=_bearer(collection_pat), timeout=30)
        assert opened.status_code == 403, f"{opened.status_code} {opened.text[:300]}"
        assert requests.get(f"{kb_api}/{corpus['kb_in']}", headers=_bearer(collection_pat), timeout=30).status_code == 200

    def test_the_collection_list_names_only_what_is_shared(self, base_url, collection_pat, corpus) -> None:
        listed = requests.get(f"{base_url}/api/v1/knowledgeBase/", headers=_bearer(collection_pat),
                              params={"limit": 100}, timeout=30)
        assert listed.status_code == 200, listed.text[:300]
        assert corpus["kb_out"] not in listed.text and corpus["kb_in"] in listed.text

    def test_a_folder_limit_lists_its_folder_at_the_knowledge_hub_root(self, base_url, folder_pat, corpus) -> None:
        listed = requests.get(f"{base_url}/api/v1/knowledgeBase/knowledge-hub/nodes",
                              headers=_bearer(folder_pat), params={"limit": 50}, timeout=30)
        assert listed.status_code == 200, listed.text[:300]
        ids = [item["id"] for item in listed.json()["items"]]
        assert corpus["picked_folder"] in ids, ids
        assert not {corpus["kb_in"], corpus["kb_out"], corpus["other_folder"]} & set(ids), ids
        assert listed.json()["pagination"]["totalItems"] == len(ids)

    @pytest.mark.parametrize("token", ["folder_pat", "file_pat"])
    def test_a_narrow_limits_filter_menu_names_no_partly_shared_collection(
        self, base_url, corpus, request, token,
    ) -> None:
        pat = request.getfixturevalue(token)
        for params in ({"limit": 50}, {"limit": 50, "q": corpus["tag"]}):
            listed = requests.get(f"{base_url}/api/v1/knowledgeBase/knowledge-hub/nodes", headers=_bearer(pat),
                                  params={**params, "include": "availableFilters"}, timeout=60)
            assert listed.status_code == 200, listed.text[:300]
            available = (listed.json().get("filters") or {}).get("available") or {}
            offered = {option["id"] for option in available.get("connectors") or []}
            assert not {corpus["kb_in"], corpus["kb_out"]} & offered, offered
            assert f"pp7-in-{corpus['tag']}" not in str(available)

    def test_a_folder_limited_search_pages_over_what_it_shares(self, base_url, folder_pat, corpus) -> None:
        """Every name in the corpus carries the tag; the limit shares only the folder and its file."""
        url = f"{base_url}/api/v1/knowledgeBase/knowledge-hub/nodes"
        first = requests.get(url, headers=_bearer(folder_pat), params={"q": corpus["tag"], "limit": 1}, timeout=60)
        assert first.status_code == 200, first.text[:300]
        page = first.json()["pagination"]
        assert len(first.json()["items"]) == 1 and page["hasNext"] and page["nextCursor"], page
        second = requests.get(url, headers=_bearer(folder_pat),
                              params={"cursor": page["nextCursor"], "limit": 1}, timeout=60)
        assert second.status_code == 200, second.text[:300]
        found = {item["id"] for body in (first.json(), second.json()) for item in body["items"]}
        assert found == {corpus["picked_folder"], corpus["inside_record"]}, found
        assert second.json()["pagination"]["hasNext"] is False
        assert second.json()["pagination"]["totalItems"] == 2

    def test_a_file_limit_does_not_name_the_folder_above_the_file(
        self, base_url, file_pat, folder_pat, corpus,
    ) -> None:
        folder, folder_name = corpus["picked_folder"], f"picked-{corpus['tag']}"
        listed = requests.get(f"{base_url}/api/v1/knowledgeBase/knowledge-hub/nodes", headers=_bearer(file_pat),
                              params={"q": corpus["tag"], "limit": 50}, timeout=60)
        assert listed.status_code == 200, listed.text[:300]
        rows = {item["id"]: item for item in listed.json()["items"]}
        assert corpus["inside_record"] in rows, list(rows)
        assert rows[corpus["inside_record"]].get("parent") is None
        assert rows[corpus["inside_record"]].get("parentId") is None
        assert folder not in listed.text and folder_name not in listed.text

        searched = _rest_search(base_url, file_pat, corpus["inside_needle"])
        assert corpus["inside_record"] in searched
        assert folder not in searched and folder_name not in searched
        # Nor the collection, by its name on each hit.
        assert f"pp7-in-{corpus['tag']}" not in searched

        # The folder's own limit shares the folder, so the same file names it.
        assert folder in _rest_search(base_url, folder_pat, corpus["inside_needle"])

    def test_a_file_limit_reads_its_file_without_the_folder_or_its_path(
        self, base_url, file_pat, collection_pat, corpus,
    ) -> None:
        folder, folder_name = corpus["picked_folder"], f"picked-{corpus['tag']}"
        record_url = f"{base_url}/api/v1/knowledgeBase/record/{corpus['inside_record']}"
        details = requests.get(record_url, headers=_bearer(file_pat), timeout=60)
        assert details.status_code == 200, details.text[:300]
        body = details.json()
        assert body.get("folder") is None and body.get("knowledgeBase") is None
        assert ((body.get("record") or {}).get("fileRecord") or {}).get("path") is None
        assert folder not in details.text and folder_name not in details.text

        content = requests.get(f"{base_url}/api/v1/connectors/record/{corpus['inside_record']}/content",
                               headers=_bearer(file_pat), timeout=120)
        assert content.status_code == 200, content.text[:300]
        assert corpus["inside_needle"] in content.text
        assert folder not in content.text and folder_name not in content.text

        # The whole collection's limit shares the folder, so the same file names
        # it, by its source parent: the details' ``folder`` is not filled for a
        # collection file on either store, limited or not.
        whole = requests.get(record_url, headers=_bearer(collection_pat), timeout=60)
        assert whole.status_code == 200, whole.text[:300]
        assert (whole.json().get("record") or {}).get("externalParentId") == folder, whole.text[:500]
