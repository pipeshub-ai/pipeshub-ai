"""C4.08 (AD-09, AD-10, AD-12): an agent's definition as one environment hands it to another, and its
re-binding in the importing organization. The portable form names every reference and carries no id, no
credential and no sharing; an import binds each name it finds once, and reports the rest for re-binding."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from app.agents.portable import (
    ImportCatalog,
    excluded_of,
    plan_import,
    portable_agent,
)
from app.harness.contracts.generated.pipeshub.api.portable_agent import PortableAgent

_LLMS_A = [
    {"modelKey": "key-a1", "provider": "openAI", "isReasoning": True, "configuration": {"model": "gpt-x, gpt-x-mini"}},
    {"modelKey": "key-a2", "provider": "anthropic", "isReasoning": False, "configuration": {"model": "claude-y"}},
]
# The importing deployment configures the same models under its own keys.
_LLMS_B = [
    {"modelKey": "key-b9", "provider": "openai", "isReasoning": True, "configuration": {"model": "gpt-x,gpt-x-mini"}},
    {"modelKey": "key-b7", "provider": "anthropic", "isReasoning": False, "configuration": {"model": "claude-y"}},
]


def _definition(**fields: object) -> dict[str, Any]:
    """A version's definition as `agent_definition` freezes it from `get_agent`, every reference an id."""
    return {
        "name": "Support triage",
        "description": "Sorts new tickets",
        "startMessage": "Hi!",
        "systemPrompt": "You triage support tickets.",
        "instructions": None,
        "tags": ["support"],
        "sendUserContext": True,
        "webSearch": "tavily",
        "defaultReasoningEffort": "low",
        "models": ["key-a1_gpt-x", "key-a2"],
        "roleModels": json.dumps({"default": {"modelKey": "key-a1", "modelName": "gpt-x-mini", "provider": "openai"}}),
        "engineProfile": json.dumps({"mode": "plan"}),
        "modelAccountId": "acct-secret-binding",
        "toolsets": [{
            "_key": "ts-1", "name": "jira", "displayName": "Jira", "type": "app",
            "instanceId": "inst-a-jira", "instanceName": "Jira Cloud", "selectedTools": None,
            "tools": [{"_key": "t-1", "name": "search", "fullName": "jira.search", "toolsetName": "jira", "description": "Search"}],
        }],
        "mcpServers": [{
            "_key": "m-1", "name": "github", "displayName": "GitHub", "typeId": "github",
            "instanceId": "mcp-a-1", "allTools": False,
            "tools": [{"_key": "mt-1", "name": "list_prs", "fullName": "mcp_github_list_prs", "description": ""}],
        }],
        "knowledge": [
            {"_key": "k-1", "connectorId": "conn-a-drive", "filters": "{}", "name": "Support Drive", "type": "DRIVE", "displayName": "Support Drive"},
            {"_key": "k-2", "connectorId": "conn-a-kb", "filters": json.dumps({"recordGroups": ["rg-a-1", "rg-a-2"]}), "name": "Handbook", "type": "KB", "displayName": "Handbook"},
        ],
        "skills": [{"name": "xlsx", "description": "Spreadsheets"}],
        **fields,
    }


def _catalog_b(**fields: list[dict[str, Any]]) -> ImportCatalog:
    return ImportCatalog(
        llm_entries=fields.get("llm_entries", _LLMS_B),
        toolset_instances=fields.get("toolset_instances", [
            {"_id": "inst-b-jira", "orgId": "org-2", "instanceName": "Jira Cloud", "toolsetType": "jira"},
        ]),
        mcp_instances=fields.get("mcp_instances", [{"_id": "mcp-b-1", "name": "github", "typeId": "github"}]),
        connectors=fields.get("connectors", [
            {"_key": "conn-b-drive", "name": "Support Drive", "type": "DRIVE"},
            {"_key": "conn-b-kb", "name": "Handbook", "type": "KB"},
        ]),
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


class TestAd09Export:
    def test_ad09_the_export_names_every_reference_and_carries_no_id_or_credential(self) -> None:
        agent = portable_agent(_definition(), _LLMS_A)

        PortableAgent.model_validate(agent)
        assert agent["models"] == [
            {"provider": "openai", "modelName": "gpt-x"},
            {"provider": "anthropic", "modelName": "claude-y"},
        ]
        assert agent["roleModels"] == {"default": {"provider": "openai", "modelName": "gpt-x-mini"}}
        assert agent["engineProfile"] == {"mode": "plan"}
        assert agent["toolsets"] == [{
            "name": "jira", "displayName": "Jira", "type": "app", "instanceName": "Jira Cloud",
            "tools": [{"name": "search", "fullName": "jira.search", "description": "Search"}],
        }]
        assert agent["mcpServers"][0]["name"] == "github"
        assert agent["knowledge"] == [
            {"name": "Support Drive", "type": "DRIVE", "scope": "whole", "limitedItems": 0},
            {"name": "Handbook", "type": "KB", "scope": "limited", "limitedItems": 2},
        ]
        assert agent["skills"] == ["xlsx"]
        carried = set(_strings(agent))
        for binding in ("key-a1", "key-a2", "acct-secret-binding", "inst-a-jira", "mcp-a-1", "conn-a-drive",
                        "conn-a-kb", "rg-a-1", "ts-1", "t-1", "k-1", "modelAccountId", "instanceId", "connectorId"):
            assert binding not in carried, binding

    def test_ad09_a_field_the_definition_gains_later_is_never_carried_unless_named(self) -> None:
        agent = portable_agent(_definition(apiToken="sk-live-123", orgId="org-1", createdBy="k-alice"), _LLMS_A)

        assert "sk-live-123" not in set(_strings(agent))
        assert set(agent) == {field.alias or name for name, field in PortableAgent.model_fields.items()}

    def test_ad09_a_model_this_deployment_no_longer_configures_is_left_out(self) -> None:
        agent = portable_agent(_definition(models=["gone-key_gpt-old", "key-a2"]), _LLMS_A)

        assert agent["models"] == [{"provider": "anthropic", "modelName": "claude-y"}]

    def test_ad09_an_unreadable_knowledge_limit_is_carried_as_limited_never_whole(self) -> None:
        knowledge = [{"connectorId": "c", "filters": "{not json", "name": "Wiki", "type": "CONFLUENCE"}]

        agent = portable_agent(_definition(knowledge=knowledge), _LLMS_A)

        assert agent["knowledge"] == [{"name": "Wiki", "type": "CONFLUENCE", "scope": "limited", "limitedItems": 0}]

    def test_ad09_what_is_left_out_by_design_is_named(self) -> None:
        live = {"shareWithOrg": True, "ownerTeam": "team-1", "isServiceAccount": True}

        assert excluded_of(live, _definition()) == ["modelAccount", "ownerTeam", "serviceAccount", "sharing"]
        assert excluded_of({"shareWithOrg": False}, _definition(modelAccountId=None)) == []

_GATES = {"planApproval": "required", "verify": {"checks": [{"kind": "file", "path": "report.md"}], "onFail": "flag"}}


class TestWp56GatesTravel:
    """WP-56 (C4.08 x WP-51): an agent's own gates (plan approval, result checks) travel with it. The portable form
    carries them, an import stores them on the new agent, and an export no longer names them as left out."""

    def test_wp56_gates_1_the_portable_form_carries_the_agents_gates_never_named_as_left_out(self) -> None:
        for stored in (json.dumps(_GATES), _GATES):
            agent = portable_agent(_definition(gates=stored), _LLMS_A)

            assert agent["gates"] == _GATES
            assert excluded_of({}, _definition(modelAccountId=None, gates=stored)) == []
        for none in (None, json.dumps({}), {}):
            assert portable_agent(_definition(gates=none), _LLMS_A)["gates"] is None

    def test_wp56_gates_1_gates_the_agent_could_not_run_with_are_refused_rather_than_exported_loose(self) -> None:
        with pytest.raises(ValueError, match="gates"):
            portable_agent(_definition(gates=json.dumps({"planApproval": True})), _LLMS_A)

    def test_wp56_gates_2_an_import_stores_the_gates_on_the_new_agent(self) -> None:
        plan = plan_import(portable_agent(_definition(gates=_GATES), _LLMS_A), _catalog_b())

        assert plan.body["gates"] == _GATES
        assert "gates" not in plan_import(portable_agent(_definition(), _LLMS_A), _catalog_b()).body

    def test_wp56_gates_2_gates_that_are_not_an_agents_are_refused_before_anything_is_planned(self) -> None:
        exported = portable_agent(_definition(), _LLMS_A)
        for gates in (
            {"dryRun": True},
            {"planApproval": "always"},
            {"verify": {"checks": [], "onFail": "flag"}},
            {"verify": {"checks": [{"kind": "file", "path": "../etc/passwd"}], "onFail": "flag"}},
            {"other": 1},
        ):
            with pytest.raises(ValueError, match="gates"):
                plan_import({**exported, "gates": gates}, _catalog_b())

    def test_wp56_gates_3_export_import_export_keeps_the_gates(self) -> None:
        first = portable_agent(_definition(gates=json.dumps(_GATES)), _LLMS_A)
        written = copy.deepcopy(plan_import(first, _catalog_b()).body)
        written["gates"] = json.dumps(written["gates"])

        assert portable_agent(_definition(gates=written["gates"]), _LLMS_A)["gates"] == first["gates"] == _GATES


class TestAd10Import:
    def test_ad10_every_name_found_once_is_bound_to_the_importers_own_ids(self) -> None:
        plan = plan_import(portable_agent(_definition(), _LLMS_A), _catalog_b())

        body = plan.body
        assert body["models"] == [
            {"modelKey": "key-b9", "modelName": "gpt-x", "isReasoning": True},
            {"modelKey": "key-b7", "modelName": "claude-y", "isReasoning": False},
        ]
        assert body["roleModels"] == {"default": {"modelKey": "key-b9", "modelName": "gpt-x-mini", "provider": "openai"}}
        assert body["toolsets"][0] | {"tools": []} == {
            "name": "jira", "displayName": "Jira", "type": "app", "instanceId": "inst-b-jira",
            "instanceName": "Jira Cloud", "tools": [],
        }
        assert body["mcpServers"][0]["instanceId"] == "mcp-b-1"
        assert body["knowledge"] == [{"connectorId": "conn-b-drive", "filters": {}}]
        assert body["engineProfile"] == {"mode": "plan"}
        assert body["shareWithOrg"] is False
        assert body["isServiceAccount"] is False
        assert "modelAccountId" not in body
        assert plan.rebind == [{
            "kind": "knowledge", "name": "Handbook", "reason": "limited",
            "detail": "It searched 2 chosen folders or files; choose them again.",
        }]

    def test_ad10_a_name_not_found_or_found_twice_is_reported_and_never_guessed(self) -> None:
        catalog = _catalog_b(
            toolset_instances=[
                {"_id": "i1", "instanceName": "Jira Cloud", "toolsetType": "jira"},
                {"_id": "i2", "instanceName": "jira cloud", "toolsetType": "jira"},
            ],
            mcp_instances=[{"_id": "other", "name": "gitlab", "typeId": "gitlab"}],
            connectors=[],
            llm_entries=[_LLMS_B[0]],
        )

        plan = plan_import(portable_agent(_definition(), _LLMS_A), catalog)

        assert plan.body["toolsets"] == []
        assert plan.body["mcpServers"] == []
        assert plan.body["knowledge"] == []
        assert plan.body["models"] == [{"modelKey": "key-b9", "modelName": "gpt-x", "isReasoning": True}]
        reasons = {(r["kind"], r["name"], r["reason"]) for r in plan.rebind}
        assert reasons == {
            ("model", "anthropic/claude-y", "not_found"),
            ("toolset", "Jira Cloud", "ambiguous"),
            ("mcpServer", "github", "not_found"),
            ("knowledge", "Support Drive", "not_found"),
            ("knowledge", "Handbook", "limited"),
        }

    def test_ad10_the_default_entry_settles_a_model_two_entries_serve(self) -> None:
        twice = [{**_LLMS_B[0], "modelKey": "key-b1"}, {**_LLMS_B[0], "modelKey": "key-b2", "isDefault": True}]

        plan = plan_import(
            portable_agent(_definition(models=["key-a1_gpt-x"], roleModels=None), _LLMS_A), _catalog_b(llm_entries=twice),
        )

        assert plan.body["models"] == [{"modelKey": "key-b2", "modelName": "gpt-x", "isReasoning": True}]

    def test_ad10_matched_models_none_of_which_reasons_here_fall_back_to_the_org_default(self) -> None:
        plain = [{**_LLMS_B[0], "isReasoning": False}]

        plan = plan_import(
            portable_agent(_definition(models=["key-a1_gpt-x"], roleModels=None), _LLMS_A), _catalog_b(llm_entries=plain),
        )

        assert plan.body["models"] == []
        assert [(r["kind"], r["reason"]) for r in plan.rebind if r["kind"] == "model"] == [("model", "not_reasoning")]

    def test_ad10_a_toolset_bound_to_no_instance_stays_unbound(self) -> None:
        toolsets = [{"name": "calculator", "displayName": "Calculator", "type": "app", "tools": []}]

        plan = plan_import(portable_agent(_definition(toolsets=toolsets), _LLMS_A), _catalog_b())

        assert plan.body["toolsets"] == [{"name": "calculator", "displayName": "Calculator", "type": "app", "tools": []}]

    def test_agb01_a_drafted_action_without_an_instance_binds_to_the_sole_one_of_its_type(self) -> None:
        unnamed = [{"name": "jira", "displayName": "Jira", "type": "app", "instanceName": None, "tools": []}]
        agent = {**portable_agent(_definition(), _LLMS_A), "toolsets": unnamed}

        plan = plan_import(agent, _catalog_b(), sole_instance=True)

        assert plan.body["toolsets"] == [{
            "name": "jira", "displayName": "Jira", "type": "app", "tools": [],
            "instanceId": "inst-b-jira", "instanceName": "Jira Cloud",
        }]
        assert {"kind": "toolset", "name": "jira"} in plan.bound
        # Without the flag an export's unbound action stays unbound.
        assert "instanceId" not in plan_import(agent, _catalog_b()).body["toolsets"][0]

    def test_agb01_several_instances_of_the_type_are_listed_to_choose_again(self) -> None:
        unnamed = [{"name": "jira", "displayName": "Jira", "type": "app", "instanceName": None, "tools": []}]
        agent = {**portable_agent(_definition(), _LLMS_A), "toolsets": unnamed}
        two = [
            {"_id": "j1", "orgId": "org-2", "instanceName": "Jira Cloud", "toolsetType": "jira"},
            {"_id": "j2", "orgId": "org-2", "instanceName": "Jira Server", "toolsetType": "jira"},
        ]

        plan = plan_import(agent, _catalog_b(toolset_instances=two), sole_instance=True)

        assert plan.body["toolsets"] == []
        assert [(r["kind"], r["name"], r["reason"]) for r in plan.rebind if r["kind"] == "toolset"] == [
            ("toolset", "jira", "ambiguous"),
        ]

    def test_agb01_a_platform_action_with_no_instance_anywhere_stays_unbound(self) -> None:
        unnamed = [{"name": "calculator", "displayName": "Calculator", "type": "app", "instanceName": None, "tools": []}]
        agent = {**portable_agent(_definition(), _LLMS_A), "toolsets": unnamed}

        plan = plan_import(agent, _catalog_b(), sole_instance=True)

        assert plan.body["toolsets"] == [{"name": "calculator", "displayName": "Calculator", "type": "app", "tools": []}]

    def test_ad10_the_import_takes_nothing_but_the_portable_fields(self) -> None:
        exported = portable_agent(_definition(), _LLMS_A)
        tampered = {**exported, "orgId": "org-1", "createdBy": "k-alice", "shareWithOrg": True}

        with pytest.raises(ValueError, match="orgId|createdBy|shareWithOrg"):
            plan_import(tampered, _catalog_b())


class TestAd12RoundTrip:
    def test_ad12_export_import_export_is_the_same_document_when_every_name_binds(self) -> None:
        first = portable_agent(_definition(), _LLMS_A)
        plan = plan_import(first, _catalog_b())
        # The draft the import writes, as get_agent reads it back in org B (instance names come from the instances).
        written = copy.deepcopy(plan.body)
        written["models"] = [f"{m['modelKey']}_{m['modelName']}" for m in written["models"]]
        written["knowledge"] = [
            {**k, "name": "Support Drive", "type": "DRIVE"} for k in written["knowledge"]
        ] + [{"connectorId": "conn-b-kb", "filters": json.dumps({"recordGroups": ["rg-b-1", "rg-b-2"]}), "name": "Handbook", "type": "KB"}]
        written["skills"] = [{"name": n} for n in written["skills"]]

        second = portable_agent(written, _LLMS_B)

        assert second == first


_ENVIRONMENT = {
    "environmentId": "node-toolchain",
    "level": "agent",
    "setup": "npm ci --prefix /opt/tools",
    "env": {"NODE_ENV": "test"},
    "cachePaths": ["/opt/tools"],
}


class TestWp100Fu9EnvironmentTravels:
    """WP100-FU-9 (C4.08 x C20.18): an agent's environment travels with it. It names no id,
    so the portable form carries it as stored and an import stores it on the new agent; an export made before it
    travelled imports as setting none."""

    def test_wp100_fu9_env_1_the_portable_form_carries_the_agents_environment(self) -> None:
        for stored in (json.dumps(_ENVIRONMENT), _ENVIRONMENT):
            assert portable_agent(_definition(environment=stored), _LLMS_A)["environment"] == _ENVIRONMENT
        for none in (None, json.dumps({}), {}):
            assert portable_agent(_definition(environment=none), _LLMS_A)["environment"] is None

    def test_wp100_fu9_env_1_an_environment_the_agent_could_not_run_with_is_refused_rather_than_exported(self) -> None:
        with pytest.raises(ValueError, match="environment"):
            portable_agent(_definition(environment={"environmentId": "x", "level": "agent", "env": {"API_TOKEN": "s3cr3t-value"}}), _LLMS_A)

    def test_wp100_fu9_env_2_an_import_stores_the_environment_on_the_new_agent(self) -> None:
        plan = plan_import(portable_agent(_definition(environment=_ENVIRONMENT), _LLMS_A), _catalog_b())

        assert plan.body["environment"] == _ENVIRONMENT
        assert "environment" not in plan_import(portable_agent(_definition(), _LLMS_A), _catalog_b()).body

    def test_wp100_fu9_env_2_an_export_from_before_it_travelled_imports_as_none(self) -> None:
        older = portable_agent(_definition(), _LLMS_A)
        del older["environment"]

        assert "environment" not in plan_import(older, _catalog_b()).body

    def test_wp100_fu9_env_2_an_environment_that_is_not_one_is_refused_before_anything_is_planned(self) -> None:
        exported = portable_agent(_definition(), _LLMS_A)
        for environment in (
            {"level": "agent"},
            {"environmentId": "x", "level": "agent", "env": {"API_TOKEN": "s3cr3t-value"}},
            {"environmentId": "x", "level": "agent", "other": 1},
        ):
            with pytest.raises(ValueError, match="environment"):
                plan_import({**exported, "environment": environment}, _catalog_b())
