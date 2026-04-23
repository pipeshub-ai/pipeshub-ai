"""ImageGenerator toolset -- generative-AI image creation from a text prompt.

Internal, always-on toolset (like Calculator) that calls a configured
image-generation provider (OpenAI gpt-image / DALL-E, or Gemini image /
Imagen) and delivers the result through the existing artifact pipeline
(blob upload + ArtifactRecord + ``::artifact`` stream marker).

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

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
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
        app_name="image_generator",
        tool_name="generate_image",
        args_schema=GenerateImageInput,
        llm_description=(
            "Generate a brand-new image from a natural-language prompt using a "
            "generative AI model (OpenAI gpt-image / DALL-E, or Gemini image / Imagen). "
            "Use ONLY for creative imagery: illustrations, concept art, photorealistic "
            "scenes, logos, mockups, stylised art. "
            "DO NOT use for anything that a coding sandbox can produce (charts, plots, "
            "graphs, diagrams, tables, screenshots of data, PDFs from text, SVG from "
            "data) -- use coding_sandbox.execute_python or coding_sandbox.execute_typescript "
            "for those instead. "
            "Always pass a descriptive snake_case `file_name` derived from the "
            "subject of the user's prompt (e.g. 'mona_lisa' for a Mona Lisa request, "
            "'coffee_shop_logo' for a coffee-shop logo). Never use the model name "
            "or random IDs as the file name. "
            "The generated image is attached to the response as an artifact; the text "
            "result just acknowledges success and carries metadata."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "User asks for a creative illustration or artwork",
            "User asks for a photorealistic scene or concept art",
            "User asks for a logo, icon, or mockup of something visual",
            "User asks to 'generate', 'create', 'draw', or 'paint' an image from a description",
        ],
        when_not_to_use=[
            "User wants a chart, graph, plot, or data visualisation (use coding_sandbox.execute_python)",
            "User wants a diagram, flowchart, or org chart (use coding_sandbox or a diagramming tool)",
            "User wants a document, spreadsheet, presentation, or PDF",
            "User wants to edit or annotate an existing image they already have",
            "The task can be solved by executing code or querying data",
            "User wants a screenshot of a UI, data, or website",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Generate an image of a sunset over snowy mountains",
            "Create a minimalist logo for a coffee shop",
            "Draw a cartoon of a cat wearing sunglasses",
            "Make a photorealistic image of a futuristic city skyline",
        ],
    )
    async def generate_image(
        self,
        prompt: str,
        file_name: str = "",
        size: str = "1024x1024",
        n: int = 1,
    ) -> tuple[bool, str]:
        """Generate one or more images from ``prompt`` and attach them as artifacts."""
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

        logger.info(
            "[generate_image] generating provider=%s model=%s size=%s n=%d prompt_len=%d",
            adapter.provider, adapter.model, size, n, len(prompt),
        )

        try:
            images = await adapter.generate(prompt, size=size, n=n)
        except Exception as e:
            logger.exception("[generate_image] provider call failed")
            return self._result(False, {
                "success": False,
                "provider": adapter.provider,
                "model": adapter.model,
                "error": f"Image generation failed: {e}",
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
        )

        return self._result(True, {
            "success": True,
            "message": (
                f"Generated {len(images)} image(s). The image file(s) are "
                "attached to this response automatically as artifacts — the "
                "UI renders them from the ::artifact marker that is appended "
                "after the message. Do NOT include markdown images, links, "
                "or base64 data in your reply; just briefly confirm the image "
                "was generated."
            ),
            "provider": adapter.provider,
            "model": adapter.model,
            "size": size,
            "count": len(images),
            "file_name": _sanitize_file_stem(file_name),
        })

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
    ) -> None:
        """Upload generated images in the background and register the task.

        If essential context (conversation/org) is missing we log and skip the
        upload; the tool still returns success with metadata so the caller can
        surface a helpful message. When ``blob_store`` is absent, we try to
        build one on-demand from ``config_service`` + ``graph_provider``
        (same fallback coding_sandbox uses).
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
                    "to blob store for conversation=%s",
                    idx + 1, len(images), file_name, len(image_bytes),
                    conversation_id,
                )
                try:
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
