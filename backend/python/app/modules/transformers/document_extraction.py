import json
from typing import List, Literal

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import TypeAdapter
from typing_extensions import TypedDict

from app.config.constants.arangodb import DepartmentNames
from app.models.blocks import Block, SemanticMetadata
from app.modules.extraction.prompt_template import (
    prompt_for_document_extraction,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.utils.llm import get_llm
from app.utils.streaming import _apply_structured_output, cleanup_content

DEFAULT_CONTEXT_LENGTH = 128000
CONTENT_TOKEN_RATIO = 0.85
SentimentType = Literal["Positive", "Neutral", "Negative"]

class SubCategories(TypedDict):
    level1: str
    level2: str
    level3: str

class DocumentClassification(TypedDict):
    departments: List[str]
    category: str
    subcategories: SubCategories
    languages: List[str]
    sentiment: SentimentType
    confidence_score: float
    topics: List[str]
    summary: str

document_classification_adapter = TypeAdapter(DocumentClassification)

class DocumentExtraction(Transformer):
    def __init__(self, logger, base_arango_service, config_service) -> None:
        super().__init__()
        self.logger = logger
        self.arango_service = base_arango_service
        self.config_service = config_service

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        blocks = record.block_containers.blocks

        document_classification = await self.process_document(blocks, record.org_id)
        if document_classification is None:
            record.semantic_metadata = None
            return
        record.semantic_metadata = SemanticMetadata(
            departments=document_classification.get("departments", []),
            languages=document_classification.get("languages", []),
            topics=document_classification.get("topics", []),
            summary=document_classification.get("summary", ""),
            categories=[document_classification.get("category", "")],
            sub_category_level_1=document_classification.get("subcategories", {}).get("level1", ""),
            sub_category_level_2=document_classification.get("subcategories", {}).get("level2", ""),
            sub_category_level_3=document_classification.get("subcategories", {}).get("level3", ""),
        )
        self.logger.info("🎯 Document extraction completed successfully")


    def _prepare_content(self, blocks: List[Block], is_multimodal_llm: bool, context_length: int | None) -> List[dict]:
        MAX_TOKENS = int(context_length * CONTENT_TOKEN_RATIO)
        MAX_IMAGES = 50
        total_tokens = 0
        image_count = 0
        image_cap_logged = False
        content = []

        # Lazy import tiktoken; fall back to a rough heuristic if unavailable
        enc = None
        try:
            import tiktoken  # type: ignore
            try:
                enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                enc = None
        except Exception:
            enc = None

        def count_tokens(text: str) -> int:
            if not text:
                return 0
            if enc is not None:
                try:
                    return len(enc.encode(text))
                except Exception:
                    pass
            # Fallback heuristic: ~4 chars per token
            return max(1, len(text) // 4)

        for block in blocks:
            if block.type.value == "text":
                if block.data:
                    candidate = {
                        "type": "text",
                        "text": block.data if block.data else ""
                    }
                    increment = count_tokens(candidate["text"])
                    if total_tokens + increment > MAX_TOKENS:
                        self.logger.info("✂️ Content exceeds %d tokens (%d). Truncating to head.", MAX_TOKENS, total_tokens + increment)
                        break
                    content.append(candidate)
                    total_tokens += increment
            elif block.type.value == "image":
                # Respect provider limits on images per request
                if image_count >= MAX_IMAGES:
                    if not image_cap_logged:
                        self.logger.info("🛑 Reached image cap of %d. Skipping additional images.", MAX_IMAGES)
                        image_cap_logged = True
                    continue
                if is_multimodal_llm:
                    if block.data and block.format.value == "base64":
                        image_data = block.data
                        image_data = image_data.get("uri")

                        # Validate that the image URL is either a valid HTTP/HTTPS URL or a base64 data URL
                        if image_data and (
                            image_data.startswith("http://") or
                            image_data.startswith("https://") or
                            image_data.startswith("data:image/")
                        ):
                            candidate = {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_data
                                }
                            }
                            # Images are provider-specific for token accounting; treat as zero-text here
                            content.append(candidate)
                            image_count += 1
                        else:
                            self.logger.warning(f"⚠️ Skipping invalid image URL format: {image_data[:100] if image_data else 'None'}")
                            continue
                    else:
                        continue
                else:
                    continue

            elif block.type.value == "table_row":
                if block.data:
                    if isinstance(block.data, dict):
                        table_row_text = block.data.get("row_natural_language_text")
                    else:
                        table_row_text = str(block.data)
                    candidate = {
                        "type": "text",
                        "text": table_row_text if table_row_text else ""
                    }
                    increment = count_tokens(candidate["text"])
                    if total_tokens + increment > MAX_TOKENS:
                        self.logger.info("✂️ Content exceeds %d tokens (%d). Truncating to head.", MAX_TOKENS, total_tokens + increment)
                        break
                    content.append(candidate)
                    total_tokens += increment

        return content

    async def extract_metadata(
        self, blocks: List[Block], org_id: str
    ) -> DocumentClassification:
        """
        Extract metadata from document content.
        """
        self.logger.info("🎯 Extracting domain metadata")
        self.llm, config= await get_llm(self.config_service)
        is_multimodal_llm = config.get("isMultimodal")
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

        self.logger.info(f"Context length: {context_length}")

        try:
            self.logger.info(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.arango_service.get_departments(org_id)
            if not departments:
                departments = [dept.value for dept in DepartmentNames]

            department_list = "\n".join(f'     - "{dept}"' for dept in departments)

            sentiment_list = "\n".join(
                f'     - "{sentiment}"' for sentiment in SentimentType.__args__
            )

            filled_prompt = prompt_for_document_extraction.replace(
                "{department_list}", department_list
            ).replace("{sentiment_list}", sentiment_list)


            # Prepare multimodal content
            content = self._prepare_content(blocks, is_multimodal_llm, context_length)

            if len(content) == 0:
                self.logger.info("No content to process in document extraction")
                return None
            # Create the multimodal message
            message_content = [
                {
                    "type": "text",
                    "text": filled_prompt
                },
                {
                    "type": "text",
                    "text": "Document Content: "
                }
            ]
            # Add the multimodal content
            message_content.extend(content)

            # Create the message for VLM
            messages = [HumanMessage(content=message_content)]

            # Use retry wrapper for LLM call
            response = await self._call_llm(messages)

            try:
                if isinstance(response, dict):
                    self.logger.info(f"Response is structured output (dict): {response}")
                    parsed_response = document_classification_adapter.validate_python(response)
                else:
                    self.logger.info("Response is non-structured output (AIMessage)")
                    response = response.content
                    response_text = cleanup_content(response)
                    parsed_response = document_classification_adapter.validate_json(response_text)

                return parsed_response

            except Exception as parse_error:
                self.logger.error(f"❌ Failed to parse response: {str(parse_error)}")

                # Reflection: attempt to fix the validation issue by providing feedback to the LLM
                try:
                    self.logger.info(
                        "🔄 Attempting reflection to fix validation issues"
                    )
                    reflection_prompt = f"""
                    The previous response failed validation with the following error:
                    {str(parse_error)}

                    Please correct your response to match the expected schema.
                    Ensure all fields are properly formatted and all required fields are present.
                    Respond only with valid JSON that matches the schema.
                    """

                    reflection_messages = [
                        HumanMessage(content=message_content),
                        AIMessage(content=json.dumps(response)),
                        HumanMessage(content=reflection_prompt),
                    ]

                    # Use retry wrapper for reflection LLM call
                    reflection_response = await self._call_llm(reflection_messages)

                    # Check if reflection response is already structured (dict) or needs parsing (AIMessage)
                    if isinstance(reflection_response, dict):
                        # Structured output - response is already parsed
                        self.logger.info("Reflection response is structured output (dict)")
                        parsed_reflection = document_classification_adapter.validate_python(reflection_response)
                    else:
                        # Non-structured output - need to parse from response.content
                        reflection_text = reflection_response.content
                        reflection_text = cleanup_content(reflection_text)

                        self.logger.info(f"🎯 Reflection response: {reflection_text}")

                        parsed_reflection = document_classification_adapter.validate_json(reflection_text)

                    self.logger.info(
                        "✅ Reflection successful - validation passed on second attempt"
                    )
                    return parsed_reflection
                except Exception as reflection_error:
                    self.logger.error(
                        f"❌ Reflection attempt failed: {str(reflection_error)}"
                    )
                    raise ValueError(
                        f"Failed to parse LLM response and reflection attempt failed: {str(parse_error)}"
                    )
        except Exception as e:
            self.logger.error(f"❌ Error during metadata extraction: {str(e)}")
            raise

    async def _call_llm(self, messages) -> dict | AIMessage:
        """Wrapper for LLM calls with retry logic. Returns dict when structured output is used, AIMessage otherwise."""
        llm_with_structured_output = _apply_structured_output(self.llm, schema=DocumentClassification)
        return await llm_with_structured_output.ainvoke(messages)

    async def process_document(self, blocks: List[Block], org_id: str) -> DocumentClassification:
            self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
            return await self.extract_metadata(blocks, org_id)



