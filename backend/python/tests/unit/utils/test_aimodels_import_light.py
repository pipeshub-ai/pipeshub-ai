"""Importing the model helper must not load connector or graph code."""

import subprocess
import sys


def test_importing_aimodels_does_not_load_graph_providers() -> None:
    code = (
        "import sys, app.utils.aimodels; "
        "print('app.services.graph_db.neo4j.neo4j_provider' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-2000:]
