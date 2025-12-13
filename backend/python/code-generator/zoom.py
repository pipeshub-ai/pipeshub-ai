# ruff: noqa

# NOTE — Development-only generator (Zoom)

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "zoom"
# Zoom OpenAPI spec URL - update if available
SPEC_URL = "https://raw.githubusercontent.com/zoom/developer-api/main/openapi.json"


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python zoom.py
        python zoom.py --only /users /meetings
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for Zoom: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (Zoom)!")


if __name__ == "__main__":
    main()
