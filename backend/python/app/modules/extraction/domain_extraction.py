import asyncio
import json
import uuid
from typing import List, Literal

import aiohttp
import jwt
import numpy as np
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from langchain.schema import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.config.constants.arangodb import (
    CollectionNames,
    DepartmentNames,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
from app.modules.extraction.prompt_template import prompt
from app.modules.transformers.document_extraction import DocumentClassification
from app.utils.chat_helpers import count_tokens_text
from app.utils.llm import get_llm
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Update the Literal types
SentimentType = Literal["Positive", "Neutral", "Negative"]
MAX_CONTENT_TOKENS = 30000
def get_first_n_tokens(text: str, n: int) -> str:
    """Return the first n tokens of text.

    Uses tiktoken with cl100k_base when available; otherwise falls back to a
    simple character heuristic (~4 chars per token).
    """
    if not text or n <= 0:
        return ""
    # Try tiktoken if available
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        token_ids = enc.encode(text)
        return enc.decode(token_ids[:n])
    except (ImportError, Exception):
        # tiktoken not available; fall back to heuristic
        pass

    # Heuristic fallback: assume ~4 chars per token
    approx_chars = n * 4
    return text[:approx_chars]

class SubCategories(BaseModel):
    level1: str = Field(description="Level 1 subcategory")
    level2: str = Field(description="Level 2 subcategory")
    level3: str = Field(description="Level 3 subcategory")

class DomainExtractor:
    def __init__(self, logger, base_arango_service, config_service) -> None:
        self.logger = logger
        self.arango_service = base_arango_service
        self.config_service = config_service
        self.logger.info("🚀 self.arango_service: %s", self.arango_service)
        self.logger.info("🚀 self.arango_service.db: %s", self.arango_service.db)

        self.parser = PydanticOutputParser(pydantic_object=DocumentClassification)

        # Initialize topics storage
        self.topics_store = set()  # Store all accepted topics

        # Initialize TF-IDF vectorizer for topic similarity
        self.vectorizer = TfidfVectorizer(stop_words="english")
        self.similarity_threshold = 0.65  # Adjusted for TF-IDF similarity

        # Initialize LDA model as backup
        self.lda = LatentDirichletAllocation(
            n_components=10, random_state=42  # Adjust based on your needs
        )

        # Configure retry parameters
        self.max_retries = 3
        self.min_wait = 1  # seconds
        self.max_wait = 10  # seconds

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=lambda retry_state: retry_state.args[0].logger.warning(
            f"Retrying LLM call after error. Attempt {retry_state.attempt_number}"
        ),
    )

    async def _call_llm(self, messages) -> dict | None:
        """Wrapper for LLM calls with retry logic and timeout"""
        try:
            # Add a 300 second (5 minute) timeout to prevent indefinite hanging
            return await asyncio.wait_for(
                self.llm.ainvoke(messages),
                timeout=300.0
            )
        except asyncio.TimeoutError:
            self.logger.error("❌ LLM call timed out after 300 seconds")
            raise

    async def find_similar_topics(self, new_topic: str) -> str:
        """
        Find if a similar topic already exists in the topics store using TF-IDF similarity.
        Returns the existing topic if a match is found, otherwise returns the new topic.
        """
        # First check exact matches
        if new_topic in self.topics_store:
            return new_topic

        # If no topics exist yet, return the new topic
        if not self.topics_store:
            return new_topic

        try:
            # Convert topics to TF-IDF vectors
            all_topics = list(self.topics_store) + [new_topic]
            tfidf_matrix = self.vectorizer.fit_transform(all_topics)

            # Calculate cosine similarity between new topic and existing topics
            # Get the last row (new topic)
            new_topic_vector = tfidf_matrix[-1:]
            # Get all but the last row
            existing_topics_matrix = tfidf_matrix[:-1]

            similarities = cosine_similarity(new_topic_vector, existing_topics_matrix)[
                0
            ]

            # Find the most similar topic
            max_similarity_idx = np.argmax(similarities)
            max_similarity = similarities[max_similarity_idx]

            if max_similarity >= self.similarity_threshold:
                return list(self.topics_store)[max_similarity_idx]

            # If TF-IDF similarity is low, try LDA as backup
            if max_similarity < self.similarity_threshold:
                try:
                    # Fit LDA on all topics
                    dtm = self.vectorizer.fit_transform(all_topics)
                    topic_distributions = self.lda.fit_transform(dtm)

                    # Compare topic distributions
                    new_topic_dist = topic_distributions[-1]
                    existing_topics_dist = topic_distributions[:-1]

                    # Calculate Jensen-Shannon divergence or cosine similarity
                    lda_similarities = cosine_similarity(
                        [new_topic_dist], existing_topics_dist
                    )[0]
                    max_lda_sim_idx = np.argmax(lda_similarities)
                    max_lda_similarity = lda_similarities[max_lda_sim_idx]

                    if max_lda_similarity >= self.similarity_threshold:
                        return list(self.topics_store)[max_lda_sim_idx]

                except Exception as e:
                    self.logger.error(f"❌ Error in LDA similarity check: {str(e)}")

        except Exception as e:
            self.logger.error(f"❌ Error in topic similarity check: {str(e)}")

        return new_topic

    async def process_new_topics(self, new_topics: List[str]) -> List[str]:
        """
        Process new topics against existing topics store.
        Returns list of topics, using existing ones where matches are found.
        """
        processed_topics = []
        for topic in new_topics:
            matched_topic = await self.find_similar_topics(topic)
            processed_topics.append(matched_topic)
            # Only add to topics_store if it's a new topic
            if matched_topic == topic:  # This means no match was found
                self.topics_store.add(topic)

        return list(set(processed_topics))

    async def extract_metadata(
        self, content: str, org_id: str
    ) -> DocumentClassification:
        """
        Extract metadata from document content using Azure OpenAI.
        Includes reflection logic to attempt recovery from parsing failures.
        """
        self.logger.info("🎯 Extracting domain metadata")
        try:
            self.llm, _ = await get_llm(self.config_service)
            self.logger.info("✅ LLM initialized successfully")
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize LLM: {str(e)}")
            raise

        try:
            self.logger.info(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.arango_service.get_departments(org_id)
            if not departments:
                departments = [dept.value for dept in DepartmentNames]

            # Format department list for the prompt
            department_list = "\n".join(f'     - "{dept}"' for dept in departments)

            # Format sentiment list for the prompt
            sentiment_list = "\n".join(
                f'     - "{sentiment}"' for sentiment in SentimentType.__args__
            )

            filled_prompt = prompt.replace(
                "{department_list}", department_list
            ).replace("{sentiment_list}", sentiment_list)
            self.prompt_template = PromptTemplate.from_template(filled_prompt)
            token_count = count_tokens_text(content,None)
            if token_count > MAX_CONTENT_TOKENS:
                self.logger.info("🎯 Prompt exceeds MAX_CONTENT_TOKENS tokens, truncating content")
                content = get_first_n_tokens(content,MAX_CONTENT_TOKENS)

            formatted_prompt = self.prompt_template.format(content=content)
            self.logger.info("🎯 Prompt formatted successfully")

            messages = [HumanMessage(content=formatted_prompt)]
            # Use retry wrapper for LLM call
            self.logger.info("🚀 Making LLM call for domain metadata extraction")
            response = await self._call_llm(messages)
            self.logger.info("✅ LLM call completed successfully")
            # Remove any thinking tags if present
            if '</think>' in response.content:
                response.content = response.content.split('</think>')[-1]
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

                # Process topics through similarity check
                # canonical_topics = await self.process_new_topics(parsed_response.topics)
                # parsed_response.topics = canonical_topics

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
                        HumanMessage(content=formatted_prompt),
                        AIMessage(content=response_text),
                        HumanMessage(content=reflection_prompt),
                    ]

                    # Use retry wrapper for reflection LLM call
                    self.logger.info("🔄 Making reflection LLM call to fix validation issues")
                    reflection_response = await self._call_llm(reflection_messages)
                    self.logger.info("✅ Reflection LLM call completed successfully")
                    if '</think>' in reflection_response.content:
                        reflection_response.content = reflection_response.content.split('</think>')[-1]
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

                    # Process topics through similarity check
                    canonical_topics = await self.process_new_topics(
                        parsed_reflection.topics
                    )
                    parsed_reflection.topics = canonical_topics

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

    async def save_metadata_to_db(
        self, org_id: str, record_id: str, metadata: DocumentClassification, virtual_record_id: str
    ) -> dict | None:
        """
        Extract metadata from a document in ArangoDB and create department relationships
        """
        self.logger.info("🚀 Saving metadata to ArangoDB")

        try:
            # Retrieve the document content from ArangoDB
            record = await self.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            doc = dict(record)
            # Create relationships with departments
            for department in metadata.departments:
                try:
                    dept_query = f"FOR d IN {CollectionNames.DEPARTMENTS.value} FILTER d.departmentName == @department RETURN d"
                    cursor = self.arango_service.db.aql.execute(
                        dept_query, bind_vars={"department": department}
                    )
                    dept_doc = cursor.next()
                    self.logger.info(f"🚀 Department: {dept_doc}")

                    if dept_doc:
                        edge = {
                            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                            "_to": f"{CollectionNames.DEPARTMENTS.value}/{dept_doc['_key']}",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        await self.arango_service.batch_create_edges(
                            [edge], CollectionNames.BELONGS_TO_DEPARTMENT.value
                        )
                        self.logger.info(
                            f"🔗 Created relationship between document {record_id} and department {department}"
                        )

                except StopIteration:
                    self.logger.warning(f"⚠️ No department found for: {department}")
                    continue
                except Exception as e:
                    self.logger.error(
                        f"❌ Error creating relationship with department {department}: {str(e)}"
                    )
                    continue

            # Handle single category
            category_query = f"FOR c IN {CollectionNames.CATEGORIES.value} FILTER c.name == @name RETURN c"
            cursor = self.arango_service.db.aql.execute(
                category_query, bind_vars={"name": metadata.category}
            )
            try:
                category_doc = cursor.next()
                if category_doc is None:
                    raise KeyError("No category found")
                category_key = category_doc["_key"]
            except (StopIteration, KeyError, TypeError):
                category_key = str(uuid.uuid4())
                self.arango_service.db.collection(
                    CollectionNames.CATEGORIES.value
                ).insert(
                    {
                        "_key": category_key,
                        "name": metadata.category,
                    }
                )

            # Create category relationship if it doesn't exist
            edge_query = f"""
            FOR e IN {CollectionNames.BELONGS_TO_CATEGORY.value}
            FILTER e._from == @from AND e._to == @to
            RETURN e
            """
            cursor = self.arango_service.db.aql.execute(
                edge_query,
                bind_vars={
                    "from": f"records/{record_id}",
                    "to": f"categories/{category_key}",
                },
            )
            if not cursor.count():
                self.arango_service.db.collection(
                    CollectionNames.BELONGS_TO_CATEGORY.value
                ).insert(
                    {
                        "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                        "_to": f"{CollectionNames.CATEGORIES.value}/{category_key}",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                )

            # Handle subcategories with similar pattern
            def handle_subcategory(name, level, parent_key, parent_collection) -> str:
                collection_name = getattr(
                    CollectionNames, f"SUBCATEGORIES{level}"
                ).value
                query = f"FOR s IN {collection_name} FILTER s.name == @name RETURN s"
                cursor = self.arango_service.db.aql.execute(
                    query, bind_vars={"name": name}
                )
                try:
                    doc = cursor.next()
                    if doc is None:
                        raise KeyError("No subcategory found")
                    key = doc["_key"]
                except (StopIteration, KeyError, TypeError):
                    key = str(uuid.uuid4())
                    self.arango_service.db.collection(collection_name).insert(
                        {
                            "_key": key,
                            "name": name,
                        }
                    )

                # Create belongs_to relationship
                edge_query = f"""
                FOR e IN {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER e._from == @from AND e._to == @to
                RETURN e
                """
                cursor = self.arango_service.db.aql.execute(
                    edge_query,
                    bind_vars={
                        "from": f"{CollectionNames.RECORDS.value}/{record_id}",
                        "to": f"{collection_name}/{key}",
                    },
                )
                if not cursor.count():
                    self.arango_service.db.collection(
                        CollectionNames.BELONGS_TO_CATEGORY.value
                    ).insert(
                        {
                            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                            "_to": f"{collection_name}/{key}",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                    )

                # Create hierarchy relationship
                if parent_key:
                    edge_query = f"""
                    FOR e IN {CollectionNames.INTER_CATEGORY_RELATIONS.value}
                    FILTER e._from == @from AND e._to == @to
                    RETURN e
                    """
                    cursor = self.arango_service.db.aql.execute(
                        edge_query,
                        bind_vars={
                            "from": f"{collection_name}/{key}",
                            "to": f"{parent_collection}/{parent_key}",
                        },
                    )
                    if not cursor.count():
                        self.arango_service.db.collection(
                            CollectionNames.INTER_CATEGORY_RELATIONS.value
                        ).insert(
                            {
                                "_from": f"{collection_name}/{key}",
                                "_to": f"{parent_collection}/{parent_key}",
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            }
                        )
                return key

            # Process subcategories
            if metadata.subcategories:
                if metadata.subcategories.level1:
                    sub1_key = handle_subcategory(
                        metadata.subcategories.level1, "1", category_key, "categories"
                    )
                if metadata.subcategories.level2 and sub1_key:
                    sub2_key = handle_subcategory(
                        metadata.subcategories.level2, "2", sub1_key, "subcategories1"
                    )
                if metadata.subcategories.level3 and sub2_key:
                    handle_subcategory(
                        metadata.subcategories.level3, "3", sub2_key, "subcategories2"
                    )

            # Handle languages
            for language in metadata.languages:
                query = f"FOR l IN {CollectionNames.LANGUAGES.value} FILTER l.name == @name RETURN l"
                cursor = self.arango_service.db.aql.execute(
                    query, bind_vars={"name": language}
                )
                try:
                    lang_doc = cursor.next()
                    if lang_doc is None:
                        raise KeyError("No language found")
                    lang_key = lang_doc["_key"]
                except (StopIteration, KeyError, TypeError):
                    lang_key = str(uuid.uuid4())
                    self.arango_service.db.collection(
                        CollectionNames.LANGUAGES.value
                    ).insert(
                        {
                            "_key": lang_key,
                            "name": language,
                        }
                    )

                # Create relationship if it doesn't exist
                edge_query = f"""
                FOR e IN {CollectionNames.BELONGS_TO_LANGUAGE.value}
                FILTER e._from == @from AND e._to == @to
                RETURN e
                """
                cursor = self.arango_service.db.aql.execute(
                    edge_query,
                    bind_vars={
                        "from": f"records/{record_id}",
                        "to": f"languages/{lang_key}",
                    },
                )
                if not cursor.count():
                    self.arango_service.db.collection(
                        CollectionNames.BELONGS_TO_LANGUAGE.value
                    ).insert(
                        {
                            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                            "_to": f"{CollectionNames.LANGUAGES.value}/{lang_key}",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                    )

            # Handle topics
            for topic in metadata.topics:
                query = f"FOR t IN {CollectionNames.TOPICS.value} FILTER t.name == @name RETURN t"
                cursor = self.arango_service.db.aql.execute(
                    query, bind_vars={"name": topic}
                )
                try:
                    topic_doc = cursor.next()
                    if topic_doc is None:
                        raise KeyError("No topic found")
                    topic_key = topic_doc["_key"]
                except (StopIteration, KeyError, TypeError):
                    topic_key = str(uuid.uuid4())
                    self.arango_service.db.collection(
                        CollectionNames.TOPICS.value
                    ).insert(
                        {
                            "_key": topic_key,
                            "name": topic,
                        }
                    )

                # Create relationship if it doesn't exist
                edge_query = f"""
                FOR e IN {CollectionNames.BELONGS_TO_TOPIC.value}
                FILTER e._from == @from AND e._to == @to
                RETURN e
                """
                cursor = self.arango_service.db.aql.execute(
                    edge_query,
                    bind_vars={
                        "from": f"records/{record_id}",
                        "to": f"topics/{topic_key}",
                    },
                )
                if not cursor.count():
                    self.arango_service.db.collection(
                        CollectionNames.BELONGS_TO_TOPIC.value
                    ).insert(
                        {
                            "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                            "_to": f"{CollectionNames.TOPICS.value}/{topic_key}",
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                    )

            # Handle summary document
            document_id = None
            if metadata.summary:
                document_id = await self.save_summary_to_storage(org_id, record_id,virtual_record_id, metadata.summary)
                if document_id is None:
                    self.logger.error("❌ Failed to save summary to storage")

            self.logger.info(
                f"🚀 Metadata saved successfully for document: {document_id}"
            )

            doc.update(
                {
                    "summaryDocumentId": document_id,
                    "extractionStatus": "COMPLETED",
                    "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
                }
            )
            docs = [doc]

            self.logger.info(
                f"🎯 Upserting domain metadata for document: {document_id}"
            )
            await self.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )

            doc.update(
                {
                    "departments": [dept for dept in metadata.departments],
                    "categories": metadata.category,
                    "subcategoryLevel1": metadata.subcategories.level1,
                    "subcategoryLevel2": metadata.subcategories.level2,
                    "subcategoryLevel3": metadata.subcategories.level3,
                    "topics": metadata.topics,
                    "languages": metadata.languages,
                    "summary": metadata.summary,
                }
            )

            return doc

        except Exception as e:
            self.logger.error(f"❌ Error saving metadata to ArangoDB: {str(e)}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=lambda retry_state: retry_state.args[0].logger.warning(
            f"Retrying API call after error. Attempt {retry_state.attempt_number}"
        ),
    )

    async def _create_placeholder(self, session, url, data, headers) -> dict | None:
        """Helper method to create placeholder with retry logic"""
        try:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to create placeholder. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to create placeholder. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise aiohttp.ClientError(f"Failed with status {response.status}")

                response_data = await response.json()
                self.logger.debug("✅ Successfully created placeholder")
                return response_data
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error creating placeholder: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error creating placeholder: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=lambda retry_state: retry_state.args[0].logger.warning(
            f"Retrying API call after error. Attempt {retry_state.attempt_number}"
        ),
    )
    async def _get_signed_url(self, session, url, data, headers) -> dict | None:
        """Helper method to get signed URL with retry logic"""
        try:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to get signed URL. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to get signed URL. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise aiohttp.ClientError(f"Failed with status {response.status}")

                response_data = await response.json()
                self.logger.debug("✅ Successfully retrieved signed URL")
                return response_data
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error getting signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error getting signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=lambda retry_state: retry_state.args[0].logger.warning(
            f"Retrying API call after error. Attempt {retry_state.attempt_number}"
        ),
    )

    async def _upload_to_signed_url(self, session, signed_url, data) -> int | None:
        """Helper method to upload to signed URL with retry logic"""
        try:
            async with session.put(
                signed_url,
                json=data,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to upload to signed URL. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to upload to signed URL. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise aiohttp.ClientError(f"Failed to upload with status {response.status}")

                self.logger.debug("✅ Successfully uploaded to signed URL")
                return response.status
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error uploading to signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error uploading to signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")


    async def save_summary_to_storage(self, org_id: str, record_id: str, virtual_record_id: str, summary_doc: dict) -> str | None:
        """
        Save summary document to storage using FormData upload
        Returns:
            str | None: document_id if successful, None if failed
        """
        try:
            self.logger.info("🚀 Starting summary storage process for record: %s", record_id)

            # Generate JWT token
            try:
                payload = {
                    "orgId": org_id,
                    "scopes": [TokenScopes.STORAGE_TOKEN.value],
                }
                secret_keys = await self.config_service.get_config(
                    config_node_constants.SECRET_KEYS.value
                )
                scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
                if not scoped_jwt_secret:
                    raise ValueError("Missing scoped JWT secret")

                jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")
                headers = {
                    "Authorization": f"Bearer {jwt_token}"
                }
            except Exception as e:
                self.logger.error("❌ Failed to generate JWT token: %s", str(e))
                return None

            # Get endpoint configuration
            try:
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                nodejs_endpoint = endpoints.get("cm", {}).get("endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value)
                if not nodejs_endpoint:
                    raise ValueError("Missing CM endpoint configuration")

                storage = await self.config_service.get_config(
                    config_node_constants.STORAGE.value
                )
                storage_type = storage.get("storageType")
                if not storage_type:
                    raise ValueError("Missing storage type configuration")
                self.logger.info("🚀 Storage type: %s", storage_type)
            except Exception as e:
                self.logger.error("❌ Failed to get endpoint configuration: %s", str(e))
                return None

            if storage_type == "local":
                try:
                    async with aiohttp.ClientSession() as session:
                        # Convert summary_doc to JSON string and then to bytes
                        upload_data = {
                            "summary": summary_doc,
                            "virtualRecordId": virtual_record_id
                        }
                        json_data = json.dumps(upload_data).encode('utf-8')

                        # Create form data
                        form_data = aiohttp.FormData()
                        form_data.add_field('file',
                                        json_data,
                                        filename=f'summary_{record_id}.json',
                                        content_type='application/json')
                        form_data.add_field('documentName', f'summary_{record_id}')
                        form_data.add_field('documentPath', 'summaries')
                        form_data.add_field('isVersionedFile', 'true')
                        form_data.add_field('extension', 'json')
                        form_data.add_field('recordId', record_id)

                        # Make upload request
                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                        self.logger.info("📤 Uploading summary to storage for record: %s", record_id)

                        async with session.post(upload_url,
                                            data=form_data,
                                            headers=headers) as response:
                            if response.status != HttpStatusCode.SUCCESS.value:
                                try:
                                    error_response = await response.json()
                                    self.logger.error("❌ Failed to upload summary. Status: %d, Error: %s",
                                                    response.status, error_response)
                                except aiohttp.ContentTypeError:
                                    error_text = await response.text()
                                    self.logger.error("❌ Failed to upload summary. Status: %d, Response: %s",
                                                    response.status, error_text[:200])
                                return None

                            response_data = await response.json()
                            document_id = response_data.get('_id')

                            if not document_id:
                                self.logger.error("❌ No document ID in upload response")
                                return None

                            self.logger.info("✅ Successfully uploaded summary for document: %s", document_id)
                            return document_id

                except aiohttp.ClientError as e:
                    self.logger.error("❌ Network error during upload process: %s", str(e))
                    return None
                except Exception as e:
                    self.logger.error("❌ Unexpected error during upload process: %s", str(e))
                    self.logger.exception("Detailed error trace:")
                    return None

            else:
                placeholder_data = {
                    "documentName": f"summary_{record_id}",
                    "documentPath": "summaries",
                    "extension": "json"
                }

                try:
                    async with aiohttp.ClientSession() as session:
                        # Step 1: Create placeholder
                        self.logger.info("📝 Creating placeholder for record: %s", record_id)
                        placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                        document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)

                        document_id = document.get("_id")
                        if not document_id:
                            self.logger.error("❌ No document ID in placeholder response")
                            return None

                        self.logger.info("📄 Created placeholder with ID: %s", document_id)

                        # Step 2: Get signed URL
                        self.logger.info("🔑 Getting signed URL for document: %s", document_id)
                        upload_data = {
                            "summary": summary_doc,
                            "virtualRecordId": virtual_record_id
                        }

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                        upload_result = await self._get_signed_url(session, upload_url, upload_data, headers)

                        signed_url = upload_result.get('signedUrl')
                        if not signed_url:
                            self.logger.error("❌ No signed URL in response for document: %s", document_id)
                            return None

                        # Step 3: Upload to signed URL
                        self.logger.info("📤 Uploading summary to storage for document: %s", document_id)
                        await self._upload_to_signed_url(session, signed_url, upload_data)

                        self.logger.info("✅ Successfully completed summary storage process for document: %s", document_id)
                        return document_id

                except aiohttp.ClientError as e:
                    self.logger.error("❌ Network error during storage process: %s", str(e))
                    return None
                except Exception as e:
                    self.logger.error("❌ Unexpected error during storage process: %s", str(e))
                    self.logger.exception("Detailed error trace:")
                    return None

        except Exception as e:
            self.logger.error("❌ Critical error in saving summary to storage: %s", str(e))
            self.logger.exception("Detailed error trace:")
            return None
