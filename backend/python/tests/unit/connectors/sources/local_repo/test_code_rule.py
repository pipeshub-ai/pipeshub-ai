"""local_repo's code rule judges a credential file by its repo path, not its bare name."""
import pytest

from app.connectors.sources.local_repo.code_rule import decide


@pytest.mark.parametrize("rel", [".kube/config", "deploy/.docker/config.json", "keys/id_rsa_work"])
def test_a_credential_file_is_refused_as_sensitive_without_reading_it(rel: str) -> None:
    def head() -> bytes:
        raise AssertionError("a credential file must not be read")

    keep, _role, reason = decide(rel, head)

    assert (keep, reason) == (False, "sensitive")
