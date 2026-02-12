import json
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter(prefix="/api/v1/entity", tags=["Entity"])

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


@router.post("/team")
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
    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Generate a unique key for the team
    team_key = str(uuid.uuid4())

    team_body = {
        "_key": team_key,
        "name": body_dict.get("name"),
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

    # Add other users (excluding creator to avoid duplicate)
    for user_role in user_roles:
        user_id = user_role.get("userId")
        role = user_role.get("role", "READER")
        if not user_id:
            continue
        if user_id != creator_key:  # Skip creator as they already have OWNER role
            user_team_edges.append({
                "from_id": user_id,
                "from_collection": CollectionNames.USERS.value,
                "to_id": team_key,
                "to_collection": CollectionNames.TEAMS.value,
                "type": "USER",
                "role": role,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            })
    logger.info(f"User team edges: {user_team_edges}")
    transaction_id = None
    try:
        transaction_id = await graph_provider.begin_transaction(
            read=[],
            write=[
                CollectionNames.TEAMS.value,
                CollectionNames.PERMISSION.value,
            ]
        )

        # Create the team first
        result = await graph_provider.batch_upsert_nodes([team_body], CollectionNames.TEAMS.value, transaction=transaction_id)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create team")
        result = await graph_provider.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value, transaction=transaction_id)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create creator permissions")

        await graph_provider.commit_transaction(transaction_id)
        logger.info(f"Team created successfully: {team_body}")

        # Fetch the created team with users and permissions
        team_with_users = await graph_provider.get_team_with_users(team_id=team_key, user_key=user['_key'])

    except Exception as e:
        logger.error(f"Error in create_team: {str(e)}", exc_info=True)
        if transaction_id:
            await graph_provider.rollback_transaction(transaction_id)
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": "Team created successfully",
            "data": team_with_users
        }
    )

@router.get("/team/list")
async def get_teams(
    request: Request,
    search: Optional[str] = Query(None, description="Search teams by name"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Number of items per page")
) -> JSONResponse:
    """Get all teams for the current user's organization with pagination and search"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Use interface method to get teams
        result_list, total_count = await graph_provider.get_teams(
            org_id=user_info.get("orgId"),
            user_key=user['_key'],
            search=search,
            page=page,
            limit=limit
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
                "message": "Teams fetched successfully",
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
    except Exception as e:
        logger.error(f"Error in get_teams: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch teams")

@router.get("/team/{team_id}")
async def get_team(request: Request, team_id: str) -> JSONResponse:
    """Get a specific team with its users and permissions"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        # Use interface method to get team with users
        result = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])
        if not result:
            raise HTTPException(status_code=404, detail="Team not found")

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

@router.put("/team/{team_id}")
async def update_team(request: Request, team_id: str) -> JSONResponse:
    """Update a team - OWNER role. Supports updating name, description, and managing members"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to update the team
    permission = await graph_provider.get_edge(
        user['_key'],
        CollectionNames.USERS.value,
        team_id,
        CollectionNames.TEAMS.value,
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Updating team: {body_dict}")

    # Filter out None values to avoid overwriting with null
    updates = {
        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
    }

    if body_dict.get("name") is not None:
        updates["name"] = body_dict.get("name")
    if body_dict.get("description") is not None:
        updates["description"] = body_dict.get("description")

    try:
        # Update team basic info (always update timestamp)
        result = await graph_provider.update_node(team_id, CollectionNames.TEAMS.value, updates)
        if not result:
            raise HTTPException(status_code=404, detail="Team not found")

        # Handle member additions and removals
        add_user_roles = body_dict.get("addUserRoles", [])  # Array of {userId, role}
        remove_user_ids = body_dict.get("removeUserIds", [])
        # Support legacy format for backward compatibility
        if not add_user_roles and body_dict.get("addUserIds"):
            default_role = body_dict.get("role", "READER")
            add_user_roles = [{"userId": uid, "role": default_role} for uid in body_dict.get("addUserIds", [])]

        # Remove users if specified
        if remove_user_ids:
            await _validate_owner_removal(graph_provider, team_id, remove_user_ids, logger)
            deleted_list = await graph_provider.delete_team_member_edges(
                team_id=team_id,
                user_ids=remove_user_ids
            )
            if deleted_list:
                logger.info(f"Removed {len(deleted_list)} users from team {team_id}")

        # Update individual user roles if specified (batch update)
        update_user_roles = body_dict.get("updateUserRoles", [])  # Array of {userId, role}
        if update_user_roles:
            # Filter out invalid entries early
            valid_user_roles = [
                user_role for user_role in update_user_roles
                if user_role.get("userId") and user_role.get("role")
            ]

            if not valid_user_roles:
                logger.warning("No valid user roles to update")
            else:
                # Validate and filter owner updates using shared helper
                filtered_updates, total_owner_count = await _validate_and_filter_owner_updates(
                    graph_provider, team_id, valid_user_roles, logger
                )

                # Process filtered updates
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
                        raise HTTPException(status_code=500, detail=f"Failed to update user roles: {str(e)}")

        # Add users if specified (excluding creator to preserve OWNER role)
        if add_user_roles:
            user_team_edges = []
            for user_role in add_user_roles:
                user_id = user_role.get("userId")
                role = user_role.get("role", "READER")
                if not user_id:
                    continue
                # Skip if trying to add creator - they already have OWNER role
                if user_id != user['_key']:
                    user_team_edges.append({
                        "from_id": user_id,
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
        updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])

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

@router.post("/team/{team_id}/users")
async def add_users_to_team(request: Request, team_id: str) -> JSONResponse:
    """Add users to a team - OWNER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to update the team
    permission = await graph_provider.get_edge(user['_key'], CollectionNames.USERS.value, team_id, CollectionNames.TEAMS.value, CollectionNames.PERMISSION.value)
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to add users to this team")

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Adding users to team: {body_dict}")

    # Support both new format (userRoles) and legacy format (userIds + role)
    user_roles = body_dict.get("userRoles", [])  # Array of {userId, role}
    if not user_roles and body_dict.get("userIds"):
        # Legacy format: single role for all users
        role = body_dict.get("role", "READER")
        user_roles = [{"userId": uid, "role": role} for uid in body_dict.get("userIds", [])]

    if not user_roles:
        raise HTTPException(status_code=400, detail="No users provided")

    user_team_edges = []
    creator_key = user['_key']

    # Prevent adding creator with non-OWNER role - they should always be OWNER
    for user_role in user_roles:
        user_id = user_role.get("userId")
        role = user_role.get("role", "READER")
        if not user_id:
            continue
        if user_id == creator_key:
            # Skip creator - they already have OWNER role
            logger.info(f"Skipping creator {creator_key} - they already have OWNER role")
            continue
        user_team_edges.append({
            "from_id": user_id,
            "from_collection": CollectionNames.USERS.value,
            "to_id": team_id,
            "to_collection": CollectionNames.TEAMS.value,
            "type": "USER",
            "role": role,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        })

    if not user_team_edges:
        # If only creator was in the list, just return the team
        updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "No users to add (creator already has OWNER role)",
                "team": updated_team
            }
        )

    try:
        result = await graph_provider.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to add users to team")

        # Return updated team with users
        updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Users added to team successfully",
                "team": updated_team
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in add_users_to_team: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add users to team")

@router.delete("/team/{team_id}/users")
async def remove_user_from_team(request: Request, team_id: str) -> JSONResponse:
    """Remove a user from a team - OWNER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Removing users from team: {body_dict}")

    user_ids = body_dict.get("userIds", [])
    if not user_ids:
        raise HTTPException(status_code=400, detail="No user IDs provided")

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to delete the team members
    permission = await graph_provider.get_edge(
        user['_key'],
        CollectionNames.USERS.value,
        team_id,
        CollectionNames.TEAMS.value,
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to remove users from this team")

    logger.info(f"Removing users {user_ids} from team {team_id}")

    try:
        # Validate owner removal using shared helper
        await _validate_owner_removal(graph_provider, team_id, user_ids, logger)

        # Delete permissions using interface method
        deleted_list = await graph_provider.delete_team_member_edges(
            team_id=team_id,
            user_ids=user_ids
        )
        if not deleted_list:
            raise HTTPException(status_code=404, detail="No users found in team to remove")

        logger.info(f"Successfully removed {len(deleted_list)} users from team {team_id}")

        # Return updated team with users
        updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": f"Successfully removed {len(deleted_list)} user(s) from team",
                "team": updated_team
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in remove_user_from_team: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to remove user from team")

@router.put("/team/{team_id}/users/permissions")
async def update_user_permissions(request: Request, team_id: str) -> JSONResponse:
    """Update user permissions in a team - requires OWNER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to update the team
    permission = await graph_provider.get_edge(
        user['_key'],
        CollectionNames.USERS.value,
        team_id,
        CollectionNames.TEAMS.value,
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Updating user permissions: {body_dict}")

    # Support both new format (userRoles) and legacy format (userIds + role)
    user_roles = body_dict.get("userRoles", [])  # Array of {userId, role}
    if not user_roles and body_dict.get("userIds"):
        # Legacy format: single role for all users
        role = body_dict.get("role", "READER")
        user_roles = [{"userId": uid, "role": role} for uid in body_dict.get("userIds", [])]

    if not user_roles:
        raise HTTPException(status_code=400, detail="No users provided")

    try:
        timestamp = get_epoch_timestamp_in_ms()

        # Filter out invalid entries early
        valid_user_roles = [
            user_role for user_role in user_roles
            if user_role.get("userId") and user_role.get("role")
        ]

        if not valid_user_roles:
            raise HTTPException(status_code=400, detail="No valid user roles to update")

        # Validate and filter owner updates using shared helper
        filtered_updates, total_owner_count = await _validate_and_filter_owner_updates(
            graph_provider, team_id, valid_user_roles, logger
        )

        # Early exit if no updates needed
        if not filtered_updates:
            updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])

            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "No changes needed (Owners remain unchanged)",
                    "team": updated_team
                }
            )

        # Batch update all user roles using interface method
        updated_permissions = await graph_provider.batch_update_team_member_roles(
            team_id=team_id,
            user_roles=filtered_updates,
            timestamp=timestamp
        )

        if not updated_permissions:
            raise HTTPException(status_code=404, detail="No user permissions found to update")

        logger.info(f"Updated {len(updated_permissions)} user permissions")

        # Return updated team with users
        updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "User permissions updated successfully",
                "team": updated_team,
                "updated_count": len(updated_permissions)
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_user_permissions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update user permissions")

@router.delete("/team/{team_id}")
async def delete_team(request: Request, team_id: str) -> JSONResponse:
    """Delete a team and all its permissions - requires OWNER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to delete the team (OWNER only)
    permission = await graph_provider.get_edge(
        user['_key'],
        CollectionNames.USERS.value,
        team_id,
        CollectionNames.TEAMS.value,
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to delete this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to delete this team")

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

@router.get("/user/teams")
async def get_user_teams(
    request: Request,
    search: Optional[str] = Query(None, description="Search teams by name"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of items per page")
) -> JSONResponse:
    """Get all teams that the current user is a member of"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Use interface method to get user teams
        result_list, total_count = await graph_provider.get_user_teams(
            user_key=user['_key'],
            search=search,
            page=page,
            limit=limit
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
    except Exception as e:
        logger.error(f"Error in get_user_teams: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user teams")

@router.get("/user/teams/created")
async def get_user_created_teams(
    request: Request,
    search: Optional[str] = Query(None, description="Search teams by name"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of items per page")
) -> JSONResponse:
    """Get all teams created by the current user"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Use interface method to get user created teams
        result_list, total_count = await graph_provider.get_user_created_teams(
            org_id=user_info.get("orgId"),
            user_key=user['_key'],
            search=search,
            page=page,
            limit=limit
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
                "message": "User created teams fetched successfully",
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
    except Exception as e:
        logger.error(f"Error in get_user_created_teams: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user created teams")

@router.get("/user/list")
async def get_users(
    request: Request,
    search: Optional[str] = Query(None, description="Search users by name or email"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of items per page")
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
        # Use interface method to get organization users
        result_list, total_count = await graph_provider.get_organization_users(
            org_id=user_info.get("orgId"),
            search=search,
            page=page,
            limit=limit
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

@router.get("/team/{team_id}/users")
async def get_team_users(request: Request, team_id: str) -> JSONResponse:
    """Get all users in a specific team - requires MEMBER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Use interface method to get team users
        result = await graph_provider.get_team_users(
            team_id=team_id,
            org_id=user_info.get("orgId"),
            user_key=user['_key']
        )

        if not result:
            raise HTTPException(status_code=404, detail="Team not found")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Team users fetched successfully",
                "team": result
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_team_users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch team users")

@router.post("/team/{team_id}/bulk-users")
async def bulk_manage_team_users(request: Request, team_id: str) -> JSONResponse:
    """Bulk add/remove users from a team -OWNER role"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Bulk managing team users: {body_dict}")

    add_user_ids = body_dict.get("addUserIds", [])
    remove_user_ids = body_dict.get("removeUserIds", [])
    role = body_dict.get("role", "MEMBER")

    if not add_user_ids and not remove_user_ids:
        raise HTTPException(status_code=400, detail="No users to add or remove")

    try:
        # Prevent removing the team owner
        if remove_user_ids and user["_key"] in remove_user_ids:
            raise HTTPException(status_code=400, detail="Cannot remove team owner from team")

        # Remove users if specified
        if remove_user_ids:
            permissions = await graph_provider.delete_team_member_edges(
                team_id=team_id,
                user_ids=remove_user_ids
            )
            if not permissions:
                raise HTTPException(status_code=404, detail="No users found in team to remove")
            logger.info(f"Successfully removed {len(permissions)} users from team {team_id}")

        # Add users if specified
        if add_user_ids:
            user_team_edges = []
            for user_id in add_user_ids:
                user_team_edges.append({
                    "from_id": user_id,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": team_id,
                    "to_collection": CollectionNames.TEAMS.value,
                    "type": "USER",
                    "role": role,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                })
            logger.info(f"Adding {len(add_user_ids)} users to team {team_id}")
            result = await graph_provider.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value)
            if not result:
                raise HTTPException(status_code=500, detail="Failed to add users to team")
            logger.info(f"Successfully added {len(add_user_ids)} users to team {team_id}")

        # Return updated team with users
        updated_team = await graph_provider.get_team_with_users(team_id=team_id, user_key=user['_key'])

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Team users updated successfully",
                "team": updated_team,
                "added": len(add_user_ids) if add_user_ids else 0,
                "removed": len(remove_user_ids) if remove_user_ids else 0,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk_manage_team_users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update team users")

@router.get("/team/search")
async def search_teams(request: Request) -> JSONResponse:
    """Search teams by name or description"""
    services = await get_services(request)
    graph_provider = services["graph_provider"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await graph_provider.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get query parameters
    query = request.query_params.get("q", "")
    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))

    if not query:
        raise HTTPException(status_code=400, detail="Search query is required")

    try:
        # Use interface method to search teams
        result = await graph_provider.search_teams(
            org_id=user_info.get("orgId"),
            user_key=user['_key'],
            query=query,
            limit=limit,
            offset=offset
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Teams search completed",
                "data": result,
                "query": query,
                "limit": limit,
                "offset": offset,
                "count": len(result)
            }
        )
    except Exception as e:
        logger.error(f"Error in search_teams: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to search teams")
