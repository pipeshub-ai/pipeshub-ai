import asyncio
import logging
import os

from app.sources.client.workday.workday import WorkdayClient
from app.sources.external.workday.workday import WorkdayDataSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _build_client_from_env() -> WorkdayClient:
    base_url = os.environ["WORKDAY_BASE_URL"]
    client_id = os.environ["WORKDAY_CLIENT_ID"]
    client_secret = os.environ["WORKDAY_CLIENT_SECRET"]
    refresh_token = os.environ["WORKDAY_REFRESH_TOKEN"]
    token_endpoint = os.environ["WORKDAY_TOKEN_ENDPOINT"]

    return WorkdayClient(
        base_url=base_url,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        token_endpoint=token_endpoint,
    )


async def _main() -> None:
    client = _build_client_from_env()
    data_source = WorkdayDataSource(client)

    response = await data_source.get_workers(limit=10)
    response.raise_for_status()
    logger.info(response.json())


if __name__ == "__main__":
    asyncio.run(_main())
