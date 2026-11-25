"""The mixed-fleet model: with the identity rules, no skip relies on an identity that claims a verification it did not have.

Three records sharing bytes, renames, forced runs with the twin re-point and the md5 twin attach, with 30 % of the processes on the previous
release. The gate is *known-identity wrong skips: 0*; the residual is records that never had an identity (indexed before it existed),
which shrinks as they are indexed again. The negative controls remove one rule each and must break the gate.
"""
from __future__ import annotations

import pytest

from app.modules.pipeline import parser_identity as pi
from tests.support import parser_identity_model as model

#: A third of each reviewer range (the full ranges are in the ledger).
SEEDS = [range(a, a + 700) for a, _ in model.RANGES]


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")


@pytest.mark.parametrize("seeds", SEEDS, ids=lambda r: f"seeds-{r.start}")
@pytest.mark.parametrize("p_old", [0.0, 0.3])
def test_no_known_identity_relies_on_a_wrong_copy(seeds: range, p_old: float) -> None:
    wrong, skips = model.total(seeds, p_old=p_old)
    assert sum(skips.values()) > 1000, "the world must exercise the skip"
    assert model.known_wrong(wrong) == 0, dict(wrong)
    assert not [k for k in wrong if k[0] == "new" and k[1] in model.KNOWN_STATES]


def test_the_residual_is_only_records_that_never_had_an_identity() -> None:
    wrong, _ = model.total(SEEDS[0], p_old=0.3)
    assert {state for (_, state) in wrong if state not in ("none",)} <= {"absent", "adopted"}
    assert model.never_identified_wrong(wrong) < 400  # about 150 per 700 seeds on these ranges; the reviewer's full-range figure is 435-459 per 2000


def test_without_the_vrid_binding_known_identities_claim_a_wrong_copy() -> None:
    """An old process's re-point moves the copy and leaves the timestamp, so the timestamp alone cannot show it."""
    def timestamp_only(rec: dict) -> str | None:
        stored = rec.get(pi.IDENTITY_FIELD)
        return stored if stored and rec.get(pi.IDENTITY_AT_FIELD) == rec.get("lastIndexTimestamp") else None

    wrong, _ = model.total(SEEDS[0], p_old=0.3, known=timestamp_only)
    assert model.known_wrong(wrong) > 0


def test_an_attach_that_ignores_the_identity_leaves_a_record_adopted_over_the_wrong_copy() -> None:
    """The full run a mismatch forces is settled by an md5 twin of another parser; the record then adopts its own type."""
    wrong, _ = model.total(SEEDS[0], p_old=0.0, gate_attach=False, copy_identity=False)
    assert model.never_identified_wrong(wrong) > 0
    gated, _ = model.total(SEEDS[0], p_old=0.0)
    assert model.never_identified_wrong(gated) < model.never_identified_wrong(wrong)


def test_without_carrying_the_source_an_adopted_label_is_laundered_into_a_verified_one() -> None:
    wrong, _ = model.total(SEEDS[0], p_old=0.3, carry_source=False)
    clean, _ = model.total(SEEDS[0], p_old=0.3)
    assert model.known_wrong(wrong) > model.known_wrong(clean) == 0
