import uuid

from app.config.constants.arangodb import (
    CollectionNames,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.blocks import SemanticMetadata
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class GraphDBTransformer(Transformer):
    def __init__(self, graph_provider: IGraphDBProvider, logger) -> None:
        super().__init__()
        self.logger = logger
        self.graph_data_store = GraphDataStore(logger, graph_provider)

    async def apply(self, ctx: TransformContext) -> None:
        record = ctx.record
        metadata = record.semantic_metadata
        virtual_record_id = record.virtual_record_id
        record_id = record.id

        if metadata is None:
            try:
                async with self.graph_data_store.transaction() as tx_store:
                    # Update extraction status for the record
                    timestamp = get_epoch_timestamp_in_ms()
                    # Update indexing status for the record
                    status_doc = {
                        "id": record_id,
                        "extractionStatus": "FAILED",
                        "lastExtractionTimestamp": timestamp,
                        "indexingStatus": "COMPLETED",
                        "isDirty": False,
                        "virtualRecordId": virtual_record_id,
                        "lastIndexTimestamp": timestamp,
                    }
                    self.logger.info(
                        "🎯 Upserting indexing status metadata for document"
                    )
                    await tx_store.batch_upsert_nodes(
                        [status_doc], CollectionNames.RECORDS.value
                    )
            except Exception as e:
                self.logger.error(f"❌ Error saving metadata to graph database: {str(e)}")
                raise
        else:
            is_vlm_ocr_processed = getattr(record, 'is_vlm_ocr_processed', False)
            await self.save_metadata_to_db(record_id, metadata, virtual_record_id, is_vlm_ocr_processed)

    async def save_metadata_to_db(
        self,  record_id: str, metadata: SemanticMetadata, virtual_record_id: str, is_vlm_ocr_processed: bool = False
    ) -> None:
        """
        Extract metadata from a document and create department relationships
        """
        self.logger.info("🚀 Saving metadata to graph database")
        async with self.graph_data_store.transaction() as tx_store:
            try:
                # Retrieve the document content from graph database
                record = await tx_store.get_record_by_key(
                    record_id
                )

                if record is None:
                    self.logger.error(f"❌ Record {record_id} not found in database")
                    raise Exception(f"Record {record_id} not found in database")


                # Create relationships with departments
                for department in metadata.departments:
                    try:
                        # Find department by name using graph_provider
                        dept_nodes = await tx_store.get_nodes_by_filters(
                            CollectionNames.DEPARTMENTS.value,
                            {"departmentName": department}
                        )

                        if not dept_nodes:
                            self.logger.warning(f"⚠️ No department found for: {department}")
                            continue

                        dept_doc = dept_nodes[0]
                        # Handle both id and _key formats
                        dept_key = dept_doc.get("_key") or dept_doc.get("id")
                        self.logger.info(f"🚀 Department: {dept_doc}")

                        if dept_key:
                            # Check if edge already exists
                            existing_edge = await tx_store.get_edge(
                                from_id=record_id,
                                from_collection=CollectionNames.RECORDS.value,
                                to_id=dept_key,
                                to_collection=CollectionNames.DEPARTMENTS.value,
                                collection=CollectionNames.BELONGS_TO_DEPARTMENT.value
                            )

                            if not existing_edge:
                                edge = {
                                    "from_id": record_id,
                                    "from_collection": CollectionNames.RECORDS.value,
                                    "to_id": dept_key,
                                    "to_collection": CollectionNames.DEPARTMENTS.value,
                                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                }
                                await tx_store.batch_create_edges(
                                    [edge], CollectionNames.BELONGS_TO_DEPARTMENT.value
                                )
                                self.logger.info(
                                    f"🔗 Created relationship between document {record_id} and department {department}"
                                )
                            else:
                                self.logger.info(
                                    f"🔗 Relationship between document {record_id} and department {department} already exists"
                                )

                    except Exception as e:
                        self.logger.error(
                            f"❌ Error creating relationship with department {department}: {str(e)}"
                        )
                        continue

                # Handle single category
                category_nodes = await tx_store.get_nodes_by_filters(
                    CollectionNames.CATEGORIES.value,
                    {"name": metadata.categories[0]}
                )

                if category_nodes:
                    category_doc = category_nodes[0]
                    category_key = category_doc.get("_key") or category_doc.get("id")
                else:
                    category_key = str(uuid.uuid4())
                    # Create category node
                    category_node = {
                        "id": category_key,
                        "name": metadata.categories[0],
                    }
                    await tx_store.batch_upsert_nodes(
                        [category_node],
                        CollectionNames.CATEGORIES.value
                    )

                # Create category relationship if it doesn't exist
                existing_category_edge = await tx_store.get_edge(
                    from_id=record_id,
                    from_collection=CollectionNames.RECORDS.value,
                    to_id=category_key,
                    to_collection=CollectionNames.CATEGORIES.value,
                    collection=CollectionNames.BELONGS_TO_CATEGORY.value
                )

                if not existing_category_edge:
                    category_edge = {
                        "from_id": record_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": category_key,
                        "to_collection": CollectionNames.CATEGORIES.value,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    await tx_store.batch_create_edges(
                        [category_edge],
                        CollectionNames.BELONGS_TO_CATEGORY.value
                    )

                # Handle subcategories with similar pattern
                async def handle_subcategory(name, level, parent_key, parent_collection) -> str:
                    collection_name = getattr(
                        CollectionNames, f"SUBCATEGORIES{level}"
                    ).value

                    # Find subcategory by name
                    subcategory_nodes = await tx_store.get_nodes_by_filters(
                        collection_name,
                        {"name": name}
                    )

                    if subcategory_nodes:
                        doc = subcategory_nodes[0]
                        key = doc.get("_key") or doc.get("id")
                    else:
                        key = str(uuid.uuid4())
                        # Create subcategory node
                        subcategory_node = {
                            "id": key,
                            "name": name,
                        }
                        await tx_store.batch_upsert_nodes(
                            [subcategory_node],
                            collection_name
                        )

                    # Create belongs_to relationship if it doesn't exist
                    existing_belongs_edge = await tx_store.get_edge(
                        from_id=record_id,
                        from_collection=CollectionNames.RECORDS.value,
                        to_id=key,
                        to_collection=collection_name,
                        collection=CollectionNames.BELONGS_TO_CATEGORY.value
                    )

                    if not existing_belongs_edge:
                        belongs_edge = {
                            "from_id": record_id,
                            "from_collection": CollectionNames.RECORDS.value,
                            "to_id": key,
                            "to_collection": collection_name,
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        await tx_store.batch_create_edges(
                            [belongs_edge],
                            CollectionNames.BELONGS_TO_CATEGORY.value
                        )

                    # Create hierarchy relationship if parent exists
                    if parent_key:
                        existing_hierarchy_edge = await tx_store.get_edge(
                            from_id=key,
                            from_collection=collection_name,
                            to_id=parent_key,
                            to_collection=parent_collection,
                            collection=CollectionNames.INTER_CATEGORY_RELATIONS.value
                        )

                        if not existing_hierarchy_edge:
                            hierarchy_edge = {
                                "from_id": key,
                                "from_collection": collection_name,
                                "to_id": parent_key,
                                "to_collection": parent_collection,
                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                            }
                            await tx_store.batch_create_edges(
                                [hierarchy_edge],
                                CollectionNames.INTER_CATEGORY_RELATIONS.value
                            )
                    return key

                # Process subcategories
                sub1_key = None
                sub2_key = None
                if metadata.sub_category_level_1:
                    sub1_key = await handle_subcategory(
                        metadata.sub_category_level_1, "1", category_key, "categories"
                    )
                if metadata.sub_category_level_2 and sub1_key:
                    sub2_key = await handle_subcategory(
                        metadata.sub_category_level_2, "2", sub1_key, "subcategories1"
                    )
                if metadata.sub_category_level_3 and sub2_key:
                    await handle_subcategory(
                        metadata.sub_category_level_3, "3", sub2_key, "subcategories2"
                    )

                # Handle languages
                for language in metadata.languages:
                    # Find language by name
                    language_nodes = await tx_store.get_nodes_by_filters(
                        CollectionNames.LANGUAGES.value,
                        {"name": language}
                    )

                    if language_nodes:
                        lang_doc = language_nodes[0]
                        lang_key = lang_doc.get("_key") or lang_doc.get("id")
                    else:
                        lang_key = str(uuid.uuid4())
                        # Create language node
                        language_node = {
                            "id": lang_key,
                            "name": language,
                        }
                        await tx_store.batch_upsert_nodes(
                            [language_node],
                            CollectionNames.LANGUAGES.value
                        )

                    # Create relationship if it doesn't exist
                    existing_lang_edge = await tx_store.get_edge(
                        from_id=record_id,
                        from_collection=CollectionNames.RECORDS.value,
                        to_id=lang_key,
                        to_collection=CollectionNames.LANGUAGES.value,
                        collection=CollectionNames.BELONGS_TO_LANGUAGE.value
                    )

                    if not existing_lang_edge:
                        lang_edge = {
                            "from_id": record_id,
                            "from_collection": CollectionNames.RECORDS.value,
                            "to_id": lang_key,
                            "to_collection": CollectionNames.LANGUAGES.value,
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        await tx_store.batch_create_edges(
                            [lang_edge],
                            CollectionNames.BELONGS_TO_LANGUAGE.value
                        )

                # Handle topics
                for topic in metadata.topics:
                    # Find topic by name
                    topic_nodes = await tx_store.get_nodes_by_filters(
                        CollectionNames.TOPICS.value,
                        {"name": topic}
                    )

                    if topic_nodes:
                        topic_doc = topic_nodes[0]
                        topic_key = topic_doc.get("_key") or topic_doc.get("id")
                    else:
                        topic_key = str(uuid.uuid4())
                        # Create topic node
                        topic_node = {
                            "id": topic_key,
                            "name": topic,
                        }
                        await tx_store.batch_upsert_nodes(
                            [topic_node],
                            CollectionNames.TOPICS.value
                        )

                    # Create relationship if it doesn't exist
                    existing_topic_edge = await tx_store.get_edge(
                        from_id=record_id,
                        from_collection=CollectionNames.RECORDS.value,
                        to_id=topic_key,
                        to_collection=CollectionNames.TOPICS.value,
                        collection=CollectionNames.BELONGS_TO_TOPIC.value
                    )

                    if not existing_topic_edge:
                        topic_edge = {
                            "from_id": record_id,
                            "from_collection": CollectionNames.RECORDS.value,
                            "to_id": topic_key,
                            "to_collection": CollectionNames.TOPICS.value,
                            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        }
                        await tx_store.batch_create_edges(
                            [topic_edge],
                            CollectionNames.BELONGS_TO_TOPIC.value
                        )

                self.logger.info(
                    "🚀 Metadata saved successfully for document"
                )

                # Update extraction status for the record
                timestamp = get_epoch_timestamp_in_ms()
                # Update extraction status for the record
                status_doc = {
                    "id": record_id,
                    "extractionStatus": "COMPLETED",
                    "lastExtractionTimestamp": timestamp,
                    "indexingStatus": "COMPLETED",
                    "isDirty": False,
                    "virtualRecordId": virtual_record_id,
                    "lastIndexTimestamp": timestamp,
                }

                if is_vlm_ocr_processed:
                    status_doc["isVLMOcrProcessed"] = True

                self.logger.info(
                    "🎯 Upserting extraction status metadata for document"
                )
                await tx_store.batch_upsert_nodes(
                    [status_doc], CollectionNames.RECORDS.value
                )


            except Exception as e:
                self.logger.error(f"❌ Error saving metadata to graph database: {str(e)}")
                raise

