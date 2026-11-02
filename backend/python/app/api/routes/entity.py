import json
import re
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.api.middlewares.auth import require_scopes, require_service_token
from app.api.middlewares.caller_role import fetch_caller_role
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes, TokenScopes
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.core.org_context import current_org_id
from app.services.cache.invalidation_hooks import notify_connector_sync_completed
from app.services.graph_db.user_email_identity import GraphUserEmailConflictError
from app.utils.logger import create_logger
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_lookup import get_user_or_503
from app.utils.user_messages import PEOPLE_GONE, action_failed, not_found

if TYPE_CHECKING:
    from logging import Logger

    from app.connectors.core.base.data_store.data_store import TransactionStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_log = create_logger(__name__)
router = APIRouter(prefix="/api/v1/entity", tags=["Entity"])

MONGO_USER_GRAPH_KEY_LOOKUP_CHUNK_SIZE = 500


_GRAPH_EMAIL = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class UserEmailUpdateRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def require_mailbox_shape(cls, value: str) -> str:
        email = value.lower().strip()
        if not _GRAPH_EMAIL.fullmatch(email):
            raise ValueError("Invalid email")
        return email


async def get_services(request: Request) -> Dict[str, Any]:
    """Get all required services from the container"""
    container = request.app.container

    # Get services
    graph_provider = request.app.state.graph_provider
    logger = container.logger()

    return {
        "graph_provider": graph_provider,
        "logger": logger,
    }


TEAM_NAME_TAKEN = "A team with this name already exists. Choose a different name."
TEAM_NAME_REQUIRED = "Give the team a name."


def _clean_team_name(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=400, detail=TEAM_NAME_REQUIRED)
    return raw.strip()


async def _refuse_taken_team_name(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    name: str,
    logger: "Logger",
    exclude_team_id: str | None = None,
) -> None:
    """409 when another team in the org already has this name, ignoring case.

    Best effort: two creates racing each other can both pass, since the graph
    stores have no shared unique constraint we could lean on. A lookup that
    fails lets the write through rather than blocking every team change.
    """
    try:
        clash = await graph_provider.find_team_by_name(
            org_id=org_id, name=name, exclude_team_id=exclude_team_id,
        )
    except Exception as exc:
        logger.warning("Could not check whether team name is taken: %s", exc)
        return
    if clash:
        raise HTTPException(status_code=409, detail=TEAM_NAME_TAKEN)


async def _authorize_team_change(
    graph_provider: "IGraphDBProvider",
    *,
    team_id: str,
    user_key: str,
    org_id: str | None,
    is_org_admin: bool,
    action: str,
) -> dict | None:
    """The caller's permission edge on the team, once they may change it.

    A team that is not there and a team this caller has nothing to do with get
    the same answer, so the status code cannot be used to discover which team
    ids exist. An administrator may change any team in their own org, and only
    their own: the team document is not org-scoped, so its orgId is checked.
    """
    team = await graph_provider.get_document(team_id, CollectionNames.TEAMS.value)
    permission = await graph_provider.get_edge(
        user_key,
        CollectionNames.USERS.value,
        team_id,
        CollectionNames.TEAMS.value,
        CollectionNames.PERMISSION.value
    )
    admin_of_team_org = is_org_admin and bool(team) and bool(org_id) and team.get("orgId") == org_id
    if not team or not (permission or admin_of_team_org):
        raise HTTPException(status_code=404, detail=not_found("This team"))
    if admin_of_team_org:
        return permission
    # Someone with a role on the team already knows it exists, so telling them
    # they are not the owner gives nothing away and is the honest answer.
    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail=f"User does not have permission to {action} this team")
    return permission


async def _validate_owner_removal(
    graph_provider,
    team_id: str,
    user_ids_to_remove: list,
    logger
) -> None:
    """
    Validate that removing owners won't leave the team without any owners.
    Raises HTTPException if validation fails.
    """
    if not user_ids_to_remove:
        return

    # Get owner removal info using graph provider
    validation_data = await graph_provider.get_team_owner_removal_info(
        team_id=team_id,
        user_ids=user_ids_to_remove
    )

    owners_being_removed = validation_data.get("owners_being_removed", [])
    total_owner_count = validation_data.get("total_owner_count", 0)

    if owners_being_removed:
        remaining_owners = total_owner_count - len(owners_being_removed)
        if remaining_owners < 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove all owners from the team. At least one owner must remain."
            )
        logger.info(f"Removing {len(owners_being_removed)} Owner(s) from team {team_id} (remaining owners: {remaining_owners})")


async def _validate_and_filter_owner_updates(
    graph_provider,
    team_id: str,
    valid_user_roles: list,
    logger
) -> tuple[list, int]:
    """
    Validate owner role updates and filter out unchanged owners.
    Returns (filtered_updates, total_owner_count).
    Raises HTTPException if validation fails.
    """
    # Get team info, current permissions, and owner count using graph provider
    user_ids_to_check = [ur.get("userId") for ur in valid_user_roles]
    result_data = await graph_provider.get_team_permissions_and_owner_count(
        team_id=team_id,
        user_ids=user_ids_to_check
    )

    if not result_data or not result_data.get("team"):
        raise HTTPException(status_code=404, detail="Team not found")

    current_permissions = result_data.get("permissions", {})
    total_owner_count = result_data.get("owner_count", 0)

    # Filter out unchanged Owners and validate
    filtered_updates = []
    owners_being_updated = []

    for user_role in valid_user_roles:
        user_id = user_role.get("userId")
        new_role = user_role.get("role")
        current_role = current_permissions.get(user_id)

        # Skip unchanged Owners (no-op)
        if current_role == 'OWNER' and new_role == 'OWNER':
            continue

        # Track Owners being updated
        if current_role == 'OWNER':
            owners_being_updated.append(user_id)

        filtered_updates.append(user_role)

    # Early exit if no updates needed
    if not filtered_updates:
        logger.info("No user role updates needed (all Owners unchanged)")
        return [], total_owner_count

    # Bulk Operation Prevention: Cannot perform bulk operations on Owner permissions
    if len(filtered_updates) > 1 and owners_being_updated:
        raise HTTPException(
            status_code=400,
            detail="Cannot perform bulk operations on Owner permissions. Please update Owners one at a time."
        )

    # Single Owner Update Validation
    if len(owners_being_updated) == 1 and len(filtered_updates) == 1:
        owner_user_id = owners_being_updated[0]
        owner_update = filtered_updates[0]  # Only one item, so direct access
        if owner_update.get("role") != "OWNER":
            # Owner is being downgraded - check minimum requirement using already fetched count
            if total_owner_count <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove all owners from the team. At least one owner must remain."
                )
            logger.info(f"Downgrading Owner {owner_user_id} to {owner_update.get('role')} on team {team_id} (remaining owners: {total_owner_count - 1})")

    return filtered_updates, total_owner_count


@router.post("/team", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_WRITE))])
async def create_team(request: Request) -> JSONResponse:
    """Create a team"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Creating team: {body_dict}")

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await get_user_or_503(graph_provider, user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    team_name = _clean_team_name(body_dict.get("name"))
    await _refuse_taken_team_name(graph_provider, current_org_id(), team_name, logger)
    # Generate a unique key for the team
    team_key = str(uuid.uuid4())

    team_body = {
        "_key": team_key,
        "name": team_name,
        "description": body_dict.get("description"),
        "createdBy": user['_key'],
        "orgId": user_info.get("orgId"),
        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }

    # Support both new format (userRoles) and legacy format (userIds + role)
    user_roles = body_dict.get("userRoles", [])  # Array of {userId, role}
    if not user_roles and body_dict.get("userIds"):
        # Legacy format: single role for all users
        role = body_dict.get("role", "READER")
        user_roles = [{"userId": uid, "role": role} for uid in body_dict.get("userIds", [])]

    logger.info(f"Creating team with users: body_dict: {body_dict}")
    user_team_edges = []
    creator_key = user['_key']
    creator_mongo_id = user_info.get("userId")

    # First, ensure creator always gets OWNER role
    creator_permission = {
        "from_id": creator_key,
        "from_collection": CollectionNames.USERS.value,
        "to_id": team_key,
        "to_collection": CollectionNames.TEAMS.value,
        "type": "USER",
        "role": "OWNER",
        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }
    user_team_edges.append(creator_permission)

    member_mongo_ids = [
        user_role.get("userId")
        for user_role in user_roles
        if user_role.get("userId") and user_role.get("userId") != creator_mongo_id
    ]
    mongo_to_key: dict[str, str] = {}
    if member_mongo_ids:
        try:
            mongo_to_key = await graph_provider.get_graph_user_keys_by_mongo_user_ids(
                member_mongo_ids,
                org_id=user_info.get("orgId"),
                chunk_size=MONGO_USER_GRAPH_KEY_LOOKUP_CHUNK_SIZE,
            )
        except ValueError as exc:
            # the provider names the missing ids; people only need to know who to remove
            logger.error("Team member lookup failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=400, detail=PEOPLE_GONE) from exc

    for user_role in user_roles:
        mongo_id = user_role.get("userId")
        role = user_role.get("role", "READER")
        if not mongo_id or mongo_id == creator_mongo_id:
            continue
        user_team_edges.append({
            "from_id": mongo_to_key[mongo_id],
            "from_collection": CollectionNames.USERS.value,
            "to_id": team_key,
            "to_collection": CollectionNames.TEAMS.value,
            "type": "USER",
            "role": role,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        })
    logger.info(f"User team edges: {user_team_edges}")

    async def write_team(tx_store: "TransactionStore") -> None:
        if not await tx_store.batch_upsert_nodes([team_body], CollectionNames.TEAMS.value):
            raise RuntimeError("Failed to create team")
        if not await tx_store.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value):
            raise RuntimeError("Failed to create creator permissions")

    # The edges lock the creator's and every member's user node, so two creates can
    # collide. explicit: a failure leaves no team behind on Neo4j either. Every write
    # is keyed by team_key, so the re-run also completes the same team where the node
    # committed on its own (NEO4J_CREATE_TRANSACTIONS=false).
    try:
        await GraphDataStore(logger, graph_provider).execute_idempotent_in_transaction(write_team, explicit=True)
    except Exception as e:
        logger.error(f"Error in create_team: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("create this team")) from e
    logger.info(f"Team created successfully: {team_body}")

    # The team is committed by now. The providers answer None to a failed read, and
    # any error response makes the dashboard say "try again", which would create
    # the team a second time; so a failed read-back answers with the team as written.
    try:
        team_with_users = await graph_provider.get_team_with_users(
            team_id=team_key, org_id=user_info.get("orgId"), user_key=user['_key'],
        )
    except Exception as e:
        logger.error(f"Team {team_key} was created but could not be read back: {str(e)}", exc_info=True)
        team_with_users = None
    if not team_with_users:
        logger.warning(f"Team {team_key} was created but could not be read back; answering with what was written")
        members = [
            {"id": edge["from_id"], "role": edge["role"], "joinedAt": edge["createdAtTimestamp"],
             "isOwner": edge["role"] == "OWNER"}
            for edge in user_team_edges
        ]
        team_with_users = {
            "id": team_key,
            "name": team_body["name"],
            "description": team_body["description"],
            "createdBy": team_body["createdBy"],
            "orgId": team_body["orgId"],
            "createdAtTimestamp": team_body["createdAtTimestamp"],
            "updatedAtTimestamp": team_body["updatedAtTimestamp"],
            "members": members,
            "memberCount": len(members),
            "canEdit": True,
            "canDelete": True,
            "canManageMembers": True,
        }

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": "Team created successfully",
            "data": team_with_users
        }
    )

@router.get("/team/{team_id}", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_READ))])
async def get_team(request: Request, team_id: str) -> JSONResponse:
    """Get a specific team with its users and permissions"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await get_user_or_503(graph_provider, user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        is_org_admin = await _caller_is_an_administrator(request)
        # Same answer for a team that is not there and one the caller is not
        # in, so team ids cannot be probed. The lookup below is org-scoped,
        # which is what bounds an administrator to their own org.
        if not is_org_admin and not await _caller_is_in_team(graph_provider, user['_key'], team_id):
            raise HTTPException(status_code=404, detail=not_found("This team"))
        result = await graph_provider.get_team_with_users(
            team_id=team_id, org_id=user_info.get("orgId"), user_key=user['_key'],
            include_service_accounts=is_org_admin,
            is_org_admin=is_org_admin,
        )
        if not result:
            raise HTTPException(status_code=404, detail=not_found("This team"))

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Team fetched successfully",
                "team": result
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_team: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch team")

@router.put("/team/{team_id}", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_WRITE))])
async def update_team(request: Request, team_id: str) -> JSONResponse:
    """Update a team - OWNER role or org admin. Supports updating name, description, and managing members"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await get_user_or_503(graph_provider, user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    org_id = current_org_id()
    is_org_admin = await _caller_is_an_administrator(request)
    permission = await _authorize_team_change(
        graph_provider,
        team_id=team_id,
        user_key=user['_key'],
        org_id=org_id,
        is_org_admin=is_org_admin,
        action="update",
    )

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Updating team: {body_dict}")

    # Filter out None values to avoid overwriting with null
    updates = {
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }

    if body_dict.get("name") is not None:
        team_name = _clean_team_name(body_dict.get("name"))
        await _refuse_taken_team_name(graph_provider, org_id, team_name, logger, exclude_team_id=team_id)
        updates["name"] = team_name
    if body_dict.get("description") is not None:
        updates["description"] = body_dict.get("description")

    try:
        # Update team basic info (always update timestamp)
        result = await graph_provider.update_node(team_id, CollectionNames.TEAMS.value, updates)
        if not result:
            raise HTTPException(status_code=404, detail="Team not found")

        # Handle member additions and removals (Mongo userId on wire; resolve once)
        add_user_roles = body_dict.get("addUserRoles", [])  # Array of {userId, role}
        remove_user_mongo_ids = [
            mid for mid in body_dict.get("removeUserIds", []) if mid
        ]
        update_user_roles = body_dict.get("updateUserRoles", [])  # Array of {userId, role}
        # Support legacy format for backward compatibility
        if not add_user_roles and body_dict.get("addUserIds"):
            default_role = body_dict.get("role", "READER")
            add_user_roles = [{"userId": uid, "role": default_role} for uid in body_dict.get("addUserIds", [])]

        unique_mongo_ids: set[str] = set()
        unique_mongo_ids.update(remove_user_mongo_ids)
        for user_role in update_user_roles:
            mongo_id = user_role.get("userId")
            if mongo_id:
                unique_mongo_ids.add(mongo_id)
        for user_role in add_user_roles:
            mongo_id = user_role.get("userId")
            if mongo_id:
                unique_mongo_ids.add(mongo_id)

        mongo_to_key: dict[str, str] = {}
        if unique_mongo_ids:
            try:
                mongo_to_key = await graph_provider.get_graph_user_keys_by_mongo_user_ids(
                    list(unique_mongo_ids),
                    org_id=user_info.get("orgId"),
                    chunk_size=MONGO_USER_GRAPH_KEY_LOOKUP_CHUNK_SIZE,
                )
            except ValueError as exc:
                logger.error("Team member lookup failed: %s", exc, exc_info=True)
                raise HTTPException(status_code=400, detail=PEOPLE_GONE) from exc

        # Remove users if specified
        if remove_user_mongo_ids:
            remove_graph_keys = [mongo_to_key[mid] for mid in remove_user_mongo_ids]
            await _validate_owner_removal(graph_provider, team_id, remove_graph_keys, logger)
            deleted_list = await graph_provider.delete_team_member_edges(
                team_id=team_id,
                user_ids=remove_graph_keys
            )
            if deleted_list:
                logger.info(f"Removed {len(deleted_list)} users from team {team_id}")

        # Update individual user roles if specified (batch update)
        if update_user_roles:
            valid_user_roles = [
                {
                    "userId": mongo_to_key[user_role.get("userId")],
                    "role": user_role.get("role"),
                }
                for user_role in update_user_roles
                if user_role.get("userId") and user_role.get("role")
            ]

            if not valid_user_roles:
                logger.warning("No valid user roles to update")
            else:
                filtered_updates, total_owner_count = await _validate_and_filter_owner_updates(
                    graph_provider, team_id, valid_user_roles, logger
                )

                if filtered_updates:
                    try:
                        updated_permissions = await graph_provider.batch_update_team_member_roles(
                            team_id=team_id,
                            user_roles=filtered_updates,
                            timestamp=get_epoch_timestamp_in_ms()
                        )
                        logger.info(f"Updated {len(updated_permissions)} user roles in batch")
                    except Exception as e:
                        logger.error(f"Error updating user roles in batch: {str(e)}")
                        raise HTTPException(status_code=500, detail=action_failed("update these people's roles"))

        # Add users if specified, skipping the caller's own edge so their role is
        # kept; an administrator who is not yet in the team has none to keep.
        if add_user_roles:
            caller_mongo_id = user_info.get("userId")
            user_team_edges = []
            for user_role in add_user_roles:
                mongo_id = user_role.get("userId")
                role = user_role.get("role", "READER")
                if not mongo_id or (mongo_id == caller_mongo_id and permission):
                    continue
                user_team_edges.append({
                    "from_id": mongo_to_key[mongo_id],
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": team_id,
                    "to_collection": CollectionNames.TEAMS.value,
                    "type": "USER",
                    "role": role,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                })

            if user_team_edges:
                result = await graph_provider.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value)
                if result:
                    logger.info(f"Added {len(user_team_edges)} users to team {team_id}")

        # Return updated team with users
        updated_team = await graph_provider.get_team_with_users(
            team_id=team_id, org_id=user_info.get("orgId"), user_key=user['_key'],
            is_org_admin=is_org_admin,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Team updated successfully",
                "team": updated_team
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_team: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update team")

@router.delete("/team/{team_id}", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_WRITE))])
async def delete_team(request: Request, team_id: str) -> JSONResponse:
    """Delete a team and all its permissions - requires OWNER role or org admin"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await get_user_or_503(graph_provider, user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    org_id = user_info.get("orgId")
    if org_id and team_id == f"all_{org_id}":
        raise HTTPException(status_code=403, detail="The default All team cannot be deleted")

    # Deleting twice is a plain "not found" rather than an apparent permissions
    # failure: deleting a team removes its permission edges with it.
    await _authorize_team_change(
        graph_provider,
        team_id=team_id,
        user_key=user['_key'],
        org_id=org_id,
        is_org_admin=await _caller_is_an_administrator(request),
        action="delete",
    )

    logger.info(f"Deleting team: {team_id}")

    try:
        # Delete all permission edges using interface method
        await graph_provider.delete_all_team_permissions(team_id=team_id)

        # Delete the team
        result = await graph_provider.delete_nodes([team_id], CollectionNames.TEAMS.value)
        if not result:
            raise HTTPException(status_code=404, detail="Team not found")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Team deleted successfully",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_team: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete team")

@router.get("/user/teams", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_READ))])
async def get_user_teams(
    request: Request,
    search: Optional[str] = Query(None, description="Search teams by name"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of items per page"),
    created_by: Optional[str] = Query(None, description="Filter by creator Mongo userId"),
    created_after: Optional[int] = Query(None, description="Filter teams created after this timestamp (ms)"),
    created_before: Optional[int] = Query(None, description="Filter teams created before this timestamp (ms)")
) -> JSONResponse:
    """The teams the current user is a member of; every team in the org for an administrator"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await get_user_or_503(graph_provider, user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        graph_created_by = created_by
        if created_by:
            creator_user = await get_user_or_503(graph_provider, created_by)
            org_id = user_info.get("orgId")
            creator_org = creator_user.get("orgId") if creator_user else None
            if (
                not creator_user
                or (org_id and creator_org and creator_org != org_id)
            ):
                raise HTTPException(
                    status_code=400,
                    detail=not_found("This person"),
                )
            graph_created_by = creator_user["_key"]

        # An administrator manages every team in the org, so sees them all;
        # anyone else sees the teams they are in.
        is_org_admin = await _caller_is_an_administrator(request)
        result_list, total_count = await graph_provider.get_user_teams(
            user_key=user['_key'],
            search=search,
            page=page,
            limit=limit,
            created_by=graph_created_by,
            created_after=created_after,
            created_before=created_before,
            include_service_accounts=is_org_admin,
            org_id=current_org_id(),
            is_org_admin=is_org_admin,
        )

        if not result_list:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "No teams found",
                    "teams": [],
                    "pagination": {
                        "page": page,
                        "limit": limit,
                        "total": total_count,
                        "pages": 0
                    }
                }
            )

        # Calculate total pages
        total_pages = (total_count + limit - 1) // limit

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "User teams fetched successfully",
                "teams": result_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total_count,
                    "pages": total_pages,
                    "hasNext": page < total_pages,
                    "hasPrev": page > 1
                }
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_user_teams: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user teams")

async def _caller_is_an_administrator(request: Request) -> bool:
    """The live role Node reports for the request's own credentials.

    An unconfirmed role reads as "not an administrator". Everywhere this is
    used it only narrows what comes back, so that is the safe answer and the
    request still works — including when the configuration service cannot be
    reached at all, which must not turn a listing into an error.
    """
    try:
        config_service = request.app.container.config_service()
        return (await fetch_caller_role(request, config_service)).is_admin
    except Exception:
        _log.warning("Could not read the caller's role; answering as a member")
        return False


async def _caller_is_in_team(graph_provider: "IGraphDBProvider", user_key: str, team_id: str) -> bool:
    permission = await graph_provider.get_edge(
        user_key,
        CollectionNames.USERS.value,
        team_id,
        CollectionNames.TEAMS.value,
        CollectionNames.PERMISSION.value
    )
    return bool(permission)


async def _may_list_service_accounts(request: Request, *, asked: bool) -> bool:
    """Whether this listing should include machine identities.

    Each row carries the `userId` an agent stores when it is bound to a service
    account, so this is the same question the people list answers
    (`users.controller.ts` `getAllUsers`): only an administrator, and only when
    the caller asks. The role is the live one Node reports for the caller's own
    credentials; an unconfirmed role counts as "not an administrator", which is
    the safe answer here because it only narrows what comes back.
    """
    if not asked:
        return False
    return await _caller_is_an_administrator(request)


@router.get("/user/list", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_READ))])
async def get_users(
    request: Request,
    search: Optional[str] = Query(None, description="Search users by name or email"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of items per page"),
    includeServiceAccounts: Optional[str] = Query(
        None, description="Administrators only: 'true' also lists service accounts",
    ),
) -> JSONResponse:
    """Get all users in the current user's organization with pagination and search"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    try:
        # Compared to the exact string, as the gateway's own flag is: a query
        # value can arrive as a list, and only "true" opts in.
        include_service_accounts = await _may_list_service_accounts(
            request, asked=includeServiceAccounts == "true",
        )
        # Use interface method to get organization users
        result_list, total_count = await graph_provider.get_organization_users(
            org_id=user_info.get("orgId"),
            search=search,
            page=page,
            limit=limit,
            include_service_accounts=include_service_accounts,
        )
        if not result_list:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "No users found",
                    "users": [],
                    "pagination": {
                        "page": page,
                        "limit": limit,
                        "total": total_count,
                        "pages": 0
                    }
                }
            )

        # Calculate total pages
        total_pages = (total_count + limit - 1) // limit

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Users fetched successfully",
                "users": result_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total_count,
                    "pages": total_pages,
                    "hasNext": page < total_pages,
                    "hasPrev": page > 1
                }
            }
        )
    except Exception as e:
        logger.error(f"Error in get_users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch users")

@router.get("/team/{team_id}/users", dependencies=[Depends(require_scopes(OAuthScopes.TEAM_READ))])
async def get_team_users(
    request: Request,
    team_id: str,
    search: Optional[str] = Query(None, description="Search members by name or email"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of members per page")
) -> JSONResponse:
    """Get all users in a specific team - requires MEMBER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await get_user_or_503(graph_provider, user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        is_org_admin = await _caller_is_an_administrator(request)
        if not is_org_admin and not await _caller_is_in_team(graph_provider, user['_key'], team_id):
            raise HTTPException(status_code=404, detail=not_found("This team"))
        result = await graph_provider.get_team_users(
            team_id=team_id,
            org_id=user_info.get("orgId"),
            user_key=user['_key'],
            search=search,
            page=page,
            limit=limit,
            include_service_accounts=is_org_admin,
        )

        if not result:
            raise HTTPException(status_code=404, detail=not_found("This team"))

        total_count = result.get("memberCount", 0)
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Team users fetched successfully",
                "team": result,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total_count,
                    "totalPages": total_pages,
                    "hasNextPage": page < total_pages,
                    "hasPrevPage": page > 1
                }
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_team_users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch team users")


@router.patch(
    "/user/email",
    dependencies=[Depends(require_service_token(TokenScopes.ENTITY_USER_WRITE))],
)
async def update_user_email(
    request: Request,
    body: UserEmailUpdateRequest,
) -> JSONResponse:
    """Set the graph user's email after Mongo has already accepted a verified change."""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    email = body.email
    if not user_id or not org_id:
        raise HTTPException(status_code=400, detail="userId and orgId are required")

    try:
        result = await graph_provider.apply_verified_user_email(user_id, org_id, email)
        if not result:
            raise HTTPException(status_code=404, detail="User not found")
        logger.info("Updated graph email for userId %s", user_id)
        for connector_id in result.get("connectorIds", []):
            await notify_connector_sync_completed(connector_id, org_id)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "email": result.get("email", email),
                "mergedStubKeys": result.get("mergedStubKeys", []),
            },
        )
    except HTTPException:
        raise
    except GraphUserEmailConflictError as e:
        logger.warning(
            "Graph email conflict for userId %s: %s",
            user_id,
            e,
        )
        raise HTTPException(
            status_code=409,
            detail="Email already belongs to another login user in the graph",
        ) from e
    except Exception as e:
        logger.error(f"Error updating graph user email: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update user email")
