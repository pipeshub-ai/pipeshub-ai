"""
Build from services example
"""

import sys, os, asyncio, logging

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../.."))
APP = os.path.join(ROOT, "backend", "python")
if ROOT not in sys.path: sys.path.insert(0, ROOT)
if APP not in sys.path: sys.path.insert(0, APP)

from backend.python.app.sources.client.zoom.zoom import ZoomClient
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource
from backend.python.app.config.configuration_service import ConfigurationService


async def main():
    logger = logging.getLogger("zoom")
    cs = ConfigurationService()
    client = await ZoomClient.build_from_services(logger, cs)
    rc = client.get_client()
    ds = ZoomDataSource(rc)

    print([m for m in dir(ds) if not m.startswith("_")][:100])


if __name__ == "__main__":
    asyncio.run(main())
