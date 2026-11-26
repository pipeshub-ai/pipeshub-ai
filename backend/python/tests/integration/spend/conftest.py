import pytest

from tests.support.spend_world import fleet, sim_clock  # noqa: F401 - fixtures


class _NoHubTokenizer:
    @staticmethod
    def from_pretrained(*_args: object, **_kwargs: object) -> object:
        raise OSError("no Hugging Face download in the spend suites")


@pytest.fixture(autouse=True)
def _no_cloud_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building a provider's client must not reach past the host: botocore asks the cloud metadata address
    (169.254.169.254) for credentials when none are configured, and the Mistral embeddings download a tokenizer
    from Hugging Face (without it they count characters, which the wire contract does not look at). The network
    guard refuses both."""
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr("langchain_mistralai.embeddings.Tokenizer", _NoHubTokenizer, raising=False)
