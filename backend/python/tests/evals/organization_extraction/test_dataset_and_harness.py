"""The organisation eval's dataset and scoring, without a model."""
from __future__ import annotations

import json

from app.modules.entity_resolution.organizations import organization_key
from tests.evals.organization_extraction.run import DATASET, Score, evaluate

DATA = json.loads(DATASET.read_text())


def test_every_document_is_well_formed() -> None:
    ids = [d["id"] for d in DATA["documents"]]
    assert len(ids) == len(set(ids)) >= 40
    for doc in DATA["documents"]:
        assert doc["text"].strip() and doc["connector"] and doc["record_name"]
        keys = [organization_key(a) for aliases in doc["gold"] + doc["optional"] for a in aliases]
        assert all(keys), doc["id"]
        # One organisation per entry: no key in two entries of a document.
        entries = [{organization_key(a) for a in aliases} for aliases in doc["gold"] + doc["optional"]]
        assert all(not (a & b) for i, a in enumerate(entries) for b in entries[i + 1:]), doc["id"]


def test_the_set_has_negatives_and_traps() -> None:
    assert sum(1 for d in DATA["documents"] if not d["gold"]) >= 7
    assert any(d["optional"] for d in DATA["documents"])


async def test_the_oracle_scores_perfectly_once_the_tenant_is_filtered() -> None:
    report = await evaluate(DATA, "oracle")
    assert report["after_filter"]["precision"] == report["after_filter"]["recall"] == 1.0
    assert report["extracted"]["precision"] < 1.0  # the tenant, which the filter removes


def test_a_duplicate_spelling_and_an_unlabelled_name_count_against_precision() -> None:
    score = Score()
    doc = {"id": "x", "gold": [["Globex Corporation", "Globex"]], "optional": [["Datadog"]]}
    score.add(doc, ["Globex Corp.", "Globex", "Datadog", "Hooli"])
    report = score.report()
    assert (report["precision"], report["recall"]) == (round(1 / 3, 4), 1.0)
    assert report["errors"] == [{"id": "x", "false_positives": ["Globex", "Hooli"], "missed": []}]
