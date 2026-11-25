"""G2C-F1: the workspace note an engine task starts with."""

from __future__ import annotations

from harness_core.engines.briefing import (
    BriefedFile,
    WorkspaceEntry,
    earlier_files,
    engine_task_prompt,
    workspace_briefing,
)
from harness_core.workspace.types import ObjectRef, WorkspaceFile

SALES = b"date,region,revenue\n2025-01-03,North,1200\n2025-01-04,South,800\n\n2025-01-05,West,950\n2025-01-06,East,400\n"


def test_a_csv_input_is_described_with_columns_rows_and_a_preview() -> None:
    note = workspace_briefing([BriefedFile("sales.csv", SALES)])
    assert "- in/sales.csv (" in note
    assert "4 data rows; columns: date, region, revenue" in note
    assert "    2025-01-03,North,1200" in note and "    2025-01-05,West,950" in note
    # Only the first rows are shown: the engine computes over the rest with code.
    assert "2025-01-06" not in note
    assert "do not read a whole data file into the conversation" in note
    assert "This list is complete: start the work without listing the folder." in note


def test_a_tsv_uses_tabs_and_other_files_get_name_and_size_only() -> None:
    tsv = b"a\tb\n1\t2\n"
    note = workspace_briefing([BriefedFile("t.tsv", tsv), BriefedFile("deck.pptx", b"PK\x03\x04" + b"\x00" * 2000)])
    assert "- in/t.tsv (8 bytes): 1 data rows; columns: a, b" in note
    assert "- in/deck.pptx (2.0 KB)\n" in note


def test_no_inputs_and_where_deliverables_go() -> None:
    note = workspace_briefing([], outputs_dir="/out/")
    assert "This task has no input files: start the work without listing the folder." in note
    assert "Deliverables go under out/" in note and "mkdir -p out &&" in note
    assert "pandas" not in note


def test_fixp2_b1_a_path_the_task_names_is_not_moved_under_out() -> None:
    """FIX-P2-B1: the note's last line sent every file under out/, and every engine wrote the wiki the person asked
    for at wiki/index.md as out/wiki/index.md (S3 research-wiki, all three engines, after the instructions were fixed)."""
    note = workspace_briefing([], outputs_dir="out")
    last = note.splitlines()[-1]
    assert last.startswith("Deliverables go under out/")
    assert "A file the task or the notes in the workspace name by a path (wiki/index.md) goes at exactly that path, not under out/." in last


def test_undecodable_or_malformed_text_falls_back_to_name_and_size() -> None:
    note = workspace_briefing([BriefedFile("bad.csv", b"\xff\xfe\x00bad"), BriefedFile("empty.csv", b"")])
    assert "- in/bad.csv (6 bytes)\n" in note
    assert "- in/empty.csv (0 bytes)\n" in note


def test_the_note_is_bounded_whatever_the_inputs() -> None:
    wide = (",".join(f"column_{i}_" + "x" * 80 for i in range(200)) + "\n") * 5
    files = [BriefedFile(f"f{i}.csv", wide.encode()) for i in range(30)]
    note = workspace_briefing(files)
    assert "… and 10 more under in/" in note
    assert "(200 columns)" in note
    assert max(len(line) for line in note.splitlines()) <= 2_400
    assert len(note) < 60_000


def test_a_very_large_csv_is_previewed_from_its_head_without_a_row_count() -> None:
    big = b"id,value\n" + b"".join(b"%d,%d\n" % (i, i) for i in range(1_800_000))
    assert len(big) > 16 * 1024 * 1024
    note = workspace_briefing([BriefedFile("big.csv", big)])
    assert "columns: id, value" in note and "data rows" not in note
    assert "    0,0" in note


def test_g2c2_b1_the_task_prompt_is_the_goal_then_the_note() -> None:
    files = [BriefedFile("sales.csv", SALES)]
    assert engine_task_prompt("make a chart", files) == "make a chart\n\n" + workspace_briefing(files)


def test_g2c2_b2_files_from_earlier_work_are_listed_and_the_list_stays_complete() -> None:
    others = [WorkspaceEntry("out/deck.pptx", 34_000), WorkspaceEntry("/in/old.csv", 10)]
    note = workspace_briefing([], others=others)
    assert "This task has no input files.\nAlso in the workspace from earlier work:" in note
    assert "- out/deck.pptx (33.2 KB)" in note and "- in/old.csv (10 bytes)" in note
    assert "This list is complete: start the work without listing the folder." in note
    assert "This task has no input files: start" not in note
    with_input = workspace_briefing([BriefedFile("sales.csv", SALES)], others=others[:1])
    assert with_input.index("- in/sales.csv") < with_input.index("- out/deck.pptx")
    many = workspace_briefing([], others=[WorkspaceEntry(f"out/f{i}.md", 1) for i in range(25)])
    assert "- … and 5 more" in many


def test_g2c2_b2_earlier_files_leave_out_the_tasks_own_inputs() -> None:
    def entry(path: str, size: int) -> WorkspaceFile:
        return WorkspaceFile(path, ObjectRef("o-" + path, "h-" + path, size))

    files = {p: entry(p, n) for p, n in (("in/sales.csv", 5), ("out/chart.png", 7), ("in/old.csv", 3))}
    assert earlier_files(files, ["sales.csv"]) == (WorkspaceEntry("in/old.csv", 3), WorkspaceEntry("out/chart.png", 7))
