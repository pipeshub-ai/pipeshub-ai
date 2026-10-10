"""Arango's rows for the batch access check come from staged
queries: ``targets`` decides the arms that do not walk, and each walking arm
runs only for the nodes still undecided. What the stages admit is tested against
a real graph in ``tests/integration/graph_permissions/test_provider_v3_access_check.py``;
these tests hold the staging around them."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

ACCESS = {"grantee_ids": ["u"], "gated_app_ids": ["app"], "by_connector": {"app": ["g1"]}}


def _row(node_id: str, *, ok: bool = False, walk: bool = False) -> dict:
    return {"id": node_id, "vrid": f"vr-{node_id}", "connectorId": "app", "indexingStatus": "COMPLETED", "isInternal": False,
            "ok": ok, "walk": walk}


def _provider(answers: dict) -> tuple[ArangoHTTPProvider, list]:
    """``answers`` maps a stage to what it returns: a list, or a function of the
    asked ids."""
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    provider.http_client = MagicMock()
    stages = {text: name for name, text in ArangoHTTPProvider._kh_v3_check_aql().items()}
    calls: list = []

    async def execute_aql(query, bind_vars=None, txn_id=None, options=None, **_) -> list:  # noqa: ANN001
        stage = stages[query]
        calls.append((stage, list(bind_vars.get("kh_ids", [])), options, dict(bind_vars)))
        answer = answers.get(stage, [])
        return answer(bind_vars["kh_ids"]) if callable(answer) else answer

    provider.http_client.execute_aql = AsyncMock(side_effect=execute_aql)
    return provider, calls


async def _rows(provider, ids) -> set[str]:
    rows = await provider._kh_v3_accessible_rows("u", "org", ids, [], access=ACCESS, transaction=None)
    return {r["id"] for r in rows}


class TestStages:
    @pytest.mark.asyncio
    async def test_each_walk_runs_only_for_what_is_still_undecided(self) -> None:
        provider, calls = _provider({
            "targets": [_row("a", ok=True), _row("b", walk=True), _row("c", walk=True), _row("d", walk=True)],
            "from_app": lambda ids: [i for i in ids if i == "b"],
            "declared": lambda ids: [i for i in ids if i == "c"],
            "seeds": [{"node": "d", "seed": "s1"}],
        })
        assert await _rows(provider, ["a", "b", "c", "d", "e"]) == {"a", "b", "c", "d"}
        assert [(stage, ids) for stage, ids, *_ in calls] == [
            ("targets", ["a", "b", "c", "d", "e"]),
            ("from_app", ["b", "c", "d"]),
            ("declared", ["c", "d"]),
            ("seeds", ["d"]),
            ("seed_from_app", ["s1"]),      # is the seed admitted another way?
            ("seed_declared", ["s1"]),
        ]

    @pytest.mark.asyncio
    async def test_a_seed_another_arm_admits_opens_nothing_below_it(self) -> None:
        provider, calls = _provider({
            "targets": [_row("d", walk=True)],
            "seeds": [{"node": "d", "seed": "s1"}],
            "seed_from_app": lambda ids: [i for i in ids if i == "s1"],
        })
        assert await _rows(provider, ["d"]) == set()
        assert [stage for stage, *_ in calls] == ["targets", "from_app", "declared", "seeds", "seed_from_app"]

    @pytest.mark.asyncio
    async def test_a_seed_is_not_held_to_the_request_org(self) -> None:
        """The org test applies to the nodes asked for. A seed dropped from the
        re-check for its org (or for having none) would count as admitted by no
        other arm, and open everything below it; Neo4j never tests its org."""
        provider, calls = _provider({
            "targets": [_row("d", walk=True)], "seeds": [{"node": "d", "seed": "s1"}],
        })
        await _rows(provider, ["d"])
        rechecks = [bind for stage, _, _, bind in calls if stage.startswith("seed_")]
        assert len(rechecks) == 2 and all("org_id" not in bind for bind in rechecks)
        texts = ArangoHTTPProvider._kh_v3_check_aql()
        assert "@org_id" in texts["from_app"] and "@org_id" in texts["declared"]
        assert "@org_id" not in texts["seed_from_app"] and "@org_id" not in texts["seed_declared"]

    @pytest.mark.asyncio
    async def test_nothing_undecided_walks_nothing(self) -> None:
        provider, calls = _provider({"targets": [_row("a", ok=True)]})
        assert await _rows(provider, ["a", "x"]) == {"a"}
        assert [stage for stage, *_ in calls] == ["targets"]

    @pytest.mark.asyncio
    async def test_the_rows_carry_only_what_the_check_reads(self) -> None:
        provider, _ = _provider({"targets": [_row("a", ok=True)]})
        rows = await provider._kh_v3_accessible_rows("u", "org", ["a"], [], access=ACCESS, transaction=None)
        assert rows == [{"id": "a", "vrid": "vr-a", "connectorId": "app", "indexingStatus": "COMPLETED",
                         "isInternal": False, "groupIds": []}]

    @pytest.mark.asyncio
    async def test_no_stage_asks_for_the_plan_cache(self) -> None:
        """Storing some of these plans in Arango 3.12.4's plan cache crashes the
        server (SIGSEGV in SubqueryEndNode::estimateCost), so a request must never
        ask for it."""
        provider, calls = _provider({
            "targets": [_row("d", walk=True)], "seeds": [{"node": "d", "seed": "s1"}],
        })
        await _rows(provider, ["d"])
        assert {stage for stage, *_ in calls} == {
            "targets", "from_app", "declared", "seeds", "seed_from_app", "seed_declared",
        }
        assert all(not (options or {}).get("usePlanCache") for _, _, options, _ in calls)

    @pytest.mark.asyncio
    async def test_no_org_asks_nothing(self) -> None:
        provider, calls = _provider({})
        assert await provider._kh_v3_accessible_rows("u", "", ["a"], ["v"], access=ACCESS, transaction=None) == []
        assert calls == []
