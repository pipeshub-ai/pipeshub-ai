"""kafka_security_kwargs: the TLS and SASL options every Kafka client shares."""

import pytest

from app.services.messaging.kafka.config.kafka_config import kafka_security_kwargs


def test_a_username_without_a_password_is_refused_up_front() -> None:
    with pytest.raises(ValueError, match="no password"):
        kafka_security_kwargs(ssl_enabled=True, sasl={"username": "svc"})


def test_a_complete_pair_configures_sasl_over_tls() -> None:
    options = kafka_security_kwargs(ssl_enabled=True, sasl={"username": "svc", "password": "pw"})
    assert options["security_protocol"] == "SASL_SSL"
    assert options["sasl_mechanism"] == "SCRAM-SHA-512"
    assert options["sasl_plain_username"] == "svc"


def test_tls_without_sasl_and_neither() -> None:
    assert kafka_security_kwargs(ssl_enabled=True, sasl=None)["security_protocol"] == "SSL"
    assert kafka_security_kwargs(ssl_enabled=False, sasl={"username": "svc"}) == {}
