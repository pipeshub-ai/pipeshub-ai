"""The plug-ins this build loads: dotted module paths, each exposing ``plugin``.

Checked-in default. An image build that leaves a plug-in out removes its package
and rewrites this file with ``scripts/write_plugins_manifest.py``.
"""

PLUGINS: list[str] = [
    "app.plugins.telemetry",
]
