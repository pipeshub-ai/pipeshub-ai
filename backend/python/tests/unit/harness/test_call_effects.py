"""C-3: ``salesforce.soql_query`` is classified per call: a plain SELECT reads, anything else writes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.harness.assembly.call_effects import (
    CALL_EFFECTS,
    call_effects_for,
    soql_query_effect,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.tools.decorators import TOOL_META_ATTR
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder

READS = [
    "SELECT Id, Name FROM Account WHERE Industry = 'Technology' LIMIT 10",
    "  select Id from Contact",
    "SELECT COUNT(Id) FROM Opportunity GROUP BY StageName",
    "SELECT Id, (SELECT Id FROM Contacts) FROM Account",
    "SELECT Id FROM Case WHERE Subject = 'please update my address, for view'",
    "SELECT Id FROM Case WHERE Subject = 'O\\'Brien; DELETE' LIMIT 1",
    "SELECT Id\nFROM Account\nWHERE LastModifiedDate = TODAY",
    "SELECT Id FROM Update_Log__c",
]
WRITES = [
    "SELECT Id FROM Account FOR VIEW",
    "SELECT Id FROM Contact FOR REFERENCE",
    "SELECT Id FROM Account for\n\tview",
    "SELECT Id FROM Account LIMIT 1 FOR UPDATE",
    "SELECT Title FROM FAQ__kav WHERE Id = 'ka0' UPDATE TRACKING",
    "SELECT Title FROM FAQ__kav UPDATE VIEWSTAT",
    "DELETE FROM Account",
    "INSERT INTO Account (Name) VALUES ('x')",
    "UPSERT Account",
    "SELECT Id FROM Account; DELETE FROM Account",
    "SELECT Id FROM Case WHERE Subject = 'never closed",
    "SELECT Id FROM Case WHERE Subject = 'x\\' FOR VIEW",
    "FIND {Acme} IN ALL FIELDS RETURNING Account(Id)",
    "",
    "   ",
]


@pytest.mark.parametrize("query", READS)
def test_c3_a_plain_select_is_a_read(query: str) -> None:
    assert soql_query_effect({"query": query}) == "read"


@pytest.mark.parametrize("query", WRITES)
def test_c3_anything_else_is_a_write(query: str) -> None:
    assert soql_query_effect({"query": query}) == "write"


@pytest.mark.parametrize("arguments", [{}, {"query": None}, {"query": ["SELECT Id FROM A"]}, {"q": "SELECT Id FROM A"}])
def test_c3_arguments_that_are_not_a_query_string_fail_closed(arguments: dict[str, object]) -> None:
    assert soql_query_effect(arguments) == "write"  # type: ignore[arg-type]


def test_c3_the_classifier_names_the_real_tool_and_its_static_tag_stays_write() -> None:
    from app.agents.actions.salesforce.salesforce import Salesforce

    meta = getattr(Salesforce.soql_query, TOOL_META_ATTR)
    assert meta.path in CALL_EFFECTS
    assert classify_tool_effect("salesforce__soql_query", meta.tags).effect == "write"


def test_c3_the_run_port_classifies_each_soql_call() -> None:
    from app.agents.actions.salesforce.salesforce import Salesforce

    client = SimpleNamespace(get_base_url=lambda: "https://acme.my.salesforce.com", get_client=lambda: None)
    registry = ToolRegistry()
    ToolsetBuilder(
        Salesforce(client, {}),  # type: ignore[arg-type]
        name="salesforce",
        description="Salesforce",
        path_prefix="/tools/salesforce",
    ).register_into(registry)
    names = ["salesforce__soql_query", "salesforce__sosl_search"]
    effects = {n: classify_tool_effect(n, registry.tags_for_name(n)) for n in names}
    classifiers = call_effects_for(registry, names)
    assert set(classifiers) == {"salesforce__soql_query"}
    port = RegistryToolPort(registry, effects, call_effects=classifiers)

    def effect(query: str) -> str:
        described = port.describe_call(ToolCall(id="c", name="salesforce__soql_query", arguments={"query": query}))
        assert described is not None
        return described.effect

    assert effect("SELECT Id FROM Account LIMIT 5") == "read"
    assert effect("SELECT Id FROM Account FOR VIEW") == "write"
