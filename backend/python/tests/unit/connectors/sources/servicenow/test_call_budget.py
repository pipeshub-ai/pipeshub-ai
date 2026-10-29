"""How many Table API calls a sync makes on a model instance, and that fewer calls change nothing.

The model: 30 knowledge bases, 1,000 articles (a tenth with Cannot Read, a tenth with Can Read and an
ownership group), an attachment on every fifth article and feedback on every fourth. The bounds are
upper bounds, not exact counts: a change that reads less passes, one that reads more per page or per
article fails.
"""

from __future__ import annotations

import hashlib
from collections import Counter

import pytest
from test_permissions import Graph, Instance, _connector, _people

T0 = "2024-06-01 00:00:00"
T1 = "2024-06-02 00:00:00"  # after every row of the model changed


def _model(sc, *, articles: int = 1000, kbs: int = 30) -> tuple[Instance, Graph, list[dict]]:
    inst = Instance()
    _people(inst)
    inst.users["hr"]["department"] = "d-hr"
    for k in range(kbs):
        can = [inst.crit(f"c-hr{k}", group="g-hr"), inst.crit(f"c-d{k}", department="d-hr")]
        if k % 10 == 0:  # resolved to users: Match All over two fields
            can.append(inst.crit(f"c-all{k}", match_all="true", group="g-eng", role="r-knowledge"))
        inst.kb(f"kb{k:02d}", can=tuple(can), contribute=(inst.crit(f"c-c{k}", user="contrib"),))
    for i in range(articles):
        number = f"KB{i:07d}"
        fields: dict[str, str] = {}
        if i % 10 == 0:
            fields["cannot_read_user_criteria"] = inst.crit(f"c-no{i}", user="hr")
        elif i % 10 == 1:
            fields.update(can_read_user_criteria=f"c-hr{i % kbs}", ownership_group="g-eng")
        inst.article(number, f"kb{i % kbs:02d}", **fields)
        if i % 5 == 0:
            inst.attachments.append({
                "sys_id": f"{i:032x}", "file_name": f"f{i}.pdf", "content_type": "application/pdf",
                "size_bytes": "10", "hash": f"h{i}", "table_name": "kb_knowledge", "table_sys_id": f"s-{number}",
                "sys_created_on": T0, "sys_updated_on": T0,
            })
    graph = _connector(sc, inst)
    feedback = [{"sys_id": f"fb{i:05d}", "article": f"s-KB{i:07d}", "article.number": f"KB{i:07d}",
                 "sys_updated_on": T0} for i in range(0, articles, 4)]
    return inst, graph, feedback


async def _run(sc, inst: Instance, feedback: list[dict]) -> Counter:
    inst.fake.tables = {**inst.tables(), "kb_feedback": feedback}
    inst.fake.calls.clear()
    await sc.run_sync()
    return Counter(table for table, _, _ in inst.fake.calls)


def _later(sc) -> None:
    sc.user_sync_point.data["users"] = {"last_sync_time": T1}
    sc.article_sync_point.data["articles"] = {"last_sync_time": T1}


def _readers_digest(graph: Graph, inst: Instance) -> str:
    keys = sorted(graph.store.records)
    text = repr([(k, sorted(graph.readers(k))) for k in keys] + [(kb, sorted(graph.kb_readers(kb))) for kb in inst.kbs])
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@pytest.mark.asyncio
async def test_call_budget_full_quiet_and_after_a_membership_change(servicenow_connector) -> None:
    sc = servicenow_connector
    inst, graph, feedback = _model(sc)

    full = await _run(sc, inst, feedback)
    full_digest = _readers_digest(graph, inst)
    _later(sc)
    quiet = await _run(sc, inst, feedback)
    quiet_digest = _readers_digest(graph, inst)
    inst.members.append(("plain", "g-hr"))
    changed = await _run(sc, inst, feedback)
    changed_digest = _readers_digest(graph, inst)

    for name, calls, digest in (("full", full, full_digest), ("quiet", quiet, quiet_digest),
                                ("membership change", changed, changed_digest)):
        print(f"{name}: {sum(calls.values())} calls, readers {digest}: {dict(sorted(calls.items()))}")
    assert len(graph.store.keys("WEBPAGE")) == 1000
    assert quiet_digest == full_digest
    assert "plain" in graph.readers("KB0000001") and "plain" in graph.readers("KB0000000")

    # Upper bounds (the model made 144, 58 and 83 calls before the listings below were merged).
    assert sum(full.values()) <= 60
    assert full["sys_attachment"] == full["kb_feedback"] == 1  # once for the run, not per page
    assert full["sys_user_grmember"] <= 2 and full["sys_user_has_role"] <= 3  # criteria use the run's listings
    assert full["kb_knowledge"] <= 10 + 1  # 100-article pages, and the reconcile
    assert sum(quiet.values()) <= 30
    assert quiet["sys_user_grmember"] == quiet["sys_user_has_role"] == 1  # the 1-row access checks only
    assert sum(changed.values()) <= 50
    assert changed["sys_attachment"] <= 1  # the re-read pass lists no attachments here


def _written(sc) -> list[str]:
    written = [r.external_record_id for c in sc.data_entities_processor.on_new_records.await_args_list
               for r, _ in c.args[0]]
    sc.data_entities_processor.on_new_records.reset_mock()
    return written


@pytest.mark.asyncio
async def test_quiet_runs_after_a_change_cost_what_a_quiet_run_costs(servicenow_connector) -> None:
    # Each delta starts DELTA_OVERLAP_SECONDS before the checkpoint, so a run with nothing new reads
    # the last change again: it must not sync it again.
    sc = servicenow_connector
    inst, graph, feedback = _model(sc, articles=60, kbs=3)
    await _run(sc, inst, feedback)
    _written(sc)
    baseline = await _run(sc, inst, feedback)  # quiet, no change since the full read
    assert _written(sc) == []

    # KB0000001 has its own Can Read and an ownership group: syncing it re-reads its access.
    inst.articles["KB0000001"].update(text="<p>edited</p>", sys_updated_on="2024-06-01 05:00:00")
    changed = await _run(sc, inst, feedback)
    assert _written(sc) == ["KB0000001"]
    assert changed["user_criteria"] > baseline["user_criteria"]  # the access work the change triggers
    quiet = [await _run(sc, inst, feedback), await _run(sc, inst, feedback)]

    assert _written(sc) == []
    assert quiet == [baseline, baseline]
    # A row committed late into the overlap, before the checkpoint, is still synced.
    inst.articles["KB0000002"]["sys_updated_on"] = "2024-06-01 04:59:30"
    await _run(sc, inst, feedback)
    assert _written(sc) == ["KB0000002"]
