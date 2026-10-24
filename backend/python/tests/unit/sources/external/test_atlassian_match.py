"""Unit tests for Atlassian Cloud site URL matching helpers."""

import pytest

from app.sources.external.common.atlassian import (
    ALLOWED_SITE_HOSTS_ENV,
    AtlassianCloudResource,
    atlassian_cloud_site_root,
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


@pytest.mark.parametrize("url, root", [
    ("https://acme.atlassian.net", "https://acme.atlassian.net"),
    ("https://acme.atlassian.net/", "https://acme.atlassian.net"),
    ("https://ACME.atlassian.net", "https://acme.atlassian.net"),
    ("https://acme.jira.com", "https://acme.jira.com"),
    ("https://acme.atlassian.net:443", "https://acme.atlassian.net"),
    ("  https://acme.atlassian.net  ", "https://acme.atlassian.net"),
    # Jira answers under /jira/..., so these synced before the check: keep them working, at the root.
    ("https://acme.atlassian.net/jira", "https://acme.atlassian.net"),
    ("https://acme.atlassian.net/jira/software/projects/ENG/boards/1", "https://acme.atlassian.net"),
    ("https://acme.atlassian.net/?x=1#y", "https://acme.atlassian.net"),
    # Never send the token in clear: upgraded, as Atlassian itself redirects.
    ("http://acme.atlassian.net", "https://acme.atlassian.net"),
    ("acme.atlassian.net", "https://acme.atlassian.net"),
])
def test_a_cloud_site_url_becomes_its_site_root(url: str, root: str) -> None:
    assert atlassian_cloud_site_root(url) == root


@pytest.mark.parametrize("url", [
    "https://169.254.169.254",                       # cloud metadata
    "http://169.254.169.254/latest/meta-data",
    "https://localhost",
    "https://10.0.0.5",
    "https://jira.internal.example.com",
    "https://atlassian.net",                         # no site
    "https://acme.atlassian.net.evil.com",
    "https://evil.com/acme.atlassian.net",
    "https://evil.com#.atlassian.net",
    "https://evil.com?.atlassian.net",
    "https://user:pw@acme.atlassian.net",
    "https://acme.atlassian.net@evil.com",
    "https://acme.atlassian.net:8443",
    "https://acme.atlassian.net:notaport",
    "ftp://acme.atlassian.net",
    "",
])
def test_any_other_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="your-company.atlassian.net"):
        atlassian_cloud_site_root(url)


def test_an_operator_allowed_host_is_kept_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_SITE_HOSTS_ENV, "jira-sim.internal, other.test")
    assert atlassian_cloud_site_root("http://jira-sim.internal") == "http://jira-sim.internal"
    assert atlassian_cloud_site_root("http://jira-sim.internal:8080/") == "http://jira-sim.internal:8080"
    with pytest.raises(ValueError):
        atlassian_cloud_site_root("http://jira-sim.internal.evil.com")
    with pytest.raises(ValueError):
        atlassian_cloud_site_root("ftp://jira-sim.internal")
