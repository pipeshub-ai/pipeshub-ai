"""What the record handler's gate accepts, the legacy dispatch reads, with the reader the gate judged (F23b).

The universe is built from the registry's own data, so a type added later is covered without
editing this file; the only literals are the mimes the local-folder connector really stamps on
repository files. The dispatch's decision is ``EventProcessor._read_plan``, the one ``on_event`` makes.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.config import file_types
from app.config.file_types import Dispatch, Route
from app.events.events import EventProcessor

_OCTET, _TEXT = "application/octet-stream", "text/plain"
_PPTM_MIME = "application/vnd.ms-powerpoint.presentation.macroEnabled.12"
# What the local-folder connector's mimetypes guess stamps on repository files that have no
# grammar (its .css, .pl, .pm, .hs, .sql): the gate takes them by name, the dispatch reads the mime.
LOCAL_FOLDER_MIMES = ("text/css", "text/x-perl", "text/x-haskell", "application/sql")

Case = tuple[Any, Any, str, str]  # (extension, mime, record name, record type)


def _universe() -> list[Case]:
    cases: set[Case] = set()
    for ft in file_types.FILE_TYPES:
        for ext in ft.extensions:
            for mime in {None, "unknown", _OCTET, _TEXT, *ft.mimes}:
                cases.add((ext, mime, f"file.{ext}", "FILE"))
    mimes = {m for ft in file_types.FILE_TYPES for m in ft.mimes}
    mimes |= set(file_types.MIME_EXTENSION_HINTS) | set(file_types.GATE_MIMES)
    for mime in mimes:
        for ext in (None, "unknown"):
            for name in ("file", "Report.DOCX"):
                cases.add((ext, mime, name, "FILE"))
    extensions = {e for ft in file_types.FILE_TYPES for e in ft.extensions} | set(file_types.REPO_CODE_EXTENSIONS)
    for ext in extensions:
        own = file_types.by_extension(ext)
        for mime in {None, _TEXT, _OCTET, *(own.mimes if own else ()), *LOCAL_FOLDER_MIMES}:
            cases.add((ext, mime, f"file.{ext}", "CODE_FILE"))
    return sorted(cases, key=repr)


def _gate(case: Case) -> file_types.GateDecision:
    ext, mime, name, record_type = case
    is_code_file = record_type == "CODE_FILE"
    return file_types.gate_decision(
        mime=mime, extension=ext, record_name=name, is_code_file=is_code_file,
        code_file_extension=name.rsplit(".", 1)[-1].lower() if is_code_file and "." in name else None,
    )


def _own_route(case: Case) -> Dispatch | None:
    ext, mime, name, record_type = case
    return EventProcessor._read_plan(record_type, mime, ext, None, name, None)[0]


_UNIVERSE = _universe()
_ACCEPTED = [(case, decision) for case in _UNIVERSE if (decision := _gate(case)).supported]

# The gate judges these on an extension the dispatch never sees, so their readers differ: one taken
# from the record name (Report.DOCX), or from the pptm mime's hint (ppt). Queued as F23c; pinned so
# a new disagreement fails and so does a fixed one.
_KNOWN_DISAGREEMENTS = frozenset({
    ("unknown", "application/sql_table", "Report.DOCX", "FILE", "docx", "sql_table"),
    ("unknown", "application/sql_view", "Report.DOCX", "FILE", "docx", "sql_view"),
    ("unknown", "application/x-yaml", "Report.DOCX", "FILE", "docx", "yaml"),
    ("unknown", "text/yaml", "Report.DOCX", "FILE", "docx", "yaml"),
    (None, _PPTM_MIME, "file", "FILE", "ppt", "pptx"),
    (None, _PPTM_MIME, "Report.DOCX", "FILE", "ppt", "pptx"),
    ("unknown", _PPTM_MIME, "file", "FILE", "ppt", "pptx"),
    ("unknown", _PPTM_MIME, "Report.DOCX", "FILE", "ppt", "pptx"),
})


def test_the_universe_reaches_the_cases_that_were_refused() -> None:
    assert len(_ACCEPTED) > 1500
    accepted = {case for case, _ in _ACCEPTED}
    assert ("css", "text/css", "file.css", "CODE_FILE") in accepted
    assert ("pptm", _PPTM_MIME, "file.pptm", "FILE") in accepted
    assert (None, _PPTM_MIME, "file", "FILE") in accepted


def test_what_the_gate_accepts_the_dispatch_routes() -> None:
    unrouted = [
        case for case, decision in _ACCEPTED
        if EventProcessor._read_plan(case[3], case[1], case[0], None, case[2], decision.route)[0] is None
    ]
    assert unrouted == []


def test_the_gate_and_the_dispatch_pick_the_same_reader() -> None:
    disagreements = set()
    for case, decision in _ACCEPTED:
        own = _own_route(case)
        # A FILE: the reader the gate judged (the type its off switch reads). A CODE_FILE: the gate's
        # route, set only for repository text, since the gate does not judge a CODE_FILE's mime.
        if case[3] == "CODE_FILE":
            judged = decision.route.route if decision.route is not None else None
        else:
            judged = decision.file_type.route if decision.file_type is not None else None
        if own is not None and judged is not None and own.route is not judged:
            disagreements.add((*case, judged.value, own.route.value))
    assert disagreements == _KNOWN_DISAGREEMENTS


@pytest.mark.parametrize(
    ("ext", "mime", "expected"),
    [
        ("css", "text/css", Route.CODE),
        ("tf", _OCTET, Route.CODE),
        ("sql", "application/sql", Route.CODE),
        ("hs", None, Route.CODE),
        # An extension with a reader of its own is read by it, so the gate names nothing.
        ("md", "text/markdown", None),
        ("pdf", _OCTET, None),
        ("py", _TEXT, None),
    ],
)
def test_the_gate_names_code_only_for_repository_text(ext: str, mime: str | None, expected: Route | None) -> None:
    decision = _gate((ext, mime, f"file.{ext}", "CODE_FILE"))
    assert decision.supported
    assert (decision.route.route if decision.route else None) is expected
    if expected is not None:
        assert decision.route == Dispatch(Route.CODE, None)


def test_the_gate_names_no_route_for_a_file_or_a_refusal() -> None:
    assert _gate(("pdf", "application/pdf", "file.pdf", "FILE")).route is None
    assert _gate((None, _PPTM_MIME, "deck", "FILE")).route is None
    refused = _gate(("zip", _OCTET, "file.zip", "CODE_FILE"))
    assert (refused.supported, refused.route) == (False, None)
    disabled = file_types.gate_decision(
        mime="text/css", extension="css", record_name="file.css", is_code_file=True,
        code_file_extension="css", disabled=frozenset({"css"}),
    )
    assert (disabled.supported, disabled.refusal, disabled.route) == (False, "disabled", None)


def test_the_pptm_mime_is_read_as_the_pptx_it_is_by_the_dispatch_itself() -> None:
    for ext, name in ((None, "deck"), ("unknown", "deck"), ("pptm", "deck.pptm")):
        own = _own_route((ext, _PPTM_MIME, name, "FILE"))
        assert own is not None and own.route is Route.PPTX, ext
    # The extension alone is still not indexable.
    assert not _gate(("pptm", _OCTET, "deck.pptm", "FILE")).supported
