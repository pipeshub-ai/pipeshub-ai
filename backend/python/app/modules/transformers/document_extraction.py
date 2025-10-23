from typing import List, Literal

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from langchain.schema import AIMessage, HumanMessage
from pydantic import BaseModel, Field

from app.config.constants.arangodb import DepartmentNames
from app.models.blocks import Block, SemanticMetadata
from app.modules.extraction.prompt_template import (
    prompt_for_document_extraction,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.utils.llm import get_llm

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
    def __init__(self, logger, base_arango_service, config_service) -> None:
        super().__init__()
        self.logger = logger
        self.arango_service = base_arango_service
        self.config_service = config_service
        self.parser = PydanticOutputParser(pydantic_object=DocumentClassification)

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

    def _prepare_content(self, blocks: List[Block], is_multimodal_llm: bool) -> List[dict]:
        MAX_TOKENS = 30000
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
            self.prompt_template = PromptTemplate.from_template(filled_prompt)

            # Prepare multimodal content
            content = self._prepare_content(blocks, is_multimodal_llm)

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

            # Clean the response content
            response_text = response.content.strip()
            if response_text.startswith("```json"):
                response_text = response_text.replace("```json", "", 1)
            if response_text.endswith("```"):
                response_text = response_text.rsplit("```", 1)[0]
            response_text = response_text.strip()

            try:
                # Parse the response using the Pydantic parser
                parsed_response = self.parser.parse(response_text)
                return parsed_response

            except Exception as parse_error:
                self.logger.error(f"❌ Failed to parse response: {str(parse_error)}")
                self.logger.error(f"Response content: {response_text}")

                # Reflection: attempt to fix the validation issue by providing feedback to the LLM
                try:
                    self.logger.info(
                        "🔄 Attempting reflection to fix validation issues"
                    )
                    reflection_prompt = f"""
                    The previous response failed validation with the following error:
                    {str(parse_error)}

                    The response was:
                    {response_text}

                    Please correct your response to match the expected schema.
                    Ensure all fields are properly formatted and all required fields are present.
                    Respond only with valid JSON that matches the DocumentClassification schema.
                    """

                    reflection_messages = [
                        HumanMessage(content=message_content),
                        AIMessage(content=response_text),
                        HumanMessage(content=reflection_prompt),
                    ]

                    # Use retry wrapper for reflection LLM call
                    reflection_response = await self._call_llm(reflection_messages)
                    reflection_text = reflection_response.content.strip()

                    # Clean the reflection response
                    if reflection_text.startswith("```json"):
                        reflection_text = reflection_text.replace("```json", "", 1)
                    if reflection_text.endswith("```"):
                        reflection_text = reflection_text.rsplit("```", 1)[0]
                    reflection_text = reflection_text.strip()

                    self.logger.info(f"🎯 Reflection response: {reflection_text}")

                    # Try parsing again with the reflection response
                    parsed_reflection = self.parser.parse(reflection_text)

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

    async def _call_llm(self, messages) -> dict | None:
        """Wrapper for LLM calls with retry logic"""
        return await self.llm.ainvoke(messages)


    async def process_document(self, blocks: List[Block], org_id: str) -> DocumentClassification:
            self.logger.info("🖼️ Processing blocks for semantic metadata extraction")
            return await self.extract_metadata(blocks, org_id)



