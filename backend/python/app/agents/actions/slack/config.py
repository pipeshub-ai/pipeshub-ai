from dataclasses import dataclass

from slack_sdk import WebClient  # type: ignore


class SlackConfigBase:
    def create_client(self) -> WebClient: # type: ignore
        raise NotImplementedError

@dataclass
class SlackTokenConfig(SlackConfigBase):
    token: str
    def create_client(self) -> WebClient:
        return WebClient(token=self.token)
