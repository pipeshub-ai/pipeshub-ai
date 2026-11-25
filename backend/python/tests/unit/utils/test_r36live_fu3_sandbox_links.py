"""R36LIVE-FU-3: an engine's answer that links a file by its own sandbox (Codex's
``[Download fibonacci.py](sandbox:/mnt/data/out/fibonacci.py)``, a ``file://`` URL, an absolute or relative workspace
path) links the saved artifact instead, at the one place engine answers are post-processed
(``link_workspace_files``: the job's presenter and ``code_task``). A file the run did not save keeps its words and
loses the dead link: the artifact card under the answer is how it is opened."""

from __future__ import annotations

import pytest

from app.utils.answer_links import link_workspace_files

SAVED = {"fibonacci.py": "rec-1"}


@pytest.mark.parametrize(
    ("written", "shown"),
    [
        ("[Download fibonacci.py](sandbox:/mnt/data/out/fibonacci.py)", "[Download fibonacci.py](/record/rec-1)"),
        ("[Download fibonacci.py](sandbox:/mnt/data/fibonacci.py)", "[Download fibonacci.py](/record/rec-1)"),
        ("[the script](file:///workspace/out/fibonacci.py)", "[the script](/record/rec-1)"),
        ("[the script](/workspace/out/fibonacci.py)", "[the script](/record/rec-1)"),
        ("[the script](./out/fibonacci.py)", "[the script](/record/rec-1)"),
        ("[out/fibonacci.py](out/fibonacci.py)", "[fibonacci.py](/record/rec-1)"),
        ("[sandbox:/mnt/data/out/fibonacci.py](sandbox:/mnt/data/out/fibonacci.py)", "[fibonacci.py](/record/rec-1)"),
    ],
)
def test_r36live_fu3_a_sandbox_link_becomes_the_saved_artifacts(written: str, shown: str) -> None:
    assert link_workspace_files(f"Done. {written}", SAVED) == f"Done. {shown}"


@pytest.mark.parametrize(
    "written",
    ["[Download notes.txt](sandbox:/mnt/data/out/notes.txt)", "[notes](file:///tmp/notes.txt)", "[notes](/workspace/in/notes.txt)"],
)
def test_r36live_fu3_an_unsaved_files_link_is_dropped_and_its_words_kept(written: str) -> None:
    out = link_workspace_files(written, SAVED)
    assert "](" not in out and "sandbox:" not in out and "file:" not in out and "/workspace" not in out
    assert "notes" in out


def test_r36live_fu3_real_links_citations_and_app_routes_are_kept() -> None:
    text = "[site](https://example.com/out/fibonacci.py) [rec](/record/abc) [1](ref1) [top](#top)"
    assert link_workspace_files(text, SAVED) == text
