# ruff: noqa

# NOTE — Development-only generator (Dropbox)

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "dropbox"
# TODO: Dropbox OpenAPI spec URL (replace with actual spec if available)
SPEC_URL = "https://raw.githubusercontent.com/dropbox/dropbox-api-spec/main/openapi.yaml"


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python dropbox.py
        python dropbox.py --only /2/files/list_folder /2/files/get_metadata
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for Dropbox: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (Dropbox)!")


if __name__ == "__main__":
    main()
