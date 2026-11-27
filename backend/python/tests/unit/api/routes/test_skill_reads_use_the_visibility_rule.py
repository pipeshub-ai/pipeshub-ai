"""Every read of a skill decides who may see it with one rule, `SkillVisibility.allows`.

Checked from the source, closed-world (a new route or store method fails until it is
classified here):

1. The rule. `GraphSkillStore._sees` starts from `SkillVisibility.allows`, and the agent builder's
   link check and its view of an agent's skills read through the store, never the graph itself.
2. The store. Every public read of a skill by name reads it through `_get_org_doc` (the
   visible-only read) before anything else; a listing filters with `_sees`; usage names
   only agents the caller may open (`_may_see_agent`) and skills it can see.
3. The routes. Every GET route of the skills router is listed below. A route builds the
   caller's manager (`_build_manager(request)`), or an admin's only after `_is_admin`;
   a route on one skill reads it only through the manager methods named for it; and no route
   code touches the org-wide usage collector.

The check is syntactic; the route and store tests check the behaviour.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.agents.agent_loop.skills import graph_store
from app.api.routes import agent as agent_routes
from app.api.routes import skills as skills_routes

# GET route -> the manager methods through which it may read a skill.
GET_ROUTES = {
    "list_skills": {"list_skills"},
    "list_org_skills": {"list_skills"},
    "get_categories": {"get_categories", "get_tags"},
    "search_skills": {"search"},
    "get_skill": {"get_skill"},
    "export_skill": {"get_skill"},
    "get_skill_usage": {"get_referential_usage"},
    "list_versions": {"list_versions"},
    "get_version": {"get_version"},
    "get_resource": {"load_resource"},
    "get_pending_candidates": {"get_pending_candidates"},
}
# Routes that read no skill: where a command may be shared, and the names of an author and a team.
NO_SKILL_ROUTES = {"command_share_targets", "command_provenance"}
# Routes that may build an admin's (org-wide) manager, after asking Node for the caller's role.
ADMIN_GET_ROUTES = {"list_org_skills"}
PROVIDER_METHODS = {
    "get_document", "get_nodes_by_filters", "get_edges_to_node", "get_edges_from_node",
    "check_agent_permission", "execute_query",
}

# GraphSkillStore public methods, by how they decide visibility.
STORE_NAME_READS = {
    "get_skill", "get_resource", "get_resources", "exists", "get_status", "get_provenance",
    "list_versions", "get_version", "get_referential_usage", "sharing", "label",
    # Only names why `get_status` found nothing, for a log line: it reads the visible doc first and shows no field of any doc.
    "hidden_reason",
}
STORE_LIST_READS = {"list_skills", "sharings", "labels"}
# Reads of a run's pinned content, whatever the skill's visibility is now: never a person's read of a skill.
STORE_PINNED_READS = {"content_files", "content_versions", "in_force"}
STORE_WRITES = {
    # Decided by `_get_writable_doc`, the create's unscoped check or the bound delete.
    "create_skill", "update_skill", "patch_skill", "deletion_binding", "delete_skill",
    "detach_from_agents", "write_resource", "remove_resource", "deprecate_skill",
    "set_skill_status", "rollback", "share", "rename",
}
# Learning-loop candidates are proposals, not skills, and have no creator today.
STORE_CANDIDATES = {"queue_candidate", "get_pending_candidates", "remove_candidate"}


def _tree(obj: object) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(obj)))


def _calls(tree: ast.AST) -> list[ast.Call]:
    return sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))


def _callee(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _on(call: ast.Call, owner: str) -> bool:
    return isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == owner


def _get_endpoints() -> dict[str, object]:
    return {
        route.endpoint.__name__: route.endpoint
        for route in skills_routes.router.routes
        if "GET" in getattr(route, "methods", set())
    }


# ---- 1. the rule ---------------------------------------------------------------------------------

def test_the_store_and_the_agent_builder_use_the_one_rule() -> None:
    assert "allows" in {_callee(c) for c in _calls(_tree(graph_store.GraphSkillStore._sees))}
    for builder in (agent_routes._create_skill_edges, agent_routes._skills_as_the_caller_may_see_them):
        calls = _calls(_tree(builder))
        assert "GraphSkillStore" in {_callee(c) for c in calls}
        assert not {_callee(c) for c in calls} & set(PROVIDER_METHODS), f"{builder.__name__} reads the graph itself"
    assert "_sees" in {_callee(c) for c in _calls(_tree(graph_store.GraphSkillStore._get_org_doc))}


# ---- 2. the store --------------------------------------------------------------------------------

def test_every_public_store_method_is_classified() -> None:
    public = {
        name for name, member in vars(graph_store.GraphSkillStore).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }
    classified = STORE_NAME_READS | STORE_LIST_READS | STORE_PINNED_READS | STORE_WRITES | STORE_CANDIDATES
    assert public == classified, f"classify: {sorted(public - classified)}; gone: {sorted(classified - public)}"


DATA_READS = {
    "_get_org_doc", "_get_unscoped_org_doc", "_get_version_doc", "collect_referential_usage", *PROVIDER_METHODS,
}


@pytest.mark.parametrize("method", sorted(STORE_NAME_READS))
def test_a_read_by_name_reads_the_visible_doc_first(method: str) -> None:
    reads = [_callee(c) for c in _calls(_tree(getattr(graph_store.GraphSkillStore, method))) if _callee(c) in DATA_READS]
    assert reads, f"{method} reads nothing"
    assert reads[0] == "_get_org_doc", f"{method} reads {reads[0]} before the visible doc"


@pytest.mark.parametrize("method", sorted(STORE_LIST_READS))
def test_a_listing_filters_with_the_rule(method: str) -> None:
    assert "_sees" in {_callee(c) for c in _calls(_tree(getattr(graph_store.GraphSkillStore, method)))}


def test_usage_names_only_what_the_caller_may_see() -> None:
    callees = [_callee(c) for c in _calls(_tree(graph_store.GraphSkillStore.get_referential_usage))]
    assert callees.index("_get_org_doc") < callees.index("collect_referential_usage")
    assert "_may_see_agent" in callees
    assert callees.count("_get_org_doc") >= 2, "dependant skills are not checked against the rule"
    assert "check_agent_permission" in {_callee(c) for c in _calls(_tree(graph_store.GraphSkillStore._may_see_agent))}


# ---- 3. the routes -------------------------------------------------------------------------------

def test_every_get_route_is_classified() -> None:
    classified = set(GET_ROUTES) | NO_SKILL_ROUTES
    assert set(_get_endpoints()) == classified, (
        f"classify: {sorted(set(_get_endpoints()) - classified)}; gone: {sorted(classified - set(_get_endpoints()))}"
    )


@pytest.mark.parametrize("route", sorted(GET_ROUTES))
def test_a_get_route_builds_the_callers_manager(route: str) -> None:
    calls = _calls(_tree(_get_endpoints()[route]))
    builds = [c for c in calls if _callee(c) == "_build_manager"]
    assert builds, f"{route} reads skills without the caller's manager"
    for build in builds:
        if any(k.arg == "is_admin" for k in build.keywords):
            assert route in ADMIN_GET_ROUTES, f"{route} builds an org-wide manager"
            checks = [c for c in calls if _callee(c) == "_is_admin"]
            assert checks and checks[0].lineno < build.lineno, f"{route} builds an admin's manager before the role check"


@pytest.mark.parametrize("route", sorted(GET_ROUTES))
def test_a_get_route_reads_through_its_manager_methods_only(route: str) -> None:
    used = {_callee(c) for c in _calls(_tree(_get_endpoints()[route])) if _on(c, "manager")}
    assert used <= GET_ROUTES[route], f"{route} also calls manager.{sorted(used - GET_ROUTES[route])}"
    assert used & GET_ROUTES[route], f"{route} no longer reads through {sorted(GET_ROUTES[route])}"


def test_the_usage_route_answers_a_skill_the_caller_cannot_see_as_get_does() -> None:
    callees = [_callee(c) for c in _calls(_tree(skills_routes.get_skill_usage))]
    assert "_load_skill_metadata" in callees
    assert callees.index("_load_skill_metadata") < callees.index("get_referential_usage")


def test_no_route_code_uses_the_org_wide_usage_collector() -> None:
    module = ast.parse(inspect.getsource(skills_routes))
    names = {n.id for n in ast.walk(module) if isinstance(n, ast.Name)}
    names |= {a.name for n in ast.walk(module) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "collect_referential_usage" not in names
