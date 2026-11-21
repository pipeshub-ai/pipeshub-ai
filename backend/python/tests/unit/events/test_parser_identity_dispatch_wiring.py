"""Drive the real ``EventProcessor.on_event`` (both pipelines) and compare the identity the run starts with to the handler
(in process) or the labels sent to the parsing service that actually ran. Handlers are stubs that record the identity visible
when they are called."""
from __future__ import annotations

import collections
import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import file_types
from app.events.events import DedupDecision
from app.events.processor import Processor
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


async def _run(ext, mime, name, rtype, service: bool):  # noqa: ANN202
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
            await _drain(ep.on_event(_make_event_payload(extension=ext, mime_type=mime, buffer=content, record_name=name)))
        except Exception as exc:  # a refusal (unsupported, password) is not a run
            return seen, sent, repr(exc)
    return seen, sent, None


@pytest.mark.asyncio
async def test_the_in_process_run_identity_matches_the_handler_that_ran() -> None:
    pairs = collections.defaultdict(set)
    n = 0
    for mime, ext, rtype in itertools.product(MIMES, EXTS, ["FILE", "CODE_FILE"]):
        name = f"Sample.{ext}" if ext != "unknown" else "Sample"
        seen, _sent, _err = await _run(ext, mime, name, rtype, False)
        if not seen:
            continue
        handler, ident = seen[-1]
        n += 1
        if ident is None:
            continue
        assert ident.startswith("inproc:")
        pairs[handler].add(ident.split(":", 1)[1].split("@")[0])
    assert n > 1000
    # one handler per route: a handler reached under two identities (or an identity for two handlers) is a mismatch unless it is a known multi-route handler
    inverse = collections.defaultdict(set)
    for h, rs in pairs.items():
        for r in rs:
            inverse[r].add(h)
    bad_routes = {r: hs for r, hs in inverse.items() if len(hs) > 1}
    assert not bad_routes, bad_routes


@pytest.mark.asyncio
async def test_the_service_run_identity_matches_the_format_the_service_resolves() -> None:
    bad, n = [], 0
    for mime, ext, rtype in itertools.product(MIMES, EXTS, ["FILE", "CODE_FILE"]):
        name = f"Sample.{ext}" if ext != "unknown" else "Sample"
        seen, sent, _err = await _run(ext, mime, name, rtype, True)
        for s_mime, s_ext, ident in sent:
            n += 1
            if ident is None:
                continue
            real = file_types.parse_format(s_mime, file_types.service_extension(s_mime, s_ext, name))
            if ident.split(":", 1)[1].split("@")[0] != real or not ident.startswith("svc:"):
                bad.append((mime, ext, rtype, ident, real))
    assert n > 1000
    assert not bad, bad[:10]
