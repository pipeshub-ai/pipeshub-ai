import uuid

from app.config.constants.arangodb import (
    CollectionNames,
)
from app.core.ai_arango_service import ArangoService
from app.models.blocks import SemanticMetadata
from app.modules.transformers.transformer import TransformContext, Transformer
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class Arango(Transformer):
    def __init__(self, arango_service: ArangoService, logger) -> None:
        super().__init__()
        self.arango_service = arango_service
        self.logger = logger

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        metadata = record.semantic_metadata
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        await self.save_metadata_to_db( record_id, metadata, virtual_record_id)

    async def save_metadata_to_db(
        self,  record_id: str, metadata: SemanticMetadata, virtual_record_id: str
    ) -> None:
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
                category_query, bind_vars={"name": metadata.categories[0]}
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
                        "name": metadata.categories[0],
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
            if metadata.sub_category_level_1:
                sub1_key = handle_subcategory(
                    metadata.sub_category_level_1, "1", category_key, "categories"
                )
            if metadata.sub_category_level_2 and sub1_key:
                sub2_key = handle_subcategory(
                    metadata.sub_category_level_2, "2", sub1_key, "subcategories1"
                )
            if metadata.sub_category_level_3 and sub2_key:
                handle_subcategory(
                    metadata.sub_category_level_3, "3", sub2_key, "subcategories2"
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

            self.logger.info(
                "🚀 Metadata saved successfully for document"
            )

            doc.update(
                {
                    "extractionStatus": "COMPLETED",
                    "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
                }
            )
            docs = [doc]

            self.logger.info(
                "🎯 Upserting domain metadata for document"
            )
            await self.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )

        except Exception as e:
            self.logger.error(f"❌ Error saving metadata to ArangoDB: {str(e)}")
            raise

