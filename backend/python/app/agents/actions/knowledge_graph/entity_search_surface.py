"""The knowledge-graph tools one request sees, from its ``KnowledgeGraphPolicy``
(``agent_loop/knowledge_graph_policy``).

- ``off``: no entity tools, and ``search`` without ``entity_ids``, which only
  takes ids those tools return.
- ``taxonomy``: the entity tools with people and organisations edited out of
  their descriptions, since the arm never returns them.
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
# Tools that read only named entities: left out with extraction off, where they could only fail.
_NAMED_ENTITY_ONLY_TOOLS = frozenset({FIND_RECORDS_BY_VALUE_TOOL_NAME, FIND_CO_MENTIONED_TOOL_NAME})
ENTITY_TOOL_NAMES: frozenset[str] = frozenset({
    SEARCH_ENTITIES_TOOL_NAME,
    FIND_RECORDS_BY_ENTITY_TOOL_NAME,
    SEARCH_BY_ENTITIES_TOOL_NAME,
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
}


def tools_for_request(instance: Any, tools: list[Tool], policy: KnowledgeGraphPolicy) -> list[Tool]:  # noqa: ANN401
    """``tools`` as ``policy`` serves them: the tools it withdraws left out, the
    others with their text and parameters edited to match."""
    withdrawn = _withdrawn(policy)
    return [_served(instance, tool, policy) for tool in tools if tool.name not in withdrawn]


def _withdrawn(policy: KnowledgeGraphPolicy) -> frozenset[str]:
    if not policy.entity_tools:
        return ENTITY_TOOL_NAMES
    withdrawn = set() if policy.search_by_entities else {SEARCH_BY_ENTITIES_TOOL_NAME}
    if not policy.named_entities:
        withdrawn |= _NAMED_ENTITY_ONLY_TOOLS
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
    return tool if meta is declared else BoundMethodTool(getattr(instance, method), meta)


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


__all__ = ["ENTITY_TOOL_NAMES", "tools_for_request", "variant_meta"]
