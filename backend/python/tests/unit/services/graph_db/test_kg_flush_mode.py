"""The writer projects its own changes only with PG_KG_FLUSH=inline; the projection cannot be turned off (PG_KG_PROJECT=0 is refused when the provider is built)."""
import pytest

from app.services.graph_db.postgres.kg import flush


@pytest.mark.parametrize(("project", "flush_mode", "inline"), [
    (None, None, False), ("", None, False), ("1", None, False), ("1", "async", False), ("1", "inline", True), (None, "inline", True),
])
def test_the_inline_flush_follows_the_environment(monkeypatch: pytest.MonkeyPatch, project: str | None, flush_mode: str | None, inline: bool) -> None:
    for name, value in (("PG_KG_PROJECT", project), ("PG_KG_FLUSH", flush_mode)):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    assert flush.enabled() is inline


def test_the_projection_off_is_refused_when_the_provider_is_built_with_a_message_that_names_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "0")
    with pytest.raises(ValueError, match="PG_KG_PROJECT=0 is not supported"):
        flush.check_env()
    with pytest.raises(ValueError, match="PG_KG_PROJECT=0 is not supported"):
        flush.install(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(("name", "bad"), [("PG_KG_PROJECT", "true"), ("PG_KG_PROJECT", "yes"), ("PG_KG_READ", "true"), ("PG_KG_READ", "false"), ("PG_KG_FLUSH", "inlne"), ("PG_KG_GUARD", "Strict"), ("PG_KG_STRICT", "on")])
def test_a_setting_this_code_does_not_know_is_an_error_that_names_it(monkeypatch: pytest.MonkeyPatch, name: str, bad: str) -> None:
    for var in ("PG_KG_PROJECT", "PG_KG_READ", "PG_KG_FLUSH", "PG_KG_GUARD", "PG_KG_STRICT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name):
        flush.check_env()
    with pytest.raises(ValueError, match=name):
        flush.enabled()
    with pytest.raises(ValueError, match=name):
        flush.install(object())  # type: ignore[arg-type]


def test_the_known_values_and_an_unset_or_empty_setting_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, value in (("PG_KG_PROJECT", "1"), ("PG_KG_FLUSH", "inline"), ("PG_KG_GUARD", "strict"), ("PG_KG_STRICT", "1")):
        monkeypatch.setenv(var, value)
    flush.check_env()
    for var in ("PG_KG_PROJECT", "PG_KG_FLUSH", "PG_KG_GUARD", "PG_KG_STRICT"):
        monkeypatch.setenv(var, "")
    flush.check_env()
    assert flush.enabled() is False
