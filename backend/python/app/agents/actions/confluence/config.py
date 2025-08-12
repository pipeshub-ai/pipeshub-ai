from dataclasses import asdict, dataclass

from atlassian import Confluence  # type: ignore


class ConfluenceConfigBase:
    def create_client(self) -> Confluence: # type: ignore
        raise NotImplementedError # pragma: no cover

@dataclass
class ConfluenceUsernamePasswordConfig(ConfluenceConfigBase):
    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> Confluence:
        return Confluence(url=self.base_url, username=self.username, password=self.password, ssl=self.ssl)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ConfluenceTokenConfig(ConfluenceConfigBase):
    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> Confluence:
        return Confluence(url=self.base_url, token=self.token, ssl=self.ssl)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ConfluenceApiKeyConfig(ConfluenceConfigBase):
    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> Confluence:
        return Confluence(url=self.base_url, email=self.email, api_key=self.api_key, ssl=self.ssl)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)
