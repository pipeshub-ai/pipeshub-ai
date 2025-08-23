# ruff: noqa

# NOTE — Development-only generator (Confluence)

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "confluence"
SPEC_URL = "https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json?_v=1.8061.0-0.1325.0"


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python confluence.py
        python confluence.py --only /wiki/rest/api/content /wiki/rest/api/space
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for Confluence: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (Confluence)!")


if __name__ == "__main__":
    main()
