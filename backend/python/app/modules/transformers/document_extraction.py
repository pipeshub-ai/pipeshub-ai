from typing import List
from app.modules.transformers.transformer import Transformer, TransformContext
from app.models.blocks import Block, SemanticMetadata
from typing import List, Literal
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from langchain.schema import AIMessage, HumanMessage
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from app.config.constants.arangodb import DepartmentNames
from app.modules.extraction.prompt_template import prompt_for_docling_document_extraction
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
            
    def _prepare_multimodal_content(self, blocks: List[Block]) -> List[dict]:
        """
        Prepare blocks for VLM processing by converting them to the appropriate format.
        Returns a list of content items that can be sent to a VLM.
        """
        multimodal_content = []
        
        for block in blocks:
            if block.type.value == "text":
                # Add text content
                if block.data:
                    multimodal_content.append({
                        "type": "text",
                        "text": block.data if block.data else ""
                })
            elif block.type.value == "image":
                # Add image content
                if block.data and block.format.value == "base64":
                    # Remove data URL prefix if present
                    image_data = block.data
                    # print(json.dumps(image_data, indent=4))
                    # Extract base64 data from data URL
                    image_data = image_data.get("uri")
                    
                    multimodal_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": image_data
                        }
                    })
            elif block.type.value == "table":
                if block.data:
                    multimodal_content.append({
                        "type": "text",
                        "text": block.data if block.data else ""
                    })
        
        return multimodal_content

    async def extract_metadata_with_vlm(
        self, blocks: List[Block], org_id: str
    ) -> DocumentClassification:
        """
        Extract metadata from document content using Vision Language Model (VLM).
        Supports both text and image blocks simultaneously.
        """
        self.logger.info("🎯 Extracting domain metadata using VLM")
        self.llm, _ = await get_llm(self.config_service)
        try:
            self.logger.info(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.arango_service.get_departments(org_id)
            if not departments:
                departments = [dept.value for dept in DepartmentNames]

            department_list = "\n".join(f'     - "{dept}"' for dept in departments)

            sentiment_list = "\n".join(
                f'     - "{sentiment}"' for sentiment in SentimentType.__args__
            )

            filled_prompt = prompt_for_docling_document_extraction.replace(
                "{department_list}", department_list
            ).replace("{sentiment_list}", sentiment_list)
            self.prompt_template = PromptTemplate.from_template(filled_prompt)

            # Prepare multimodal content
            multimodal_content = self._prepare_multimodal_content(blocks)
            
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
            message_content.extend(multimodal_content)
            
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

    async def extract_metadata(
        self, blocks: List[Block], org_id: str
    ) -> DocumentClassification:
        """
        Extract metadata from document content using regular LLM.
        Includes reflection logic to attempt recovery from parsing failures.
        """
        self.logger.info("🎯 Extracting domain metadata")
        self.llm, config = await get_llm(self.config_service)
        is_multimodal_llm = config.get("isMultimodal")
        is_multimodal_llm = True
        try:
            self.logger.info(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.arango_service.get_departments(org_id)
            if not departments:
                departments = [dept.value for dept in DepartmentNames]

            department_list = "\n".join(f'     - "{dept}"' for dept in departments)

            sentiment_list = "\n".join(
                f'     - "{sentiment}"' for sentiment in SentimentType.__args__
            )

            filled_prompt = prompt_for_docling_document_extraction.replace(
                "{department_list}", department_list
            ).replace("{sentiment_list}", sentiment_list)
            self.prompt_template = PromptTemplate.from_template(filled_prompt)

            # Extract text content from blocks
            text_content = ""
            for block in blocks:
                if block.type.value in ["text", "table"] and block.data:
                    text_content += block.data + "\n"
            message_content = [
                {
                    "type": "text",
                    "text": filled_prompt
                },
                {
                    "type": "text",
                    "text": f"Document Content: {text_content}"
                }
            ]
            # formatted_prompt = self.prompt_template.format(content=text_content)
            # self.logger.info("🎯 Prompt formatted successfully")

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

    async def process_document(self, blocks: List[Block], org_id: str) -> DocumentClassification:
      
        # Check if we have any image blocks
        has_images = any(block.type.value == "image" for block in blocks)
        
        if has_images:
            # Use VLM for multimodal processing
            self.logger.info("🖼️ Processing document with VLM (text + images)")
            return await self.extract_metadata_with_vlm(blocks, org_id)
        else:
            # Use regular LLM for text-only processing
            self.logger.info("📝 Processing document with regular LLM (text only)")
            return await self.extract_metadata(blocks, org_id)
   

