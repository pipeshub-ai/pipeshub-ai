"""The knowledge-graph tools one request sees, from its ``KnowledgeGraphPolicy``
(``agent_loop/knowledge_graph_policy``).

- ``off``: no entity tools, and ``search`` without ``entity_ids``, which only
  takes ids those tools return.
- ``taxonomy``: the entity tools with people and organisations edited out of
  their descriptions, since the arm never returns them, and without
  ``find_records_by_person``, which only lists people's records.
- ``full``: the toolset as declared.

Where ``search_by_entities`` is offered, the request also gets the
descriptions that only hold with named-entity extraction on (``search_entities``
finds named entities, ``search``/``find_records_by_entity`` take their ids),
``search_entities`` takes several queries at once and explains the kinds it
cannot serve, and whatever names ``search_by_entities`` does so only because
it is granted. Without named entities, the request also goes without
``find_records_by_value``, ``find_co_mentioned_entities`` and
``search_entities``' ``named_entity_kinds``, which could only fail there.

Each variant is a small, named edit of the declared text, so the declared tool
stays the single source of its wording; an edit whose anchor is gone fails
loudly instead of silently shipping the old text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, ToolParameter
from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool
from app.agents.actions.knowledge_graph.ops.record_query import (
    MENTIONS_CONDITION_TEXT,
    roles_guide,
)
from app.agents.actions.knowledge_graph.ops.timeline import TIMELINE_TOOL_NAME
from app.agents.agent_loop.knowledge_graph_policy import SEARCH_BY_ENTITIES_TOOL_NAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.tools.base import Tool
    from app.agent_loop_lib.tools.decorators import ToolMeta
    from app.agents.agent_loop.knowledge_graph_policy import KnowledgeGraphPolicy

SEARCH_TOOL_NAME = "knowledgegraph__search"
SEARCH_ENTITIES_TOOL_NAME = "knowledgegraph__search_entities"
FIND_RECORDS_BY_ENTITY_TOOL_NAME = "knowledgegraph__find_records_by_entity"
FIND_RECORDS_BY_VALUE_TOOL_NAME = "knowledgegraph__find_records_by_value"
FIND_CO_MENTIONED_TOOL_NAME = "knowledgegraph__find_co_mentioned_entities"
FIND_RECORDS_TOOL_NAME = "knowledgegraph__find_records"
# Tools that read only named entities: left out with extraction off, where they could only fail.
_NAMED_ENTITY_ONLY_TOOLS = frozenset({FIND_RECORDS_BY_VALUE_TOOL_NAME, FIND_CO_MENTIONED_TOOL_NAME})
FIND_RECORDS_BY_PERSON_TOOL_NAME = "knowledgegraph__find_records_by_person"
ENTITY_TOOL_NAMES: frozenset[str] = frozenset({
    SEARCH_ENTITIES_TOOL_NAME,
    FIND_RECORDS_BY_ENTITY_TOOL_NAME,
    FIND_RECORDS_TOOL_NAME,
    SEARCH_BY_ENTITIES_TOOL_NAME,
    FIND_RECORDS_BY_PERSON_TOOL_NAME,
}) | _NAMED_ENTITY_ONLY_TOOLS


@dataclass(frozen=True)
class _Edit:
    old: str
    new: str


@dataclass(frozen=True)
class _Variant:
    short_description: tuple[_Edit, ...] = ()
    description: tuple[_Edit, ...] = ()
    parameters: tuple[tuple[str, tuple[_Edit, ...]], ...] = ()
    """``(parameter name, edits of its description)``."""
    method: str | None = None
    """The instance method that serves the variant, when it takes more arguments."""
    extra_parameters: tuple[ToolParameter, ...] = ()
    optional: tuple[str, ...] = ()
    """Parameters the variant no longer requires."""
    removed: tuple[str, ...] = ()
    """Parameters the variant leaves out."""
    args_summary: Callable[[dict[str, Any]], str | None] | None = None


_NAMED_ENTITY_IDS = _Edit(
    "department/category/subcategory/topic/language entities and record_group "
    "entities (a Drive folder, Jira project, Slack channel, ...; restricts to "
    "that group's records). Nothing",
    "department/category/subcategory/topic/language entities, record_group "
    "entities (a Drive folder, Jira project, Slack channel, ...; restricts to "
    "that group's records) and named entities (an organization, product, event, "
    "...; restricts to the records that mention it). Nothing",
)

_VARIANTS: dict[str, _Variant] = {
    SEARCH_TOOL_NAME: _Variant(parameters=(("entity_ids", (_NAMED_ENTITY_IDS,)),)),
    SEARCH_ENTITIES_TOOL_NAME: _Variant(
        description=(
            _Edit(
                "and record titles. Only entities backed by records you can "
                "access are returned.",
                "and record titles, and the organizations, products, events, places and other "
                "named things mentioned inside documents (entityType 'named_entity'; narrow with "
                "named_entity_kinds). People are found by name with "
                "knowledgegraph__search_by_entities (records that mention them) or "
                "knowledgegraph__find_records_by_person (records they are assigned, reported, "
                "created or lead). Only entities backed by records you can "
                "access are returned.",
            ),
            _Edit(
                "a project or space, a colleague, a customer, a document title — to find the matching entity, alongside or "
                "before a content search.",
                "a project or space, a colleague, a customer, a document title, or a product — to find the "
                "matching entity, alongside or before a content search. A named entity on a few "
                "records narrows the search well; one on most records narrows nothing.",
            ),
            _Edit(
                "department/category/subcategory/topic/language, record_group, person or organization entity.",
                "department/category/subcategory/topic/language, record_group, person, organization or named entity.",
            ),
        ),
        parameters=(
            ("entity_types", (_Edit("'record_group', 'person', 'organization', 'record'. Omit", "'record_group', 'person', 'organization', 'record', 'named_entity'. Omit"),)),
            ("named_entity_kinds", (_Edit(
                "Optional kinds when searching extracted named entities "
                "(person, organization, product, date, currency, ...). "
                "Ignored unless named-entity extraction is enabled.",
                "Optional kinds when searching extracted named entities: organization, product, "
                "event, skill, person_type, location, city, state, country_region. People and "
                "addresses are not in this search (look them up by name with "
                "knowledgegraph__search_by_entities), nor dates and amounts (use "
                "knowledgegraph__find_records_by_value).",
            ),)),
        ),
        method="_search_entities_with_queries",
        extra_parameters=(
            ToolParameter(
                name="queries",
                type=ParameterType.ARRAY,
                description=(
                    "Several names or descriptions to look up in one call (up to 5), instead of one "
                    "call each; the result groups the matches under each query."
                ),
                required=False,
                items={"type": "string"},
            ),
        ),
        optional=("query",),
        args_summary=lambda args: _searched_entities_for(args),
    ),
    FIND_CO_MENTIONED_TOOL_NAME: _Variant(
        description=(_Edit(
            "entity_ids search_entities returned for that one entity",
            "entity_ids search_entities or search_by_entities returned for that one entity",
        ),),
        parameters=(("entity_ids", (_Edit(
            "that search_entities returned", "that search_entities or search_by_entities returned",
        ),)),),
    ),
    FIND_RECORDS_BY_ENTITY_TOOL_NAME: _Variant(
        description=(
            _Edit(
                "every record directly inside a record_group, every record naming a person, every "
                "record of an organization's account or naming the organization, or the record itself",
                "every record directly inside a record_group, every record naming a person, every "
                "record of an organization's account or naming the organization, every record that "
                "mentions a named entity, or the record itself",
            ),
        ),
        parameters=(
            ("entity_type", (_Edit(
                "'language', 'record_group', 'person', 'organization' or 'record'. Optional",
                "'language', 'record_group', 'person', 'organization', 'record' or 'named_entity'. Optional",
            ),)),
        ),
    ),
}


# What the taxonomy arm's entity tools leave out: it never returns a person or
# an organisation, so naming them would send the model after nothing.
_TAXONOMY_VARIANTS: dict[str, _Variant] = {
    SEARCH_ENTITIES_TOOL_NAME: _Variant(
        short_description=(_Edit("record groups, people, organizations and record titles", "record groups and record titles"),),
        description=(
            _Edit(
                "folder, channel, ...), people (members of the organization named on records as "
                "author, sender, recipient, assignee, reporter, reviewer or owner), organizations "
                "(customer and prospect companies from CRM accounts, and companies or institutions "
                "named in two or more documents) and record titles.",
                "folder, channel, ...) and record titles.",
            ),
            _Edit("a project or space, a colleague, a customer, a document title", "a project or space, a document title"),
            _Edit("language/record_group/person/organization results", "language/record_group results"),
            _Edit(
                "department/category/subcategory/topic/language, record_group, person or organization entity.",
                "department/category/subcategory/topic/language or record_group entity.",
            ),
        ),
        parameters=(("entity_types", (_Edit("'record_group', 'person', 'organization', 'record'.", "'record_group', 'record'."),)),),
    ),
    FIND_RECORDS_BY_ENTITY_TOOL_NAME: _Variant(
        description=(
            _Edit(
                "every record directly inside a record_group, every record naming a person, every "
                "record of an organization's account or naming the organization, or the record",
                "every record directly inside a record_group, or the record",
            ),
        ),
        parameters=(("entity_type", (_Edit("'record_group', 'person', 'organization' or 'record'", "'record_group' or 'record'"),)),),
    ),
    FIND_RECORDS_TOOL_NAME: _Variant(
        description=(
            _Edit('{"person": {"role": role, "name": full name, email or "me"}}, {"organization": {"ids": [...]}}, ',
                  ""),
            _Edit(f"\nRoles: {roles_guide()}.", ""),
            _Edit("A person name matching several people lists them to pick by email. Get organization and entity "
                  "ids from search_entities.", "Get entity ids from search_entities."),
        ),
        parameters=(("group_by", (_Edit(', {"person_role": role}, {"by_person": true}, {"organization": true}', ""),)),),
    ),
}


# Without entity tools, nothing returns an entityId for these to take.
_OFF_VARIANTS: dict[str, _Variant] = {
    SEARCH_TOOL_NAME: _Variant(removed=("entity_ids",)),
    TIMELINE_TOOL_NAME: _Variant(
        description=(_Edit(
            " To keep to one channel, "
            "project or space, pass its record_group entityId from "
            "knowledgegraph__search_entities in entity_ids.",
            "",
        ),),
        removed=("entity_ids",),
    ),
}

_NO_NAMED_ENTITY_VARIANTS: dict[str, _Variant] = {
    SEARCH_ENTITIES_TOOL_NAME: _Variant(removed=("named_entity_kinds",)),
    FIND_RECORDS_TOOL_NAME: _Variant(description=(_Edit(MENTIONS_CONDITION_TEXT, ""),)),
}


# Listing tools find_records replaces when record query is on. Their names stay
# callable (``retired_tools``) for agents and plans written against them.
MERGED_INTO_FIND_RECORDS: frozenset[str] = frozenset({
    FIND_RECORDS_BY_ENTITY_TOOL_NAME, FIND_RECORDS_BY_PERSON_TOOL_NAME, FIND_RECORDS_BY_VALUE_TOOL_NAME,
})


def retired_tools(instance: Any, tools: list[Tool], policy: KnowledgeGraphPolicy) -> list[tuple[Tool, str]]:  # noqa: ANN401
    """With record query on, each merged tool under its old name and schema,
    served by a route to the equivalent find_records query, with the short
    name of the tool it was merged into; nothing otherwise."""
    if not policy.record_query:
        return []
    # Only what the request would have offered without record query stays callable.
    offered = MERGED_INTO_FIND_RECORDS - _withdrawn(replace(policy, record_query=False))
    out: list[tuple[Tool, str]] = []
    for tool in tools:
        meta = _declared_meta(instance, tool)
        if tool.name in offered and meta is not None:
            route = getattr(instance, f"_merged_{_method_name(tool)}")
            out.append((BoundMethodTool(route, meta), FIND_RECORDS_TOOL_NAME.split("__", 1)[-1]))
    return out


def tools_for_request(instance: Any, tools: list[Tool], policy: KnowledgeGraphPolicy) -> list[Tool]:  # noqa: ANN401
    """``tools`` as ``policy`` serves them: the tools it withdraws left out, the
    others with their text and parameters edited to match."""
    withdrawn = _withdrawn(policy)
    return [_served(instance, tool, policy) for tool in tools if tool.name not in withdrawn]


def _withdrawn(policy: KnowledgeGraphPolicy) -> frozenset[str]:
    if not policy.entity_tools:
        return ENTITY_TOOL_NAMES
    withdrawn = set() if policy.search_by_entities else {SEARCH_BY_ENTITIES_TOOL_NAME}
    # One listing tool per request: find_records takes the conditions of the three it replaces.
    withdrawn |= MERGED_INTO_FIND_RECORDS if policy.record_query else {FIND_RECORDS_TOOL_NAME}
    if not policy.named_entities:
        withdrawn |= _NAMED_ENTITY_ONLY_TOOLS
    if not policy.people_and_companies:
        withdrawn.add(FIND_RECORDS_BY_PERSON_TOOL_NAME)
    return frozenset(withdrawn)


def _served(instance: Any, tool: Tool, policy: KnowledgeGraphPolicy) -> Tool:  # noqa: ANN401
    declared = _declared_meta(instance, tool)
    if declared is None:
        return tool
    meta, method = declared, _method_name(tool)
    if policy.search_by_entities and tool.name in _VARIANTS:
        meta = variant_meta(tool.name, meta)
        method = _VARIANTS[tool.name].method or method
    for applies, variants in (
        (not policy.entity_tools, _OFF_VARIANTS),
        (not policy.people_and_companies, _TAXONOMY_VARIANTS),
        (not policy.named_entities, _NO_NAMED_ENTITY_VARIANTS),
    ):
        if applies and tool.name in variants:
            meta = _edited_meta(tool.name, meta, variants[tool.name])
    if policy.record_query:
        meta = _pointed_at_find_records(meta)
    return tool if meta is declared else BoundMethodTool(getattr(instance, method), meta)


# With record query on, other tools' text names find_records instead of the
# tools it replaced, which are no longer offered.
_TO_FIND_RECORDS = (
    ("knowledgegraph__find_records_by_entity(entity_id=...) lists the records connected to one entity, "
     "newest first, with paging.",
     'knowledgegraph__find_records(where=[{"entity": {"type": entityType, "id": entityId}}]) lists and '
     "counts the records connected to one entity."),
    ("list every record of one with find_records_by_entity.",
     'list and count every record of one with knowledgegraph__find_records(where=[{"entity": '
     '{"type": "named_entity", "id": entity_id}}]).'),
    ("knowledgegraph__find_records_by_person", "knowledgegraph__find_records"),
    ("knowledgegraph__find_records_by_value", "knowledgegraph__find_records (mentions)"),
    ("find_records_by_value", "knowledgegraph__find_records (mentions)"),
)


def _pointed_at_find_records(meta: ToolMeta) -> ToolMeta:
    def pointed(text: str) -> str:
        for old, new in _TO_FIND_RECORDS:
            text = text.replace(old, new)
        return text

    if not any(old in text for old, _ in _TO_FIND_RECORDS
               for text in (meta.description, *(p.description for p in meta.parameters))):
        return meta
    return replace(meta, description=pointed(meta.description),
                   parameters=[replace(p, description=pointed(p.description)) for p in meta.parameters])


def _declared_meta(instance: Any, tool: Tool) -> ToolMeta | None:  # noqa: ANN401
    return getattr(getattr(type(instance), _method_name(tool), None), TOOL_META_ATTR, None)


def _method_name(tool: Tool) -> str:
    return tool.name.split("__", 1)[-1]


def variant_meta(tool_name: str, declared: ToolMeta) -> ToolMeta:
    return _edited_meta(tool_name, declared, _VARIANTS[tool_name])


def _edited_meta(tool_name: str, declared: ToolMeta, variant: _Variant) -> ToolMeta:
    edits = dict(variant.parameters)
    parameters = [
        replace(
            parameter,
            description=_edited(parameter.description, edits.get(parameter.name, ()), f"{tool_name}.{parameter.name}"),
            required=parameter.required and parameter.name not in variant.optional,
        )
        for parameter in declared.parameters
        if parameter.name not in variant.removed
    ]
    missing = (set(edits) | set(variant.removed)) - {parameter.name for parameter in declared.parameters}
    if missing:
        raise ValueError(f"{tool_name}: no parameter {sorted(missing)} to edit")
    return replace(
        declared,
        short_description=_edited(declared.short_description, variant.short_description, tool_name),
        description=_edited(declared.description, variant.description, tool_name),
        parameters=[*parameters, *variant.extra_parameters],
        args_summary=variant.args_summary or declared.args_summary,
    )


def _searched_entities_for(args: dict[str, Any]) -> str | None:
    asked = [q for q in [args.get("query"), *(args.get("queries") or [])] if isinstance(q, str) and q.strip()]
    return "Searched entities for " + ", ".join(f'"{q}"' for q in dict.fromkeys(asked)) if asked else None


def _edited(text: str, edits: tuple[_Edit, ...], where: str) -> str:
    for edit in edits:
        if text.count(edit.old) != 1:
            raise ValueError(f"{where}: the declared text no longer reads {edit.old!r}")
        text = text.replace(edit.old, edit.new, 1)
    return text


__all__ = ["ENTITY_TOOL_NAMES", "MERGED_INTO_FIND_RECORDS", "retired_tools", "tools_for_request", "variant_meta"]
