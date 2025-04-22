import uuid
from datetime import datetime, timezone
from typing import List, Literal

import numpy as np
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from langchain.schema import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config.utils.named_constants.arangodb_constants import CollectionNames, DepartmentNames
from app.modules.extraction.prompt_template import prompt
from app.utils.llm import get_llm

# Update the Literal types
SentimentType = Literal["Positive", "Neutral", "Negative"]


class SubCategories(BaseModel):
    level1: str = Field(
        description="Level 1 subcategory"
    )
    level2: str = Field(
        description="Level 2 subcategory"
    )
    level3: str = Field(
        description="Level 3 subcategory"
    )

class DocumentClassification(BaseModel):
    departments: List[str] = Field(
        description="The list of departments this document belongs to",
        max_items=3
    )
    categories: str = Field(
        description="Main category this document belongs to"
    )
    subcategories: SubCategories = Field(
        description="Nested subcategories for the document"
    )
    languages: List[str] = Field(
        description="List of languages detected in the document"
    )
    sentiment: SentimentType = Field(
        description="Overall sentiment of the document"
    )
    confidence_score: float = Field(
        description="Confidence score of the classification",
        ge=0,
        le=1
    )
    topics: List[str] = Field(
        description="List of key topics/themes extracted from the document"
    )


class DomainExtractor:
    def __init__(self, logger, base_arango_service, config_service):
        self.logger = logger
        self.arango_service = base_arango_service
        self.config_service = config_service
        self.logger.info("🚀 self.arango_service: %s", self.arango_service)
        self.logger.info("🚀 self.arango_service.db: %s", self.arango_service.db)

        self.parser = PydanticOutputParser(
            pydantic_object=DocumentClassification)

        # Initialize topics storage
        self.topics_store = set()  # Store all accepted topics

        # Initialize TF-IDF vectorizer for topic similarity
        self.vectorizer = TfidfVectorizer(stop_words='english')
        self.similarity_threshold = 0.65  # Adjusted for TF-IDF similarity

        # Initialize LDA model as backup
        self.lda = LatentDirichletAllocation(
            n_components=10,  # Adjust based on your needs
            random_state=42
        )

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

            similarities = cosine_similarity(
                new_topic_vector, existing_topics_matrix)[0]

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
                        [new_topic_dist], existing_topics_dist)[0]
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

    async def extract_metadata(self, content: str, org_id: str) -> DocumentClassification:
        """
        Extract metadata from document content using Azure OpenAI.
        Includes reflection logic to attempt recovery from parsing failures.
        """
        self.logger.info("🎯 Extracting domain metadata")
        self.llm = await get_llm(self.logger, self.config_service)

        try:
            self.logger.info(f"🎯 Extracting departments for org_id: {org_id}")
            departments = await self.arango_service.get_departments(org_id)
            self.logger.info(f"🎯 Departments: {departments}")
            if not departments:
                departments = [dept.value for dept in DepartmentNames]

            # Format department list for the prompt
            department_list = "\n".join(
                f"     - \"{dept}\"" for dept in departments)

            # Format sentiment list for the prompt
            sentiment_list = "\n".join(
                f"     - \"{sentiment}\"" for sentiment in SentimentType.__args__)

            filled_prompt = prompt.replace("{department_list}", department_list).replace(
            "{sentiment_list}", sentiment_list)
            self.prompt_template = PromptTemplate.from_template(filled_prompt)

            self.logger.info(f"🎯 Prompt template: {self.prompt_template}")

            formatted_prompt = self.prompt_template.format(content=content)
            self.logger.info(f"🎯 Prompt formatted successfully {formatted_prompt}")

            messages = [HumanMessage(content=formatted_prompt)]
            response = await self.llm.ainvoke(messages)

            # Clean the response content
            response_text = response.content.strip()
            if response_text.startswith("```json"):
                response_text = response_text.replace("```json", "", 1)
            if response_text.endswith("```"):
                response_text = response_text.rsplit("```", 1)[0]
            response_text = response_text.strip()

            self.logger.info(f"🎯 Response received: {response_text}")

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
                    self.logger.info("🔄 Attempting reflection to fix validation issues")
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
                        HumanMessage(content=reflection_prompt)
                    ]

                    reflection_response = await self.llm.ainvoke(reflection_messages)
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
                    canonical_topics = await self.process_new_topics(parsed_reflection.topics)
                    parsed_reflection.topics = canonical_topics

                    self.logger.info("✅ Reflection successful - validation passed on second attempt")
                    return parsed_reflection

                except Exception as reflection_error:
                    self.logger.error(f"❌ Reflection attempt failed: {str(reflection_error)}")
                    # Re-raise the original error after reflection attempt failed
                    raise ValueError(f"Failed to parse LLM response and reflection attempt failed: {str(parse_error)}")

        except Exception as e:
            self.logger.error(f"❌ Error during metadata extraction: {str(e)}")
            raise

    async def save_metadata_to_arango(self, document_id: str, metadata: DocumentClassification):
        """
        Extract metadata from a document in ArangoDB and create department relationships
        """
        self.logger.info("🚀 Saving metadata to ArangoDB")

        try:
            # Retrieve the document content from ArangoDB
            record = await self.arango_service.get_document(document_id, CollectionNames.RECORDS.value)
            self.logger.info(f"🚀 Record: {record}")

            # Create domain metadata document for batch upsert
            doc = dict(record)
            doc.update({
                "_key": document_id,
                "extractionStatus": "COMPLETED",
                "lastExtractionTimestamp": int(datetime.now(timezone.utc).timestamp())
            })
            docs = [doc]

            self.logger.info(f"🎯 Upserting domain metadata for document: {document_id}")
            await self.arango_service.batch_upsert_nodes(docs, CollectionNames.RECORDS.value)

            # Create relationships with departments
            for department in metadata.departments:
                try:
                    dept_query = f'FOR d IN {CollectionNames.DEPARTMENTS.value} FILTER d.departmentName == @department RETURN d'
                    cursor = self.arango_service.db.aql.execute(
                        dept_query,
                        bind_vars={'department': department}
                    )
                    dept_doc = cursor.next()
                    self.logger.info(f"🚀 Department: {dept_doc}")

                    if dept_doc:
                        edge = {
                            "_from": f"{CollectionNames.RECORDS.value}/{document_id}",
                            "_to": f"{CollectionNames.DEPARTMENTS.value}/{dept_doc['_key']}",
                            "createdAtTimestamp": int(datetime.now(timezone.utc).timestamp())
                        }
                        await self.arango_service.batch_create_edges([edge], CollectionNames.BELONGS_TO_DEPARTMENT.value)
                        self.logger.info(f"🔗 Created relationship between document {document_id} and department {department}")

                except StopIteration:
                    self.logger.warning(f"⚠️ No department found for: {department}")
                    continue
                except Exception as e:
                    self.logger.error(f"❌ Error creating relationship with department {department}: {str(e)}")
                    continue

            # Handle single category
            category_query = f'FOR c IN {CollectionNames.CATEGORIES.value} FILTER c.name == @name RETURN c'
            cursor = self.arango_service.db.aql.execute(
                category_query, bind_vars={'name': metadata.categories})
            try:
                category_doc = cursor.next()
                if category_doc is None:
                    raise KeyError("No category found")
                category_key = category_doc['_key']
            except (StopIteration, KeyError, TypeError):
                category_key = str(uuid.uuid4())
                self.arango_service.db.collection(CollectionNames.CATEGORIES.value).insert({
                    "_key": category_key,
                    "name": metadata.categories,
                })

            # Create category relationship if it doesn't exist
            edge_query = f'''
            FOR e IN {CollectionNames.BELONGS_TO_CATEGORY.value}
            FILTER e._from == @from AND e._to == @to
            RETURN e
            '''
            cursor = self.arango_service.db.aql.execute(edge_query, bind_vars={
                'from': f"records/{document_id}",
                'to': f"categories/{category_key}"
            })
            if not cursor.count():
                self.arango_service.db.collection(CollectionNames.BELONGS_TO_CATEGORY.value).insert({
                    "_from": f"records/{document_id}",
                    "_to": f"categories/{category_key}",
                    "createdAtTimestamp": int(datetime.now(timezone.utc).timestamp())
                })

            # Handle subcategories with similar pattern
            def handle_subcategory(name, level, parent_key, parent_collection):
                collection_name = getattr(CollectionNames, f'SUBCATEGORIES{level}').value
                query = f'FOR s IN {collection_name} FILTER s.name == @name RETURN s'
                cursor = self.arango_service.db.aql.execute(query, bind_vars={'name': name})
                try:
                    doc = cursor.next()
                    if doc is None:
                        raise KeyError("No subcategory found")
                    key = doc['_key']
                except (StopIteration, KeyError, TypeError):
                    key = str(uuid.uuid4())
                    self.arango_service.db.collection(collection_name).insert({
                        "_key": key,
                        "name": name,
                    })

                # Create belongs_to relationship
                edge_query = f'''
                FOR e IN {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER e._from == @from AND e._to == @to
                RETURN e
                '''
                cursor = self.arango_service.db.aql.execute(edge_query, bind_vars={
                    'from': f"records/{document_id}",
                    'to': f"{collection_name}/{key}"
                })
                if not cursor.count():
                    self.arango_service.db.collection(CollectionNames.BELONGS_TO_CATEGORY.value).insert({
                        "_from": f"records/{document_id}",
                        "_to": f"{collection_name}/{key}",
                        "createdAtTimestamp": int(datetime.now(timezone.utc).timestamp())
                    })

                # Create hierarchy relationship
                if parent_key:
                    edge_query = f'''
                    FOR e IN {CollectionNames.INTER_CATEGORY_RELATIONS.value}
                    FILTER e._from == @from AND e._to == @to
                    RETURN e
                    '''
                    cursor = self.arango_service.db.aql.execute(edge_query, bind_vars={
                        'from': f"{collection_name}/{key}",
                        'to': f"{parent_collection}/{parent_key}"
                    })
                    if not cursor.count():
                        self.arango_service.db.collection(CollectionNames.INTER_CATEGORY_RELATIONS.value).insert({
                            "_from": f"{collection_name}/{key}",
                            "_to": f"{parent_collection}/{parent_key}",
                            "createdAtTimestamp": int(datetime.now(timezone.utc).timestamp())
                        })
                return key

            # Process subcategories
            sub1_key = handle_subcategory(metadata.subcategories.level1, '1', category_key, 'categories')
            sub2_key = handle_subcategory(metadata.subcategories.level2, '2', sub1_key, 'subcategories1')
            handle_subcategory(metadata.subcategories.level3, '3', sub2_key, 'subcategories2')

            # Handle languages
            for language in metadata.languages:
                query = f'FOR l IN {CollectionNames.LANGUAGES.value} FILTER l.name == @name RETURN l'
                cursor = self.arango_service.db.aql.execute(query, bind_vars={'name': language})
                try:
                    lang_doc = cursor.next()
                    if lang_doc is None:
                        raise KeyError("No language found")
                    lang_key = lang_doc['_key']
                except (StopIteration, KeyError, TypeError):
                    lang_key = str(uuid.uuid4())
                    self.arango_service.db.collection(CollectionNames.LANGUAGES.value).insert({
                        "_key": lang_key,
                        "name": language,
                    })

                # Create relationship if it doesn't exist
                edge_query = f'''
                FOR e IN {CollectionNames.BELONGS_TO_LANGUAGE.value}
                FILTER e._from == @from AND e._to == @to
                RETURN e
                '''
                cursor = self.arango_service.db.aql.execute(edge_query, bind_vars={
                    'from': f"records/{document_id}",
                    'to': f"languages/{lang_key}"
                })
                if not cursor.count():
                    self.arango_service.db.collection(CollectionNames.BELONGS_TO_LANGUAGE.value).insert({
                        "_from": f"records/{document_id}",
                        "_to": f"languages/{lang_key}",
                        "createdAtTimestamp": int(datetime.now(timezone.utc).timestamp())
                    })

            # Handle topics
            for topic in metadata.topics:
                query = f'FOR t IN {CollectionNames.TOPICS.value} FILTER t.name == @name RETURN t'
                cursor = self.arango_service.db.aql.execute(query, bind_vars={'name': topic})
                try:
                    topic_doc = cursor.next()
                    if topic_doc is None:
                        raise KeyError("No topic found")
                    topic_key = topic_doc['_key']
                except (StopIteration, KeyError, TypeError):
                    topic_key = str(uuid.uuid4())
                    self.arango_service.db.collection(CollectionNames.TOPICS.value).insert({
                        "_key": topic_key,
                        "name": topic,
                    })

                # Create relationship if it doesn't exist
                edge_query = f'''
                FOR e IN {CollectionNames.BELONGS_TO_TOPIC.value}
                FILTER e._from == @from AND e._to == @to
                RETURN e
                '''
                cursor = self.arango_service.db.aql.execute(edge_query, bind_vars={
                    'from': f"records/{document_id}",
                    'to': f"topics/{topic_key}"
                })
                if not cursor.count():
                    self.arango_service.db.collection(CollectionNames.BELONGS_TO_TOPIC.value).insert({
                        "_from": f"records/{document_id}",
                        "_to": f"topics/{topic_key}",
                        "createdAtTimestamp": int(datetime.now(timezone.utc).timestamp())
                    })

            self.logger.info(f"🚀 Metadata saved successfully for document: {document_id}")

            # Add metadata fields to doc
            doc.update({
                "departments": [dept for dept in metadata.departments],
                "categories": metadata.categories,
                "subcategoryLevel1": metadata.subcategories.level1,
                "subcategoryLevel2": metadata.subcategories.level2,
                "subcategoryLevel3": metadata.subcategories.level3,
                "topics": metadata.topics,
                "languages": metadata.languages
            })

            return doc

        except Exception as e:
            self.logger.error(f"❌ Error saving metadata to ArangoDB: {str(e)}")
            raise
