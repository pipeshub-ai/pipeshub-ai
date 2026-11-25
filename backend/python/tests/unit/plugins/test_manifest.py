"""The checked-in plug-in manifest and the script an image build rewrites it with."""

import tomllib
from pathlib import Path

import pytest

from app import plugins


def test_the_checked_in_manifest_is_a_list_of_importable_plugin_modules() -> None:
    from app.plugins_manifest import PLUGINS

    assert PLUGINS == ["app.plugins.telemetry"]
    for path in PLUGINS:
        plugin = __import__(path, fromlist=["plugin"]).plugin
        assert isinstance(plugin, plugins.Plugin)


def test_the_manifest_writer_writes_an_importable_list(tmp_path) -> None:
    import importlib.util

    from scripts.write_plugins_manifest import main

    def read(path) -> list[str]:
        spec = importlib.util.spec_from_file_location("written_manifest", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PLUGINS

    out = tmp_path / "plugins_manifest.py"
    assert main(["--output", str(out)]) == 0
    assert read(out) == []
    assert main(["--output", str(out), "app.plugins.telemetry", "pkg.other"]) == 0
    assert read(out) == ["app.plugins.telemetry", "pkg.other"]
    with pytest.raises(ValueError):
        main(["--output", str(out), "not a module; import os"])


PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _dependencies(text: str) -> list[str]:
    return tomllib.loads(text)["project"]["dependencies"]


def test_the_prometheus_client_is_declared_only_as_the_telemetry_plugins_extra() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert [d for d in project["dependencies"] if "prometheus" in d.lower()] == []
    assert [d for d in project["optional-dependencies"]["telemetry"] if d.startswith("prometheus-client==")]
    # the default install still gets it: the project depends on its own extra
    assert f"{project['name']}[telemetry]" in project["dependencies"]


def test_the_writer_drops_the_extra_of_a_plugin_the_build_leaves_out(tmp_path) -> None:
    from scripts.write_plugins_manifest import drop_plugin_extras, main

    original = PYPROJECT.read_text(encoding="utf-8")
    kept, dropped = drop_plugin_extras(original, ["app.plugins.telemetry"])
    assert (kept, dropped) == (original, [])

    stripped, dropped = drop_plugin_extras(original, [])
    assert dropped == ["telemetry"]
    assert sorted(set(_dependencies(original)) - set(_dependencies(stripped))) == ["ai-service[telemetry]"]
    # still declared, so an install can ask for it by name
    assert "telemetry" in tomllib.loads(stripped)["project"]["optional-dependencies"]

    copy = tmp_path / "pyproject.toml"
    copy.write_text(original, encoding="utf-8")
    assert main(["--output", str(tmp_path / "manifest.py"), "--pyproject", str(copy)]) == 0
    assert copy.read_text(encoding="utf-8") == stripped
    copy.write_text(original, encoding="utf-8")
    assert main(["--output", str(tmp_path / "manifest.py"), "--pyproject", str(copy), "app.plugins.telemetry"]) == 0
    assert copy.read_text(encoding="utf-8") == original


def test_only_a_line_that_is_the_projects_own_extra_is_dropped() -> None:
    from scripts.write_plugins_manifest import drop_plugin_extras

    text = (
        '[project]\nname = "svc"\ndependencies = [\n    "svc[meter]",  # why\n    "other[meter]==1",\n'
        '    "svc[kept]",\n    "svc-tools[meter]",\n]\n[project.optional-dependencies]\nmeter = ["m==1"]\n'
    )
    out, dropped = drop_plugin_extras(text, ["pkg.kept"])
    assert dropped == ["meter"]
    assert _dependencies(out) == ["other[meter]==1", "svc[kept]", "svc-tools[meter]"]
    with pytest.raises(ValueError):
        drop_plugin_extras("[tool.x]\n", [])
