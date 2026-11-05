"""The projection's mode comes from the environment: off unless PG_KG_PROJECT=1, then async, or inline with PG_KG_FLUSH=inline."""
import pytest

from app.services.graph_db.postgres.kg import flush


@pytest.mark.parametrize(("project", "flush_mode", "mode", "inline"), [
    (None, None, "off", False), ("0", "inline", "off", False), ("1", None, "async", False), ("1", "async", "async", False), ("1", "inline", "inline", True),
])
def test_the_mode_follows_the_environment(monkeypatch: pytest.MonkeyPatch, project: str | None, flush_mode: str | None, mode: str, inline: bool) -> None:
    for name, value in (("PG_KG_PROJECT", project), ("PG_KG_FLUSH", flush_mode)):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    assert flush.mode() == mode and flush.enabled() is inline
@pytest.mark.parametrize(("name", "bad"), [("PG_KG_PROJECT", "true"), ("PG_KG_PROJECT", "yes"), ("PG_KG_FLUSH", "inlne"), ("PG_KG_GUARD", "Strict"), ("PG_KG_STRICT", "on")])
def test_a_setting_this_code_does_not_know_is_an_error_that_names_it(monkeypatch: pytest.MonkeyPatch, name: str, bad: str) -> None:
    for var in ("PG_KG_PROJECT", "PG_KG_FLUSH", "PG_KG_GUARD", "PG_KG_STRICT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name):
        flush.check_env()
    with pytest.raises(ValueError, match=name):
        flush.mode()
    with pytest.raises(ValueError, match=name):
        flush.install(object())  # type: ignore[arg-type]


def test_the_known_values_and_an_unset_or_empty_setting_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, value in (("PG_KG_PROJECT", "1"), ("PG_KG_FLUSH", "inline"), ("PG_KG_GUARD", "strict"), ("PG_KG_STRICT", "1")):
        monkeypatch.setenv(var, value)
    flush.check_env()
    for var in ("PG_KG_PROJECT", "PG_KG_FLUSH", "PG_KG_GUARD", "PG_KG_STRICT"):
        monkeypatch.setenv(var, "")
    flush.check_env()
    assert flush.mode() == "off"
