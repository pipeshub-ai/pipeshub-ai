"""ArtifactManager toolset — direct save/update/list/download-link access to
the versioned artifact registry (``app/services/artifact_registry/``), for
content the MODEL composes directly (a report, a data table, JSON/CSV/
markdown text it wrote itself) rather than a file another tool already
produced on disk. Files `run_code` writes and images `generate_image`
creates are captured automatically by their own pipelines
(`app/agents/agent_loop/sandbox_bridge.py`, `image_generator.py`) — this
toolset exists for everything else that still deserves to be a durable,
versioned, downloadable artifact instead of dead prose in the chat
transcript.

Internal, always-on toolset (like Calculator/ImageGenerator, no
authentication required) — every operation still goes through
`ArtifactRegistryService`'s permission checks regardless of how the tool
itself is exposed, so there is no privilege gap versus the sandbox path.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER, LEGACY_FORMATTER, ArtifactSSEPayload
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.qna.chat_state import ChatState
from app.sandbox.artifact_upload import infer_artifact_type
from app.services.artifact_registry import Actor, ArtifactMetadata, ArtifactVisibility, VersionConflictError
from app.services.artifact_registry.access import AccessDeniedError, ArtifactNotFoundError
from app.utils.conversation_tasks import register_task

logger = logging.getLogger(__name__)


def _result(success: bool, payload: dict[str, Any]) -> tuple[bool, str]:
    return success, json.dumps(payload, default=str)


@ToolsetBuilder("Artifact Manager")\
    .in_group("Internal Tools")\
    .with_description("Save, update, list, and share versioned artifacts generated in this conversation - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .as_essential()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/artifact.svg"))\
    .build_decorator()
class ArtifactManager:
    """Direct artifact save/update/list/download-link tools, backed by
    `app.services.artifact_registry.ArtifactRegistryService`."""

    def __init__(self, state: ChatState) -> None:
        self.chat_state = state

    def _registry(self) -> Any:
        graph_provider = self.chat_state.get("graph_provider")
        blob_store = self.chat_state.get("blob_store")
        if graph_provider is None or blob_store is None:
            return None
        from app.services.artifact_registry import ArtifactRegistryService
        return ArtifactRegistryService(graph_provider, blob_store)

    def _actor(self) -> Actor:
        return Actor(org_id=self.chat_state.get("org_id", ""), user_id=self.chat_state.get("user_id", ""))

    def _decode(self, content: str, is_base64: bool) -> bytes | str:
        try:
            return base64.b64decode(content) if is_base64 else content.encode("utf-8")
        except Exception as e:
            return f"__error__:Invalid base64 content: {e}"

    @tool(
        path="/tools/artifacts/save_artifact",
        short_description="Save text/data content you composed as a new versioned artifact, or update an existing one with the same name",
        description=(
            "Save content YOU composed directly (a written report, a data table, generated "
            "JSON/CSV/markdown text) as a durable, versioned artifact the user can download. Use "
            "this for content you authored yourself in this turn — NOT for files run_code already "
            "wrote to disk (captured automatically) or images from generate_image (also automatic). "
            "If an artifact with this exact `name` already exists in this conversation, this call "
            "bumps its version instead of creating a duplicate — reuse the SAME name across turns "
            "to keep updating one artifact rather than creating a new one each time. Returns "
            "`artifact_id`; keep it to pass into run_code's `input_artifacts`, or into "
            "update_artifact/get_artifact_download_url later. Set visibility='STAGING' to save an "
            "intermediate artifact to blob storage without surfacing it to the user — call "
            "promote_artifact(artifact_id) later when the final result is ready to be shown."
        ),
        parameters=[
            ToolParameter(
                name="name", type=ParameterType.STRING, required=True,
                description="File name including extension, e.g. 'quarterly_report.md'. Stable across versions — reuse it to update the same artifact.",
            ),
            ToolParameter(
                name="content", type=ParameterType.STRING, required=True,
                description="The artifact's full content, as UTF-8 text (or base64 when is_base64=true).",
            ),
            ToolParameter(
                name="mime_type", type=ParameterType.STRING, required=False, default="text/plain",
                description="MIME type, e.g. 'text/markdown', 'application/json', 'text/csv'.",
            ),
            ToolParameter(
                name="description", type=ParameterType.STRING, required=False, default="",
                description="One-sentence description of what this artifact contains.",
            ),
            ToolParameter(
                name="is_base64", type=ParameterType.BOOLEAN, required=False, default=False,
                description="Set true when `content` is base64-encoded binary data.",
            ),
            ToolParameter(
                name="visibility", type=ParameterType.STRING, required=False, default="VISIBLE",
                description=(
                    "Controls whether the artifact is shown to the user. "
                    "'VISIBLE' (default) — artifact appears in the response as a download card. "
                    "'STAGING' — artifact is saved durably but hidden from the user; use for intermediate pipeline files. "
                    "Call promote_artifact(artifact_id) to make a STAGING artifact visible."
                ),
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def save_artifact(
        self,
        name: str,
        content: str,
        mime_type: str = "text/plain",
        description: str = "",
        is_base64: bool = False,
        visibility: str = "VISIBLE",
    ) -> tuple[bool, str]:
        registry = self._registry()
        conversation_id = self.chat_state.get("conversation_id")
        if registry is None or not conversation_id:
            return _result(False, {"success": False, "error": "Artifact storage is unavailable in this context"})

        raw = self._decode(content, is_base64)
        if isinstance(raw, str):
            return _result(False, {"success": False, "error": raw.removeprefix("__error__:")})

        try:
            vis = ArtifactVisibility(visibility.upper())
        except ValueError:
            return _result(False, {"success": False, "error": f"Invalid visibility {visibility!r}; must be 'VISIBLE' or 'STAGING'"})

        try:
            metadata, version = await registry.register_output(
                actor=self._actor(),
                name=name,
                artifact_type=infer_artifact_type(mime_type),
                mime_type=mime_type,
                content=raw,
                conversation_id=conversation_id,
                description=description,
                visibility=vis,
                source_tool="artifacts.save_artifact",
            )
        except ValueError as e:
            return _result(False, {"success": False, "error": str(e)})
        except Exception:
            logger.exception("[save_artifact] failed for %s", name)
            return _result(False, {"success": False, "error": "Failed to save artifact"})

        self._schedule_marker(conversation_id, metadata)
        # Use metadata.visibility (the ACTUAL state in the registry) for
        # the response message, not the caller's `vis` parameter — when
        # bumping an existing artifact, the doc's visibility wins and the
        # message should reflect reality.
        actual_vis = metadata.visibility
        if actual_vis == ArtifactVisibility.STAGING:
            message = (
                f"Saved as staging artifact (artifact_id={metadata.artifact_id}). "
                "It is stored durably but NOT shown to the user. Call promote_artifact(artifact_id) "
                "when you are ready to surface it."
            )
        else:
            message = (
                "Saved. The file is attached to this response automatically as an artifact — "
                "do NOT include its raw content or a download link in your reply; just briefly "
                f"confirm it was saved (artifact_id={metadata.artifact_id})."
            )
        return _result(True, {
            "success": True,
            "artifact_id": metadata.artifact_id,
            "name": metadata.name,
            "version": metadata.version,
            "visibility": metadata.visibility.value,
            "deduplicated": bool(version and version.deduplicated),
            "message": message,
        })

    @tool(
        path="/tools/artifacts/update_artifact",
        short_description="Update an existing artifact (by artifact_id) with new content, creating a new version",
        description=(
            "Update an existing artifact's content, bumping its version. Use this when you have an "
            "`artifact_id` (from save_artifact, list_artifacts, or a run_code result's `artifacts` "
            "block) and want to REPLACE its content rather than create a new, disconnected file. "
            "Pass `expected_version` (from the artifact's last known version) to guard against "
            "clobbering a concurrent update — the call fails with a clear error instead of silently "
            "overwriting if the version has moved since you last read it."
        ),
        parameters=[
            ToolParameter(
                name="artifact_id", type=ParameterType.STRING, required=True,
                description="The artifact's ID to update.",
            ),
            ToolParameter(
                name="content", type=ParameterType.STRING, required=True,
                description="The artifact's new full content (replaces the previous version entirely), as UTF-8 text (or base64 when is_base64=true).",
            ),
            ToolParameter(
                name="mime_type", type=ParameterType.STRING, required=False, default=None,
                description="New MIME type, if it changed. Omit to keep the artifact's existing MIME type.",
            ),
            ToolParameter(
                name="is_base64", type=ParameterType.BOOLEAN, required=False, default=False,
                description="Set true when `content` is base64-encoded binary data.",
            ),
            ToolParameter(
                name="expected_version", type=ParameterType.INTEGER, required=False, default=None,
                description="The version you last saw for this artifact. If it no longer matches the current version, the update is rejected rather than silently overwritten.",
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def update_artifact(
        self,
        artifact_id: str,
        content: str,
        mime_type: str | None = None,
        is_base64: bool = False,
        expected_version: int | None = None,
    ) -> tuple[bool, str]:
        registry = self._registry()
        if registry is None:
            return _result(False, {"success": False, "error": "Artifact storage is unavailable in this context"})

        raw = self._decode(content, is_base64)
        if isinstance(raw, str):
            return _result(False, {"success": False, "error": raw.removeprefix("__error__:")})

        try:
            version, metadata = await registry.add_version(
                actor=self._actor(),
                artifact_id=artifact_id,
                content=raw,
                mime_type=mime_type,
                expected_version=expected_version,
            )
        except ArtifactNotFoundError:
            return _result(False, {"success": False, "error": f"No artifact found with id {artifact_id!r}"})
        except AccessDeniedError:
            return _result(False, {"success": False, "error": "You do not have permission to update this artifact"})
        except VersionConflictError as e:
            return _result(False, {"success": False, "error": str(e)})
        except ValueError as e:
            return _result(False, {"success": False, "error": str(e)})
        except Exception:
            logger.exception("[update_artifact] failed for %s", artifact_id)
            return _result(False, {"success": False, "error": "Failed to update artifact"})

        conversation_id = self.chat_state.get("conversation_id")
        if conversation_id:
            self._schedule_marker(conversation_id, metadata)
        if metadata.visibility == ArtifactVisibility.STAGING:
            message = (
                f"Updated staging artifact (artifact_id={metadata.artifact_id}, version={metadata.version}). "
                "It is stored durably but NOT shown to the user. Call promote_artifact(artifact_id) "
                "when you are ready to surface it."
            )
        else:
            message = (
                "Updated — the new version is attached to this response automatically as an "
                "artifact; do NOT include its raw content or a download link in your reply."
            )
        return _result(True, {
            "success": True,
            "artifact_id": metadata.artifact_id,
            "name": metadata.name,
            "version": metadata.version,
            "visibility": metadata.visibility.value,
            "deduplicated": version.deduplicated,
            "message": message,
        })

    @tool(
        path="/tools/artifacts/get_artifact_download_url",
        short_description="Get a short-lived, permission-checked download URL for an existing artifact",
        description=(
            "Get a short-lived download URL for an artifact you (or another tool) already created, "
            "by its `artifact_id`. Rarely needed — run_code/save_artifact/update_artifact already "
            "attach their output as a downloadable artifact automatically. Use this only when the "
            "user explicitly asks for a direct link, or another agent tool needs the URL for its "
            "own purposes. Do NOT use this to feed content into run_code — pass the artifact's name "
            "in run_code's `input_artifacts` parameter instead; the URL returned here is for "
            "external use only and is not injected into any sandbox."
        ),
        parameters=[
            ToolParameter(
                name="artifact_id", type=ParameterType.STRING, required=True,
                description="The artifact's ID.",
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def get_artifact_download_url(self, artifact_id: str) -> tuple[bool, str]:
        registry = self._registry()
        if registry is None:
            return _result(False, {"success": False, "error": "Artifact storage is unavailable in this context"})
        try:
            url = await registry.get_download_url(actor=self._actor(), artifact_id=artifact_id)
        except ArtifactNotFoundError:
            return _result(False, {"success": False, "error": f"No artifact found with id {artifact_id!r}"})
        except AccessDeniedError:
            return _result(False, {"success": False, "error": "You do not have permission to access this artifact"})
        except Exception:
            logger.exception("[get_artifact_download_url] failed for %s", artifact_id)
            return _result(False, {"success": False, "error": "Failed to get a download URL"})
        return _result(True, {
            "success": True,
            "artifact_id": artifact_id,
            "download_url": url,
            "note": "This link is short-lived and permission-scoped to this user; request a fresh one if it expires.",
        })

    @tool(
        path="/tools/artifacts/get_record_download_url",
        short_description="Get a download URL for a record (uploaded file or connector record)",
        description=(
            "Get a download URL for any PipesHub record by its record ID. "
            "For uploaded files (chat attachments, KB uploads), returns a short-lived signed "
            "download URL you can embed directly in artifacts. For connector records (Google "
            "Drive, Slack, etc.), returns the external source URL. Do NOT use this for "
            "artifacts you created — use get_artifact_download_url for those."
        ),
        parameters=[
            ToolParameter(
                name="record_id", type=ParameterType.STRING, required=True,
                description="The PipesHub record ID of the file.",
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def get_record_download_url(self, record_id: str) -> tuple[bool, str]:
        graph_provider = self.chat_state.get("graph_provider")
        blob_store = self.chat_state.get("blob_store")
        org_id = self.chat_state.get("org_id", "")
        if graph_provider is None or blob_store is None:
            return _result(False, {"success": False, "error": "Storage is unavailable in this context"})
        try:
            record = await graph_provider.get_record_by_id(record_id)
        except Exception:
            logger.exception("[get_record_download_url] lookup failed for %s", record_id)
            return _result(False, {"success": False, "error": "Failed to look up record"})
        if record is None:
            return _result(False, {"success": False, "error": f"No record found with id {record_id!r}"})
        if record.org_id != org_id:
            return _result(False, {"success": False, "error": "You do not have permission to access this record"})

        from app.config.constants.arangodb import OriginTypes

        if record.origin == OriginTypes.UPLOAD:
            if not record.external_record_id:
                return _result(False, {"success": False, "error": "This record has no downloadable content"})
            try:
                url = await blob_store.get_download_url(org_id, record.external_record_id)
            except Exception:
                logger.exception("[get_record_download_url] signed URL failed for %s", record_id)
                return _result(False, {"success": False, "error": "Failed to generate a download URL"})
            return _result(True, {
                "success": True,
                "record_id": record_id,
                "download_url": url,
                "file_name": record.record_name,
                "mime_type": record.mime_type,
                "url_type": "direct_download",
                "note": "Short-lived signed URL for an uploaded file. Can be embedded directly in artifacts.",
            })

        return _result(False, {
            "success": False,
            "error": (
                "This record is from an external connector — no embeddable download URL "
                "is available. To use this content in an artifact, use the text already "
                "provided in search results rather than trying to link to the source file."
            ),
            "source_url": record.weburl or None,
            "file_name": record.record_name,
            "hint": (
                "source_url is a link to the file in its source system (e.g. Google Drive). "
                "You may include it as a clickable reference link in an artifact, but do NOT "
                "use it as an image src or iframe src — it will not render."
            ),
        })

    @tool(
        path="/tools/artifacts/list_artifacts",
        short_description="List every artifact generated so far in this conversation",
        description=(
            "List every artifact (chart, document, code, spreadsheet, ...) generated so far in this "
            "conversation, with its `artifact_id`, name, type, and current version. Call this before "
            "regenerating something from scratch, or before passing a name into run_code's "
            "`input_artifacts`, to confirm the exact name/ID and avoid creating a disconnected "
            "duplicate of an artifact that already exists. `derived_from_code_artifact_id` (when "
            "present) is the CODE artifact that produced this one — pass ITS id/name into "
            "run_code's input_artifacts and re-run to regenerate this output from updated code."
        ),
        parameters=[],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def list_artifacts(self) -> tuple[bool, str]:
        registry = self._registry()
        conversation_id = self.chat_state.get("conversation_id")
        if registry is None or not conversation_id:
            return _result(False, {"success": False, "error": "Artifact storage is unavailable in this context"})
        try:
            artifacts: list[ArtifactMetadata] = await registry.list_for_conversation(
                actor=self._actor(), conversation_id=conversation_id,
            )
        except Exception:
            logger.exception("[list_artifacts] failed for conversation=%s", conversation_id)
            return _result(False, {"success": False, "error": "Failed to list artifacts"})

        return _result(True, {
            "success": True,
            "count": len(artifacts),
            "artifacts": [self._serialize_artifact(a) for a in artifacts],
        })

    @staticmethod
    def _serialize_artifact(a: "ArtifactMetadata") -> dict:
        entry: dict = {
            "artifact_id": a.artifact_id,
            "name": a.name,
            "artifact_type": a.artifact_type.value,
            "version": a.version,
            "mime_type": a.mime_type,
            "source_tool": a.source_tool,
            "visibility": a.visibility.value,
        }
        if a.description:
            entry["description"] = a.description
        if a.derived_from_code_artifact_id:
            entry["derived_from_code_artifact_id"] = a.derived_from_code_artifact_id
            entry["derived_from_code_version"] = a.derived_from_code_version
        return entry

    @tool(
        path="/tools/artifacts/promote_artifact",
        short_description="Promote a staging artifact to visible, surfacing it to the user as a download card",
        description=(
            "Make a STAGING artifact visible to the user. STAGING artifacts are saved to blob storage "
            "and the registry but hidden from the chat UI — call this when an intermediate artifact is "
            "ready to be presented as a final deliverable. Idempotent: calling on an already-VISIBLE "
            "artifact is a no-op. Returns the artifact's current metadata so you can confirm its "
            "name, version, and type before referencing it in your reply."
        ),
        parameters=[
            ToolParameter(
                name="artifact_id", type=ParameterType.STRING, required=True,
                description="The artifact_id returned by save_artifact or a run_code result block.",
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def promote_artifact(self, artifact_id: str) -> tuple[bool, str]:
        registry = self._registry()
        conversation_id = self.chat_state.get("conversation_id")
        if registry is None or not conversation_id:
            return _result(False, {"success": False, "error": "Artifact storage is unavailable in this context"})
        try:
            metadata = await registry.promote_to_visible(actor=self._actor(), artifact_id=artifact_id)
        except ArtifactNotFoundError:
            return _result(False, {"success": False, "error": f"No artifact found with id {artifact_id!r}"})
        except AccessDeniedError:
            return _result(False, {"success": False, "error": "You do not have permission to promote this artifact"})
        except Exception:
            logger.exception("[promote_artifact] failed for %s", artifact_id)
            return _result(False, {"success": False, "error": "Failed to promote artifact"})

        # Emit a live SSE artifact event when an event_sink is available in
        # the chat state (stored there by stream_bridge / bridge at context
        # construction time).  Falls back gracefully to the ::artifact marker
        # only path when streaming is not active (e.g. background tasks).
        await self._emit_promotion_sse(metadata)

        self._schedule_marker(conversation_id, metadata)
        return _result(True, {
            "success": True,
            "artifact_id": metadata.artifact_id,
            "name": metadata.name,
            "version": metadata.version,
            "visibility": metadata.visibility.value,
            "message": (
                f"Artifact {metadata.name!r} is now visible to the user as a download card. "
                "Do NOT include its raw content or a separate download link in your reply."
            ),
        })

    async def _emit_promotion_sse(self, metadata: ArtifactMetadata) -> None:
        """Emit a live SSE artifact event for a just-promoted artifact.

        The `event_sink` is available in `chat_state` when streaming is active
        (stored by `stream_bridge.py` / `bridge.py` at context construction).
        """
        event_sink = self.chat_state.get("event_sink")
        if event_sink is None:
            return
        registry = self._registry()
        actor = self._actor()
        try:
            download_url = await registry.get_download_url(actor=actor, artifact_id=metadata.artifact_id)
        except Exception:
            logger.warning(
                "[promote_artifact] Failed to obtain download URL for live SSE: %s",
                metadata.artifact_id, exc_info=True,
            )
            return

        protocol = self.chat_state.get("sse_protocol", "legacy")
        formatter = AGUI_FORMATTER if protocol == "agui" else LEGACY_FORMATTER

        # Build a minimal AgentContext-like object with only the fields the
        # formatter needs (run_id and conversation_id).
        class _MinCtx:
            run_id = self.chat_state.get("conversation_id", "")
            conversation_id = self.chat_state.get("conversation_id", "")

        artifact_data = ArtifactSSEPayload(
            artifactId=metadata.artifact_id,
            fileName=metadata.name,
            mimeType=metadata.mime_type,
            sizeBytes=metadata.size_bytes,
            downloadUrl=download_url,
            artifactType=metadata.artifact_type.value,
            isTemporary=metadata.is_temporary,
            recordId=metadata.artifact_id,
            version=metadata.version,
            derivedFromCodeArtifactId=metadata.derived_from_code_artifact_id,
            visibility=metadata.visibility.value,
        )
        try:
            for evt in formatter.artifact(_MinCtx(), artifact_data=artifact_data):
                await event_sink.write(evt)
        except Exception:
            logger.warning(
                "[promote_artifact] Failed to emit live SSE for %s", metadata.artifact_id, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Delivery — same `::artifact` marker mechanism image_generator.py's
    # `_schedule_artifact_upload` uses (no live SSE push from this
    # dict-`ChatState`-based action layer — that's only wired for the
    # agent_loop_lib-hook path, see `sandbox_bridge.py`).
    # ------------------------------------------------------------------

    def _schedule_marker(self, conversation_id: str, metadata: ArtifactMetadata) -> None:
        # STAGING artifacts are suppressed from user-visible delivery.
        if metadata.visibility == ArtifactVisibility.STAGING:
            logger.debug(
                "Staging artifact %s (%s) suppressed from ::artifact marker", metadata.artifact_id, metadata.name,
            )
            return
        # Deliver each (artifact_id, version) at most once per request — a
        # repeated save_artifact call with unchanged content dedupes to the
        # SAME version, and queuing a second marker for it would render a
        # duplicate download card in the UI.
        delivered: set = self.chat_state.setdefault("_delivered_artifact_versions", set())
        delivery_key = f"{metadata.artifact_id}:{metadata.version}"
        if delivery_key in delivered:
            return
        delivered.add(delivery_key)

        registry = self._registry()
        actor = self._actor()

        async def _resolve_and_mark() -> dict[str, Any] | None:
            try:
                download_url = await registry.get_download_url(actor=actor, artifact_id=metadata.artifact_id)
            except Exception:
                # Not fatal to delivery: the persisted marker keys on
                # `recordId` and carries `record:<id>` in the URL slot
                # (`streaming.py::_append_task_markers`), so the card still
                # downloads. Losing it here would drop the user's only
                # handle on a file the tool reports as saved.
                logger.warning(
                    "Failed to resolve download URL for artifact %s — delivering the "
                    "marker without one", metadata.artifact_id, exc_info=True,
                )
                download_url = ""
            return {"type": "artifacts", "artifacts": [{
                "documentId": metadata.document_id,
                "fileName": metadata.name,
                "mimeType": metadata.mime_type,
                "sizeBytes": metadata.size_bytes,
                "recordId": metadata.artifact_id,
                "downloadUrl": download_url,
                "artifactType": metadata.artifact_type.value,
                "version": metadata.version,
            }]}

        task = asyncio.create_task(_resolve_and_mark())
        register_task(conversation_id, task)


__all__ = ["ArtifactManager"]
