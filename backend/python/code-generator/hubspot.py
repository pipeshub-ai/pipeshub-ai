# ruff: noqa

# NOTE — Development-only generator (HubSpot)

import sys
from pathlib import Path
from typing import List, Optional

from utils import process_connector

CONNECTOR = "hubspot"
# HubSpot provides OpenAPI specs for their APIs
# Using the CRM API spec as the main one
SPEC_URL = "https://api.hubspot.com/api-catalog-public/v1/apis/crm/v3/objects/contacts"


def _parse_args(argv: list[str]) -> Optional[List[str]]:
    """
    Usage:
        python hubspot.py
        python hubspot.py --only /crm/v3/objects/contacts /crm/v3/objects/companies
    """
    if len(argv) >= 2 and argv[1] == "--only":
        return argv[2:] or None
    return None


def main() -> None:
    prefixes = _parse_args(sys.argv)
    if prefixes:
        print(f"🔎 Path filter enabled for HubSpot: {prefixes}")
    base_dir = Path(__file__).parent
    process_connector(CONNECTOR, SPEC_URL, base_dir, path_prefixes=prefixes)
    print("\n🎉 Done (HubSpot)!")


if __name__ == "__main__":
    main()
