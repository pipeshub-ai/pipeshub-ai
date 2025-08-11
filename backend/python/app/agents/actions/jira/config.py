from dataclasses import asdict, dataclass

from jira import JIRA  # type: ignore


class JiraConfigBase:
    def create_client(self) -> JIRA: # type: ignore
        raise NotImplementedError

@dataclass
class JiraUsernamePasswordConfig(JiraConfigBase):
    base_url: str
    username: str
    password: str

    def create_client(self) -> JIRA:
        return JIRA(server=self.base_url, basic_auth=(self.username, self.password))

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraTokenConfig(JiraConfigBase):
    base_url: str
    token: str

    def create_client(self) -> JIRA:
        return JIRA(server=self.base_url, token_auth=self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraApiKeyConfig(JiraConfigBase):
    base_url: str
    email: str
    api_key: str

    def create_client(self) -> JIRA:
        return JIRA(server=self.base_url, basic_auth=(self.email, self.api_key))

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)
