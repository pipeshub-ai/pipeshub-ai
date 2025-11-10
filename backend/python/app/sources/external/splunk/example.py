"""Example usage of SplunkClient and SplunkDataSource."""

from app.sources.client.splunk.splunk import (
    SplunkClient,
    SplunkTokenConfig,
)
from app.sources.external.splunk_datasource import SplunkDataSource


def main() -> None:
    # build authentication config
    config = SplunkTokenConfig(
        host="your-splunk-host",
        token="your-splunk-token",
    )

    # build SplunkClient using config
    client = SplunkClient.build_with_config(config)

    # pass client to datasource
    datasource = SplunkDataSource(client)

    # run a query
    query = "search index=_internal | head 5"
    job_id = datasource.search(query)

    print("Splunk Search Job ID:", job_id)


if __name__ == "__main__":
    main()
