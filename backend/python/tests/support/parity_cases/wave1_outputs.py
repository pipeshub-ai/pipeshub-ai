"""What a wave 1 case's reference solution produces, as the tests that drive it check it (WP123-FU-1).

A replay or lane test that drives a case with the scripted reference model asserts its own output here: the file it
must have written and what that file holds, or the figures and sources its answer must give. These are the
reference's fixed outputs, written out per case; they are not a grader and score nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.support.office_files import open_file

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.support.office_files import FileView
    from tests.support.parity_cases.wave1_systems import SystemRun


def _file(run: SystemRun, name: str) -> FileView:
    found = [open_file(f.name, f.content) for f in run.files if f.name == name]
    assert found, (name, [f.name for f in run.files])
    view = found[0]
    assert view.valid, (name, view.reason)
    return view


def _incident_memo(run: SystemRun) -> None:
    memo = _file(run, "q3_incidents_memo.docx")
    assert memo.headings == ("Summary", "Incidents", "Action items"), memo.headings
    for fact in ("47 minutes", "312 payments", "18% of iOS logins", "41 orders"):
        assert fact in memo.text, (fact, memo.text[:400])


def _top_region(run: SystemRun) -> None:
    assert "top_region: West" in run.answer and "top_region_revenue: 572880" in run.answer, run.answer[:400]


def _globex_status(run: SystemRun) -> None:
    assert "latency is 180 ms against a 100 ms SLA" in run.answer, run.answer[:400]
    assert set(run.cited_sources) == {"cust-globex", "tkt-nw-114"}, run.cited_sources


def _revenue_by_product(run: SystemRun) -> None:
    assert "arm200_h1_units: 182" in run.answer, run.answer[:400]
    book = _file(run, "revenue_by_product.xlsx")
    (table,) = book.tables
    assert table.header == ("product", "revenue"), table.header
    *products, total = table.rows
    assert [p[0] for p in products] == ["Arm-100", "Arm-200", "Gripper G2", "Vision Kit"], products
    assert total == ("Total", sum(int(p[1]) for p in products)) == ("Total", 1_771_780), total


PRODUCED: dict[str, Callable[[SystemRun], None]] = {
    "od-01-incident-memo": _incident_memo,
    "da-02-top-region": _top_region,
    "rp-03-globex-status": _globex_status,
    "da-05-revenue-by-product-file": _revenue_by_product,
}


def assert_produced(case_id: str, run: SystemRun) -> None:
    """The run delivered what ``case_id``'s reference solution produces."""
    PRODUCED[case_id](run)


__all__ = ["PRODUCED", "assert_produced"]
