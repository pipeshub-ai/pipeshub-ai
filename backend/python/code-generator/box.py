# ruff: noqa

# NOTE — Development-only generator (Box)

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "box"
SPEC_URL = "https://raw.githubusercontent.com/box/box-openapi/main/openapi.json"


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python box.py
        python box.py --only /users /folders
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for Box: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (Box)!")


if __name__ == "__main__":
    main()
