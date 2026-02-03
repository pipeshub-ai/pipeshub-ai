from typing import List, Literal, Optional

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.config.constants.arangodb import DepartmentNames
from app.models.blocks import Block, SemanticMetadata
from app.modules.extraction.prompt_template import (
    prompt_for_document_extraction,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.llm import get_llm
from app.utils.streaming import invoke_with_structured_output_and_reflection

DEFAULT_CONTEXT_LENGTH = 128000
CONTENT_TOKEN_RATIO = 0.85
SentimentType = Literal["Positive", "Neutral", "Negative"]

class SubCategories(BaseModel):
    level1: str = Field(description="Level 1 subcategory")
    level2: str = Field(description="Level 2 subcategory")
    level3: str = Field(description="Level 3 subcategory")

class DocumentClassification(BaseModel):
    departments: List[str] = Field(
        description="The list of departments this document belongs to", max_items=3
    )
    category: str = Field(description="Main category this document belongs to")
    subcategories: SubCategories = Field(
        description="Nested subcategories for the document"
    )
    languages: List[str] = Field(
        description="List of languages detected in the document"
    )
    sentiment: SentimentType = Field(description="Overall sentiment of the document")
    confidence_score: float = Field(
        description="Confidence score of the classification", ge=0, le=1
    )
    topics: List[str] = Field(
        description="List of key topics/themes extracted from the document"
    )
    summary: str = Field(description="Summary of the document")

class DocumentExtraction(Transformer):
    def __init__(self, logger, graph_provider: IGraphDBProvider, config_service) -> None:
        super().__init__()
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        blocks = record.block_containers.blocks

        document_classification = await self.process_document(blocks, record.org_id)
        if document_classification is None:
            record.semantic_metadata = None
            return
        record.semantic_metadata = SemanticMetadata(
            departments=document_classification.departments,
            languages=document_classification.languages,
            topics=document_classification.topics,
            summary=document_classification.summary,
            categories=[document_classification.category],
            sub_category_level_1=document_classification.subcategories.level1,
            sub_category_level_2=document_classification.subcategories.level2,
            sub_category_level_3=document_classification.subcategories.level3,
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
    ) -> Optional[DocumentClassification]:
        """
        Extract metadata from document content.
        """
        self.logger.info("🎯 Extracting domain metadata")
        self.llm, config = await get_llm(self.config_service)
        is_multimodal_llm = config.get("isMultimodal")
        context_length = config.get("contextLength") or DEFAULT_CONTEXT_LENGTH

        self.logger.info(f"Context length: {context_length}")

        try:
            self.logger.info(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.graph_provider.get_departments(org_id)
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

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                self.llm, messages, DocumentClassification
            )

            if parsed_response is not None:
                self.logger.info("✅ Document classification parsed successfully")
                return parsed_response
            else:
                self.logger.error("❌ Failed to parse document classification after all attempts")
                raise ValueError("Failed to parse document classification after all attempts")

        except Exception as e:
            self.logger.error(f"❌ Error during metadata extraction: {str(e)}")
            raise

    async def process_document(self, blocks: List[Block], org_id: str) -> DocumentClassification:
            self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
            return await self.extract_metadata(blocks, org_id)



