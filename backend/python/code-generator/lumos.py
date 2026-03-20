# ruff: noqa

# NOTE — Development-only generator (Lumos)
# Generates LumosDataSource from Lumos OpenAPI spec.
# Authentication: Bearer token (API key passed as Bearer token).
# Base URL: https://api.lumos.com
# No official SDK — uses HTTPClient.

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "lumos"
# download openapi spec from https://api.lumos.com/openapi.json 
# Use the local OpenAPI spec file shipped in the repo
SPEC_URL = str(Path(__file__).resolve().parent.parent / "lumos-openapi.json")


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python lumos.py
        python lumos.py --only /apps /users /accounts
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for Lumos: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (Lumos)!")


if __name__ == "__main__":
    main()
