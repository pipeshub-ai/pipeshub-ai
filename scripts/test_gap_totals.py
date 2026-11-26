"""Tests for scripts/gap_totals.py (run: python3 -m unittest discover -s scripts -p 'test_*.py')."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gap_totals as gt  # noqa: E402

TSV_HEADER = "gap\tcomponent\tstatus\n"


def doc_with(totals: str, appendix: dict[str, str]) -> str:
    rows = "\n".join(f"| {g} | X | what | {s} |" for g, s in appendix.items())
    return (f"# Gaps\n\n| | Count |\n|---|---|\n| Component, integration and e2e gaps | {gt.BEGIN}{totals}{gt.END} |\n"
            f"| Consolidated gaps | 35 |\n\n{gt.APPENDIX}\n\n| Gap | C | What | Status |\n|---|---|---|---|\n{rows}\n")


class ParseStatusTest(unittest.TestCase):
    def test_states_and_the_first_wp_named(self):
        p = gt.parse_status
        self.assertEqual(p("G", "open"), gt.Status("open", None))
        self.assertEqual(p("G", "open: QA-1 tests in, strict xfail until CPD-6"), gt.Status("open", None))
        self.assertEqual(p("G", "closed (QA-4)"), gt.Status("closed", "QA-4"))
        self.assertEqual(p("G", "closed: CPS NR-1..NR-6"), gt.Status("closed", "CPS"))
        self.assertEqual(p("G", "closed (H10)"), gt.Status("closed", "H10"))
        self.assertEqual(p("G", "closed (MCP-E2E): the hub on a stack"), gt.Status("closed", "MCP-E2E"))
        self.assertEqual(p("G", "partial: MCPFIX the API side"), gt.Status("partial", "MCPFIX"))
        self.assertEqual(p("G", "closed: already run by test_x.py (found by QA-19)"), gt.Status("closed", "QA-19"))
        self.assertEqual(p("G", "partial: CPS PN-6 claim-time drift"), gt.Status("partial", "CPS"))
        self.assertEqual(p("G", "partly closed (QA-8; browser check with QA-6)"), gt.Status("partial", "QA-8"))

    def test_a_lettered_perf_package_is_a_work_package_and_its_case_ids_are_not(self):
        self.assertEqual(gt.parse_status("G", "closed (PERF-B, PERFB-16)"), gt.Status("closed", "PERF-B"))
        self.assertEqual(gt.parse_status("G", "partial: PERFB-16 holds it (PERF-B)").wp, "PERF-B")

    def test_case_ids_are_not_work_packages(self):
        self.assertEqual(gt.parse_status("G", "closed: QA27-F4 fixed by QA-27").wp, "QA-27")
        self.assertEqual(gt.parse_status("G", "closed: QA15F-2 holds it (QA-15F)").wp, "QA-15F")
        with self.assertRaisesRegex(gt.GapStatusError, "names no work package"):
            gt.parse_status("ADM-G8", "partial: surfaces ask the decider (QA8-9)")

    def test_unknown_state_is_refused(self):
        with self.assertRaisesRegex(gt.GapStatusError, "does not start with"):
            gt.parse_status("G", "done by QA-4")


class TotalsTest(unittest.TestCase):
    def test_counts_per_wp_in_wp_order_and_partial_counts_as_not_closed(self):
        statuses = {"A-G1": "closed (QA-10)", "A-G2": "closed (QA-4)", "A-G3": "partial: QA-4 half",
                    "A-G4": "open", "A-G5": "closed: CPS NO-6", "A-G6": "partly closed (QA-4; rest open)"}
        self.assertEqual(gt.totals_text(statuses),
                         "6 found; 1 closed by CPS, 1 closed and 2 partly closed by QA-4, 1 closed by QA-10; "
                         "3 not closed, partly closed included (`data/gaps.tsv`)")

    def test_render_touches_only_the_marked_span(self):
        doc = doc_with("old text", {"A-G1": "open"})
        new = gt.render(doc, {"A-G1": "open"})
        self.assertEqual(new, doc.replace("old text", gt.totals_text({"A-G1": "open"})))
        self.assertEqual(gt.render(new, {"A-G1": "open"}), new)

    def test_render_refuses_a_document_without_one_marked_span(self):
        with self.assertRaisesRegex(gt.GapStatusError, "exactly one"):
            gt.render("| Component, integration and e2e gaps | 281 found |\n", {})


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.doc, self.tsv = d / "05.md", d / "gaps.tsv"
        statuses = {"A-G1": "closed (QA-4)", "A-G2": "open"}
        self.tsv.write_text(TSV_HEADER + "".join(f"{g}\tA\t{s}\n" for g, s in statuses.items()))
        self.doc.write_text(doc_with(gt.totals_text(statuses), statuses))

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh(self):
        self.assertEqual(gt.check(self.doc, self.tsv), [])

    def test_a_status_change_in_both_places_makes_only_the_totals_stale(self):
        self.tsv.write_text(TSV_HEADER + "A-G1\tA\tclosed (QA-4)\nA-G2\tA\tclosed (QA-9)\n")
        self.doc.write_text(self.doc.read_text().replace("| open |", "| closed (QA-9) |"))
        problems = gt.check(self.doc, self.tsv)
        self.assertEqual(len(problems), 1)
        self.assertIn("stale gap totals", problems[0])

    def test_a_status_changed_in_one_place_only_is_reported(self):
        self.tsv.write_text(TSV_HEADER + "A-G1\tA\tclosed (QA-4)\nA-G2\tA\tclosed (QA-9)\n")
        problems = gt.check(self.doc, self.tsv)
        self.assertTrue(any(p.startswith("A-G2: gaps.tsv 'closed (QA-9)' != appendix 'open'") for p in problems))

    def test_a_gap_missing_from_the_appendix_is_reported(self):
        self.tsv.write_text(self.tsv.read_text() + "A-G3\tA\topen\n")
        self.assertIn("A-G3: in gaps.tsv, not in the appendix", gt.check(self.doc, self.tsv))


class CommittedFilesTest(unittest.TestCase):
    def test_the_committed_totals_and_appendix_agree_with_gaps_tsv(self):
        self.assertEqual(gt.check(), [])


if __name__ == "__main__":
    unittest.main()
