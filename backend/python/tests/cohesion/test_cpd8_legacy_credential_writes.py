"""CPD-8 LW-4: every legacy toolset and MCP credential write goes through ``LegacyCredentialRecords``.

With ``CONTROL_PLANE_CREDENTIALS`` on the old credential paths are read-only; a route that wrote one directly would
put a credential where nothing reads it first and miss the binder's audit. The scan finds ``set_config`` and
``delete_config`` calls under ``app/`` whose key is built by a per-owner credential path helper (directly, or through
a name assigned from one in the same function). The allow-list is the seam itself and what runs only with the switch
off and is deleted after the window (CPD-8 §3).
"""

from __future__ import annotations

import ast

from tests.cohesion.support import PYTHON_ROOT

PATH_HELPERS = frozenset(
    {"get_toolset_config_path", "get_mcp_credentials_path", "_get_user_auth_path", "_get_agent_auth_path"}
)
WRITES = frozenset({"set_config", "delete_config"})
ALLOWED = {
    "app/harness/credentials/legacy_records.py": "the seam",
    "app/agents/mcp/token_refresh.py": "the switch-off refresh of MCP records; deleted after the window",
    "app/connectors/core/base/token_service/toolset_token_refresh_service.py": "switch off only; deleted after the window",
    "app/connectors/core/base/token_service/mcp_token_refresh_service.py": "switch off only; deleted after the window",
}


def _helper_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name in PATH_HELPERS


def offenders(source: str, label: str) -> list[str]:
    found: list[str] = []
    tree = ast.parse(source)
    for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        paths = {
            target.id
            for node in ast.walk(function)
            if isinstance(node, ast.Assign) and _helper_call(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITES):
                continue
            key = node.args[0] if node.args else None
            if key is not None and (_helper_call(key) or (isinstance(key, ast.Name) and key.id in paths)):
                found.append(f"{label}:{node.lineno} {function.name}")
    return found


def test_lw4_no_legacy_credential_write_outside_the_seam() -> None:
    found: list[str] = []
    for path in sorted((PYTHON_ROOT / "app").rglob("*.py")):
        label = str(path.relative_to(PYTHON_ROOT))
        if label in ALLOWED:
            continue
        source = path.read_text(encoding="utf-8-sig")
        # An offender names a path helper and a write, so only such files are parsed (a few of app/'s 1,800).
        if any(name in source for name in PATH_HELPERS) and any(name in source for name in WRITES):
            found += offenders(source, label)
    assert found == []


def test_lw4_the_scan_finds_a_direct_write() -> None:
    direct = """
async def save(config, instance_id, user_id, record):
    path = get_mcp_credentials_path(instance_id, user_id)
    await config.set_config(path, record)
    await config.delete_config(_get_user_auth_path(instance_id, user_id))
    await config.set_config("/services/elsewhere", record)
"""
    assert offenders(direct, "x.py") == ["x.py:4 save", "x.py:5 save"]


def test_lw4_the_allow_list_names_files_that_exist() -> None:
    assert all((PYTHON_ROOT / label).is_file() for label in ALLOWED)
