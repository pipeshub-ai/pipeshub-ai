"""PX-3 driver: one structured file through the real parse path, with the loop probe and phase intervals.

    python -m tests.perf.parse_bench.px3_bench --file F [--entry e2e|parse] [--runs N] [--llm-delay-ms 20] [--out R.json]

Entries, labelled in every result:

* ``e2e`` (json, yaml, html, epub): the real ``Processor.process_*_document`` generator, with the sinks
  stubbed (graph read, record conversion and ``pipeline.apply`` are no-ops), so the GC of held blocks
  and the rest of the indexing process are inside the window. Claim: parse plus stubbed pipeline (R2-9).
* ``parse``: the parser alone (``JSONParser.parse``, ``YAMLParser.parse``, the HTML passes and
  ``parse_to_blocks``, the Excel loader and ``get_blocks_from_workbook``). Reported separately.

xlsx always uses the parser entry: the load runs in a thread (as ``ExcelParser.parse`` does, a stand-in
for the lane worker) and the model calls are replaced by ``--llm-delay-ms`` sleeps.

Per run it prints the probe summary (max single freeze, freezes over 50/100/250 ms, total time above
100 ms, p99 gap, callbacks per second) and the phase attribution (which parse phase each freeze
overlaps). It works unchanged on ``origin/main`` (phases that do not exist there are skipped), so the
same command gives the baseline and the branch figure.

The attribution phases are wrapped around the real functions; the wrapper costs two ``perf_counter``
reads. py-spy is used only for a confirmation run, with a bench-only opt-out of the non-dumpable mark.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import resource
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.perf.parse_bench.loop_probe import (
    LoopProbe,
    PhaseRing,
    attribute,
    instrument,
)

PHASES: list[tuple[str, str, str]] = [
    ("app.modules.parsers.json.json_parser", "decode_text", "json.decode_text"),
    ("app.modules.parsers.json.json_parser", "_decode_and_load", "json.decode_and_load"),
    ("app.modules.parsers.json.json_parser", "_loads_allowing_comments", "json.loads"),
    ("app.modules.parsers.json.json_parser.JSONParser", "parse_data", "json.walk"),
    ("app.modules.parsers.yaml.yaml_parser", "_decode_utf8", "yaml.decode"),
    ("app.modules.parsers.yaml.yaml_parser.YAMLParser", "_load_documents", "yaml.load_all"),
    ("app.modules.parsers.html_parser.selectolax_html_parser.SelectolaxHtmlParser", "clean_html", "html.clean_html"),
    ("app.modules.parsers.html_parser.selectolax_html_parser.SelectolaxHtmlParser", "replace_relative_image_urls", "html.replace_relative_image_urls"),
    ("app.modules.parsers.html_parser.selectolax_html_parser.SelectolaxHtmlParser", "extract_and_replace_images", "html.extract_images"),
    ("app.modules.parsers.html_parser.selectolax_html_parser.SelectolaxHtmlParser", "parse_to_blocks", "html.parse_to_blocks"),
    ("app.modules.parsers.epub.epub_reader", "read_epub", "epub.read_epub"),
    ("app.modules.parsers.excel.excel_parser.ExcelParser", "load_workbook_from_binary", "xlsx.load"),
    ("app.modules.parsers.excel.excel_parser.ExcelParser", "find_tables", "xlsx.find_tables"),
    ("app.modules.parsers.excel.excel_parser.ExcelParser", "process_sheet_with_summaries", "xlsx.process_sheet"),
    ("app.modules.parsers.excel.excel_parser.ExcelParser", "get_blocks_from_workbook", "xlsx.blocks"),
    ("app.modules.parsers.excel.workbook_model.SheetModel", "value_positions", "xlsx.value_positions"),
]


def _resolve(path: str) -> Any:  # noqa: ANN401
    import importlib

    parts = path.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for name in parts[i:]:
            obj = getattr(obj, name)
        return obj
    raise ImportError(path)


def install_phases(ring: PhaseRing) -> tuple[list[Any], list[str]]:
    undo, present = [], []
    for target_path, attr, label in PHASES:
        try:
            target = _resolve(target_path)
            getattr(target, attr)
        except (ImportError, AttributeError):
            continue  # the phase does not exist on this checkout
        undo.append(instrument(ring, target, attr, label))
        present.append(label)
    return undo, present


def _processor() -> Any:  # noqa: ANN401
    from app.events.processor import Processor

    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        proc = Processor(
            logger=logging.getLogger("px3-bench"), config_service=AsyncMock(), indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(), parsers=_parsers(), document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )

    class StubPipeline:
        async def apply(self, _ctx: object) -> None:  # the sink is stubbed
            return None

    proc._convert_record = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    proc._create_transform_context = MagicMock()  # type: ignore[method-assign]
    proc._pipeline = lambda: StubPipeline()  # type: ignore[method-assign]
    proc._mark_record = AsyncMock()  # type: ignore[method-assign]
    return proc


def _parsers() -> dict[str, Any]:
    from app.config.constants.arangodb import ExtensionTypes
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )
    from app.modules.parsers.json.json_parser import JSONParser
    from app.modules.parsers.yaml.yaml_parser import YAMLParser

    class NoImages:  # no network: every remote image is left unfetched
        async def urls_to_base64(self, urls: list[str], allow_remote_fetch: bool = True) -> list[None]:
            return [None] * len(urls)

    return {
        ExtensionTypes.PNG.value: NoImages(),
        ExtensionTypes.JSON.value: JSONParser(),
        ExtensionTypes.YAML.value: YAMLParser(),
        ExtensionTypes.HTML.value: SelectolaxHtmlParser(),
    }


async def _drain(gen: Any) -> int:  # noqa: ANN401
    n = 0
    async for _ in gen:
        n += 1
    return n


async def _e2e(path: Path, data: bytes) -> str:
    proc = _processor()
    ext = path.suffix.lstrip(".").lower()
    if ext == "json":
        await _drain(proc.process_structured_document("r", "rid", data, "vr", "json"))
    elif ext in ("yaml", "yml"):
        await _drain(proc.process_structured_document("r", "rid", data, "vr", "yaml"))
    elif ext == "html":
        await _drain(proc.process_html_document("r", "rid", "1", "src", "org", data, "vr"))
    elif ext == "epub":
        await _drain(proc.process_epub_document("r", "rid", "1", "src", "org", data, "vr"))
    else:
        raise SystemExit(f"no e2e entry for .{ext}")
    return "e2e (sinks stubbed)"


async def _parse_only(path: Path, data: bytes, llm_delay_s: float) -> str:
    ext = path.suffix.lstrip(".").lower()
    if ext == "json":
        from app.modules.parsers.json.json_parser import JSONParser

        await JSONParser().parse(data, "r")
    elif ext in ("yaml", "yml"):
        from app.modules.parsers.yaml.yaml_parser import YAMLParser

        await YAMLParser().parse(data, "r")
    elif ext in ("html", "epub"):
        from app.events import processor as pm
        from app.modules.parsers.html_parser.selectolax_html_parser import (
            SelectolaxHtmlParser,
        )

        if ext == "epub":
            from app.modules.parsers.epub.epub_reader import read_epub

            html = (await asyncio.to_thread(read_epub, data)).to_html()
        else:
            html = data.decode("utf-8")
        parser = SelectolaxHtmlParser()
        hop = getattr(pm, "_html_pass", None)  # absent on main: the passes run on the loop there
        if hop is not None:
            html = await hop(parser, "clean_html", html)
            html = await hop(parser, "replace_relative_image_urls", html)
        else:
            html = parser.clean_html(html)
            html = parser.replace_relative_image_urls(html)
        html, _images = await pm.offload_if_large(parser.extract_and_replace_images, html)
        await parser.parse_to_blocks(html, caption_map=None, name="r")
    elif ext == "xlsx":
        await _xlsx(data, llm_delay_s)
    else:
        raise SystemExit(f"unknown extension .{ext}")
    return "parse only"


async def _xlsx(data: bytes, llm_delay_s: float) -> None:
    from app.modules.parsers.excel import excel_parser
    from app.modules.parsers.excel.excel_parser import ExcelParser
    from app.modules.parsers.excel.prompt_template import ExcelHeaderDetection

    parser = ExcelParser(logging.getLogger("px3-bench"), MagicMock())
    await asyncio.to_thread(parser.load_workbook_from_binary, data)

    async def detect(_rows: object, _llm: object) -> ExcelHeaderDetection:
        await asyncio.sleep(llm_delay_s)
        return ExcelHeaderDetection(has_headers=True, num_header_rows=1, confidence="high", reasoning="r")

    async def summary(_table: object) -> str:
        await asyncio.sleep(llm_delay_s)
        return "S"

    async def describe(_llm: object, rows: list[dict[str, Any]], *_a: object, **_k: object) -> list[str]:
        await asyncio.sleep(llm_delay_s)
        return ["row"] * len(rows)

    parser.detect_excel_headers_with_llm = detect  # type: ignore[method-assign,assignment]
    parser.get_table_summary = summary  # type: ignore[method-assign,assignment]
    with patch.object(excel_parser, "describe_row_dicts", describe):
        await parser.get_blocks_from_workbook(object())  # type: ignore[arg-type]


async def one_run(path: Path, entry: str, llm_delay_s: float) -> dict[str, Any]:
    data = path.read_bytes()
    ring = PhaseRing()
    undo, present = install_phases(ring)
    gc.collect()
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        async with LoopProbe() as probe:
            t0 = time.perf_counter()
            if entry == "e2e" and path.suffix.lower() != ".xlsx":
                label = await _e2e(path, data)
            else:
                label = await _parse_only(path, data, llm_delay_s)
            seconds = time.perf_counter() - t0
    finally:
        for u in reversed(undo):
            u()
    return {
        "file": path.name, "bytes": len(data), "entry": label, "parse_wall_s": round(seconds, 3),
        "files_per_s": round(1 / seconds, 4) if seconds else None,
        "maxrss_delta_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss0,
        "probe": probe.summary(),
        "attribution_over_50ms": attribute(probe.freezes(), ring, 0.050),
        "phases_instrumented": present, "ring_overwritten": ring.overwritten,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--entry", choices=["e2e", "parse"], default="e2e")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=1, help="untimed runs first (lazy imports); 0 for none")
    ap.add_argument("--llm-delay-ms", type=float, default=20.0)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.disable(logging.WARNING)  # the parsers log at INFO per table; keep the probe window quiet
    for _ in range(args.warmup):  # the first run pays the lazy imports; it is not reported
        asyncio.run(one_run(args.file, args.entry, args.llm_delay_ms / 1000))
    runs = [asyncio.run(one_run(args.file, args.entry, args.llm_delay_ms / 1000)) for _ in range(args.runs)]
    text = json.dumps({"runs": runs}, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
