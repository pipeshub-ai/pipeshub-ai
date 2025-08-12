from dataclasses import asdict, dataclass

from github import Auth, Github  # type: ignore


@dataclass
class GithubConfig:
    """Configuration for Github"""
    base_url: str
    token: str

    def create_client(self) -> Github:
        return Github(base_url=self.base_url, auth=Auth.Token(self.token)) # type: ignore

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)
