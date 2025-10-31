import json
import uuid
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain.chat_models.base import BaseChatModel
from pydantic import BaseModel

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import BaseArangoService
from app.modules.agents.qna.chat_state import build_initial_state
from app.modules.agents.qna.graph import agent_graph
from app.modules.reranker.reranker import RerankerService
from app.modules.retrieval.retrieval_service import RetrievalService
from app.utils.time_conversion import get_epoch_timestamp_in_ms

router = APIRouter()


class ChatQuery(BaseModel):
    query: str
    limit: Optional[int] = 50
    previousConversations: List[Dict] = []
    quickMode: bool = False
    filters: Optional[Dict[str, Any]] = None
    retrievalMode: Optional[str] = "HYBRID"
    systemPrompt: Optional[str] = None
    tools: Optional[List[str]] = None


async def get_services(request: Request) -> Dict[str, Any]:
    """Get all required services from the container"""
    container = request.app.container

    # Get services
    retrieval_service = await container.retrieval_service()
    arango_service = await container.arango_service()
    reranker_service = container.reranker_service()
    config_service = container.config_service()
    logger = container.logger()

    # Get and verify LLM
    llm = retrieval_service.llm
    if llm is None:
        llm = await retrieval_service.get_llm_instance()
        if llm is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to initialize LLM service. LLM configuration is missing.",
            )

    return {
        "retrieval_service": retrieval_service,
        "arango_service": arango_service,
        "reranker_service": reranker_service,
        "config_service": config_service,
        "logger": logger,
        "llm": llm,
    }

async def get_user_org_info(request: Request, user_info: Dict[str, Any], arango_service: BaseArangoService, logger: Logger) -> Dict[str, Any]:
    """Get user and org info from request"""
    org_info = None
    try:
        # Get user document
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        if not user or not isinstance(user, dict):
            raise HTTPException(status_code=404, detail="User not found")

        # Extract user email and add to user_info
        user_email = str(user.get("email", "")).strip()
        if not user_email:
            raise HTTPException(status_code=400, detail="User email missing")

        # Add user_email and _key to user_info
        user_info["userEmail"] = user_email
        user_info["_key"] = user.get("_key")

        # Get organization document
        org_doc = await arango_service.get_document(user_info.get("orgId"), CollectionNames.ORGS.value)
        if not org_doc or not isinstance(org_doc, dict):
            raise HTTPException(status_code=404, detail="Organization not found")

        # Determine account type
        raw_account_type = str(org_doc.get("accountType", "")).lower()
        account_type = "enterprise" if raw_account_type == "enterprise" else ("individual" if raw_account_type == "individual" else "")
        if account_type == "":
            raise HTTPException(status_code=400, detail="Invalid account type")

        org_info = {
            "orgId": user_info.get("orgId"),
            "accountType": account_type
        }
        return org_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user/org info: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch user/org information")


@router.post("/agent-chat")
async def askAI(request: Request, query_info: ChatQuery) -> JSONResponse:
    """Process chat query using LangGraph agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        reranker_service = services["reranker_service"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
            "sendUserInfo": request.query_params.get("sendUserInfo", True),
        }

        # Fetch user and org info for impersonation
        org_info = await get_user_org_info(request, user_info, arango_service, logger)

        # Build initial state
        initial_state = build_initial_state(
            query_info.model_dump(),
            user_info,
            llm,
            logger,
            retrieval_service,
            arango_service,
            reranker_service,
            org_info,
        )

        # Execute the graph with async
        logger.info(f"Starting LangGraph execution for query: {query_info.query}")

        config = {"recursion_limit": 50}

        final_state = await agent_graph.ainvoke(initial_state, config=config)  # Using async invoke

        # Check for errors
        if final_state.get("error"):
            error = final_state["error"]
            return JSONResponse(
                status_code=error.get("status_code", 500),
                content={
                    "status": error.get("status", "error"),
                    "message": error.get("message", error.get("detail", "An error occurred")),
                    "searchResults": [],
                    "records": [],
                },
            )

        # Return the response
        return final_state["response"]

    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in askAI: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


async def stream_response(
    query_info: Dict[str, Any],
    user_info: Dict[str, Any],
    llm: BaseChatModel,
    logger: Logger,
    retrieval_service: RetrievalService,
    arango_service: BaseArangoService,
    reranker_service: RerankerService,
    org_info: Dict[str, Any] = None,
) -> AsyncGenerator[str, None]:
    # Build initial state
    initial_state = build_initial_state(
        query_info,
        user_info,
        llm,
        logger,
        retrieval_service,
        arango_service,
        reranker_service,
        org_info,
    )

    # Execute the graph with async
    logger.info(f"Query info: {query_info}")
    logger.info(f"Starting LangGraph execution for query: {query_info.get('query')}")

    config = {"recursion_limit": 50}  # Increased from default 25 to 50

    async for chunk in agent_graph.astream(initial_state, config=config, stream_mode="custom"):
        if isinstance(chunk, dict) and "event" in chunk:
            # Convert dict to JSON string for streaming
            yield f"event: {chunk['event']}\ndata: {json.dumps(chunk['data'])}\n\n"


@router.post("/agent-chat-stream")
async def askAIStream(request: Request, query_info: ChatQuery) -> StreamingResponse:
    """Process chat query using LangGraph agent with streaming"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        reranker_service = services["reranker_service"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
            "sendUserInfo": request.query_params.get("sendUserInfo", True),
        }

        # Fetch user and org info for impersonation
        org_info = await get_user_org_info(request, user_info, arango_service, logger)

        # Stream the response
        return StreamingResponse(
            stream_response(
                query_info.model_dump(), user_info, llm, logger, retrieval_service, arango_service, reranker_service, org_info
            ),
            media_type="text/event-stream",
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error in askAIStream: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/template/create")
async def create_agent_template(request: Request) -> JSONResponse:
    """Create a new agent template"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))

        # Validate required fields
        required_fields = ["name", "description", "systemPrompt"]
        for field in required_fields:
            if not body_dict.get(field) or not body_dict.get(field).strip():
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        time = get_epoch_timestamp_in_ms()
        template_key = str(uuid.uuid4())

        # Get user first
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Create the template with all required fields
        template = {
            "_key": template_key,
            "name": body_dict.get("name").strip(),
            "description": body_dict.get("description").strip(),
            "startMessage": body_dict.get("startMessage", "").strip() or "Hello! How can I help you today?",  # Provide default
            "systemPrompt": body_dict.get("systemPrompt").strip(),
            "tools": body_dict.get("tools", []),
            "models": body_dict.get("models", []),
            "memory": body_dict.get("memory", {"type": []}),
            "tags": body_dict.get("tags", []),
            "orgId": user_info.get("orgId"),
            "isActive": True,
            "createdBy": user.get("_key"),
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": body_dict.get("isDeleted", False),
        }

        logger.info(f"Creating agent template: {template}")

        user_template_access = {
            "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
            "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_key}",
            "role": "OWNER",
            "type": "USER",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }

        # Create the template
        result = await arango_service.batch_upsert_nodes([template], CollectionNames.AGENT_TEMPLATES.value)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent template")

        result = await arango_service.batch_create_edges([user_template_access], CollectionNames.PERMISSION.value)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent template access")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template created successfully",
                "template": template,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in create_agent_template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/template/list")
async def get_agent_templates(request: Request) -> JSONResponse:
    """Get all agent templates"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for getting agent templates")
        # Get all templates
        templates = await arango_service.get_all_agent_templates(user.get("_key"))
        if not templates:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "No agent templates found",
                    "templates": [],
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent templates retrieved successfully",
                "templates": templates,
            },
        )
    except Exception as e:
        logger.error(f"Error in get_agent_templates: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/template/{template_id}")
async def get_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Get an agent template by ID"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for getting agent template")
        # Get the template access
        template = await arango_service.get_template(template_id, user.get("_key"))
        if template is None:
            raise HTTPException(status_code=404, detail="Agent template not found")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template retrieved successfully",
                "template": template,
            },
        )
    except Exception as e:
        logger.error(f"Error in get_agent_template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/share-template/{template_id}")
async def share_agent_template(request: Request, template_id: str, user_ids: List[str] = Body(...), team_ids: List[str] = Body(...)) -> JSONResponse:
    """Share an agent template"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for sharing agent template")
        # Get the template
        template = await arango_service.get_template(template_id, user.get("_key"))
        if not template:
            raise HTTPException(status_code=404, detail="Agent template not found")
        # Share the template
        result = await arango_service.share_agent_template(template_id, user.get("_key"), user_ids, team_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to share agent template")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template shared successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in share_agent_template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/template/{template_id}/clone")
async def clone_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Clone an agent template"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Clone the template
        cloned_template_id = await arango_service.clone_agent_template(template_id)
        if cloned_template_id is None:
            raise HTTPException(status_code=400, detail="Failed to clone agent template")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template cloned successfully",
                "templateId": cloned_template_id,
            },
        )
    except Exception as e:
        logger.error(f"Error in clone_agent_template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/template/{template_id}")
async def delete_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Delete an agent template"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for deleting agent template")
        # Delete the template
        result = await arango_service.delete_agent_template(template_id,user.get("_key"))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to delete agent template")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template deleted successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in delete_agent_template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/template/{template_id}")
async def update_agent_template(request: Request, template_id: str) -> JSONResponse:
    """Update an agent template"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))
        # Update the template
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for updating agent template")
        result = await arango_service.update_agent_template(template_id, body_dict, user.get("_key"))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to update agent template")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent template updated successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in update_agent_template: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/create")
async def create_agent(request: Request) -> JSONResponse:
    """Create a new agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        time = get_epoch_timestamp_in_ms()
        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for creating agent")

        # Validate that at least one reasoning model is present
        models = body_dict.get("models", [])
        if models and isinstance(models, list) and len(models) > 0:
            has_reasoning_model = any(
                model.get("isReasoning", False) is True
                for model in models
                if isinstance(model, dict)
            )
            if not has_reasoning_model:
                raise HTTPException(
                    status_code=400,
                    detail="At least one reasoning model must be present in the models array. Please add a reasoning model to your agent configuration."
                )

        agent = {
            "_key": str(uuid.uuid4()),
            "name": body_dict.get("name"),
            "description": body_dict.get("description"),
            "startMessage": body_dict.get("startMessage"),
            "systemPrompt": body_dict.get("systemPrompt"),
            "tools": body_dict.get("tools"),
            "models": body_dict.get("models"),
            "apps": body_dict.get("apps"),
            "kb": body_dict.get("kb"),
            "vectorDBs": body_dict.get("vectorDBs"),
            "tags": body_dict.get("tags"),
            "orgId": user_info.get("orgId"),
            "createdBy": user.get("_key"),
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
            "isDeleted": False,
        }
        # Create the agent
        result = await arango_service.batch_upsert_nodes([agent], CollectionNames.AGENT_INSTANCES.value)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent")
        # create user/teams agent edge
        edge = {
            "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
            "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent.get('_key')}",
            "role": "OWNER",
            "type": "USER",
            "createdAtTimestamp": time,
            "updatedAtTimestamp": time,
        }
        result = await arango_service.batch_create_edges([edge], CollectionNames.PERMISSION.value)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to create agent permission")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent created successfully",
                "agent": agent,
            },
        )
    except HTTPException as he:
        # Re-raise HTTP exceptions with their original status codes
        raise he
    except Exception as e:
        logger.error(f"Error in create_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{agent_id}")
async def get_agent(request: Request, agent_id: str) -> JSONResponse:
    """Get an agent by ID"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for getting agent")

        agent =  await arango_service.get_agent(agent_id, user.get("_key"))
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent retrieved successfully",
                "agent": agent,
            },
        )
    except Exception as e:
        logger.error(f"Error in get_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/")
async def get_agents(request: Request) -> JSONResponse:
    """Get all agents"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for getting agents")
        # Get all agents
        agents = await arango_service.get_all_agents(user.get("_key"))
        if agents is None or len(agents) == 0:
            raise HTTPException(status_code=404, detail="No agents found")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agents retrieved successfully",
                "agents": agents,
            },
        )
    except Exception as e:
        logger.error(f"Error in get_agents: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{agent_id}")
async def update_agent(request: Request, agent_id: str) -> JSONResponse:
    """Update an agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))

        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for updating agent")

        # Check if user has access to the agent and can edit it
        agent_with_permission = await arango_service.get_agent(agent_id, user.get("_key"))
        if agent_with_permission is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Only OWNER can edit the agent
        if not agent_with_permission.get("can_edit", False):
            raise HTTPException(status_code=403, detail="Only the owner can edit this agent")

        # Update the agent
        result = await arango_service.update_agent(agent_id, body_dict, user.get("_key"))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to update agent")
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent updated successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in update_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{agent_id}")
async def delete_agent(request: Request, agent_id: str) -> JSONResponse:
    """Delete an agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for deleting agent")

        # Check if user has access to the agent and can delete it
        agent_with_permission = await arango_service.get_agent(agent_id, user.get("_key"))
        if agent_with_permission is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Only OWNER can delete the agent
        if not agent_with_permission.get("can_delete", False):
            raise HTTPException(status_code=403, detail="Only the owner can delete this agent")

        # Delete the agent
        result = await arango_service.delete_agent(agent_id, user.get("_key"))
        if not result:
            raise HTTPException(status_code=400, detail="Failed to delete agent")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent deleted successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in delete_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{agent_id}/share")
async def share_agent(request: Request, agent_id: str) -> JSONResponse:
    """Share an agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))
        user_ids = body_dict.get("userIds", [])
        team_ids = body_dict.get("teamIds", [])

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }

        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for sharing agent")

        # Check if user has permission to share the agent
        agent_with_permission = await arango_service.get_agent(agent_id, user.get("_key"))
        if agent_with_permission is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Only OWNER and ORGANIZER can share the agent
        if not agent_with_permission.get("can_share", False):
            raise HTTPException(status_code=403, detail="You don't have permission to share this agent")

        result = await arango_service.share_agent(agent_id, user.get("_key"), user_ids, team_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to share agent")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent shared successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in share_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{agent_id}/unshare")
async def unshare_agent(request: Request, agent_id: str) -> JSONResponse:
    """Unshare an agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))
        user_ids = body_dict.get("userIds", [])
        team_ids = body_dict.get("teamIds", [])

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }

        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for unsharing agent")

        # Check if user has permission to unshare the agent
        agent_with_permission = await arango_service.get_agent(agent_id, user.get("_key"))
        if agent_with_permission is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Only OWNER and ORGANIZER can unshare the agent
        if not agent_with_permission.get("can_share", False):
            raise HTTPException(status_code=403, detail="You don't have permission to unshare this agent")

        # Unshare the agent
        result = await arango_service.unshare_agent(agent_id, user.get("_key"), user_ids, team_ids)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to unshare agent")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent unshared successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in unshare_agent: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{agent_id}/permissions")
async def get_agent_permissions(request: Request, agent_id: str) -> JSONResponse:
    """Get all permissions for an agent - only OWNER can view all permissions"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }

        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for viewing agent permissions")

        # Get agent permissions (only OWNER can view all permissions)
        permissions = await arango_service.get_agent_permissions(agent_id, user.get("_key"))
        if permissions is None:
            raise HTTPException(status_code=403, detail="You don't have permission to view permissions for this agent")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent permissions retrieved successfully",
                "permissions": permissions,
            },
        )
    except Exception as e:
        logger.error(f"Error in get_agent_permissions: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{agent_id}/permissions")
async def update_agent_permission(request: Request, agent_id: str) -> JSONResponse:
    """Update permission role for a user on an agent - only OWNER can do this"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
        }

        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))
        user_ids = body_dict.get("userIds", [])
        team_ids = body_dict.get("teamIds", [])
        role = body_dict.get("role")

        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        logger.info(f"User: {user}")
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for updating agent permission")

        # Update the permission (only OWNER can do this)
        result = await arango_service.update_agent_permission(agent_id, user.get("_key"), user_ids, team_ids, role)
        if not result:
            raise HTTPException(status_code=400, detail="Failed to update agent permission")

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Agent permission updated successfully",
            },
        )
    except Exception as e:
        logger.error(f"Error in update_agent_permission: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{agent_id}/chat")
async def chat(request: Request, agent_id: str, chat_query: ChatQuery) -> JSONResponse:
    """Chat with an agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]
        reranker_service = services["reranker_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
            "sendUserInfo": request.state.user.get("sendUserInfo", True),
        }

        # Fetch user and org info for impersonation
        org_info = await get_user_org_info(request, user_info, arango_service, logger)
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for chatting with agent")

        # Get the agent
        agent = await arango_service.get_agent(agent_id, user.get("_key"))
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Build filters object
        filters = {}
        if chat_query.filters is not None:
            # Use chat query filters if provided
            filters = chat_query.filters.copy()
        else:
            # If no filters, create filters from agent defaults
            filters = {
                "apps": agent.get("apps"),
                "kb": agent.get("kb"),
                "vectorDBs": agent.get("vectorDBs")
            }

        # Override individual filter values if they exist in chat query
        if chat_query.filters is not None:
            if chat_query.filters.get("apps") is not None:
                filters["apps"] = chat_query.filters.get("apps")
            if chat_query.filters.get("kb") is not None:
                filters["kb"] = chat_query.filters.get("kb")
            if chat_query.filters.get("vectorDBs") is not None:
                filters["vectorDBs"] = chat_query.filters.get("vectorDBs")

        # Override tools if provided in chat query
        tools = chat_query.tools if chat_query.tools is not None else agent.get("tools")
        system_prompt = agent.get("systemPrompt")

        query_info = {
            "query": chat_query.query,
            "limit": chat_query.limit,
            "messages": [],
            "previous_conversations": chat_query.previousConversations,
            "quick_mode": chat_query.quickMode,
            "filters": filters,  # Send the entire filters object
            "tools": tools,
            "systemPrompt": system_prompt,
        }

        initial_state = build_initial_state(
            query_info,
            user_info,
            llm,
            logger,
            retrieval_service,
            arango_service,
            reranker_service,
            org_info,
        )

        # Execute the graph with async
        logger.info(f"Starting LangGraph execution for query: {query_info.query}")

        config = {"recursion_limit": 50}  # Increased from default 25 to 50

        final_state = await agent_graph.ainvoke(initial_state, config=config)  # Using async invoke

        # Check for errors
        if final_state.get("error"):
            error = final_state["error"]
            return JSONResponse(
                status_code=error.get("status_code", 500),
                content={
                    "status": error.get("status", "error"),
                    "message": error.get("message", error.get("detail", "An error occurred")),
                    "searchResults": [],
                    "records": [],
                },
            )

        # Return the response
        return final_state["response"]

    except Exception as e:
        logger.error(f"Error in chat: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{agent_id}/chat/stream")
async def chat_stream(request: Request, agent_id: str) -> StreamingResponse:
    """Chat with an agent"""
    try:
        # Get all services
        services = await get_services(request)
        logger = services["logger"]
        arango_service = services["arango_service"]
        retrieval_service = services["retrieval_service"]
        llm = services["llm"]
        reranker_service = services["reranker_service"]

        # Extract user info from request
        user_info = {
            "orgId": request.state.user.get("orgId"),
            "userId": request.state.user.get("userId"),
            "sendUserInfo": request.state.user.get("sendUserInfo", True),
        }

        # Fetch user and org info for impersonation
        org_info = await get_user_org_info(request, user_info, arango_service, logger)

        # Get the agent
        user = await arango_service.get_user_by_user_id(user_info.get("userId"))
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for chatting with agent")

        agent = await arango_service.get_agent(agent_id, user.get("_key"))
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        body = await request.body()
        body_dict = json.loads(body.decode('utf-8'))

        logger.info(f"body_dict: {body_dict}")
        chat_query = ChatQuery(**body_dict)

        logger.info(f"chat_query: {chat_query}")

        # Build filters object
        filters = {}
        if chat_query.filters is not None:
            # Use chat query filters if provided
            filters = chat_query.filters.copy()
        else:
            # If no filters, create filters from agent defaults
            filters = {
                "apps": agent.get("apps"),
                "kb": agent.get("kb"),
                "vectorDBs": agent.get("vectorDBs")
            }

        # Override individual filter values if they exist in chat query
        if chat_query.filters is not None:
            if chat_query.filters.get("apps") is not None:
                filters["apps"] = chat_query.filters.get("apps")
            if chat_query.filters.get("kb") is not None:
                filters["kb"] = chat_query.filters.get("kb")
            if chat_query.filters.get("vectorDBs") is not None:
                filters["vectorDBs"] = chat_query.filters.get("vectorDBs")

        # Override tools if provided in chat query
        if chat_query.tools is not None:
            tools = chat_query.tools
            logger.info(f"Using tools from chat query: {tools}")
        else:
            tools = agent.get("tools")
            logger.info(f"Using tools from agent config: {tools}")

        logger.info(f"Tools: {tools}")
        logger.info(f"Filters: {filters}")
        logger.info(f"chat query: {chat_query}")

        system_prompt = agent.get("systemPrompt")

        query_info = {
            "query": chat_query.query,
            "limit": chat_query.limit,
            "messages": [],
            "previous_conversations": chat_query.previousConversations,
            "quick_mode": chat_query.quickMode,
            "filters": filters,  # Send the entire filters object
            "tools": tools,
            "systemPrompt": system_prompt,
        }

        return StreamingResponse(
            stream_response(
                query_info, user_info, llm, logger, retrieval_service, arango_service, reranker_service, org_info
            ),
            media_type="text/event-stream",
        )
    except Exception as e:
        logger.error(f"Error in chat_stream: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
