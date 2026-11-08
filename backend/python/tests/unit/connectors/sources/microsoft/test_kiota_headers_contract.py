"""The generated Microsoft wrappers rely on how kiota-abstractions treats an empty headers collection."""
from kiota_abstractions.base_request_configuration import RequestConfiguration


def test_a_request_configuration_keeps_a_headers_collection_the_wrappers_can_add_to() -> None:
    """The wrappers in app/sources/external/microsoft/ write `if not config.headers: config.headers = {}` and then call
    `config.headers.add(...)`. That only works while an empty collection is truthy; when it is not, the collection is
    replaced by a dict and every call that sets a header (a delta page size, ConsistencyLevel) fails."""
    config = RequestConfiguration()
    if not config.headers:
        config.headers = {}
    config.headers.add("Prefer", "odata.maxpagesize=200")
    assert config.headers.get("Prefer") == {"odata.maxpagesize=200"}
