"""ImageGenerator toolset -- generative-AI image creation and editing.

Internal, always-on toolset (like Calculator) that calls a configured
image-generation provider (OpenAI gpt-image / DALL-E, or Gemini image /
Imagen) and delivers the result through the existing artifact pipeline
(blob upload + ArtifactRecord + ``::artifact`` stream marker).

Passing an existing image artifact's ``record_id`` switches the tool from
text-to-image generation to image *editing*: the existing artifact's bytes
are fetched from the artifact registry and sent to the provider's
image-edit API (``adapter.edit``) alongside the prompt, instead of
generating from scratch (``adapter.generate``). Providers without a native
edit API (e.g. OpenRouter) surface a clear error rather than silently
falling back to generation.

The tool is deliberately narrow: it is for *generative* imagery only. Cases
that can be solved by executing code (charts, plots, diagrams, documents)
are routed to ``coding_sandbox.execute_python/typescript`` via the
``when_not_to_use`` hints so the planner does not mis-select it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.modules.agents.qna.chat_state import ChatState
from app.sandbox.artifact_upload import upload_bytes_artifact
from app.utils.conversation_tasks import register_task
from app.utils.llm import get_image_generation_config

logger = logging.getLogger(__name__)


_SUPPORTED_SIZES = {
    "1024x1024",  # square
    "1024x1792",  # portrait
    "1792x1024",  # landscape
}


class GenerateImageInput(BaseModel):
    prompt: str = Field(
        ...,
        description=(
            "A detailed natural-language description of the image to create. "
            "The more specific the prompt, the better the result."
        ),
    )
    file_name: str = Field(
        ...,
        description=(
            "A short, descriptive, filesystem-safe base file name for the image, "
            "WITHOUT extension. Derive it from the subject of the user's request "
            "using snake_case (lowercase letters, digits, underscores; 2-40 chars). "
            "Examples: 'mona_lisa', 'coffee_shop_logo', 'cat_with_sunglasses', "
            "'futuristic_city_skyline'. Do NOT use the model name, timestamps, "
            "or random IDs. If the user did not specify a name, invent a concise "
            "one that summarises the subject of the image."
        ),
    )
    size: str = Field(
        default="1024x1024",
        description=(
            "Image dimensions. One of 1024x1024 (square), 1024x1792 (portrait), "
            "or 1792x1024 (landscape)."
        ),
    )
    n: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Number of images to generate (1-4).",
    )
    record_id: Optional[str] = Field(
        default=None,
        description=(
            "The artifact record_id of an EXISTING image (from a prior "
            "generate_image call, or list_artifacts) to edit/update, instead "
            "of generating a brand-new image. When provided, the existing "
            "image's content is fetched and sent to the image model along "
            "with `prompt` describing the desired change. Omit this to "
            "generate a new image from scratch."
        ),
    )


@ToolsetBuilder("Image Generator")\
    .in_group("Internal Tools")\
    .with_description("Generative-AI image creation from a text prompt - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/image.svg"))\
    .build_decorator()
class ImageGenerator:
    """Image generation tool exposed to agents."""

    def __init__(self, state: ChatState) -> None:
        self.chat_state = state

    def _result(self, success: bool, payload: dict[str, Any]) -> tuple[bool, str]:
        return success, json.dumps(payload, default=str)

    @tool(
        path="/tools/image_generator/generate_image",
        short_description="Generate a brand-new image, or edit an existing one by record_id, from a natural-language prompt",
        description=(
            "Generate a brand-new image from a natural-language prompt, OR edit/update an "
            "EXISTING image artifact by passing its `record_id`, using a generative AI model "
            "(OpenAI gpt-image / DALL-E, or Gemini image / Imagen). "
            "Use ONLY for creative imagery: illustrations, concept art, photorealistic "
            "scenes, logos, mockups, stylised art. "
            "DO NOT use for anything that a coding sandbox can produce (charts, plots, "
            "graphs, diagrams, tables, screenshots of data, PDFs from text, SVG from "
            "data) -- use coding_sandbox.execute_python or coding_sandbox.execute_typescript "
            "for those instead. "
            "To UPDATE a previously generated image (e.g. 'make the sky orange', 'add a hat "
            "to the cat'), pass that image's `record_id` (from a prior generate_image result "
            "or list_artifacts) along with a `prompt` describing the change -- do NOT try to "
            "describe the whole scene from scratch. Omit `record_id` to generate a new image. "
            "Not every provider supports editing; if the configured model doesn't, you will "
            "get a clear error instead of a silently regenerated image. "
            "Always pass a descriptive snake_case `file_name` derived from the "
            "subject of the user's prompt (e.g. 'mona_lisa' for a Mona Lisa request, "
            "'coffee_shop_logo' for a coffee-shop logo). Never use the model name "
            "or random IDs as the file name. "
            "The generated image is attached to the response as an artifact; the text "
            "result just acknowledges success and carries metadata."
        ),
        parameters=[
            ToolParameter(
                name="prompt",
                type=ParameterType.STRING,
                description=(
                    "A detailed natural-language description of the image to create, or "
                    "(when `record_id` is set) of the change to make to the existing image. "
                    "The more specific the prompt, the better the result."
                ),
                required=True,
            ),
            ToolParameter(
                name="file_name",
                type=ParameterType.STRING,
                description=(
                    "A short, descriptive, filesystem-safe base file name for the image, "
                    "WITHOUT extension. Derive it from the subject of the user's request "
                    "using snake_case (lowercase letters, digits, underscores; 2-40 chars). "
                    "Examples: 'mona_lisa', 'coffee_shop_logo', 'cat_with_sunglasses', "
                    "'futuristic_city_skyline'. Do NOT use the model name, timestamps, "
                    "or random IDs. If the user did not specify a name, invent a concise "
                    "one that summarises the subject of the image."
                ),
                required=True,
            ),
            ToolParameter(
                name="size",
                type=ParameterType.STRING,
                description=(
                    "Image dimensions. One of 1024x1024 (square), 1024x1792 (portrait), "
                    "or 1792x1024 (landscape)."
                ),
                required=False,
                default="1024x1024",
                enum=["1024x1024", "1024x1792", "1792x1024"],
            ),
            ToolParameter(
                name="n",
                type=ParameterType.INTEGER,
                description="Number of images to generate (1-4).",
                required=False,
                default=1,
            ),
            ToolParameter(
                name="record_id",
                type=ParameterType.STRING,
                description=(
                    "The artifact record_id of an EXISTING image to edit/update, instead of "
                    "generating a brand-new one. Get this from a prior generate_image result "
                    "or list_artifacts. Omit to generate a new image from scratch."
                ),
                required=False,
                default=None,
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="action")],
    )
    async def generate_image(
        self,
        prompt: str,
        file_name: str = "",
        size: str = "1024x1024",
        n: int = 1,
        record_id: str | None = None,
    ) -> tuple[bool, str]:
        """Generate one or more images from ``prompt``, or edit the existing
        artifact ``record_id`` per ``prompt``, and attach the result as
        artifacts."""
        if not prompt or not prompt.strip():
            return self._result(False, {
                "success": False,
                "error": "Prompt is required and cannot be empty",
            })

        if size not in _SUPPORTED_SIZES:
            logger.warning(
                "[generate_image] unsupported size %r, falling back to 1024x1024", size,
            )
            size = "1024x1024"
        n = max(1, min(4, int(n or 1)))

        config_service = self.chat_state.get("config_service")
        conversation_id = self.chat_state.get("conversation_id")
        org_id = self.chat_state.get("org_id")
        user_id = self.chat_state.get("user_id")
        blob_store = self.chat_state.get("blob_store")
        graph_provider = self.chat_state.get("graph_provider")

        if not config_service:
            return self._result(False, {
                "success": False,
                "error": "Internal error: config_service unavailable in chat state",
            })

        try:
            image_config = await get_image_generation_config(config_service)
        except Exception as e:
            logger.exception("[generate_image] failed to load image generation config")
            return self._result(False, {
                "success": False,
                "error": f"Failed to load image generation config: {e}",
            })

        if not image_config:
            return self._result(False, {
                "success": False,
                "error": (
                    "No image-generation model is configured. Add one under "
                    "Workspace -> AI Models -> Image Generation."
                ),
            })

        provider = image_config.get("provider")
        try:
            # Local import to avoid loading provider SDKs at module import time.
            from app.utils.aimodels import get_image_generation_model

            adapter = get_image_generation_model(provider, image_config)
        except Exception as e:
            logger.exception(
                "[generate_image] failed to build image generation adapter",
            )
            return self._result(False, {
                "success": False,
                "error": f"Failed to initialise {provider} image adapter: {e}",
            })

        is_edit = bool(record_id and record_id.strip())
        source_image: bytes | None = None
        if is_edit:
            source_image, error_response = await self._fetch_edit_source_image(
                record_id=record_id,
                graph_provider=graph_provider,
                blob_store=blob_store,
                org_id=org_id,
                user_id=user_id,
            )
            if error_response is not None:
                return error_response

        logger.info(
            "[generate_image] %s provider=%s model=%s size=%s n=%d prompt_len=%d "
            "record_id=%s",
            "editing" if is_edit else "generating",
            adapter.provider, adapter.model, size, n, len(prompt), record_id,
        )

        try:
            if is_edit:
                images = await adapter.edit(
                    prompt, input_image=source_image, size=size, n=n,
                )
            else:
                images = await adapter.generate(prompt, size=size, n=n)
        except NotImplementedError as e:
            logger.warning(
                "[generate_image] provider=%s does not support image editing: %s",
                adapter.provider, e,
            )
            return self._result(False, {
                "success": False,
                "provider": adapter.provider,
                "model": adapter.model,
                "error": str(e),
            })
        except Exception as e:
            logger.exception("[generate_image] provider call failed")
            return self._result(False, {
                "success": False,
                "provider": adapter.provider,
                "model": adapter.model,
                "error": f"Image {'editing' if is_edit else 'generation'} failed: {e}",
            })

        if not images:
            return self._result(False, {
                "success": False,
                "provider": adapter.provider,
                "model": adapter.model,
                "error": "Provider returned no images",
            })

        # Schedule the upload as a background conversation task so the
        # artifact markers are appended by ``_append_task_markers`` when the
        # streaming response completes (same pattern as coding_sandbox).
        self._schedule_artifact_upload(
            images=images,
            blob_store=blob_store,
            graph_provider=graph_provider,
            org_id=org_id,
            conversation_id=conversation_id,
            user_id=user_id,
            model_name=adapter.model,
            file_name_hint=file_name,
            edit_record_id=record_id if is_edit else None,
        )

        action_word = "Updated" if is_edit else "Generated"
        return self._result(True, {
            "success": True,
            "message": (
                f"{action_word} {len(images)} image(s). The image file(s) are "
                "attached to this response automatically as artifacts — the "
                "UI renders them from the ::artifact marker that is appended "
                "after the message. Do NOT include markdown images, links, "
                "or base64 data in your reply; just briefly confirm the image "
                f"was {'updated' if is_edit else 'generated'}."
            ),
            "provider": adapter.provider,
            "model": adapter.model,
            "size": size,
            "count": len(images),
            "file_name": _sanitize_file_stem(file_name),
            "action": "edit" if is_edit else "generate",
            "source_record_id": record_id if is_edit else None,
        })

    # ------------------------------------------------------------------
    # Edit source resolution
    # ------------------------------------------------------------------

    async def _fetch_edit_source_image(
        self,
        *,
        record_id: str,
        graph_provider: Any,
        blob_store: Any,
        org_id: str | None,
        user_id: str | None,
    ) -> tuple[Optional[bytes], Optional[tuple[bool, str]]]:
        """Resolve ``record_id`` to raw image bytes via the artifact registry.

        Returns ``(image_bytes, None)`` on success, or ``(None, error_result)``
        where ``error_result`` is the ``(bool, str)`` tuple the tool should
        return immediately.
        """
        if graph_provider is None or blob_store is None:
            return None, self._result(False, {
                "success": False,
                "error": "Artifact storage is unavailable in this context — cannot fetch the source image to edit",
            })
        if not org_id or not user_id:
            return None, self._result(False, {
                "success": False,
                "error": "Internal error: org_id/user_id unavailable in chat state",
            })

        from app.services.artifact_registry import Actor, ArtifactRegistryService
        from app.services.artifact_registry.access import (
            AccessDeniedError,
            ArtifactNotFoundError,
        )

        registry = ArtifactRegistryService(graph_provider, blob_store)
        actor = Actor(org_id=org_id, user_id=user_id)
        try:
            image_bytes = await registry.get_content(actor=actor, artifact_id=record_id)
        except ArtifactNotFoundError:
            return None, self._result(False, {
                "success": False,
                "error": f"No artifact found with record_id {record_id!r}",
            })
        except AccessDeniedError:
            return None, self._result(False, {
                "success": False,
                "error": "You do not have permission to access this artifact",
            })
        except Exception as e:
            logger.exception(
                "[generate_image] failed to fetch source image for record_id=%s",
                record_id,
            )
            return None, self._result(False, {
                "success": False,
                "error": f"Failed to fetch the source image: {e}",
            })

        if not image_bytes:
            return None, self._result(False, {
                "success": False,
                "error": f"Artifact {record_id!r} has no content to edit",
            })
        return image_bytes, None

    async def _add_version_to_artifact(
        self,
        *,
        artifact_id: str,
        image_bytes: bytes,
        blob_store: Any,
        graph_provider: Any,
        org_id: str | None,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        """Bump ``artifact_id`` directly to a new version holding the edited
        image, instead of matching/creating by logical name — this is what
        makes editing update the SAME artifact the caller pointed at (see
        ``ArtifactManager.update_artifact`` for the equivalent direct-update
        pattern used by ``save_artifact``/``update_artifact``).

        Returns the same upload-info shape ``upload_bytes_artifact`` does
        (``documentId``, ``fileName``, ``mimeType``, ``sizeBytes``,
        ``recordId``, ``downloadUrl``, ``version``, ``artifactType``), or
        ``None`` on failure so the caller's existing "skip this entry"
        handling applies unchanged.
        """
        from app.services.artifact_registry import Actor, ArtifactRegistryService

        registry = ArtifactRegistryService(graph_provider, blob_store)
        actor = Actor(org_id=org_id, user_id=user_id)
        try:
            version, metadata = await registry.add_version(
                actor=actor,
                artifact_id=artifact_id,
                content=image_bytes,
                mime_type="image/png",
            )
        except Exception:
            logger.exception(
                "[generate_image] failed to add new version to artifact %s",
                artifact_id,
            )
            return None

        entry: dict[str, Any] = {
            "documentId": metadata.document_id,
            "fileName": metadata.name,
            "mimeType": metadata.mime_type,
            "sizeBytes": metadata.size_bytes,
            "recordId": metadata.artifact_id,
            "version": metadata.version,
            "artifactType": metadata.artifact_type.value,
        }
        if version.deduplicated:
            entry["deduplicated"] = True
        try:
            entry["downloadUrl"] = await registry.get_download_url(
                actor=actor, artifact_id=metadata.artifact_id,
            )
        except Exception:
            logger.warning(
                "[generate_image] failed to obtain download URL for edited artifact %s",
                artifact_id, exc_info=True,
            )
        return entry

    # ------------------------------------------------------------------
    # Artifact upload
    # ------------------------------------------------------------------

    def _schedule_artifact_upload(
        self,
        *,
        images: list[bytes],
        blob_store: Any,
        graph_provider: Any,
        org_id: str | None,
        conversation_id: str | None,
        user_id: str | None,
        model_name: str,
        file_name_hint: str | None = None,
        edit_record_id: str | None = None,
    ) -> None:
        """Upload generated images in the background and register the task.

        If essential context (conversation/org) is missing we log and skip the
        upload; the tool still returns success with metadata so the caller can
        surface a helpful message. When ``blob_store`` is absent, we try to
        build one on-demand from ``config_service`` + ``graph_provider``
        (same fallback coding_sandbox uses).

        When ``edit_record_id`` is set, the FIRST image bumps that exact
        artifact's version directly (via ``ArtifactRegistryService.add_version``)
        instead of matching/creating by logical name — this is what makes an
        "edit" update the artifact the caller pointed at rather than a
        different one that happens to share a file name. Any additional
        images (``n`` > 1) are uploaded as new, separate artifacts so a
        multi-variation edit never discards a variation by overwriting the
        same version repeatedly.
        """
        if not (conversation_id and org_id):
            logger.warning(
                "[generate_image] artifact upload skipped — missing "
                "conversation_id=%r or org_id=%r",
                conversation_id, org_id,
            )
            return

        # Capture config_service here so the background task can build a
        # BlobStorage lazily if one wasn't injected into chat_state.
        config_service = self.chat_state.get("config_service")

        async def _upload() -> Optional[dict[str, Any]]:
            store = blob_store
            if store is None:
                try:
                    from app.modules.transformers.blob_storage import (
                        BlobStorage,
                    )
                    store = BlobStorage(
                        logger=logger,
                        config_service=config_service,
                        graph_provider=graph_provider,
                    )
                    logger.info(
                        "[generate_image] created BlobStorage on-demand for conversation=%s",
                        conversation_id,
                    )
                except Exception:
                    logger.exception(
                        "[generate_image] could not build BlobStorage on-demand",
                    )
                    return None

            if store is None:
                logger.warning(
                    "[generate_image] upload aborted — no BlobStorage available",
                )
                return None

            uploaded: list[dict[str, Any]] = []
            total = len(images)
            for idx, image_bytes in enumerate(images):
                file_name = _build_file_name(
                    model_name, idx, total=total, hint=file_name_hint,
                )
                logger.info(
                    "[generate_image] uploading image %d/%d (%s, %d bytes) "
                    "to blob store for conversation=%s%s",
                    idx + 1, len(images), file_name, len(image_bytes),
                    conversation_id,
                    f" (editing artifact {edit_record_id})" if edit_record_id and idx == 0 else "",
                )
                try:
                    if edit_record_id and idx == 0:
                        entry = await self._add_version_to_artifact(
                            artifact_id=edit_record_id,
                            image_bytes=image_bytes,
                            blob_store=store,
                            graph_provider=graph_provider,
                            org_id=org_id,
                            user_id=user_id,
                        )
                    else:
                        entry = await upload_bytes_artifact(
                            file_name=file_name,
                            file_bytes=image_bytes,
                            mime_type="image/png",
                            blob_store=store,
                            org_id=org_id,
                            conversation_id=conversation_id,
                            user_id=user_id,
                            graph_provider=graph_provider,
                            connector_name=Connectors.IMAGE_GENERATION,
                            source_tool="image_generator.generate_image",
                        )
                except Exception:
                    logger.exception(
                        "[generate_image] upload failed for image %d (%s)",
                        idx, file_name,
                    )
                    continue
                if entry:
                    logger.info(
                        "[generate_image] uploaded %s — documentId=%s recordId=%s url=%s",
                        entry.get("fileName"),
                        entry.get("documentId"),
                        entry.get("recordId"),
                        entry.get("signedUrl") or entry.get("downloadUrl"),
                    )
                    uploaded.append(entry)
                else:
                    logger.warning(
                        "[generate_image] upload returned no entry for %s",
                        file_name,
                    )
            if not uploaded:
                logger.warning(
                    "[generate_image] no artifacts uploaded for conversation=%s",
                    conversation_id,
                )
                return None
            logger.info(
                "[generate_image] registered %d artifact(s) for conversation=%s",
                len(uploaded), conversation_id,
            )
            return {"type": "artifacts", "artifacts": uploaded}

        task = asyncio.create_task(_upload())
        register_task(conversation_id, task)
        logger.info(
            "[generate_image] scheduled background upload task for "
            "conversation=%s (images=%d)",
            conversation_id, len(images),
        )


def _sanitize_file_stem(raw: str | None) -> str:
    """Normalise a user / LLM supplied file-name stem to a safe snake_case token.

    Returns an empty string when the input is None/empty or produces nothing
    meaningful after sanitisation. The caller is expected to fall back to a
    model-based default in that case.
    """
    if not raw:
        return ""
    cleaned = "".join(
        c if c.isalnum() or c in {"-", "_"} else "_" for c in raw.strip()
    ).strip("_-").lower()
    if not cleaned:
        return ""
    # Collapse runs of underscores and hyphens so "a___b---c" -> "a_b-c".
    out: list[str] = []
    prev = ""
    for ch in cleaned:
        if ch in {"_", "-"} and prev in {"_", "-"}:
            continue
        out.append(ch)
        prev = ch
    return "".join(out)[:60].strip("_-")


def _build_file_name(
    model_name: str,
    idx: int,
    *,
    total: int = 1,
    hint: str | None = None,
) -> str:
    """Return a short, filesystem-safe file name for the generated image.

    When ``hint`` sanitises to a non-empty stem, use it directly (suffixed
    with the 1-based index only when ``total`` > 1). Otherwise fall back to
    the legacy ``{model}_{idx}_{uuid8}.png`` pattern so the upload still
    succeeds even when the LLM omitted the ``file_name`` argument.
    """
    stem = _sanitize_file_stem(hint)
    if stem:
        if total > 1:
            return f"{stem}_{idx + 1}.png"
        return f"{stem}.png"

    safe_model = "".join(
        c if c.isalnum() or c in {"-", "_"} else "_" for c in (model_name or "image")
    ).strip("_") or "image"
    suffix = uuid.uuid4().hex[:8]
    return f"{safe_model}_{idx + 1}_{suffix}.png"
