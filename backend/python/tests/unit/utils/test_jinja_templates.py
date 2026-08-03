"""`compiled_template` (`app/utils/jinja_templates.py`) — the whole point is
that the same source is compiled once and reused, so the test asserts object
identity rather than that `Template` was called."""

from __future__ import annotations

from app.utils.jinja_templates import compiled_template


class TestCompiledTemplate:
    def test_same_source_returns_the_same_object(self) -> None:
        source = "test_same_source_returns_the_same_object {{ value }}"

        assert compiled_template(source) is compiled_template(source)

    def test_different_sources_do_not_share_a_template(self) -> None:
        a = compiled_template("test_different_sources a {{ v }}")
        b = compiled_template("test_different_sources b {{ v }}")

        assert a is not b

    def test_a_shared_template_renders_independently_per_call(self) -> None:
        """Reuse is only safe if rendering keeps no state between calls."""
        source = "test_a_shared_template_renders {{ v }}"

        assert compiled_template(source).render(v="first") == "test_a_shared_template_renders first"
        assert compiled_template(source).render(v="second") == "test_a_shared_template_renders second"
