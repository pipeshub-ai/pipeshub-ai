import asyncio
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
    arango_service = await container.arango_service()
    logger = container.logger()

    return {
        "arango_service": arango_service,
        "logger": logger,
    }


@router.post("/team")
async def create_team(request: Request) -> JSONResponse:
    """Create a team"""
    services = await get_services(request)
    arango_service = services["arango_service"]
    logger = services["logger"]

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Creating team: {body_dict}")

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
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

    user_ids = body_dict.get("userIds", [])
    role = body_dict.get("role", "READER")
    logger.info(f"Creating team with users: body_dict: {body_dict}")
    user_team_edges = []
    for user_id in user_ids:
        user_team_edges.append({
            "_from": f"{CollectionNames.USERS.value}/{user_id}",
            "_to": f"{CollectionNames.TEAMS.value}/{team_key}",
            "type": "USER",
            "role": role,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        })

    if user['_key'] not in user_ids:
        # Add creator to team permissions
        creator_permission = {
            "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
            "_to": f"{CollectionNames.TEAMS.value}/{team_key}",
            "type": "USER",
            "role": "OWNER",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        user_team_edges.append(creator_permission)
    logger.info(f"User team edges: {user_team_edges}")
    transaction = None
    try:
        transaction = arango_service.db.begin_transaction(
            write=[
                CollectionNames.TEAMS.value,
                CollectionNames.PERMISSION.value,
            ]
        )

        # Create the team first
        result = await arango_service.batch_upsert_nodes([team_body], CollectionNames.TEAMS.value, transaction)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create team")
        result = await arango_service.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value, transaction)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create creator permissions")

        await asyncio.to_thread(lambda: transaction.commit_transaction())
        logger.info(f"Team created successfully: {team_body}")

        # Fetch the created team with users and permissions
        team_with_users = await get_team_with_users(arango_service, team_key, user['_key'])

    except Exception as e:
        logger.error(f"Error in create_team: {str(e)}", exc_info=True)
        if transaction:
            await asyncio.to_thread(lambda: transaction.abort_transaction())
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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Calculate offset
    offset = (page - 1) * limit

    # Build search filter
    search_filter = ""
    if search:
        search_filter = "FILTER team.name LIKE CONCAT('%', @search, '%')"

    # Query to get teams with current user's permission and team members
    teams_query = f"""
    FOR team IN {CollectionNames.TEAMS.value}
    FILTER team.orgId == @orgId
    {search_filter}
    LET current_user_permission = (
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._from == @currentUserId AND permission._to == team._id
        RETURN permission
    )
    LET team_members = (
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._to == team._id
        LET user = DOCUMENT(permission._from)
        RETURN {{
            "id": user._key,
            "userId": user.userId,
            "userName": user.fullName,
            "userEmail": user.email,
            "role": permission.role,
            "joinedAt": permission.createdAtTimestamp,
            "isOwner": permission.role == "OWNER"
        }}
    )
    LET user_count = LENGTH(team_members)
    SORT team.createdAtTimestamp DESC
    LIMIT @offset, @limit
    RETURN {{
        "id": team._key,
        "name": team.name,
        "description": team.description,
        "createdBy": team.createdBy,
        "orgId": team.orgId,
        "createdAtTimestamp": team.createdAtTimestamp,
        "updatedAtTimestamp": team.updatedAtTimestamp,
        "currentUserPermission": LENGTH(current_user_permission) > 0 ? current_user_permission[0] : null,
        "members": team_members,
        "memberCount": user_count,
        "canEdit": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"],
        "canDelete": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role == "OWNER",
        "canManageMembers": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"]
    }}
    """

    # Count total teams for pagination
    count_query = f"""
    FOR team IN {CollectionNames.TEAMS.value}
    FILTER team.orgId == @orgId
    {search_filter}
    COLLECT WITH COUNT INTO total_count
    RETURN total_count
    """

    try:
        count_params = {"orgId": user_info.get("orgId")}
        # Get total count
        if search:
            count_params["search"] = search

        count_result = arango_service.db.aql.execute(count_query, bind_vars=count_params)
        count_list = list(count_result)
        total_count = count_list[0] if count_list else 0

        # Get teams with pagination
        teams_params = {
            "orgId": user_info.get("orgId"),
            "currentUserId": f"{CollectionNames.USERS.value}/{user['_key']}",
            "offset": offset,
            "limit": limit
        }
        if search:
            teams_params["search"] = search

        result = arango_service.db.aql.execute(teams_query, bind_vars=teams_params)
        result_list = list(result)

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

async def get_team_with_users(arango_service, team_key: str, user_key: str) -> Optional[Dict]:
    """Helper function to get team with users and permissions"""
    team_query = f"""
    FOR team IN {CollectionNames.TEAMS.value}
    FILTER team._key == @teamId
    LET current_user_permission = (
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._from == @currentUserId AND permission._to == team._id
        RETURN permission
    )
    LET team_members = (
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._to == team._id
        LET user = DOCUMENT(permission._from)
        RETURN {{
            "id": user._key,
            "userId": user.userId,
            "userName": user.fullName,
            "userEmail": user.email,
            "role": permission.role,
            "joinedAt": permission.createdAtTimestamp,
            "isOwner": permission.role == "OWNER"
        }}
    )
    LET user_count = LENGTH(team_members)
    RETURN {{
        "id": team._key,
        "name": team.name,
        "description": team.description,
        "createdBy": team.createdBy,
        "orgId": team.orgId,
        "createdAtTimestamp": team.createdAtTimestamp,
        "updatedAtTimestamp": team.updatedAtTimestamp,
        "currentUserPermission": LENGTH(current_user_permission) > 0 ? current_user_permission[0] : null,
        "members": team_members,
        "memberCount": user_count,
        "canEdit": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"],
        "canDelete": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role == "OWNER",
        "canManageMembers": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"]
    }}
    """

    result = arango_service.db.aql.execute(
        team_query,
        bind_vars={
            "teamId": team_key,
            "currentUserId": f"{CollectionNames.USERS.value}/{user_key}"
        }
    )
    result_list = list(result)
    return result_list[0] if result_list else None

@router.get("/team/{team_id}")
async def get_team(request: Request, team_id: str) -> JSONResponse:
    """Get a specific team with its users and permissions"""
    services = await get_services(request)
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        # Query to get team with current user's permission and team members (same structure as get_teams)
        result = await get_team_with_users(arango_service, team_id, user['_key'])
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
    """Update a team -  OWNER role"""
    services = await get_services(request)
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to update the team
    permission = await arango_service.get_edge(
        f"{CollectionNames.USERS.value}/{user['_key']}",
        f"{CollectionNames.TEAMS.value}/{team_id}",
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
        result = await arango_service.update_node(team_id, updates, CollectionNames.TEAMS.value)
        if not result:
            raise HTTPException(status_code=404, detail="Team not found")

        # Return updated team with users
        updated_team = await get_team_with_users(arango_service, team_id, user['_key'])

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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to update the team
    permission = await arango_service.get_edge(f"{CollectionNames.USERS.value}/{user['_key']}", f"{CollectionNames.TEAMS.value}/{team_id}", CollectionNames.PERMISSION.value)
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to add users to this team")

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Adding users to team: {body_dict}")

    user_ids = body_dict.get("userIds", [])
    role = body_dict.get("role", "READER")

    if not user_ids:
        raise HTTPException(status_code=400, detail="No user IDs provided")

    user_team_edges = []
    for user_id in user_ids:
        user_team_edges.append({
            "_from": f"{CollectionNames.USERS.value}/{user_id}",
            "_to": f"{CollectionNames.TEAMS.value}/{team_id}",
            "type": "USER",
            "role": role,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        })

    try:
        result = await arango_service.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to add users to team")

        # Return updated team with users
        updated_team = await get_team_with_users(arango_service, team_id,user['_key'])

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Users added to team successfully",
                "team" : updated_team
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
    arango_service = services["arango_service"]
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

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to delete the team members
    permission = await arango_service.get_edge(
        f"{CollectionNames.USERS.value}/{user['_key']}",
        f"{CollectionNames.TEAMS.value}/{team_id}",
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to remove users from this team")

    # Prevent removing the team owner
    if user['_key'] in user_ids:
        raise HTTPException(status_code=400, detail="Cannot remove team owner from team")

    logger.info(f"Removing users {user_ids} from team {team_id}")

    try:
        # Delete permissions in a single atomic AQL operation
        delete_query = f"""
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._to == @teamId
        FILTER SPLIT(permission._from, '/')[1] IN @userIds
        REMOVE permission IN {CollectionNames.PERMISSION.value}
        RETURN OLD
        """

        deleted_permissions = arango_service.db.aql.execute(
            delete_query,
            bind_vars={
                "userIds": user_ids,
                "teamId": f"{CollectionNames.TEAMS.value}/{team_id}"
            }
        )

        # Convert cursor to list if needed
        deleted_list = list(deleted_permissions)
        if not deleted_list:
            raise HTTPException(status_code=404, detail="No users found in team to remove")

        logger.info(f"Successfully removed {len(deleted_list)} users from team {team_id}")

        # Return updated team with users
        updated_team = await get_team_with_users(arango_service, team_id,user['_key'])

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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to update the team
    permission = await arango_service.get_edge(
        f"{CollectionNames.USERS.value}/{user['_key']}",
        f"{CollectionNames.TEAMS.value}/{team_id}",
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to update this team")

    body = await request.body()
    body_dict = json.loads(body.decode('utf-8'))
    logger.info(f"Updating user permissions: {body_dict}")

    user_ids = body_dict.get("userIds", [])
    role = body_dict.get("role", "READER")

    if not user_ids:
        raise HTTPException(status_code=400, detail="No user IDs provided")

    # Prevent changing the team owner's role
    if user['_key'] in user_ids:
        raise HTTPException(status_code=400, detail="Cannot change team owner's role")

    try:
        # Batch update permissions using a single query
        batch_update_query = f"""
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._to == @team_id
        FILTER SPLIT(permission._from, '/')[1] IN @user_ids
        UPDATE permission WITH {{
            role: @role,
            updatedAtTimestamp: @timestamp
        }} IN {CollectionNames.PERMISSION.value}
        RETURN {{
            _key: NEW._key,
            _from: NEW._from,
            role: NEW.role,
            updatedAt: NEW.updatedAtTimestamp
        }}
        """

        bind_vars = {
            "team_id": f"{CollectionNames.TEAMS.value}/{team_id}",
            "user_ids": user_ids,
            "role": role,
            "timestamp": get_epoch_timestamp_in_ms()
        }

        cursor = arango_service.db.aql.execute(batch_update_query, bind_vars=bind_vars)
        updated_permissions = list(cursor)

        if not updated_permissions:
            raise HTTPException(status_code=404, detail="No user permissions found to update")

        logger.info(f"Updated {len(updated_permissions)} user permissions to role {role}")

        # Return updated team with users
        updated_team = await get_team_with_users(arango_service, team_id, user['_key'])

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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }
    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has permission to delete the team (OWNER only)
    permission = await arango_service.get_edge(
        f"{CollectionNames.USERS.value}/{user['_key']}",
        f"{CollectionNames.TEAMS.value}/{team_id}",
        CollectionNames.PERMISSION.value
    )
    if not permission:
        raise HTTPException(status_code=403, detail="User does not have permission to delete this team")

    if permission.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="User does not have permission to delete this team")

    logger.info(f"Deleting team: {team_id}")

    try:
        # First delete all permission edges
        delete_query = f"""
        FOR permission IN {CollectionNames.PERMISSION.value}
        FILTER permission._to == @teamId
        REMOVE permission IN {CollectionNames.PERMISSION.value}
        RETURN OLD
        """

        permissions = arango_service.db.aql.execute(
            delete_query,
            bind_vars={"teamId": f"{CollectionNames.TEAMS.value}/{team_id}"}
        )
        permissions = list(permissions)

        # Delete the team
        result = await arango_service.delete_nodes([team_id], CollectionNames.TEAMS.value)
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
async def get_user_teams(request: Request) -> JSONResponse:
    """Get all teams that the current user is a member of"""
    services = await get_services(request)
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fixed query - use @@collection syntax for dynamic collection names
    user_teams_query = """
    FOR permission IN @@permission_collection
    FILTER permission._from == @userId
    LET team = DOCUMENT(permission._to)
    FILTER team != null AND STARTS_WITH(team._id, @teams_collection_prefix)
    LET team_members = (
        FOR member_permission IN @@permission_collection
        FILTER member_permission._to == team._id
        LET member_user = DOCUMENT(member_permission._from)
        FILTER member_user != null
        RETURN {
            "id": member_user._key,
            "userId": member_user.userId,
            "userName": member_user.fullName,
            "userEmail": member_user.email,
            "role": member_permission.role,
            "joinedAt": member_permission.createdAtTimestamp,
            "isOwner": member_permission.role == "OWNER"
        }
    )
    LET member_count = LENGTH(team_members)
    RETURN {
        "id": team._key,
        "name": team.name,
        "description": team.description,
        "createdBy": team.createdBy,
        "orgId": team.orgId,
        "createdAtTimestamp": team.createdAtTimestamp,
        "updatedAtTimestamp": team.updatedAtTimestamp,
        "currentUserPermission": permission,
        "members": team_members,
        "memberCount": member_count,
        "canEdit": permission.role IN ["OWNER"],
        "canDelete": permission.role == "OWNER",
        "canManageMembers": permission.role IN ["OWNER"]
    }
    """

    try:
        result = arango_service.db.aql.execute(
            user_teams_query,
            bind_vars={
                "userId": f"{CollectionNames.USERS.value}/{user['_key']}",
                "@permission_collection": CollectionNames.PERMISSION.value,
                "teams_collection_prefix": f"{CollectionNames.TEAMS.value}/"
            }
        )
        result_list = list(result)
        if not result_list:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "No teams found",
                    "teams": []
                }
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "User teams fetched successfully",
                "teams": result_list
            }
        )
    except Exception as e:
        logger.error(f"Error in get_user_teams: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user teams")

@router.get("/user/list")
async def get_users(
    request: Request,
    search: Optional[str] = Query(None, description="Search users by name or email"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(100, ge=1, le=100, description="Number of items per page")
) -> JSONResponse:
    """Get all users in the current user's organization with pagination and search"""
    services = await get_services(request)
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    # Calculate offset
    offset = (page - 1) * limit

    # Build search filter
    search_filter = ""
    if search:
        search_filter = """
        FILTER user.fullName LIKE CONCAT('%', @search, '%')
        OR user.email LIKE CONCAT('%', @search, '%')
        """

    # Query to get users with their team memberships
    users_query = f"""
    FOR edge IN belongsTo
    FILTER edge._to == CONCAT('organizations/', @orgId)
    AND edge.entityType == 'ORGANIZATION'
    LET user = DOCUMENT(edge._from)
    FILTER user!=null
    FILTER user.isActive == true
    {search_filter}
    SORT user.fullName ASC
    LIMIT @offset, @limit
    RETURN {{
        "id": user._key,
        "userId": user.userId,
        "name": user.fullName,
        "email": user.email,
        "isActive": user.isActive,
        "createdAtTimestamp": user.createdAtTimestamp,
        "updatedAtTimestamp": user.updatedAtTimestamp
    }}
    """

    # Count total users for pagination
    count_query = f"""
    FOR edge IN belongsTo
    FILTER edge._to == CONCAT('organizations/', @orgId)
    AND edge.entityType == 'ORGANIZATION'
    LET user = DOCUMENT(edge._from)
    FILTER user!=null
    FILTER user.isActive == true
    {search_filter}
    COLLECT WITH COUNT INTO total_count
    RETURN total_count
    """

    try:
        # Get total count
        count_params = {"orgId": user_info.get("orgId")}
        if search:
            count_params["search"] = search

        count_result = arango_service.db.aql.execute(count_query, bind_vars=count_params)
        count_list = list(count_result)
        total_count = count_list[0] if count_list else 0

        # Get users with pagination
        users_params = {
            "orgId": user_info.get("orgId"),
            "offset": offset,
            "limit": limit
        }
        if search:
            users_params["search"] = search

        result = arango_service.db.aql.execute(users_query, bind_vars=users_params)
        result_list = list(result)
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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        # Query to get team users with current user's permission (flat structure)
        team_users_query = f"""
        FOR team IN {CollectionNames.TEAMS.value}
        FILTER team._key == @teamId AND team.orgId == @orgId
        LET current_user_permission = (
            FOR permission IN {CollectionNames.PERMISSION.value}
            FILTER permission._from == @currentUserId AND permission._to == team._id
            RETURN permission
        )
        LET team_members = (
            FOR permission IN {CollectionNames.PERMISSION.value}
            FILTER permission._to == team._id
            LET user = DOCUMENT(permission._from)
            RETURN {{
                "id": user._key,
                "userId": user.userId,
                "userName": user.fullName,
                "userEmail": user.email,
                "role": permission.role,
                "joinedAt": permission.createdAtTimestamp,
                "isOwner": permission.role == "OWNER"
            }}
        )
        LET user_count = LENGTH(team_members)
        RETURN {{
            "id": team._key,
            "name": team.name,
            "description": team.description,
            "createdBy": team.createdBy,
            "orgId": team.orgId,
            "createdAtTimestamp": team.createdAtTimestamp,
            "updatedAtTimestamp": team.updatedAtTimestamp,
            "currentUserPermission": LENGTH(current_user_permission) > 0 ? current_user_permission[0] : null,
            "members": team_members,
            "memberCount": user_count,
            "canEdit": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"],
            "canDelete": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role == "OWNER",
            "canManageMembers": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"]
        }}
        """

        result = arango_service.db.aql.execute(
            team_users_query,
            bind_vars={
                "teamId": team_id,
                "orgId": user_info.get("orgId"),
                "currentUserId": f"{CollectionNames.USERS.value}/{user['_key']}"
            }
        )
        result_list = list(result)
        result = result_list[0] if result_list else None

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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
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
            delete_query = f"""
            FOR permission IN {CollectionNames.PERMISSION.value}
            FILTER permission._to == @teamId
            FILTER SPLIT(permission._from, '/')[1] IN @userIds
            REMOVE permission IN {CollectionNames.PERMISSION.value}
            RETURN OLD
            """
            permissions = arango_service.db.aql.execute(
                delete_query,
                {"teamId": f"{CollectionNames.TEAMS.value}/{team_id}", "userIds": remove_user_ids}
            )
            permissions = list(permissions)
            if not permissions:
                raise HTTPException(status_code=404, detail="No users found in team to remove")
            logger.info(f"Successfully removed {len(permissions)} users from team {team_id}")

        # Add users if specified
        if add_user_ids:
            user_team_edges = []
            for user_id in add_user_ids:
                user_team_edges.append({
                    "_from": f"{CollectionNames.USERS.value}/{user_id}",
                    "_to": f"{CollectionNames.TEAMS.value}/{team_id}",
                    "type": "USER",
                    "role": role,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                })
            logger.info(f"Adding {len(add_user_ids)} users to team {team_id}")
            result = await arango_service.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value)
            if not result:
                raise HTTPException(status_code=500, detail="Failed to add users to team")
            logger.info(f"Successfully added {len(add_user_ids)} users to team {team_id}")

        # Return updated team with users
        updated_team = await get_team_with_users(arango_service, team_id, user['_key'])

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
    arango_service = services["arango_service"]
    logger = services["logger"]

    user_info = {
        "userId": request.state.user.get("userId"),
        "orgId": request.state.user.get("orgId"),
    }

    user = await arango_service.get_user_by_user_id(user_info.get("userId"))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get query parameters
    query = request.query_params.get("q", "")
    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))

    if not query:
        raise HTTPException(status_code=400, detail="Search query is required")

    try:
        # Search teams by name or description with current user's permission
        search_query = f"""
        FOR team IN {CollectionNames.TEAMS.value}
        FILTER team.orgId == @orgId
        FILTER team.name LIKE CONCAT("%", @query, "%") OR team.description LIKE CONCAT("%", @query, "%")
        LET current_user_permission = (
            FOR permission IN {CollectionNames.PERMISSION.value}
            FILTER permission._from == @currentUserId AND permission._to == team._id
            RETURN permission
        )
        LET team_members = (
            FOR permission IN {CollectionNames.PERMISSION.value}
            FILTER permission._to == team._id
            LET user = DOCUMENT(permission._from)
            RETURN {{
                "id": user._key,
                "userId": user.userId,
                "userName": user.fullName,
                "userEmail": user.email,
                "role": permission.role,
                "joinedAt": permission.createdAtTimestamp,
                "isOwner": user._key == team.createdBy
            }}
        )
        LET user_count = LENGTH(team_members)
        LIMIT @offset, @limit
        RETURN {{
            "team": {{
                "id": team._key,
                "name": team.name,
                "description": team.description,
                "createdBy": team.createdBy,
                "orgId": team.orgId,
                "createdAtTimestamp": team.createdAtTimestamp,
                "updatedAtTimestamp": team.updatedAtTimestamp
            }},
            "currentUserPermission": LENGTH(current_user_permission) > 0 ? current_user_permission[0] : null,
            "members": team_members,
            "memberCount": user_count,
            "canEdit": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"],
            "canDelete": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role == "OWNER",
            "canManageMembers": LENGTH(current_user_permission) > 0 AND current_user_permission[0].role IN ["OWNER"]
        }}
        """

        result = arango_service.db.aql.execute(
            search_query,
            bind_vars={
                "orgId": user_info.get("orgId"),
                "query": query,
                "limit": limit,
                "offset": offset,
                "currentUserId": f"{CollectionNames.USERS.value}/{user['_key']}"
            }
        )
        result = list(result)
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
