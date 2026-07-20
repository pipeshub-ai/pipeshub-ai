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
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.qna.chat_state import ChatState
from app.sandbox.artifact_upload import infer_artifact_type
from app.services.artifact_registry import Actor, ArtifactMetadata, VersionConflictError
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
            "update_artifact/get_artifact_download_url later."
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
    ) -> tuple[bool, str]:
        registry = self._registry()
        conversation_id = self.chat_state.get("conversation_id")
        if registry is None or not conversation_id:
            return _result(False, {"success": False, "error": "Artifact storage is unavailable in this context"})

        raw = self._decode(content, is_base64)
        if isinstance(raw, str):
            return _result(False, {"success": False, "error": raw.removeprefix("__error__:")})

        try:
            metadata, version = await registry.register_output(
                actor=self._actor(),
                name=name,
                artifact_type=infer_artifact_type(mime_type),
                mime_type=mime_type,
                content=raw,
                conversation_id=conversation_id,
                description=description,
                source_tool="artifacts.save_artifact",
            )
        except ValueError as e:
            return _result(False, {"success": False, "error": str(e)})
        except Exception:
            logger.exception("[save_artifact] failed for %s", name)
            return _result(False, {"success": False, "error": "Failed to save artifact"})

        self._schedule_marker(conversation_id, metadata)
        return _result(True, {
            "success": True,
            "artifact_id": metadata.artifact_id,
            "name": metadata.name,
            "version": metadata.version,
            "deduplicated": bool(version and version.deduplicated),
            "message": (
                "Saved. The file is attached to this response automatically as an artifact — "
                "do NOT include its raw content or a download link in your reply; just briefly "
                f"confirm it was saved (artifact_id={metadata.artifact_id})."
            ),
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
        return _result(True, {
            "success": True,
            "artifact_id": metadata.artifact_id,
            "name": metadata.name,
            "version": metadata.version,
            "deduplicated": version.deduplicated,
            "message": (
                "Updated — the new version is attached to this response automatically as an "
                "artifact; do NOT include its raw content or a download link in your reply."
            ),
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
            "artifacts": [
                {
                    "artifact_id": a.artifact_id,
                    "name": a.name,
                    "artifact_type": a.artifact_type.value,
                    "version": a.version,
                    "mime_type": a.mime_type,
                    "description": a.description,
                    "derived_from_code_artifact_id": a.derived_from_code_artifact_id,
                    "derived_from_code_version": a.derived_from_code_version,
                }
                for a in artifacts
            ],
        })

    # ------------------------------------------------------------------
    # Delivery — same `::artifact` marker mechanism image_generator.py's
    # `_schedule_artifact_upload` uses (no live SSE push from this
    # dict-`ChatState`-based action layer — that's only wired for the
    # agent_loop_lib-hook path, see `sandbox_bridge.py`).
    # ------------------------------------------------------------------

    def _schedule_marker(self, conversation_id: str, metadata: ArtifactMetadata) -> None:
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
                logger.warning("Failed to resolve download URL for artifact %s", metadata.artifact_id, exc_info=True)
                return None
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
