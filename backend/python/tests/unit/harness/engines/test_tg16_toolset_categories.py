"""TG-16: every toolset category works through the Tool Gateway, one smoke call each, on E2E3's real lane.

E2E3 covers knowledge (search, fetch, lookup, navigate), Jira, MCP, code and artifacts on every engine. This module
adds the categories it left out, each as one durable opencode run assembled by the production
``PipesHubEngineAssembler``: the scripted model asks for the tool, the engine calls the run's Tool Gateway with its
run-bound token, the gateway runs the product's toolset in the worker, and the toolset reaches its service with the
credential alice's sign-in stored. Slack, Google and Microsoft are ``matrix/vendors.py`` behind their own SDKs; the
image model is its OpenAI-compatible endpoint there; storage search reads the lane's local record storage. Each case
asserts the service's own answer and that the service saw the toolset's credential, and the held sandbox is checked
for confinement and for every one of those credentials (none may reach it).

One engine: what TG-16 asks is whether each category's toolset works through the gateway, and E2E3 shows the gateway
path is the same for all three engines; the category's own code is what varies here.

``database_sandbox`` has no tool of its own on either path: main's factory and the harness's toolset source drop it
(``skip_apps``), its SQL being the code sandbox's. The case shows exactly that: never offered, refused at the gateway
when called anyway, and SQL run in the engine's own sandbox.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.engines.matrix.assembly import matrix_spec
from tests.unit.harness.engines.matrix.cells import (
    approve_all,
    assert_confined_cell,
    checks,
    engine_kind,
    tool,
)
from tests.unit.harness.engines.matrix.fixtures import (  # noqa: F401  (the lane's fixtures)
    echo_template_fixture,  # pyright: ignore[reportUnusedImport]
    matrix_fixture,  # pyright: ignore[reportUnusedImport]
)
from tests.unit.harness.engines.matrix.services import VENDOR_TOOLSETS
from tests.unit.harness.engines.matrix.vendors import (
    DRIVE_MARKER,
    GMAIL_MARKER,
    GOOGLE_TOKEN,
    GRAPH_TOKEN,
    IMAGE_KEY,
    ONEDRIVE_MARKER,
    OUTLOOK_ID,
    OUTLOOK_MARKER,
    SLACK_CHANNEL,
    SLACK_MARKER,
    SLACK_TOKEN,
    FakeVendors,
)

if TYPE_CHECKING:
    from tests.unit.harness.engines.matrix.lane import MatrixLane, RunResult

pytestmark = [
    pytest.mark.docker,
    pytest.mark.timeout(1800, method="thread"),
    pytest.mark.asyncio(loop_scope="module"),
]

ENGINE = "opencode"


def grant(kind: str) -> tuple[str, ...]:
    """The pinned tool ids of the TG-16 toolset ``kind`` (``<type>@<instance>/<tool>``)."""
    return tuple(f"{k}@{instance}/{t}" for k, instance, _, tools, _ in VENDOR_TOOLSETS if k == kind for t in tools)


def vendors(matrix: MatrixLane) -> FakeVendors:
    assert matrix.vendors is not None
    return matrix.vendors


async def run(matrix: MatrixLane, calls: list[str], *, tools: tuple[str, ...] = (), unoffered: str | None = None) -> RunResult:
    prompt = "\n".join([*calls, "hold", "say done"])
    result = await matrix.run(
        prompt, matrix_spec(matrix.org, kind=engine_kind(ENGINE), tools=tools), decide=approve_all,
        held=lambda run_id: checks(matrix, run_id, unoffered=unoffered),
    )
    assert result.status == "completed", (result.status, result.dispositions)
    return result


def assert_credential_stayed(result: RunResult, *secrets: str) -> None:
    """The service's credential never left the worker: not in the journal, not in what the model was sent."""
    sent = json.dumps([m.content for call in result.model_calls for m in call.messages])
    for secret in secrets:
        assert secret not in json.dumps(result.events)
        assert secret not in sent


async def test_tg16_slack_reads_a_channel_through_the_gateway(matrix: MatrixLane) -> None:
    """TG-16 Slack: ``slack__get_channel_history`` returns the channel's message from Slack, which saw alice's
    stored token on every call (the history and the author lookup)."""
    fake = vendors(matrix)
    before = len(fake.seen)
    result = await run(matrix, [tool("slack__get_channel_history", channel=SLACK_CHANNEL)], tools=grant("slack"))
    (history,) = result.tool_results()[:1]
    # The message, and its author as Slack's directory names her.
    assert SLACK_MARKER in history and "alice@tg16.example" in history, history[:1200]
    asked = [(m, p, a) for v, m, p, a in fake.seen[before:] if v == "slack"]
    assert {p for _, p, _ in asked} >= {"/slack/api/conversations.history", "/slack/api/users.info"}, asked
    assert {a for *_, a in asked} == {f"Bearer {SLACK_TOKEN}"}, asked
    assert_credential_stayed(result, SLACK_TOKEN)
    assert_confined_cell(matrix, result, "TG-16/slack")


async def test_tg16_google_gmail_and_drive_read_through_the_gateway(matrix: MatrixLane) -> None:
    """TG-16 Google: ``gmail__search_emails`` and ``drive__get_files_list`` answer from Gmail's and Drive's own
    endpoints, which saw alice's stored Google token."""
    fake = vendors(matrix)
    before = len(fake.seen)
    result = await run(
        matrix,
        [tool("gmail__search_emails", query="invoice"), tool("drive__get_files_list")],
        tools=(*grant("gmail"), *grant("drive")),
    )
    gmail, drive = result.tool_results()[:2]
    assert GMAIL_MARKER in gmail and "billing@vendor.example" in gmail, gmail[:1200]
    assert DRIVE_MARKER in drive, drive[:1200]
    asked = [(m, p, a) for v, m, p, a in fake.seen[before:] if v == "google"]
    paths = {p for _, p, _ in asked}
    assert "/google/gmail/gmail/v1/users/me/messages" in paths and "/google/www/drive/v3/files" in paths, asked
    assert {a for *_, a in asked} == {f"Bearer {GOOGLE_TOKEN}"}, asked
    assert_credential_stayed(result, GOOGLE_TOKEN)
    assert_confined_cell(matrix, result, "TG-16/google")


async def test_tg16_microsoft_outlook_and_onedrive_read_through_the_gateway(matrix: MatrixLane) -> None:
    """TG-16 Microsoft: ``outlook__get_message`` and ``onedrive__get_drives`` answer from Microsoft Graph for the
    signed-in user (``/users/{oid}`` from alice's token), which saw that token as the bearer."""
    fake = vendors(matrix)
    before = len(fake.seen)
    result = await run(
        matrix,
        [tool("outlook__get_message", message_id=OUTLOOK_ID), tool("onedrive__get_drives")],
        tools=(*grant("outlook"), *grant("onedrive")),
    )
    outlook, onedrive = result.tool_results()[:2]
    assert OUTLOOK_MARKER in outlook and "board@tg16.example" in outlook, outlook[:1200]
    assert ONEDRIVE_MARKER in onedrive, onedrive[:1200]
    graph = [(m, p, a) for v, m, p, a in fake.seen[before:] if v == "microsoft" and p.startswith("/microsoft/graph/")]
    assert {p.rsplit("/", 1)[-1] for _, p, _ in graph} >= {OUTLOOK_ID, "drives"}, graph
    assert {a for *_, a in graph} == {f"Bearer {GRAPH_TOKEN}"}, graph
    assert_credential_stayed(result, GRAPH_TOKEN)
    assert_confined_cell(matrix, result, "TG-16/microsoft")


async def test_tg16_image_generation_reaches_the_configured_image_model(matrix: MatrixLane) -> None:
    """TG-16 image generation: ``image_generator__generate_image`` calls the org's configured image model (an
    OpenAI-compatible endpoint, never a paid one) with its key and the run's prompt, and reports the image it got."""
    fake = vendors(matrix)
    before = len(fake.seen)
    prompt = "a lighthouse at dusk, tg16"
    result = await run(matrix, [tool("image_generator__generate_image", prompt=prompt, file_name="lighthouse")])
    (generated,) = result.tool_results()[:1]
    assert "Generated 1 image" in generated and "pxh-image-tg16" in generated, generated[:1200]
    asked = [(m, p, a) for v, m, p, a in fake.seen[before:] if v == "images"]
    assert asked == [("POST", "/images/v1/images/generations", f"Bearer {IMAGE_KEY}")], asked
    assert prompt in fake.prompts, fake.prompts
    assert_credential_stayed(result, IMAGE_KEY)
    assert_confined_cell(matrix, result, "TG-16/image")


async def test_tg16_storage_search_finds_a_record_only_storage_holds(matrix: MatrixLane) -> None:
    """TG-16 storage search: storage pattern match (``storage_pattern_match``, which ``knowledgegraph__search``
    runs beside semantic search; it is not a toolset of its own) names alice's record that only local record storage
    holds (it has no vectors), and not Bob's, whose stored file matches the same words. A grep hit is listed by its
    metadata; reading it is the next call, which the search made possible (the record counts as shown)."""
    org = matrix.org
    stored, bobs = org.ids["rec-stored"], org.ids["rec-stored-bob"]
    result = await run(
        matrix,
        [tool("knowledgegraph__search", query="KILO3307 archive audit"),
         tool("knowledgegraph__fetch_record", record_ids=[stored])],
    )
    search, fetched = result.tool_results()[:2]
    assert stored in search and "rec-stored.txt" in search, search[-2000:]
    assert bobs not in search and "rec-stored-bob.txt" not in search, search[-2000:]
    assert "KILO3307" in fetched and "LIMA9914" not in fetched, fetched[:1200]
    assert_confined_cell(matrix, result, "TG-16/storage")


async def test_tg16_knowledge_hub_lists_the_users_files(matrix: MatrixLane) -> None:
    """TG-16 knowledge hub: its file listing is ``knowledgegraph__list_files`` (main retired the ``knowledgehub``
    toolset into the knowledge graph's); it lists alice's readable files of the agent's drive, never Bob's private
    one or the trash."""
    result = await run(matrix, [tool("knowledgegraph__list_files", query="rec")])
    (listed,) = result.tool_results()[:1]
    assert "rec-alice.txt" in listed and "rec-group.txt" in listed, listed[:2000]
    assert "rec-bob.txt" not in listed and "rec-trash.txt" not in listed, listed[:2000]
    assert_confined_cell(matrix, result, "TG-16/knowledge-hub")


async def test_tg16_database_sandbox_is_sql_in_the_engine_sandbox(matrix: MatrixLane) -> None:
    """TG-16 database_sandbox: no ``database_sandbox`` tool is offered or callable (the gateway refuses it
    ``not_granted``); the engine's own sandbox runs the SQL instead."""
    sql = (
        "python3 -c \"import sqlite3; d=sqlite3.connect(':memory:'); d.execute('create table t(region, units)'); "
        "d.executemany('insert into t values (?, ?)', [('north', 1200), ('south', 800)]); "
        "print('total', d.execute('select sum(units) from t').fetchone()[0])\""
    )
    result = await run(matrix, [f"shell {sql}"], unoffered="database_sandbox__execute_sqlite")
    offered = set(result.model_calls[0].tools)
    assert not any("database_sandbox" in name for name in offered), sorted(offered)
    assert any("total 2000" in r for r in result.tool_results()), result.tool_results()
    seen = assert_confined_cell(matrix, result, "TG-16/database")
    assert "not_granted" in seen.unoffered, seen.unoffered[:600]
