import asyncio
import os

from app.sources.client.slack.slack import SlackClient, SlackTokenConfig
from app.sources.external.slack.slack import SlackDataSource


def main() -> None:
    token = os.getenv("SLACK_TOKEN")
    if not token:
        raise Exception("SLACK_TOKEN is not set")


    slack_client : SlackClient = SlackClient.build_with_config(
        SlackTokenConfig(
            token=token,
        ),
    )
    print(slack_client)
    slack_data_source = SlackDataSource(slack_client)
    print(slack_data_source)
    print(asyncio.run(slack_data_source.conversations_list()))
    print(asyncio.run(slack_data_source.conversations_info(channel="xx")))
    print(asyncio.run(slack_data_source.conversations_list()))
    print(asyncio.run(slack_data_source.conversations_members(channel="xx")))
    print(asyncio.run(slack_data_source.conversations_info(channel="xx")))
    print(asyncio.run(slack_data_source.conversations_history(channel="xx")))
    print(asyncio.run(slack_data_source.conversations_join(channel="xx")))
    print(asyncio.run(slack_data_source.conversations_create(name="test")))
    print(asyncio.run(slack_data_source.conversations_invite(channel="xx", users=['xx', 'xx', 'xx', 'xx', 'xx', 'xx'])))

if __name__ == "__main__":
    main()
