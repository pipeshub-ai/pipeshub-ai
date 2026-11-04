"""Constructs the parser used to miss, end to end: parse, project, build (P-5, P-3)."""
import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _package, _targets

CALLS = RecordRelations.CALLS.value
IMPORTS_FROM = RecordRelations.IMPORTS_FROM.value

pytestmark = pytest.mark.asyncio


async def test_a_rendered_component_is_a_call(graph, index_file) -> None:
    await index_file("panel", "web/panel.jsx",
                     b"export function Panel() {\n  return <div>panel</div>\n}\n", "javascript")
    await index_file("app", "web/app.jsx",
                     b"import { Panel } from './panel'\n\nexport function App() {\n"
                     b"  return <main><Panel title=\"x\" /></main>\n}\n", "javascript")
    await _build(graph)
    assert "web/panel.jsx::function:Panel" in _targets(graph, CALLS, "web/app.jsx")


async def test_a_required_name_is_called_through_its_module(graph, index_file) -> None:
    await index_file("util", "lib/util.js",
                     b"function helper(x) {\n  return x\n}\nmodule.exports = { helper }\n",
                     "javascript")
    await index_file("main", "lib/main.js",
                     b"const { helper } = require('./util')\n\nfunction run() {\n"
                     b"  return helper(1)\n}\n", "javascript")
    await _build(graph)
    assert "lib/util.js" in _targets(graph, IMPORTS_FROM, "lib/main.js")
    assert "lib/util.js::function:helper" in _targets(graph, CALLS, "lib/main.js")


async def test_a_dynamic_import_imports_its_module(graph, index_file) -> None:
    await index_file("util", "lib/util.ts", b"export function helper() {}\n", "typescript")
    await index_file("lazy", "lib/lazy.ts",
                     b"export async function load() {\n  const m = await import('./util')\n"
                     b"  return m\n}\n", "typescript")
    await _build(graph)
    assert "lib/util.ts" in _targets(graph, IMPORTS_FROM, "lib/lazy.ts")


async def test_a_decorator_calls_its_function(graph, index_file) -> None:
    await _package(index_file, "app")
    await index_file("auth", "app/auth.py",
                     b"def login_required(f):\n    return f\n", "python")
    await index_file("views", "app/views.py",
                     b"from app.auth import login_required\n\n\n@login_required\n"
                     b"def home():\n    return 1\n", "python")
    await _build(graph)
    assert "app/auth.py::function:login_required" in _targets(graph, CALLS, "app/views.py")


async def test_a_parameter_annotation_types_calls_on_it(graph, index_file) -> None:
    await _package(index_file, "app")
    await index_file("client", "app/client.py",
                     b"class Client:\n    def fetch(self):\n        return 1\n", "python")
    await index_file("svc", "app/svc.py",
                     b"from app.client import Client\n\n\ndef run(c: Client | None):\n"
                     b"    return c.fetch()\n", "python")
    await _build(graph)
    assert "app/client.py::method:Client.fetch" in _targets(graph, CALLS, "app/svc.py")


async def test_a_definition_inside_try_or_if_is_a_symbol(graph, index_file) -> None:
    await _package(index_file, "app")
    await index_file("compat", "app/compat.py",
                     b"try:\n    import ujson as json\nexcept ImportError:\n"
                     b"    def loads(s):\n        return s\n\nif True:\n    def dumps(o):\n        return o\n",
                     "python")
    await index_file("use", "app/use.py",
                     b"from app.compat import dumps, loads\n\n\ndef go():\n"
                     b"    return loads(dumps(1))\n", "python")
    await _build(graph)
    assert {"app/compat.py::function:loads", "app/compat.py::function:dumps"} <= _targets(
        graph, CALLS, "app/use.py")
