"""Unit tests for Atlassian Cloud site URL matching helpers."""

import pytest

from app.sources.external.common.atlassian import (
    ALLOWED_SITE_HOSTS_ENV,
    AtlassianCloudResource,
    atlassian_site_hostname,
    match_atlassian_cloud_resource,
    require_atlassian_cloud_site_url,
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


@pytest.mark.parametrize("url", [
    "https://acme.atlassian.net",
    "https://acme.atlassian.net/",
    "https://ACME.atlassian.net",
    "https://acme.jira.com",
    "https://acme.atlassian.net:443",
])
def test_a_cloud_site_url_is_accepted(url):
    require_atlassian_cloud_site_url(url)


@pytest.mark.parametrize("url", [
    "http://acme.atlassian.net",                     # the token would travel in clear
    "https://169.254.169.254",                       # cloud metadata
    "https://localhost",
    "https://10.0.0.5",
    "https://jira.internal.example.com",
    "https://atlassian.net",                         # no site
    "https://acme.atlassian.net.evil.com",
    "https://evil.com/acme.atlassian.net",
    "https://evil.com#.atlassian.net",
    "https://user:pw@acme.atlassian.net",
    "https://acme.atlassian.net@evil.com",
    "https://acme.atlassian.net:8443",
    "https://acme.atlassian.net/rest/api/3",
    "https://acme.atlassian.net/?x=1",
    "acme.atlassian.net",
    "",
])
def test_any_other_url_is_refused(url):
    with pytest.raises(ValueError, match="your-company.atlassian.net"):
        require_atlassian_cloud_site_url(url)


def test_an_operator_allowed_host_is_accepted_over_http(monkeypatch):
    monkeypatch.setenv(ALLOWED_SITE_HOSTS_ENV, "jira-sim.internal, other.test")
    require_atlassian_cloud_site_url("http://jira-sim.internal")
    with pytest.raises(ValueError):
        require_atlassian_cloud_site_url("http://jira-sim.internal.evil.com")
    with pytest.raises(ValueError):
        require_atlassian_cloud_site_url("ftp://jira-sim.internal")
