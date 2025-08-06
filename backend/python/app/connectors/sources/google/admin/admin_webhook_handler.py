from typing import Any, Dict

from app.config.constants.arangodb import CollectionNames


class AdminWebhookHandler:
    def __init__(self, logger, admin_service) -> None:
        self.admin_service = admin_service
        self.arango_service = admin_service.arango_service
        self.logger = logger

    async def process_notification(self, event_type: str, body: Dict[str, Any]) -> None:
        """Process incoming admin webhook notifications"""
        try:
            events = body.get("events", [])
            if not events:
                self.logger.error("No events found in webhook notification")
                return

            for event in events:
                event_name = event.get("name")
                if event_name == "CREATE_USER":
                    await self._handle_user_creation(event)
                elif event_name == "DELETE_USER":
                    await self._handle_user_deletion(event)
                elif event_name == "CREATE_GROUP":
                    await self._handle_group_creation(event)
                elif event_name == "DELETE_GROUP":
                    await self._handle_group_deletion(event)
                elif event_name == "ADD_GROUP_MEMBER":
                    await self._handle_group_member_addition(event)
                elif event_name == "REMOVE_GROUP_MEMBER":
                    await self._handle_group_member_removal(event)
                else:
                    self.logger.info(f"Unhandled admin event type: {event_name}")

        except Exception as e:
            self.logger.error(f"Error processing admin webhook notification: {str(e)}")
            raise

    async def _handle_user_creation(self, event: Dict[str, Any]) -> None:
        """Handle user creation event"""
        try:
            # Extract user email from the parameters array
            user_email = None
            for param in event.get("parameters", []):
                if param.get("name") == "USER_EMAIL":
                    user_email = param.get("value")
                    break

            if not user_email:
                self.logger.error("No user email found in CREATE_USER notification")
                return

            orgs = await self.arango_service.get_all_orgs(active=True)
            org = orgs[0]
            org_id = org["_key"]

            self.logger.info(f"Processing user creation for: {user_email}")

            # Call user service to handle the new user
            await self.admin_service.handle_new_user(org_id, user_email)

        except Exception as e:
            self.logger.error(f"Error handling user creation: {str(e)}")
            raise

    async def _handle_user_deletion(self, event: Dict[str, Any]) -> None:
        """Handle user deletion event"""
        try:
            # Extract user email from the parameters array
            user_email = None
            for param in event.get("parameters", []):
                if param.get("name") == "USER_EMAIL":
                    user_email = param.get("value")
                    break

            if not user_email:
                self.logger.error("No user email found in DELETE_USER notification")
                return

            user_key = await self.arango_service.get_entity_id_by_email(user_email)
            if not user_key:
                self.logger.error(f"User {user_email} not found in ArangoDB")
                return

            user = await self.arango_service.get_document(
                user_key, CollectionNames.USERS.value
            )
            org_id = user.get("orgId")

            self.logger.info(f"Processing user deletion for: {user_email}")

            await self.admin_service.handle_deleted_user(org_id, user_email)

        except Exception as e:
            self.logger.error(f"Error handling user deletion: {str(e)}")
            raise

    async def _handle_group_creation(self, event: Dict[str, Any]) -> None:
        """Handle group creation event"""
        try:
            group_email = None
            for param in event.get("parameters", []):
                if param.get("name") == "GROUP_EMAIL":
                    group_email = param.get("value")
                    break

            if not group_email:
                self.logger.error("No group email found in CREATE_GROUP notification")
                return

            orgs = await self.arango_service.get_all_orgs(active=True)
            org = orgs[0]
            org_id = org["_key"]

            self.logger.info(f"Processing group creation for: {group_email}")
            await self.admin_service.handle_new_group(org_id, group_email)

        except Exception as e:
            self.logger.error(f"Error handling group creation: {str(e)}")
            raise

    async def _handle_group_deletion(self, event: Dict[str, Any]) -> None:
        """Handle group deletion event"""
        try:
            group_email = None
            for param in event.get("parameters", []):
                if param.get("name") == "GROUP_EMAIL":
                    group_email = param.get("value")
                    break

            if not group_email:
                self.logger.warning("No group email found in DELETE_GROUP notification")
                return

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            if not group_key:
                self.logger.warning(f"Group {group_email} not found in ArangoDB")
                return

            group = await self.arango_service.get_document(
                group_key, CollectionNames.GROUPS.value
            )
            org_id = group.get("orgId")

            self.logger.info(f"Processing group deletion for: {group_email}")
            await self.admin_service.handle_deleted_group(org_id, group_email)

        except Exception as e:
            self.logger.error(f"Error handling group deletion: {str(e)}")
            raise

    async def _handle_group_member_addition(self, event: Dict[str, Any]) -> None:
        """Handle group member addition event"""
        try:
            group_email = user_email = None
            for param in event.get("parameters", []):
                if param.get("name") == "GROUP_EMAIL":
                    group_email = param.get("value")
                elif param.get("name") == "USER_EMAIL":
                    user_email = param.get("value")

            if not group_email or not user_email:
                self.logger.error("Missing email in ADD_GROUP_MEMBER notification")
                return

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            if not group_key:
                self.logger.error(f"Group {group_email} not found in ArangoDB")
                return

            group = await self.arango_service.get_document(
                group_key, CollectionNames.GROUPS.value
            )

            org_id = group.get("orgId")
            self.logger.info(f"Processing member addition to group: {group_email}")
            await self.admin_service.handle_group_member_added(
                org_id, group_email, user_email
            )

        except Exception as e:
            self.logger.error(f"Error handling group member addition: {str(e)}")
            raise

    async def _handle_group_member_removal(self, event: Dict[str, Any]) -> None:
        """Handle group member removal event"""
        try:
            group_email = user_email = None
            for param in event.get("parameters", []):
                if param.get("name") == "GROUP_EMAIL":
                    group_email = param.get("value")
                elif param.get("name") == "USER_EMAIL":
                    user_email = param.get("value")

            if not group_email or not user_email:
                self.logger.error("Missing email in REMOVE_GROUP_MEMBER notification")
                return

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            if not group_key:
                self.logger.error(f"Group {group_email} not found in ArangoDB")
                return

            group = await self.arango_service.get_document(
                group_key, CollectionNames.GROUPS.value
            )
            org_id = group.get("orgId")
            self.logger.info(f"Processing member removal from group: {group_email}")
            await self.admin_service.handle_group_member_removed(
                org_id, group_email, user_email
            )

        except Exception as e:
            self.logger.error(f"Error handling group member removal: {str(e)}")
            raise
