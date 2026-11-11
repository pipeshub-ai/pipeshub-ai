"""The net's settings are numbers in a range; anything else raises at startup naming the setting."""
import pytest

from app.services.graph_db.postgres.kg import flush, net


def test_the_defaults_are_a_one_percent_sample_and_an_hourly_verify(monkeypatch) -> None:
    for name in (net.SAMPLE_ENV, net.EVERY_ENV):
        monkeypatch.delenv(name, raising=False)
    assert net.sample_rate() == 0.01 and net.verify_every() == 3600.0
    monkeypatch.setenv(net.SAMPLE_ENV, "")
    assert net.sample_rate() == 0.01


@pytest.mark.parametrize(("name", "value", "want"), [("PG_KG_SHADOW_SAMPLE", "0", 0.0), ("PG_KG_SHADOW_SAMPLE", "1", 1.0), ("PG_KG_SHADOW_SAMPLE", "0.25", 0.25)])
def test_a_valid_share_is_read(monkeypatch, name, value, want) -> None:
    monkeypatch.setenv(name, value)
    assert net.sample_rate() == want


@pytest.mark.parametrize(("name", "value"), [("PG_KG_SHADOW_SAMPLE", "2"), ("PG_KG_SHADOW_SAMPLE", "-0.1"), ("PG_KG_SHADOW_SAMPLE", "lots"), ("PG_KG_SHADOW_SAMPLE", "nan"),
                                              ("PG_KG_VERIFY_EVERY_S", "-1"), ("PG_KG_VERIFY_EVERY_S", "hourly"), ("PG_KG_VERIFY_EVERY_S", "inf")])
def test_anything_else_raises_at_startup_and_names_the_setting(monkeypatch, name, value) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        flush.check_env()
