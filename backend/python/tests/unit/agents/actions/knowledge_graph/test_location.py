"""Permission-aware Location trails — prefix walker, render, and resolve_ancestor_locations."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.location import (
    app_only_fallback,
    filter_accessible_prefix,
    pick_parent,
    render_trail,
    resolve_ancestor_locations,
    walk_ancestors,
)
from app.models.entities import Connectors, OriginTypes, Record, RecordType


def _adj(
    nodes: dict[str, tuple[str, str]],
    parents: dict[str, list[tuple[str, str, str]]],
) -> dict[str, Any]:
    """nodes: id -> (type, name); parents: child -> [(pid, ptype, via)]"""
    return {
        "nodes": {
            nid: {"id": nid, "type": t, "name": n} for nid, (t, n) in nodes.items()
        },
        "parents": {
            cid: [
                {"parent_id": pid, "parent_type": pt, "via": via}
                for pid, pt, via in edges
            ]
            for cid, edges in parents.items()
        },
    }


def _seg(seg_id: str, seg_type: str, name: str) -> dict[str, str]:
    return {"id": seg_id, "type": seg_type, "name": name}


class TestPickParent:
    def test_prefers_record_relations_over_belongs_to(self) -> None:
        edges = [
            {"parent_id": "rg1", "parent_type": "recordGroup", "via": "belongsTo"},
            {"parent_id": "rec-p", "parent_type": "record", "via": "recordRelations"},
            {"parent_id": "app1", "parent_type": "app", "via": "belongsTo"},
        ]
        chosen = pick_parent(edges)
        assert chosen is not None
        assert chosen["parent_id"] == "rec-p"

    def test_prefers_rg_over_app(self) -> None:
        edges = [
            {"parent_id": "app1", "parent_type": "app", "via": "belongsTo"},
            {"parent_id": "rg1", "parent_type": "recordGroup", "via": "belongsTo"},
        ]
        chosen = pick_parent(edges)
        assert chosen is not None
        assert chosen["parent_id"] == "rg1"

    def test_empty_edges(self) -> None:
        assert pick_parent([]) is None


class TestWalkAncestors:
    def test_deep_chain_nested_rgs_and_records(self) -> None:
        # app1 → rg1 → rg2 → rec1 → rec2 → rec3(seed)
        adj = _adj(
            {
                "rec3": ("record", "Rec3"),
                "rec2": ("record", "Rec2"),
                "rec1": ("record", "Rec1"),
                "rg2": ("recordGroup", "RG2"),
                "rg1": ("recordGroup", "RG1"),
                "app1": ("app", "Jira"),
            },
            {
                "rec3": [("rec2", "record", "recordRelations")],
                "rec2": [("rec1", "record", "recordRelations")],
                "rec1": [("rg2", "recordGroup", "belongsTo")],
                "rg2": [("rg1", "recordGroup", "belongsTo")],
                "rg1": [("app1", "app", "belongsTo")],
            },
        )
        trail = walk_ancestors("rec3", adj)
        assert [s["id"] for s in trail] == ["app1", "rg1", "rg2", "rec1", "rec2"]
        assert trail[0]["type"] == "app"

    def test_record_parent_preferred_over_rg_shortcut(self) -> None:
        adj = _adj(
            {
                "story": ("record", "Story"),
                "epic": ("record", "Epic"),
                "rg1": ("recordGroup", "PROJ"),
                "app1": ("app", "Jira"),
            },
            {
                "story": [
                    ("epic", "record", "recordRelations"),
                    ("rg1", "recordGroup", "belongsTo"),
                ],
                "epic": [("rg1", "recordGroup", "belongsTo")],
                "rg1": [("app1", "app", "belongsTo")],
            },
        )
        trail = walk_ancestors("story", adj)
        assert [s["id"] for s in trail] == ["app1", "rg1", "epic"]

    def test_cycle_terminates(self) -> None:
        adj = _adj(
            {"a": ("record", "A"), "b": ("record", "B")},
            {
                "a": [("b", "record", "recordRelations")],
                "b": [("a", "record", "recordRelations")],
            },
        )
        trail = walk_ancestors("a", adj)
        assert [s["id"] for s in trail] == ["b"]

    def test_depth_cap_stops_walk(self) -> None:
        # r0 ← r1 ← r2 ← r3; max_depth=2 walks two hops only
        adj = _adj(
            {f"r{i}": ("record", f"R{i}") for i in range(4)},
            {f"r{i}": [(f"r{i + 1}", "record", "recordRelations")] for i in range(3)},
        )
        trail = walk_ancestors("r0", adj, max_depth=2)
        assert [s["id"] for s in trail] == ["r2", "r1"]

    def test_missing_parent_node_stops_walk(self) -> None:
        adj = _adj(
            {"child": ("record", "Child")},
            {"child": [("ghost", "record", "recordRelations")]},
        )
        assert walk_ancestors("child", adj) == []


class TestFilterAccessiblePrefix:
    def test_denied_middle_rg_breaks_there(self) -> None:
        # The canonical case: user lacks rg2 → App -> RG1 and nothing further.
        trail = [
            _seg("app1", "app", "Jira"),
            _seg("rg1", "recordGroup", "RG1"),
            _seg("rg2", "recordGroup", "RG2"),
            _seg("rec1", "record", "Rec1"),
        ]
        visible = filter_accessible_prefix(trail, {"rg1", "rec1"})
        assert [s["id"] for s in visible] == ["app1", "rg1"]

    def test_app_kept_without_check(self) -> None:
        trail = [_seg("app1", "app", "Jira"), _seg("rg1", "recordGroup", "RG1")]
        visible = filter_accessible_prefix(trail, set())
        assert [s["id"] for s in visible] == ["app1"]

    def test_all_accessible_keeps_full_trail(self) -> None:
        trail = [
            _seg("app1", "app", "Jira"),
            _seg("rg1", "recordGroup", "RG1"),
            _seg("epic", "record", "Epic"),
        ]
        visible = filter_accessible_prefix(trail, {"rg1", "epic"})
        assert [s["id"] for s in visible] == ["app1", "rg1", "epic"]

    def test_no_app_root_denied_first_node_empty(self) -> None:
        trail = [_seg("rg1", "recordGroup", "RG1"), _seg("epic", "record", "Epic")]
        assert filter_accessible_prefix(trail, {"epic"}) == []

    def test_no_app_root_accessible_renders_from_that_node(self) -> None:
        trail = [_seg("rg1", "recordGroup", "RG1"), _seg("epic", "record", "Epic")]
        visible = filter_accessible_prefix(trail, {"rg1", "epic"})
        assert [s["id"] for s in visible] == ["rg1", "epic"]


class TestRender:
    def test_names_and_full_ids(self) -> None:
        rendered = render_trail(
            [
                _seg("app1", "app", "Jira"),
                _seg("rg1", "recordGroup", "PROJ"),
                _seg("epic", "record", "Epic"),
            ]
        )
        assert rendered == (
            "Jira (App ID: app1) -> PROJ (Record Group ID: rg1) "
            "-> Epic (Record ID: epic)"
        )

    def test_folders_labeled_record(self) -> None:
        assert render_trail([_seg("f1", "record", "Folder")]) == "Folder (Record ID: f1)"

    def test_empty_trail(self) -> None:
        assert render_trail([]) == ""

    def test_depth_cap_and_permission_truncation_byte_identical(self) -> None:
        # Same visible prefix must render identically whether the trail was
        # shortened by the depth cap or by a denial — no marker leaks.
        via_depth = [_seg("app1", "app", "Jira"), _seg("rg1", "recordGroup", "RG1")]
        via_perm = filter_accessible_prefix(
            [
                _seg("app1", "app", "Jira"),
                _seg("rg1", "recordGroup", "RG1"),
                _seg("denied", "recordGroup", "Secret"),
            ],
            {"rg1"},
        )
        assert render_trail(via_perm) == render_trail(via_depth)
        assert "…" not in render_trail(via_perm)
        assert "denied" not in render_trail(via_perm)


class TestAppOnlyFallback:
    def test_app_only(self) -> None:
        assert app_only_fallback(connector_id="c1", connector_name="Jira") == (
            "Jira (App ID: c1)"
        )

    def test_empty_without_connector(self) -> None:
        assert app_only_fallback(connector_id=None, connector_name="Jira") == ""


class TestToLlmContext:
    def test_location_present(self) -> None:
        rec = Record(
            id="r1",
            org_id="o1",
            record_name="Story",
            record_type=RecordType.TICKET,
            external_record_id="1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA,
            connector_id="c1",
            location="Jira (App ID: c1) -> PROJ (Record Group ID: rg1)",
        )
        ctx = rec.to_llm_context()
        assert (
            "Location: Jira (App ID: c1) -> PROJ (Record Group ID: rg1)" in ctx
        )

    def test_location_absent_when_none(self) -> None:
        rec = Record(
            id="r1",
            org_id="o1",
            record_name="Story",
            record_type=RecordType.TICKET,
            external_record_id="1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA,
            connector_id="c1",
        )
        assert "Location" not in rec.to_llm_context()


def _jira_adjacency_payload() -> dict[str, Any]:
    return _adj(
        {
            "story": ("record", "Story"),
            "epic": ("record", "Epic"),
            "rg1": ("recordGroup", "PROJ"),
            "app1": ("app", "Jira"),
        },
        {
            "story": [
                ("epic", "record", "recordRelations"),
                ("rg1", "recordGroup", "belongsTo"),
            ],
            "epic": [("rg1", "recordGroup", "belongsTo")],
            "rg1": [("app1", "app", "belongsTo")],
        },
    )


@pytest.mark.asyncio
async def test_resolve_one_adjacency_and_one_permission_call() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value=_jira_adjacency_payload()
    )
    mock_provider.filter_nodes_with_permission_role = AsyncMock(
        return_value={"rg1", "epic"}
    )
    locs = await resolve_ancestor_locations(
        ["story", "story"],  # dedupe walks; still one provider batch
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
    )
    mock_provider.get_record_parent_adjacency.assert_called_once()
    mock_provider.filter_nodes_with_permission_role.assert_called_once()
    called_nodes = mock_provider.filter_nodes_with_permission_role.call_args[0][0]
    ids = {n["id"] for n in called_nodes}
    assert ids == {"rg1", "epic"}  # app excluded from permission batch
    assert locs["story"] == (
        "Jira (App ID: app1) -> PROJ (Record Group ID: rg1) -> Epic (Record ID: epic)"
    )


@pytest.mark.asyncio
async def test_resolve_denied_middle_renders_prefix_only() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value=_jira_adjacency_payload()
    )
    # rg1 accessible; epic (nearer the record) denied → App -> PROJ and stop.
    mock_provider.filter_nodes_with_permission_role = AsyncMock(return_value={"rg1"})
    locs = await resolve_ancestor_locations(
        ["story"],
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
    )
    assert locs["story"] == "Jira (App ID: app1) -> PROJ (Record Group ID: rg1)"
    assert "epic" not in locs["story"]


@pytest.mark.asyncio
async def test_resolve_all_ancestors_denied_renders_app_only() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value=_jira_adjacency_payload()
    )
    mock_provider.filter_nodes_with_permission_role = AsyncMock(return_value=set())
    locs = await resolve_ancestor_locations(
        ["story"],
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
    )
    assert locs["story"] == "Jira (App ID: app1)"


@pytest.mark.asyncio
async def test_resolve_kb_no_record_group() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value=_adj(
            {
                "file1": ("record", "Doc"),
                "kb1": ("app", "Engineering KB"),
            },
            {"file1": [("kb1", "app", "belongsTo")]},
        )
    )
    mock_provider.filter_nodes_with_permission_role = AsyncMock(return_value=set())
    locs = await resolve_ancestor_locations(
        ["file1"],
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
    )
    assert locs["file1"] == "Engineering KB (App ID: kb1)"
    # Trail is app-only → no permission candidates → no filter call.
    mock_provider.filter_nodes_with_permission_role.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_warns_when_no_ancestor_accessible(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value=_jira_adjacency_payload()
    )
    mock_provider.filter_nodes_with_permission_role = AsyncMock(return_value=set())
    with caplog.at_level(logging.WARNING, "app.agents.actions.knowledge_graph.location"):
        await resolve_ancestor_locations(
            ["story"],
            graph_provider=mock_provider,
            org_id="org",
            user_key="uk",
        )
    assert any("0/2 ancestors accessible" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resolve_without_user_key_renders_app_prefix_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value=_jira_adjacency_payload()
    )
    mock_provider.filter_nodes_with_permission_role = AsyncMock()
    with caplog.at_level(logging.WARNING, "app.agents.actions.knowledge_graph.location"):
        locs = await resolve_ancestor_locations(
            ["story"],
            graph_provider=mock_provider,
            org_id="org",
            user_key="",
        )
    assert locs["story"] == "Jira (App ID: app1)"
    mock_provider.filter_nodes_with_permission_role.assert_not_called()
    assert any("no user_key" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_fallback_leaks_nothing_on_provider_failure() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        side_effect=RuntimeError("DB down")
    )
    docs = {
        "story": {
            "connectorId": "app1",
            "connectorName": "Jira",
            "recordGroupId": "rg-SECRET",
            "parentNodeId": "epic-SECRET",
        }
    }
    locs = await resolve_ancestor_locations(
        ["story"],
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
        record_docs=docs,
    )
    assert locs["story"] == "Jira (App ID: app1)"
    assert "rg-SECRET" not in locs["story"]
    assert "epic-SECRET" not in locs["story"]


@pytest.mark.asyncio
async def test_fallback_on_empty_adjacency_payload() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock(
        return_value={"nodes": {}, "parents": {}}
    )
    docs = {
        "story": {
            "connectorId": "app1",
            "connectorName": "Jira",
            "recordGroupId": "rg-SECRET",
        }
    }
    locs = await resolve_ancestor_locations(
        ["story"],
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
        record_docs=docs,
    )
    assert locs["story"] == "Jira (App ID: app1)"
    assert "rg-SECRET" not in locs["story"]


@pytest.mark.asyncio
async def test_resolve_empty_ids_no_provider_calls() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_parent_adjacency = AsyncMock()
    result = await resolve_ancestor_locations(
        [],
        graph_provider=mock_provider,
        org_id="org",
        user_key="uk",
    )
    assert result == {}
    mock_provider.get_record_parent_adjacency.assert_not_called()
