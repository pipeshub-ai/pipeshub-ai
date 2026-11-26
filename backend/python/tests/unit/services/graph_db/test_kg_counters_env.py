"""The counters are read unless PG_KG_COUNTERS is 0, and PG_KG_COUNTERS and PG_KG_INLINE_MAX refuse a value they do not understand, as the other kg settings do."""
import pytest

from app.services.graph_db.postgres.kg import counts, flush, stamp


@pytest.mark.parametrize(("value", "want"), [(None, True), ("", True), ("0", False), ("1", True)])
def test_the_counters_are_read_unless_the_switch_is_zero(monkeypatch, value, want) -> None:
    monkeypatch.delenv("PG_KG_COUNTERS", raising=False)
    if value is not None:
        monkeypatch.setenv("PG_KG_COUNTERS", value)
    assert counts.serving() is want


@pytest.mark.parametrize("bad", ["true", "on", "yes", "2", "off"])
def test_any_other_counters_value_is_an_error_that_names_the_setting(monkeypatch, bad) -> None:
    monkeypatch.setenv("PG_KG_COUNTERS", bad)
    with pytest.raises(ValueError, match="PG_KG_COUNTERS"):
        flush.check_env()
    with pytest.raises(ValueError, match="PG_KG_COUNTERS"):
        counts.serving()


@pytest.mark.parametrize(("value", "want"), [(None, stamp.INLINE_MAX), ("", stamp.INLINE_MAX), ("0", 0), ("500", 500)])
def test_the_inline_restamp_bound_is_a_whole_number_or_the_default(monkeypatch, value, want) -> None:
    monkeypatch.delenv("PG_KG_INLINE_MAX", raising=False)
    if value is not None:
        monkeypatch.setenv("PG_KG_INLINE_MAX", value)
    flush.check_env()
    assert stamp.inline_max() == want


@pytest.mark.parametrize("bad", ["20k", "-1", "1e4", "2000.5", " 500"])
def test_an_inline_restamp_bound_that_is_not_a_whole_number_is_an_error_that_names_the_setting(monkeypatch, bad) -> None:
    monkeypatch.setenv("PG_KG_INLINE_MAX", bad)
    with pytest.raises(ValueError, match="PG_KG_INLINE_MAX"):
        flush.check_env()
