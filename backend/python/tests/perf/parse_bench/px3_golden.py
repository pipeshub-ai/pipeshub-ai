"""Golden for the structured-format parsers: what PX-3 must leave byte-identical.

A small, fixed, in-code corpus (JSON, YAML, HTML, Excel with the model calls stubbed) is parsed and
each result reduced to a canonical form: block and group ids renamed by order of first appearance
(uuid4 ids differ per run, ``golden.canonicalize``), keys sorted, then hashed, together with the
metadata, ``document_count``, structural-fallback flag and error code. ``run()`` returns the dict;
``python -m tests.perf.parse_bench.px3_golden --out FILE`` writes it (the app's loggers write to
stdout, so prefer ``--out`` to reading stdout).

The reference file is made on ``origin/main``, never on the branch under test::

    git worktree add --detach $TMPDIR/main origin/main
    cp <branch>/backend/python/tests/perf/parse_bench/px3_golden.py \\
       $TMPDIR/main/backend/python/tests/perf/parse_bench/
    cp -r <branch>/backend/python/tests/support/excel_workbooks.py $TMPDIR/main/backend/python/tests/support/
    (cd $TMPDIR/main/backend/python && PYTHONPATH=. python -m tests.perf.parse_bench.px3_golden --out golden.json)

Run twice on main first to prove it is stable (it is compared across ``PYTHONHASHSEED`` values by
``tests/unit/modules/parsers/test_px3_golden.py``).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from tests.perf.parse_bench import golden

JSON_CASES: dict[str, bytes] = {
    "object": json.dumps({"name": "Ada", "age": 36, "tags": ["a", "b"], "nested": {"k": [1, 2, {"z": None}]}}).encode(),
    "array_of_objects": json.dumps([{"id": i, "name": f"n{i}", "score": i / 4} for i in range(40)]).encode(),
    "array_of_ints": json.dumps(list(range(500))).encode(),
    "nested_lists": json.dumps([[[[i]]] for i in range(20)]).encode(),
    "long_text": json.dumps({"body": "word " * 600, "title": "t"}).encode(),
    "unicode": json.dumps({"k": "héllo wörld ✓", "emoji": "😀"}, ensure_ascii=False).encode(),
    "bom_utf8": b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode(),
    "utf16_bom": b"\xff\xfe" + json.dumps({"a": "b"}).encode("utf-16-le"),
    "latin1": b'{"k": "caf\xe9"}',
    "comments": b'// licence\n{"a": 1, /* inline */ "b": [1, 2]}\n',
    "empty_object": b"{}",
    "empty_array": b"[]",
    "blank": b"  \n\t ",
    "not_json": b"{not json",
}

YAML_CASES: dict[str, bytes] = {
    "mapping": b"name: Ada\nage: 36\ntags: [a, b]\nnested:\n  k: [1, 2]\n",
    "sequence_of_maps": ("".join(f"- id: {i}\n  name: n{i}\n" for i in range(30))).encode(),
    "multi_document": b"a: 1\n---\nb: 2\n---\nc: [3, 4]\n",
    "custom_tags": b"x: !Ref Foo\ny: !Sub 'a-${B}'\nz: !reference [a, b]\n",
    "comments_only": b"# nothing here\n# at all\n",
    "template_fallback": b"{{ if .Values.x }}\nkey: {{ .Values.y }}\n{{ end }}\n",
    "syntax_error_fallback": b"key: [unclosed\nother: value\n",
    "unicode": "k: héllo ✓\n".encode(),
    "bad_utf8": b"a: \xff\xfe\n",
    "blank": b"   \n",
    "anchors": b"base: &b {x: 1}\nderived:\n  <<: *b\n  y: 2\n",
}

HTML_CASES: dict[str, str] = {
    "page": "<html><head><title>t</title><style>p{}</style></head><body><nav>menu</nav><h1>Title</h1>"
    "<p>Hello <b>world</b></p><ul><li>a</li><li>b</li></ul><footer>f</footer><script>x()</script></body></html>",
    "table": "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>",
    "images": '<html><head><base href="https://example.com/dir/"></head><body><img src="a.png" alt="x">'
    '<p>text</p><img src="/b.png"></body></html>',
    "unicode": "<p>héllo wörld ✓</p>",
    "empty_body": "<html><body></body></html>",
}

EXCEL_CASES: dict[str, tuple[list[list[Any]], bool]] = {
    "headers": ([["name", "n"], ["a", 1], ["b", 2], ["c", 3]], True),
    "no_headers_generated": ([["a", 1], ["b", 2], ["c", 3]], False),
    "two_tables": ([["h1", "h2"], ["a", 1], [], [], ["k", "v"], ["x", "y"]], True),
    "blanks_inside": ([["h1", "h2", "h3"], ["a", None, 3], [None, "b", None], ["c", "d", "e"]], True),
}


def _entry(container: Any, metadata: dict[str, Any] | None) -> dict[str, Any]:  # noqa: ANN401
    payload = json.loads(container.model_dump_json())
    summary = golden.summarize(payload, len(payload["blocks"]), len(payload["block_groups"]))
    summary["metadata"] = metadata
    return summary


def _error(exc: BaseException) -> dict[str, Any]:
    return {"error": f"{type(exc).__name__}:{getattr(exc, 'code', None)}"}


async def _structured(parser: Any, content: bytes) -> dict[str, Any]:  # noqa: ANN401
    try:
        result = await parser.parse(content, "r")
    except Exception as e:
        return _error(e)
    return _entry(result.block_container, result.metadata)


async def _html(html: str) -> dict[str, Any]:
    from app.modules.parsers.html_parser.selectolax_html_parser import (
        SelectolaxHtmlParser,
    )

    parser = SelectolaxHtmlParser()
    try:
        text = parser.clean_html(html)
        text = parser.replace_relative_image_urls(text)
        text, images = parser.extract_and_replace_images(text)
        container = await parser.parse_to_blocks(text, caption_map=None, name="r")
    except Exception as e:
        return _error(e)
    return _entry(container, {"images": images})


async def _excel(grid: list[list[Any]], has_headers: bool) -> dict[str, Any]:
    from app.modules.parsers.excel import excel_parser
    from app.modules.parsers.excel.prompt_template import ExcelHeaderDetection
    from tests.support.excel_workbooks import column_letters, load, workbook_zip

    def cell(ref: str, value: Any) -> str:  # noqa: ANN401
        if value is None:
            return ""
        if isinstance(value, str):
            return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'

    rows = "".join(
        f'<row r="{r}">' + "".join(cell(f"{column_letters(c)}{r}", v) for c, v in enumerate(values, 1)) + "</row>"
        for r, values in enumerate(grid, 1)
        if values
    )
    parser = load(workbook_zip(f"<sheetData>{rows}</sheetData>"))
    parser.logger = logging.getLogger("px3-golden")  # type: ignore[assignment]
    parser.detect_excel_headers_with_llm = AsyncMock(  # type: ignore[method-assign]
        return_value=ExcelHeaderDetection(
            has_headers=has_headers, num_header_rows=1 if has_headers else 0, confidence="high", reasoning="r"
        )
    )
    parser.generate_excel_headers_with_llm = AsyncMock(return_value=["C1", "C2"])  # type: ignore[method-assign]
    parser.get_table_summary = AsyncMock(return_value="S")  # type: ignore[method-assign]

    async def describe(_llm: object, row_dicts: list[dict[str, Any]], *_a: object, **_k: object) -> list[str]:
        return [json.dumps([[repr(k), v] for k, v in r.items()], default=str) for r in row_dicts]

    saved = excel_parser.describe_row_dicts
    excel_parser.describe_row_dicts = describe  # type: ignore[assignment]
    try:
        container = await parser.get_blocks_from_workbook(object())  # type: ignore[arg-type]
    except Exception as e:
        return _error(e)
    finally:
        excel_parser.describe_row_dicts = saved  # type: ignore[assignment]
    return _entry(container, None)


async def _run() -> dict[str, dict[str, Any]]:
    from app.modules.parsers.json.json_parser import JSONParser
    from app.modules.parsers.yaml.yaml_parser import YAMLParser

    out: dict[str, dict[str, Any]] = {}
    for name, content in JSON_CASES.items():
        out[f"json/{name}"] = await _structured(JSONParser(), content)
    for name, content in YAML_CASES.items():
        out[f"yaml/{name}"] = await _structured(YAMLParser(), content)
    for name, html in HTML_CASES.items():
        out[f"html/{name}"] = await _html(html)
    for name, (grid, headers) in EXCEL_CASES.items():
        out[f"xlsx/{name}"] = await _excel(grid, headers)
    return out


def run() -> dict[str, dict[str, Any]]:
    return asyncio.run(_run())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    result = run()
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
