"""The identity a run starts with is the parser that actually ran.

The whole mime x extension grid goes through the pure key and the real dispatch plan (fast). ``on_event`` itself is driven for
one representative pair per distinct route and for the edge pairs (a json with a csv extension, a path-decided run, an
extension the client fills in), on both pipelines: handlers are stubs that record the identity visible when they are called."""
from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import file_types
from app.events.events import DedupDecision, EventProcessor
from app.events.processor import Processor
from app.modules.parsers.file_prep import prepared_labels
from app.modules.pipeline import parser_identity as pi
from tests.support import new_type_samples as samples
from tests.unit.events.test_events import (
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
)

MIMES = [m for m in sorted(file_types.MIME_FORMATS) if m] + ["text/plain", "application/octet-stream", "unknown"]
EXTS = ["md", "txt", "csv", "json", "docm", "dotx", "potx", "pptm", "xlsm", "log", "cfg", "docx", "pdf", "py", "yaml", "html", "rtf", "odt", "unknown", "msg", "tsv"]
SAMPLES = {"docm": samples.make_docm("s"), "dotx": samples.make_dotx("s"), "potx": samples.make_potx("s"),
           "pptm": samples.make_pptm("s"), "xlsm": samples.make_xlsm("s")}


async def _run(ext, mime, name, rtype, service: bool, file_path=None):  # noqa: ANN202
    ep, _, processor, gp = _make_event_processor()
    ep.parsing_client = MagicMock(circuit_open=False)
    gp.get_document.return_value = {"_key": "rec-1", "recordType": rtype}
    seen: list[tuple[str, str | None]] = []

    def stub(n):  # noqa: ANN202
        def call(*_a, **_k):  # noqa: ANN202
            seen.append((n, pi.run_identity()))
            return _mock_processor_gen()
        return call

    for m in [m for m in dir(Processor) if m.startswith("process_")] + ["_mark_record"]:
        if m != "_mark_record":
            setattr(processor, m, MagicMock(side_effect=stub(m)))
    processor._mark_record = AsyncMock()
    sent: list[tuple[str, str, str | None]] = []

    async def orchestrate(**kw):  # noqa: ANN202
        sent.append((kw["mime_type"], kw["extension"], pi.run_identity()))
        if False:
            yield None

    content = SAMPLES.get(ext, b"hello world\n")
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_dispatch_pdf_binary", side_effect=stub("_dispatch_pdf_binary")), \
         patch.object(ep, "_use_service_pipeline", return_value=service), \
         patch.object(ep, "_orchestrate_via_services", new=orchestrate):
        try:
            event = _make_event_payload(extension=ext, mime_type=mime, buffer=content, record_name=name)
            if file_path:
                event["payload"]["filePath"] = file_path
            await _drain(ep.on_event(event))
        except Exception as exc:  # a refusal (unsupported, password) is not a run
            return seen, sent, repr(exc)
    return seen, sent, None


def _route_of(mime, ext, name, rtype, file_path=None):  # noqa: ANN202
    dispatch, _code_ext, as_code = EventProcessor._read_plan(rtype, mime, ext, file_path, name, None)
    return dispatch, as_code


def test_the_in_process_key_is_the_route_of_the_dispatch_for_every_label_pair() -> None:
    checked = 0
    for mime, ext, rtype in itertools.product(MIMES, EXTS, ["FILE", "CODE_FILE"]):
        name = f"Sample.{ext}" if ext != "unknown" else "Sample"
        key = pi.parser_key(mime, ext, name, rtype, service=False)
        if key is None:
            continue
        dispatch, _ = _route_of(mime, ext, name, rtype)
        assert dispatch is not None and key.split("@")[0] == dispatch.route.value, (mime, ext, rtype, key)
        checked += 1
    assert checked > 1000


def test_the_service_key_is_the_format_the_service_is_sent_for_every_label_pair() -> None:
    checked = 0
    for mime, ext, rtype in itertools.product(MIMES, EXTS, ["FILE", "CODE_FILE"]):
        name = f"Sample.{ext}" if ext != "unknown" else "Sample"
        key = pi.parser_key(mime, ext, name, rtype, service=True)
        if key is None:
            continue
        dispatch, _ = _route_of(mime, ext, name, rtype)
        sent_ext, sent_mime = prepared_labels(dispatch.file_type if dispatch else None, ext, mime)
        real = file_types.parse_format(sent_mime, file_types.service_extension(sent_mime, sent_ext, name))
        assert key.split("@")[0] == real, (mime, ext, rtype, key, real)
        checked += 1
    assert checked > 1000


def _one_pair_per_route() -> list[tuple[str, str, str]]:
    seen: dict[str, tuple[str, str, str]] = {}
    for mime, ext in itertools.product(MIMES, EXTS):
        name = f"Sample.{ext}" if ext != "unknown" else "Sample"
        key = pi.parser_key(mime, ext, name, "FILE", service=False)
        if key is not None:
            seen.setdefault(key.split("@")[0], (mime, ext, name))
    return list(seen.values())


EDGE_PAIRS = [
    ("application/json", "csv", "Sample.csv", None),
    ("application/vnd.ms-powerpoint.presentation.macroEnabled.12", "unknown", "Sample", None),
    ("application/vnd.ms-powerpoint.presentation.macroEnabled.12", "pptm", "Sample.pptm", None),
    ("application/octet-stream", "unknown", "Report.docx", "x/app.log"),
    ("text/plain", "unknown", "notes", "src/x.py"),
]


@pytest.mark.asyncio
async def test_the_in_process_run_identity_matches_the_dispatch_that_ran() -> None:
    cases = [(m, e, n, None) for m, e, n in _one_pair_per_route()] + EDGE_PAIRS
    assert len(cases) > 15
    for mime, ext, name, path in cases:
        seen, _sent, _err = await _run(ext, mime, name, "FILE", False, path)
        if not seen:
            continue
        _handler, ident = seen[-1]
        dispatch, _ = _route_of(mime, ext, name, "FILE", path)
        key = pi.parser_key(mime, ext, name, "FILE", service=False, file_path=path)
        if ident is None:
            continue  # not tracked
        if ident == f"inproc:{pi.PATH_UNTRACKED_KEY}":
            assert path is not None and key is None  # the path chose a parser that is not tracked
            continue
        assert ident.startswith("inproc:") and dispatch is not None, (mime, ext, name, ident)
        assert ident.split(":", 1)[1] == key and key.split("@")[0] == dispatch.route.value, (mime, ext, name, ident)
    # a path-decided run records the key of the parser the path chose, not the name's
    seen, _sent, _err = await _run("unknown", "application/octet-stream", "Report.docx", "FILE", False, "x/app.log")
    assert seen and seen[-1][1] == "inproc:plain_text@1"


@pytest.mark.asyncio
async def test_the_service_run_identity_matches_the_format_the_service_resolves() -> None:
    cases = [(m, e, n, None) for m, e, n in _one_pair_per_route()] + EDGE_PAIRS
    checked = 0
    for mime, ext, name, path in cases:
        _seen, sent, _err = await _run(ext, mime, name, "FILE", True, path)
        for s_mime, s_ext, ident in sent:
            checked += 1
            if ident is None:
                continue
            if ident == f"svc:{pi.PATH_UNTRACKED_KEY}":
                assert path is not None  # the path chose a parser that is not tracked
                continue
            real = file_types.parse_format(s_mime, file_types.service_extension(s_mime, s_ext, name))
            assert ident.startswith("svc:") and ident.split(":", 1)[1].split("@")[0] == real, (mime, ext, name, ident, real)
    assert checked > 15
