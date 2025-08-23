# ruff: noqa

# NOTE — Development-only generator (Jira)

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "jira"
SPEC_URL = "https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json"


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python jira.py
        python jira.py --only /rest/api/3/issue /rest/api/3/project
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for Jira: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (Jira)!")


if __name__ == "__main__":
    main()
