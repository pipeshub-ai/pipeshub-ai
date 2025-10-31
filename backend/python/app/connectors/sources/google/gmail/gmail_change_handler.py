import uuid

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class GmailChangeHandler:
    def __init__(self, config_service: ConfigurationService, arango_service, logger) -> None:
        self.config_service = config_service
        self.arango_service = arango_service
        self.logger = logger

    async def process_changes(self, user_service, changes, org_id, user) -> bool:
        """Process changes since last sync time"""
        self.logger.info("🚀 Processing changes")
        self.logger.info(f"changes: {changes}")
        try:
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            user_id = user.get("userId")
            for change in changes.get("history", []):
                self.logger.info(f"🚀 Processing change: {change}")

                account_type = await self.arango_service.get_account_type(org_id)

                # Handle message additions
                messages_to_add = []
                seen_message_ids = set()  # Track unique message IDs
                if "messagesAdded" in change:
                    for message_added in change["messagesAdded"]:
                        message = message_added.get("message", {})
                        message_id = message.get("id")
                        if message_id and message_id not in seen_message_ids:
                            seen_message_ids.add(message_id)
                            messages_to_add.append(message)

                if "labelsAdded" in change:
                    for label_added in change["labelsAdded"]:
                        message = label_added.get("message", {})
                        message_id = message.get("id")
                        label_ids = label_added.get("labelIds", [])
                        if any(label in ["INBOX", "SENT"] for label in label_ids) and message_id not in seen_message_ids:
                            seen_message_ids.add(message_id)
                            messages_to_add.append(message)

                for message in messages_to_add:
                    message_id = message.get("id")
                    if not message_id:
                        continue

                    # Check if message already exists
                    self.logger.debug(
                        "🔍 Checking if message %s exists in ArangoDB", message_id
                    )
                    existing_message = self.arango_service.db.aql.execute(
                        f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @message_id RETURN doc",
                        bind_vars={"message_id": message_id},
                    )
                    existing_message = next(existing_message, None)

                    if existing_message:
                        self.logger.debug(
                            "♻️ Message %s already exists in ArangoDB, skipping",
                            message_id,
                        )
                        continue

                    # Fetch full message details
                    message_data = await user_service.get_message(message_id)
                    if not message_data:
                        continue

                    # Get attachments for this message
                    attachments = await user_service.list_attachments(
                        message_data, org_id, user, account_type
                    )

                    # Extract headers
                    headers = message_data.get("headers", {})

                    message_record = {
                        "_key": str(uuid.uuid4()),
                        "threadId": message_data.get("threadId"),
                        "isParent": message_data.get("threadId")
                        == message_id,  # Check if threadId and messageId are same
                        "internalDate": message_data.get("internalDate"),
                        "subject": headers.get("Subject", "No Subject"),
                        "from": headers.get("From", [""])[0],
                        "to": headers.get("To", []),
                        "cc": headers.get("Cc", []),
                        "bcc": headers.get("Bcc", []),
                        "messageIdHeader": headers.get("Message-ID", None),
                        "historyId": message_data.get("historyId"),
                        "webUrl": f"https://mail.google.com/mail?authuser={{user.email}}#all/{message_id}",
                        "labelIds": message_data.get("labelIds", []),
                    }

                    record = {
                        "_key": message_record["_key"],
                        "orgId": org_id,
                        "recordName": headers.get("Subject", "No Subject"),
                        "externalRecordId": message_id,
                        "externalRevisionId": None,
                        "recordType": RecordTypes.MAIL.value,
                        "version": 0,
                        "origin": OriginTypes.CONNECTOR.value,
                        "connectorName": Connectors.GOOGLE_MAIL.value,
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "lastSyncTimestamp": get_epoch_timestamp_in_ms(),
                        "sourceCreatedAtTimestamp": message.get("internalDate"),
                        "sourceLastModifiedTimestamp": message.get("internalDate"),
                        "isDeleted": False,
                        "isArchived": False,
                        "virtualRecordId": None,
                        "lastIndexTimestamp": None,
                        "lastExtractionTimestamp": None,
                        "indexingStatus": "NOT_STARTED",
                        "extractionStatus": "NOT_STARTED",
                        "webUrl": f"https://mail.google.com/mail?authuser={{user.email}}#all/{message_id}",
                        "isLatestVersion": True,
                        "isDirty": False,
                        "reason": None,
                        "mimeType": "text/html",
                    }

                    # Convert record to dictionary if it's a Record object
                    record_dict = record.to_dict() if hasattr(record, 'to_dict') else record

                    is_of_type_record = {
                        "_from": f'{CollectionNames.RECORDS.value}/{record_dict["_key"]}',
                        "_to": f'{CollectionNames.MAILS.value}/{message_record["_key"]}',
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }

                    # Start transaction
                    txn = self.arango_service.db.begin_transaction(
                        read=[
                            CollectionNames.MAILS.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.PERMISSIONS.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.RECORD_RELATIONS.value,
                        ],
                        write=[
                            CollectionNames.MAILS.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.PERMISSIONS.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.RECORD_RELATIONS.value,
                        ],
                    )

                    try:
                        # Store message
                        await self.arango_service.batch_upsert_nodes(
                            [message_record],
                            collection=CollectionNames.MAILS.value,
                            transaction=txn,
                        )
                        await self.arango_service.batch_upsert_nodes(
                            [record_dict],
                            collection=CollectionNames.RECORDS.value,
                            transaction=txn,
                        )
                        await self.arango_service.batch_create_edges(
                            [is_of_type_record],
                            collection=CollectionNames.IS_OF_TYPE.value,
                            transaction=txn,
                        )

                        # Store attachments if any
                        if attachments:
                            attachment_records = []
                            record_records = []
                            is_of_type_records = []
                            record_relations = []
                            for attachment in attachments:
                                attachment_record = {
                                    "_key": str(uuid.uuid4()),
                                    "orgId": org_id,
                                    "isFile": True,
                                    "mimeType": attachment.get("mimeType"),
                                    "name": attachment.get("filename"),
                                    "sizeInBytes": int(attachment.get("size", 0)),
                                    "extension": attachment.get("extension"),
                                    "webUrl": f"https://mail.google.com/mail?authuser={{user.email}}#all/{message_id}",
                                }

                                attachment_records.append(attachment_record)

                                record = {
                                    "_key": attachment_record["_key"],
                                    "orgId": org_id,
                                    "recordName": attachment.get("filename"),
                                    "recordType": RecordTypes.FILE.value,
                                    "version": 0,
                                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                    "sourceCreatedAtTimestamp": message.get(
                                        "internalDate"
                                    ),
                                    "sourceLastModifiedTimestamp": message.get(
                                        "internalDate"
                                    ),
                                    "externalRecordId": attachment.get(
                                        "attachment_id"
                                    ),
                                    "externalRevisionId": None,
                                    "origin": OriginTypes.CONNECTOR.value,
                                    "connectorName": Connectors.GOOGLE_MAIL.value,
                                    "virtualRecordId": None,
                                    "lastSyncTimestamp": get_epoch_timestamp_in_ms(),
                                    "isDeleted": False,
                                    "isArchived": False,
                                    "lastIndexTimestamp": None,
                                    "lastExtractionTimestamp": None,
                                    "indexingStatus": "NOT_STARTED",
                                    "extractionStatus": "NOT_STARTED",
                                    "isLatestVersion": True,
                                    "isDirty": False,
                                    "reason": None,
                                    "webUrl": f"https://mail.google.com/mail?authuser={{user.email}}#all/{message_id}",
                                    "mimeType": attachment.get("mimeType"),
                                }
                                is_of_type_record = {
                                    "_from": f"{CollectionNames.RECORDS.value}/{record['_key']}",
                                    "_to": f"{CollectionNames.FILES.value}/{attachment_record['_key']}",
                                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                }
                                record_relation = {
                                    "_from": f"{CollectionNames.RECORDS.value}/{message_record['_key']}",
                                    "_to": f"{CollectionNames.RECORDS.value}/{attachment_record['_key']}",
                                    "relationType": RecordRelations.ATTACHMENT.value,
                                }

                                record_records.append(record)
                                is_of_type_records.append(is_of_type_record)
                                record_relations.append(record_relation)
                            await self.arango_service.batch_upsert_nodes(
                                attachment_records,
                                collection=CollectionNames.FILES.value,
                                transaction=txn,
                            )

                            await self.arango_service.batch_upsert_nodes(
                                record_records,
                                collection=CollectionNames.RECORDS.value,
                                transaction=txn,
                            )

                            await self.arango_service.batch_create_edges(
                                is_of_type_records,
                                collection=CollectionNames.IS_OF_TYPE.value,
                                transaction=txn,
                            )

                            await self.arango_service.batch_create_edges(
                                record_relations,
                                collection=CollectionNames.RECORD_RELATIONS.value,
                                transaction=txn,
                            )

                        # Store permissions
                        permission_records = []
                        for email_type in ["from", "to", "cc", "bcc"]:
                            emails = message_record.get(email_type, [])
                            if isinstance(emails, str):
                                emails = [emails]

                            for email in emails:
                                if not email:
                                    continue

                                entity_id = await self.arango_service.get_entity_id_by_email(
                                    email
                                )
                                # Default to PEOPLE/USER; override if found in USERS or GROUPS
                                entityType = CollectionNames.PEOPLE.value
                                permType = "USER"
                                if entity_id:
                                    # Check if entity exists in users or groups
                                    if self.arango_service.db.collection(
                                        CollectionNames.USERS.value
                                    ).has(entity_id):
                                        entityType = CollectionNames.USERS.value
                                        permType = "USER"
                                    elif self.arango_service.db.collection(
                                        CollectionNames.GROUPS.value
                                    ).has(entity_id):
                                        entityType = CollectionNames.GROUPS.value
                                        permType = "GROUP"
                                else:
                                    # Save entity in people collection
                                    entity_id = str(uuid.uuid4())
                                    people_record = await self.arango_service.save_to_people_collection(
                                        entity_id, email
                                    )
                                    if people_record:
                                        entity_id = people_record["_key"]
                                    else:
                                        self.logger.error(
                                            "❌ Failed to save entity %s to people collection",
                                            entity_id,
                                        )
                                        continue

                                permission_records.append(
                                    {
                                        "_to": f"{entityType}/{entity_id}",
                                        "_from": f"{CollectionNames.RECORDS.value}/{message_record['_key']}",
                                        "externalPermissionId": None,
                                        "type": permType,
                                        "role": "READER",
                                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                        "lastUpdatedTimestampAtSource": get_epoch_timestamp_in_ms(),
                                    }
                                )
                                if attachments:
                                    for attachment_record in attachment_records:
                                        permission_records.append(
                                            {
                                                "_to": f"{entityType}/{entity_id}",
                                                "_from": f"{CollectionNames.RECORDS.value}/{attachment_record['_key']}",
                                                "externalPermissionId": None,
                                                "type": permType,
                                                "role": "READER",
                                                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                                                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                                                "lastUpdatedTimestampAtSource": get_epoch_timestamp_in_ms(),
                                            }
                                        )

                        if permission_records:
                            await self.arango_service.batch_create_edges(
                                permission_records,
                                collection=CollectionNames.PERMISSIONS.value,
                                transaction=txn,
                            )

                        txn.commit_transaction()

                    except Exception as e:
                        txn.abort_transaction()
                        self.logger.error(
                            "❌ Error processing message addition: "
                            f"org_id={org_id}, user_id={user_id}, "
                            f"message_id={message_id}, thread_id={message.get('threadId', 'N/A')}, "
                            f"history_id={change.get('id', 'N/A')}\n"
                            f"Error details: {str(e)}\n"
                            f"Message data: {message_data}"
                        )
                        # Increment error metric if you have metrics service
                        if hasattr(self, 'metrics_service'):
                            await self.metrics_service.increment_counter(
                                'gmail_change_processing_errors',
                                {'org_id': org_id, 'error_type': type(e).__name__}
                            )
                        continue

                    message_event = {
                        "orgId": org_id,
                        "recordId": message_record["_key"],
                        "recordName": headers.get("Subject", "No Subject"),
                        "recordType": RecordTypes.MAIL.value,
                        "recordVersion": 0,
                        "eventType": EventTypes.NEW_RECORD.value,
                        "body": message_data.get("body", ""),
                        "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{message_record['_key']}/signedUrl",
                        "connectorName": Connectors.GOOGLE_MAIL.value,
                        "origin": OriginTypes.CONNECTOR.value,
                        "mimeType": "text/gmail_content",
                        "createdAtSourceTimestamp": int(
                            message.get(
                                "internalDate",
                                get_epoch_timestamp_in_ms(),
                            )
                        ),
                        "modifiedAtSourceTimestamp": int(
                            message.get(
                                "internalDate",
                                get_epoch_timestamp_in_ms(),
                            )
                        ),
                    }

                    # SEND KAFKA EVENT FOR INDEXING
                    await self.arango_service.kafka_service.send_event_to_kafka(
                        message_event
                    )
                    self.logger.info(
                        "📨 Sent Kafka reindexing event for record %s",
                        record_dict["_key"],
                    )

                    if attachments:
                        for attachment in attachment_records:
                            attachment_key = attachment["_key"]
                            extension = (
                                attachment.get("extension")
                                if attachment.get("extension")
                                else attachment.get("recordName").split(".")[-1]
                            )
                            attachment_event = {
                                "orgId": org_id,
                                "recordId": attachment_key,
                                "recordName": attachment.get(
                                    "recordName", "Unnamed Attachment"
                                ),
                                "recordType": RecordTypes.ATTACHMENT.value,
                                "recordVersion": 0,
                                "eventType": EventTypes.NEW_RECORD.value,
                                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{attachment_key}/signedUrl",
                                "connectorName": Connectors.GOOGLE_MAIL.value,
                                "extension": extension,
                                "origin": OriginTypes.CONNECTOR.value,
                                "mimeType": attachment.get(
                                    "mimeType", "application/octet-stream"
                                ),
                                "createdAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                                "modifiedAtSourceTimestamp": get_epoch_timestamp_in_ms(),
                            }
                            await self.arango_service.kafka_service.send_event_to_kafka(
                                attachment_event
                            )
                            self.logger.info(
                                "📨 Sent Kafka Indexing event for attachment %s",
                                attachment_key,
                            )

                # Handle message deletions
                messages_to_delete = []
                seen_message_ids = set()  # Reset for deletions
                if "messagesDeleted" in change:
                    for message_deleted in change["messagesDeleted"]:
                        message = message_deleted.get("message", {})
                        message_id = message.get("id")
                        if message_id and message_id not in seen_message_ids:
                            seen_message_ids.add(message_id)
                            messages_to_delete.append(message)

                if "labelsAdded" in change:
                    for label_added in change["labelsAdded"]:
                        message = label_added.get("message", {})
                        message_id = message.get("id")
                        label_ids = label_added.get("labelIds", [])
                        if "TRASH" in label_ids and message_id not in seen_message_ids:
                            seen_message_ids.add(message_id)
                            messages_to_delete.append(message)

                for message in messages_to_delete:
                    message_id = message.get("id")
                    if not message_id:
                        continue
                    try:
                        # Check if message exists before attempting deletion
                        existing_message = next(
                            self.arango_service.db.aql.execute(
                                f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @message_id RETURN doc",
                                bind_vars={"message_id": message_id},
                            ),
                            None,
                        )

                        if not existing_message:
                            self.logger.debug(
                                "⚠️ Message %s not found in ArangoDB, skipping deletion",
                                message_id,
                            )
                            continue

                        # Get associated attachment records
                        attachment_records = list(self.arango_service.db.aql.execute(
                            """
                            FOR r IN recordRelations
                                FILTER r._from == @message_id
                                AND r.relationType == @relation_type
                                RETURN DOCUMENT(r._to)
                            """,
                            bind_vars={
                                "message_id": f'records/{existing_message["_key"]}',
                                "relation_type": RecordRelations.ATTACHMENT.value
                            }
                        ))

                        txn = self.arango_service.db.begin_transaction(
                            read=[
                                CollectionNames.FILES.value,
                                CollectionNames.MAILS.value,
                                CollectionNames.RECORDS.value,
                                CollectionNames.RECORD_RELATIONS.value,
                                CollectionNames.IS_OF_TYPE.value,
                                CollectionNames.USERS.value,
                                CollectionNames.GROUPS.value,
                                CollectionNames.ORGS.value,
                                CollectionNames.ANYONE.value,
                                CollectionNames.PERMISSIONS.value,
                                CollectionNames.BELONGS_TO.value,
                                CollectionNames.BELONGS_TO_DEPARTMENT.value,
                                CollectionNames.BELONGS_TO_CATEGORY.value,
                                CollectionNames.BELONGS_TO_LANGUAGE.value,
                                CollectionNames.BELONGS_TO_TOPIC.value,
                            ],
                            write=[
                                CollectionNames.FILES.value,
                                CollectionNames.MAILS.value,
                                CollectionNames.RECORDS.value,
                                CollectionNames.RECORD_RELATIONS.value,
                                CollectionNames.IS_OF_TYPE.value,
                                CollectionNames.USERS.value,
                                CollectionNames.GROUPS.value,
                                CollectionNames.ORGS.value,
                                CollectionNames.ANYONE.value,
                                CollectionNames.PERMISSIONS.value,
                                CollectionNames.BELONGS_TO.value,
                                CollectionNames.BELONGS_TO_DEPARTMENT.value,
                                CollectionNames.BELONGS_TO_CATEGORY.value,
                                CollectionNames.BELONGS_TO_LANGUAGE.value,
                                CollectionNames.BELONGS_TO_TOPIC.value,
                            ],
                        )

                        try:
                            # Delete permissions for main message
                            self.arango_service.db.aql.execute(
                                """
                                FOR p IN permissions
                                FILTER p._to == @message_id OR p._from == @message_id
                                REMOVE p IN permissions
                                """,
                                bind_vars={
                                    "message_id": f'{CollectionNames.RECORDS.value}/{existing_message["_key"]}'
                                },
                            )

                            # Send delete events for attachments and delete their permissions
                            for attachment in attachment_records:
                                # Delete permissions for attachment
                                self.arango_service.db.aql.execute(
                                    """
                                    FOR p IN permissions
                                    FILTER p._to == @attachment_id OR p._from == @attachment_id
                                    REMOVE p IN permissions
                                    """,
                                    bind_vars={
                                        "attachment_id": f'{CollectionNames.RECORDS.value}/{attachment["_key"]}'
                                    },
                                )

                                attachment_event = {
                                    "orgId": org_id,
                                    "recordId": attachment["_key"],
                                    "virtualRecordId":attachment.get("virtualRecordId", None),
                                    "recordName": attachment.get("recordName", "Unnamed Attachment"),
                                    "recordType": RecordTypes.ATTACHMENT.value,
                                    "recordVersion": 0,
                                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{attachment['_key']}/signedUrl",
                                    "eventType": EventTypes.DELETE_RECORD.value,
                                    "connectorName": Connectors.GOOGLE_MAIL.value,
                                    "origin": OriginTypes.CONNECTOR.value,
                                    "mimeType": attachment.get("mimeType"),
                                }
                                await self.arango_service.kafka_service.send_event_to_kafka(
                                    attachment_event
                                )
                                self.logger.info(
                                    "📨 Sent Kafka Delete event for attachment %s",
                                    attachment["_key"],
                                )
                                await self.arango_service.delete_records_and_relations(
                                    attachment["_key"],
                                    hard_delete=True,
                                    transaction=txn
                                )

                            # Send delete event for message
                            message_event = {
                                "orgId": org_id,
                                "recordId": existing_message["_key"],
                                "virtualRecordId": existing_message.get("virtualRecordId", None),
                                "recordName": existing_message.get("recordName", "No Subject"),
                                "recordType": RecordTypes.MAIL.value,
                                "recordVersion": 0,
                                "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/gmail/record/{existing_message['_key']}/signedUrl",
                                "eventType": EventTypes.DELETE_RECORD.value,
                                "connectorName": Connectors.GOOGLE_MAIL.value,
                                "origin": OriginTypes.CONNECTOR.value,
                            }
                            await self.arango_service.kafka_service.send_event_to_kafka(
                                message_event
                            )
                            self.logger.info(
                                "📨 Sent Kafka Delete event for message %s",
                                existing_message["_key"],
                            )

                            # Delete the message record
                            await self.arango_service.delete_records_and_relations(
                                existing_message["_key"],
                                hard_delete=True,
                                transaction=txn,
                            )

                            txn.commit_transaction()

                        except Exception as e:
                            txn.abort_transaction()
                            self.logger.error(
                                "❌ Error processing message deletion: "
                                f"org_id={org_id}, user_id={user_id}, "
                                f"message_id={message_id}, thread_id={message.get('threadId', 'N/A')}, "
                                f"history_id={change.get('id', 'N/A')}\n"
                                f"Error details: {str(e)}\n"
                                f"Message data: {message}"
                            )
                            # Increment error metric if you have metrics service
                            if hasattr(self, 'metrics_service'):
                                await self.metrics_service.increment_counter(
                                    'gmail_deletion_processing_errors',
                                    {'org_id': org_id, 'error_type': type(e).__name__}
                                )
                            continue

                    except Exception as e:
                        self.logger.error(
                            "❌ Error checking message for deletion: "
                            f"org_id={org_id}, user_id={user_id}, "
                            f"message_id={message_id}, thread_id={message.get('threadId', 'N/A')}, "
                            f"history_id={change.get('id', 'N/A')}\n"
                            f"Error details: {str(e)}"
                        )
                        if hasattr(self, 'metrics_service'):
                            await self.metrics_service.increment_counter(
                                'gmail_deletion_check_errors',
                                {'org_id': org_id, 'error_type': type(e).__name__}
                            )
                        continue
            return True
        except Exception as e:
            self.logger.error(
                "❌ Error processing changes batch: "
                f"org_id={org_id}, user_id={user_id}\n"
                f"Error details: {str(e)}"
            )
            if hasattr(self, 'metrics_service'):
                await self.metrics_service.increment_counter(
                    'gmail_changes_batch_errors',
                    {'org_id': org_id, 'error_type': type(e).__name__}
                )
            return False
