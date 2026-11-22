"""What the record handler's gate accepts, the legacy dispatch reads, with the reader the gate judged.

The universe is built from the registry's own data, so a type added later is covered without
editing this file; the only literals are the mimes the local-folder connector really stamps on
repository files and the file paths. The dispatch's decision is ``EventProcessor._read_plan``, the
one ``on_event`` makes.
"""
from __future__ import annotations

import functools
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
# The dispatch reads a record's path before its name. "same" is the record name under a folder.
_SAME = "same"
_OPAQUE_PATH = "x/blob.bin"
_PATHS = (None, _SAME, "x/app.log", "x/Report.docx", _OPAQUE_PATH)
# A mime that says nothing about the bytes, spelled out here so the check below does not lean on the
# production predicate it guards (text/plain is in the registry's set too; it routes to TXT first).
_PLACEHOLDER_MIMES = frozenset({"", "unknown", "application/unknown", "application/octet-stream", "text/plain"})

# A name with no extension, one hidden by case, one the gate takes by name, source, and a type the
# extension decides.
_SPELLING_NAMES = ("file", "Report.DOCX", "Report.docx", "Report.py", "Report.log")

Case = tuple[Any, Any, str, str]  # (extension, mime, record name, record type)


def _registry_mimes() -> set[str]:
    mimes = {m for ft in file_types.FILE_TYPES for m in ft.mimes}
    return mimes | set(file_types.MIME_EXTENSION_HINTS) | set(file_types.GATE_MIMES)


def _profile_names() -> set[str]:
    """One record name per way a name's extension can be read: the route, whether the extension
    decides, is indexable, is source, is in the gate's own list. The full sweep over every
    extension is the F23c repro's scan; one name per profile keeps the cases that differ."""
    names: dict[tuple[object, ...], str] = {}
    for ft in file_types.FILE_TYPES:
        if ft.extensions:
            ext = ft.extensions[0]
            profile = (
                ft.route, ft.ext_wins, ft.ext_indexable, ft.repo_code, ft.grammar, ft.since == 1,
                ft.route_since > 1, ft.ext_indexable and ext in file_types.GATE_EXTENSIONS,
            )
            names.setdefault(profile, f"Report.{ext}")
    return {"file", "Report.DOCX", *names.values()}


def _universe() -> list[Case]:
    cases: set[Case] = set()
    for ft in file_types.FILE_TYPES:
        for ext in ft.extensions:
            for mime in {None, "unknown", _OCTET, _TEXT, *ft.mimes}:
                cases.add((ext, mime, f"file.{ext}", "FILE"))
    # A record with no extension of its own: every registry mime named by nothing, by an upper-case
    # name, or by one name per reading profile; its upper-case and parameterised spellings, which the
    # hint lookup does not match, by the names each way of reading a name needs.
    for ext in (None, "unknown"):
        for mime in _registry_mimes() | {None, "unknown", _OCTET, _TEXT}:
            for name in _profile_names():
                cases.add((ext, mime, name, "FILE"))
        for mime in {s for m in _registry_mimes() for s in (m.upper(), f"{m}; charset=binary")}:
            for name in _SPELLING_NAMES:
                cases.add((ext, mime, name, "FILE"))
    extensions = {e for ft in file_types.FILE_TYPES for e in ft.extensions} | set(file_types.REPO_CODE_EXTENSIONS)
    for ext in extensions:
        own = file_types.by_extension(ext)
        for mime in {None, _TEXT, _OCTET, *(own.mimes if own else ()), *LOCAL_FOLDER_MIMES}:
            cases.add((ext, mime, f"file.{ext}", "CODE_FILE"))
    return sorted(cases, key=repr)


@functools.cache
def _cases() -> tuple[Case, ...]:
    return tuple(_universe())


@pytest.fixture(scope="module")
def universe() -> tuple[Case, ...]:
    """Built once per worker that runs these tests, never at import."""
    return _cases()


def _path(case: Case, path: str | None) -> str | None:
    return f"x/{case[2]}" if path == _SAME else path


def _gate(case: Case, path: str | None = None, disabled: frozenset[str] = frozenset()) -> file_types.GateDecision:
    ext, mime, name, record_type = case
    is_code_file = record_type == "CODE_FILE"
    return file_types.gate_decision(
        mime=mime, extension=ext, record_name=name, is_code_file=is_code_file,
        code_file_extension=name.rsplit(".", 1)[-1].lower() if is_code_file and "." in name else None,
        file_path=_path(case, path), disabled=disabled,
    )


def _plan(case: Case, path: str | None, gate_route: Dispatch | None) -> tuple[Dispatch | None, Any, bool]:
    ext, mime, name, record_type = case
    return EventProcessor._read_plan(record_type, mime, ext, _path(case, path), name, gate_route)


@functools.cache
def _accepted(path: str | None) -> tuple[tuple[Case, file_types.GateDecision], ...]:
    # A path is read only where the record has no extension of its own.
    cases = _cases() if path is None else [c for c in _cases() if c[0] in (None, "", "unknown")]
    return tuple((case, decision) for case in cases if (decision := _gate(case, path)).supported)


Planned = tuple[Case, file_types.GateDecision, Dispatch | None, tuple[Dispatch | None, Any, bool]]


@functools.cache
def _planned(path: str | None) -> tuple[Planned, ...]:
    """Each accepted case with the dispatch's route given the gate's, and its own plan without it."""
    out = []
    for case, decision in _accepted(path):
        own = _plan(case, path, None)
        handed = own if decision.route is None else _plan(case, path, decision.route)
        out.append((case, decision, handed[0], own))
    return tuple(out)


def _names_nothing(mime: object) -> bool:
    return mime is None or (isinstance(mime, str) and mime.split(";")[0].strip().lower() in _PLACEHOLDER_MIMES)


def _reader_key(case: Case, dispatch: Dispatch, code_ext: object) -> str:
    """The registry type that reads the file: the routed type, or for source the type named code."""
    if dispatch.file_type is not None:
        return dispatch.file_type.key
    coded = (file_types.by_extension(code_ext) if isinstance(code_ext, str) else None, file_types.by_mime(case[1]))
    return next((t.key for t in coded if t is not None and t.route is Route.CODE), Route.CODE.value)


def test_the_universe_reaches_the_cases_that_were_refused(universe: tuple[Case, ...]) -> None:
    accepted = {case for case, _ in _accepted(None)}
    assert len(accepted) > 15_000
    assert ("css", "text/css", "file.css", "CODE_FILE") in accepted
    assert ("pptm", _PPTM_MIME, "file.pptm", "FILE") in accepted
    assert (None, _PPTM_MIME, "file", "FILE") in accepted
    # Accepted by the name alone, and by a name the hint or a spelling hides.
    assert ("unknown", None, "Report.docx", "FILE") in accepted
    assert ("unknown", _PPTM_MIME.upper(), "Report.DOCX", "FILE") in accepted
    assert ("unknown", "application/pdf", "Report.py", "FILE") in accepted
    assert len(universe) > 20_000


@pytest.mark.parametrize("path", _PATHS)
def test_what_the_gate_accepts_the_dispatch_routes(path: str | None) -> None:
    unrouted = [case for case, _, handed, _ in _planned(path) if handed is None]
    # Known limitation: a specific mime is never overruled by the name, so a file accepted by its name
    # whose mime names a type with no in-process reader (zip, mp4, an upper-case code mime) stays
    # unrouted. So does one whose path's extension names nothing (the path hides the name).
    named_nothing = [case for case in unrouted if _names_nothing(case[1]) and path != _OPAQUE_PATH]
    assert named_nothing == []
    assert all(case[3] == "FILE" for case in unrouted)


@pytest.mark.parametrize("path", _PATHS)
def test_the_gate_and_the_dispatch_pick_the_same_reader(path: str | None) -> None:
    disagreements = set()
    for case, decision, _, (own, code_ext, _) in _planned(path):
        if own is None:
            continue
        if case[3] == "CODE_FILE":
            # The gate's route, set only for repository text, since the gate does not judge a CODE_FILE's mime.
            if decision.route is not None and decision.route.route is not own.route:
                disagreements.add((*case, decision.route.route.value, own.route.value))
            continue
        # A FILE: the type the gate judged, which its off switch reads, against the type that reads it.
        judged = decision.file_type.key if decision.file_type is not None else None
        if judged != (reads := _reader_key(case, own, code_ext)):
            disagreements.add((*case, judged, reads))
    assert sorted(disagreements, key=repr)[:20] == []


# (case, the type that reads it, the type the off switch named before)
_SWITCH = [
    pytest.param(("unknown", "application/x-yaml", "Report.DOCX", "FILE"), "yaml", "docx", id="yaml-named-docx"),
    pytest.param(("unknown", "application/sql_table", "Report.DOCX", "FILE"), "sql_table", "docx", id="sql-named-docx"),
    pytest.param(("unknown", "application/json; charset=utf-8", "Report.DOCX", "FILE"), "json", "docx", id="json-param"),
    pytest.param((None, _PPTM_MIME, "deck", "FILE"), "pptm", "ppt", id="pptm-by-its-hint"),
    pytest.param(("unknown", _PPTM_MIME.upper(), "Report.DOCX", "FILE"), "pptm", "docx", id="pptm-upper"),
    pytest.param(("unknown", f"{_PPTM_MIME}; charset=utf-8", "Report.DOCX", "FILE"), "pptm", "docx", id="pptm-param"),
    pytest.param((None, "TEXT/GMAIL_CONTENT", "file", "FILE"), "gmail", "html", id="gmail-upper"),
    pytest.param(("unknown", "application/pdf", "Report.py", "FILE"), "py", "pdf", id="source-by-name"),
    pytest.param(("unknown", "application/pdf; charset=binary", "Report.log", "FILE"), "log", "pdf", id="log-by-name"),
]


@pytest.mark.parametrize(("case", "reader", "before"), _SWITCH)
def test_the_off_switch_names_the_type_that_reads_the_file(case: Case, reader: str, before: str) -> None:
    own, code_ext, _ = _plan(case, None, None)
    assert own is not None and _reader_key(case, own, code_ext) == reader
    assert _gate(case, disabled=frozenset({reader})).refusal == "disabled"
    assert _gate(case, disabled=frozenset({before})).supported


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
        own = _plan((ext, _PPTM_MIME, name, "FILE"), None, None)[0]
        assert own is not None and own.route is Route.PPTX, ext
    # The extension alone is indexable too, read as the same deck.
    assert _gate(("pptm", _OCTET, "deck.pptm", "FILE")).supported
    own = _plan(("pptm", _OCTET, "deck.pptm", "FILE"), None, None)[0]
    assert own is not None and own.route is Route.PPTX
