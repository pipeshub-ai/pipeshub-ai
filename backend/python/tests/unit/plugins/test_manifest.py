"""The checked-in plug-in manifest and the script an image build rewrites it with."""

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
