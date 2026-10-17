"""Debug a text fragment URL: `python -m app.utils.text_fragments.cli --help`.

Prints each stage of generation for a snippet so a bad link can be triaged.
With `--html`, also runs the reference matcher from the test suite (run from
`backend/python` so `tests.support` is importable).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.utils.text_fragments.generator import TextFragmentGenerator
from app.utils.text_fragments.models import SourceFormat
from app.utils.text_fragments.url import append_directive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="Base page URL")
    parser.add_argument("--format", choices=[f.value for f in SourceFormat], default=None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snippet", help="Snippet text")
    source.add_argument("--snippet-file", type=Path, help="File containing the snippet text")
    parser.add_argument("--html", type=Path, help="Page HTML to run the reference matcher against")
    args = parser.parse_args(argv)

    snippet = args.snippet if args.snippet is not None else args.snippet_file.read_text(encoding="utf-8")
    source_format = SourceFormat(args.format) if args.format else None

    generator = TextFragmentGenerator()
    blocks = generator.rendered_blocks(snippet, source_format)
    directive = generator.build_directive(snippet, source_format)
    url = append_directive(args.url, directive) if directive else args.url

    report = {
        "blocks": blocks,
        "directive": None
        if directive is None
        else {"start": directive.start, "end": directive.end},
        "url": url,
    }
    if args.html:
        from tests.support.text_fragment_matcher import highlight_for_url

        report["highlight"] = highlight_for_url(args.html.read_text(encoding="utf-8"), url)

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
