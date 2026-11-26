"""SEC-D-06: the one parser of etcd's client settings (app/config/providers/etcd/etcd_env.py)."""

import pytest

from app.config.providers.etcd.etcd_env import etcd_store_config

TLS = {"ETCD_CA_FILE": "/tls/ca.crt", "ETCD_CERT_FILE": "/tls/tls.crt", "ETCD_KEY_FILE": "/tls/tls.key"}


def test_unset_is_none() -> None:
    assert etcd_store_config({}) is None
    assert etcd_store_config({"ETCD_URL": "  "}) is None


@pytest.mark.parametrize("url", ["https://etcd:2379", "HTTPS://etcd:2379", " https://etcd:2379 "])
def test_https_carries_this_components_certificate(url: str) -> None:
    config = etcd_store_config({"ETCD_URL": url, **TLS})
    assert (config.host, config.port) == ("etcd", 2379)
    assert (config.ca_cert, config.cert_cert, config.cert_key) == ("/tls/ca.crt", "/tls/tls.crt", "/tls/tls.key")


@pytest.mark.parametrize("missing", sorted(TLS))
def test_https_without_a_file_is_refused_rather_than_plaintext(missing: str) -> None:
    env = {"ETCD_URL": "https://etcd:2379", **TLS, missing: ""}
    with pytest.raises(ValueError, match=missing):
        etcd_store_config(env)


def test_http_and_a_bare_host_connect_without_tls_files() -> None:
    for url in ("http://etcd:2379", "etcd:2379", "etcd"):
        config = etcd_store_config({"ETCD_URL": url, **TLS})
        assert (config.host, config.port, config.ca_cert, config.cert_cert) == ("etcd", 2379, None, None), url


def test_timeout_is_milliseconds_and_login_optional() -> None:
    config = etcd_store_config({"ETCD_URL": "http://e:1", "ETCD_TIMEOUT": "2500", "ETCD_USERNAME": "u", "ETCD_PASSWORD": "p"})
    assert (config.port, config.timeout, config.username, config.password) == (1, 2.5, "u", "p")
