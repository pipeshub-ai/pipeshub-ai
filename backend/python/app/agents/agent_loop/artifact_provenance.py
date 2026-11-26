"""The provenance of what a request's tools make (SS0, P3.5).

A viewer of a shared conversation sees an artifact only when they may read
every record it was derived from (`viewer_access`). An artifact a tool makes
from the model's own work (a `run_code` output, a saved or updated artifact,
a generated image, a legacy code toolset's file) can draw on whatever reached
the model before the call and, for `run_code`, on the files staged into its
sandbox, so its sources are:

- every record the request's tools showed the model, labelled from the
  request's citation state exactly as a durable run's journal is
  (`labels.citation_sources` over `CitationJournal`'s view);
- the sources of every registry artifact staged into a sandbox this request,
  recorded per version.

Anything nobody can name leaves them unknown, which hides the artifact from
everyone but its owner: a tool whose results carry content from no record
(`labels.tool_kind` "opaque": connector actions, MCP, web fetches, artifact
reads), a live (unindexed) file, the run's workspace files, or a staged
artifact whose own provenance is unknown. History from earlier turns is
not a source: every participant already sees those answers.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from app.harness.citations import CitationJournal
from app.harness.labels import citation_sources, tool_kind
from app.models.citation_source import LIVE_SOURCE_KIND
from app.services.artifact_registry.models import ArtifactProvenance, ArtifactVersionPin
from harness_core.artifacts.types import union_sources

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = [
    "SANDBOX_TOOL_NAMES",
    "note_staged_inputs",
    "note_unlabelled_workspace",
    "record_output_provenance",
    "request_provenance",
    "request_sources",
    "run_code_provenance",
]

# The sandbox's own tools: what they return comes from the code and the staged
# inputs, whose sources are accounted for here.
SANDBOX_TOOL_NAMES = frozenset({"run_code", "install_packages", "read_sandbox_file"})
# A composed delegate's own calls write the same citation state and tool list.
_DELEGATES = frozenset({"internal_exploration_agent"})
# Tools whose results bring the model nothing beyond what it already had: the legacy code
# toolsets run the model's own code offline (SQLite holds only the model's SQL; PostgreSQL,
# an operator's database, is not listed), and the artifact writers answer with an id and a status.
_NO_NEW_CONTENT = frozenset(
    f"{app}{sep}{tool}"
    for app, tool in (
        ("coding_sandbox", "execute_python"),
        ("coding_sandbox", "execute_typescript"),
        ("database_sandbox", "execute_sqlite"),
        ("artifacts", "save_artifact"),
        ("artifacts", "update_artifact"),
        ("artifacts", "promote_artifact"),
        ("artifacts", "list_artifacts"),
        ("artifacts", "get_artifact_download_url"),
        ("image_generator", "generate_image"),
    )
    for sep in ("__", "_")
)
_PINS_KEY = "artifact_provenance_staged_inputs"
_UNLABELLED_KEY = "artifact_provenance_unlabelled"


def note_staged_inputs(context: AgentContext, resolved: Iterable[dict[str, Any]]) -> None:
    """Remember the registry artifact versions staged into a sandbox on this request.

    In-memory refs (version 0) are this request's own tool results, already
    accounted for by the tool list."""
    pins: set[tuple[str, int]] = context.tool_state.setdefault(_PINS_KEY, set())
    for entry in resolved:
        version = entry.get("version")
        artifact_id = entry.get("artifact_id")
        if isinstance(version, int) and version > 0 and isinstance(artifact_id, str) and artifact_id:
            pins.add((artifact_id, version))


def note_unlabelled_workspace(context: AgentContext) -> None:
    """The run's workspace files were staged: their provenance is not recorded anywhere."""
    context.tool_state[_UNLABELLED_KEY] = True


def _state_of(holder: AgentContext | Mapping[str, Any]) -> Mapping[str, Any]:
    """The request's tool state: an `AgentContext`'s, or the chat state an action toolset holds (the same dict)."""
    return holder if isinstance(holder, Mapping) else holder.tool_state


class _StateView:
    """What `CitationJournal` reads: the tool state only."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        self.tool_state = state


def request_sources(holder: AgentContext | Mapping[str, Any]) -> frozenset[str] | None:
    """The records this request showed the model; `None` when something it saw names no record."""
    state = _state_of(holder)
    if state.get(_UNLABELLED_KEY):
        return None
    calls = state.get("all_tool_results")
    if not isinstance(calls, list):
        return None
    for call in calls:
        name = str(call.get("tool_name") or "") if isinstance(call, dict) else ""
        if name in SANDBOX_TOOL_NAMES or name in _DELEGATES or name in _NO_NEW_CONTENT:
            continue
        if not name or tool_kind(name) == "opaque":
            return None
    label = citation_sources(CitationJournal(_StateView(state)).delta() or {})
    if not label.complete or any(source.kind == LIVE_SOURCE_KIND for source in label.sources):
        return None
    return frozenset(source.record_id for source in label.sources)


async def run_code_provenance(
    context: AgentContext, registry: Any, *, task_id: str, command: str | None = None,  # noqa: ANN401 - ArtifactRegistryService
) -> ArtifactProvenance:
    """One entry for every version this call registers (`version` is set by `record_derivation`)."""
    pins = sorted(context.tool_state.get(_PINS_KEY) or set())
    sources = request_sources(context)
    if sources is not None and pins:
        try:
            found = await registry.lineage.sources_of(context.org_id, [aid for aid, _ in pins])
        except Exception:
            logger.warning("artifacts.provenance_inputs_unreadable count=%d", len(pins), exc_info=True)
            sources = None
        else:
            sources = union_sources(sources, *(found.get(aid) for aid, _ in pins))
    logger.info(
        "artifacts.run_code_provenance taskId=%s inputs=%d sources=%s",
        task_id, len(pins), "unknown" if sources is None else len(sources),
    )
    return ArtifactProvenance(
        version=0,
        run_id=context.durable_run_id or context.run_id or context.conversation_id or "",
        task_id=task_id,
        command=command,
        input_artifacts=[ArtifactVersionPin(artifact_id=aid, version=version) for aid, version in pins],
        source_record_ids=sorted(sources) if sources is not None else None,
    )


def request_provenance(
    holder: AgentContext | Mapping[str, Any], *, task_id: str, command: str | None = None,
) -> ArtifactProvenance:
    """Provenance of an artifact a tool makes from the request alone (no staged inputs).

    Taken when the tool is called, not when a background upload lands, so a later
    call of the same request neither widens nor narrows it."""
    state = _state_of(holder)
    sources = request_sources(state)
    logger.info(
        "artifacts.request_provenance taskId=%s sources=%s",
        task_id, "unknown" if sources is None else len(sources),
    )
    return ArtifactProvenance(
        version=0,
        run_id=str(state.get("harness_run_id") or state.get("conversation_id") or ""),
        task_id=task_id,
        command=command,
        source_record_ids=sorted(sources) if sources is not None else None,
    )


async def record_output_provenance(
    registry: Any,  # noqa: ANN401 - ArtifactRegistryService
    artifact_id: str | None,
    version: int | None,
    provenance: ArtifactProvenance | None,
) -> None:
    """Store `provenance` for one version. A failure leaves that version without an
    entry, which reads as unknown (hidden from viewers), never as readable."""
    if provenance is None or not artifact_id or not version:
        return
    try:
        await registry.record_derivation(
            output_artifact_id=artifact_id, output_version=version, provenance=provenance,
        )
    except Exception:
        logger.warning(
            "artifacts.provenance_unrecorded artifactId=%s version=%s", artifact_id, version, exc_info=True,
        )
