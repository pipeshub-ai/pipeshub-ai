"""Unit tests for Atlassian Cloud site URL matching helpers."""

import pytest

from app.sources.external.common.atlassian import (
    AtlassianCloudResource,
    atlassian_site_hostname,
    match_atlassian_cloud_resource,
)


def test_atlassian_site_hostname():
    assert atlassian_site_hostname("https://Foo.atlassian.net/") == "foo.atlassian.net"
    assert atlassian_site_hostname("bar.atlassian.net") == "bar.atlassian.net"
    assert atlassian_site_hostname("") == ""


def test_match_empty_resources():
    with pytest.raises(ValueError, match="No Atlassian Cloud sites"):
        match_atlassian_cloud_resource([], "https://x.atlassian.net", product="Jira")


def test_match_empty_base_url():
    a = AtlassianCloudResource(id="1", name="A", url="https://a.atlassian.net", scopes=[])
    with pytest.raises(ValueError, match="baseUrl"):
        match_atlassian_cloud_resource([a], "", product="Jira")


def test_match_success():
    a = AtlassianCloudResource(id="one", name="A", url="https://one.atlassian.net", scopes=[])
    b = AtlassianCloudResource(id="two", name="B", url="https://two.atlassian.net", scopes=[])
    picked = match_atlassian_cloud_resource([a, b], "https://two.atlassian.net/", product="Jira")
    assert picked.id == "two"


def test_match_no_access():
    a = AtlassianCloudResource(id="1", name="A", url="https://a.atlassian.net", scopes=[])
    with pytest.raises(ValueError, match="no access"):
        match_atlassian_cloud_resource([a], "https://missing.atlassian.net", product="Confluence")
