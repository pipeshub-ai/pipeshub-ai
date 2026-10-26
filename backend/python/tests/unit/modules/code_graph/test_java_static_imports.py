"""`import static` binds every overload, and the import edge names the class (B1 guava)."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value
IMPORTS = RecordRelations.IMPORTS.value

PRECONDITIONS = (b"package com.acme.base;\n\npublic final class Preconditions {\n"
                 b"  public static <T> T checkNotNull(T ref) { return ref; }\n"
                 b"  public static <T> T checkNotNull(T ref, Object msg) { return ref; }\n}\n")
USER = (b"package com.acme.app;\n\nimport static com.acme.base.Preconditions.checkNotNull;\n\n"
        b"public class Service {\n  Object run(Object x) { return checkNotNull(x); }\n}\n")


@pytest.mark.asyncio
async def test_a_static_import_of_an_overloaded_method_binds_its_calls(graph, index_file) -> None:
    await index_file("p", "src/com/acme/base/Preconditions.java", PRECONDITIONS, "java")
    await index_file("s", "src/com/acme/app/Service.java", USER, "java")
    await _build(graph)
    calls = _targets(graph, CALLS, "src/com/acme/app/Service.java")
    assert len(calls) == 1
    assert next(iter(calls)).startswith("src/com/acme/base/Preconditions.java::method:Preconditions.checkNotNull")
    assert "src/com/acme/base/Preconditions.java::class:Preconditions" in _targets(
        graph, IMPORTS, "src/com/acme/app/Service.java")
