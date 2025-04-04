from app.utils.logger import create_logger
from app.setups.connector_setup import AppContainer, initialize_container, initialize_individual_account_services_fn, initialize_enterprise_account_services_fn
from app.connectors.api.router import router
from app.connectors.core.kafka_consumer import KafkaRouteConsumer
from typing import AsyncGenerator
from contextlib import asynccontextmanager
import uvicorn
import asyncio
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request, Depends, HTTPException, status
from app.config.arangodb_constants import Connectors
from app.api.middlewares.auth import authMiddleware

print("Starting connector app")

logger = create_logger(__name__)

container = AppContainer()

async def get_initialized_container() -> AppContainer:
    """Dependency provider for initialized container"""
    logger.debug("🔄 Getting initialized container")
    # Create container instance
    if not hasattr(get_initialized_container, 'initialized'):
        logger.debug("🔧 First-time container initialization")
        await initialize_container(container)
        # Wire the container after initialization
        container.wire(modules=[
            "app.core.celery_app",
            "app.connectors.google.core.sync_tasks",
            "app.connectors.api.router",
            "app.connectors.api.middleware",
            "app.core.signed_url"
        ])
        get_initialized_container.initialized = True
        logger.debug("✅ Container initialization complete")
    return container

async def resume_sync_services(app_container: AppContainer) -> None:
    """Resume sync services for users with active sync states"""
    logger.debug("🔄 Checking for sync services to resume")

    try:
        arango_service = await app_container.arango_service()

        # Get all organizations
        orgs = await arango_service.get_all_orgs(active=True)
        if not orgs:
            logger.info("No organizations found in the system")
            return

        logger.info("Found %d organizations in the system", len(orgs))
        
        # Process each organization
        for org in orgs:
            org_id = org['_key']
            accountType = org.get('accountType', 'individual')

            # Ensure the method is called on the correct object
            if accountType == 'enterprise' or accountType == 'business':
                await initialize_enterprise_account_services_fn(org_id, app_container)
            elif accountType == 'individual':
                await initialize_individual_account_services_fn(org_id, app_container)
            else:
                logger.error("Account Type not valid")
                return False

            user_type = 'enterprise' if accountType in ['enterprise', 'business'] else 'individual'

            logger.info("Processing organization %s with account type %s", org_id, accountType)

            # Get users for this organization
            users = await arango_service.get_users(org_id, active=True)
            logger.info(f"User: {users}")
            if not users:
                logger.info("No users found for organization %s", org_id)
                continue
                
            logger.info("Found %d users for organization %s", len(users), org_id)
            
            enabled_apps = await arango_service.get_org_apps(org_id)
            print(f"Enabled Apps: {enabled_apps}")
            
            for app in enabled_apps:
                if app['name'] == Connectors.GOOGLE_CALENDAR.value:
                    logger.info("Skipping calendar sync for org %s", org_id)
                    continue    
                
                if app['name'] == Connectors.GOOGLE_DRIVE.value:
                    drive_sync_service = app_container.drive_sync_service()
                    await drive_sync_service.initialize(org_id)
                    logger.info("Drive Service initialized for org %s", org_id)
                
                if app['name'] == Connectors.GOOGLE_MAIL.value:
                    gmail_sync_service = app_container.gmail_sync_service()
                    await gmail_sync_service.initialize(org_id)
                    logger.info("Gmail Service initialized for org %s", org_id)

            # Check if Drive sync needs to be initialized
            drive_service_needed = False
            for user in users:
                drive_state = (await arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_DRIVE.value) or {}).get('syncState', 'NOT_STARTED')
                if drive_state in ['COMPLETED', 'IN_PROGRESS', 'PAUSED']:
                    drive_service_needed = True
                    logger.info("Drive Service needed for org %s: %s", org_id, drive_service_needed)
                    break

            # Initialize Drive sync if needed and collect users
            drive_sync_needed = []
            if drive_service_needed:
                # Re-iterate to collect users needing sync
                for user in users:
                    drive_state = (await arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_DRIVE.value) or {}).get('syncState', 'NOT_STARTED')
                    if drive_state in ['IN_PROGRESS', 'PAUSED']:
                        logger.info("User %s in org %s needs Drive sync (state: %s)", 
                                  user['email'], org_id, drive_state)
                        drive_sync_needed.append(user)

            # Check if Gmail sync needs to be initialized
            gmail_service_needed = False
            for user in users:
                gmail_state = (await arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_MAIL.value) or {}).get('syncState', 'NOT_STARTED')
                if gmail_state in ['COMPLETED', 'IN_PROGRESS', 'PAUSED']:
                    gmail_service_needed = True
                    logger.info("Gmail Service needed for org %s: %s", org_id, gmail_service_needed)
                    break

            # Initialize Gmail sync if needed and collect users
            gmail_sync_needed = []
            if gmail_service_needed:                
                # Re-iterate to collect users needing sync
                for user in users:
                    gmail_state = (await arango_service.get_user_sync_state(user['email'], Connectors.GOOGLE_MAIL.value) or {}).get('syncState', 'NOT_STARTED')
                    if gmail_state in ['IN_PROGRESS', 'PAUSED']:
                        logger.info("User %s in org %s needs Gmail sync (state: %s)", 
                                  user['email'], org_id, gmail_state)
                        gmail_sync_needed.append(user)

            # Resume Drive syncs if needed
            if drive_sync_needed:
                logger.info("Resuming Drive sync for %d users in org %s", len(drive_sync_needed), org_id)

                for user in drive_sync_needed:
                    try:
                        if user_type == 'enterprise' or user_type == 'business':
                            await drive_sync_service.sync_specific_user(user['email'])
                            logger.info("✅ Resumed Drive sync for user %s in org %s", user['email'], org_id)
                        else:  # individual
                            # Start sync
                            await drive_sync_service.perform_initial_sync(org_id)
                            logger.info("✅ Resumed Drive sync for user %s in org %s", user['email'], org_id)
                    except Exception as e:
                        logger.error("❌ Error resuming Drive sync for user %s in org %s: %s", 
                                   user['email'], org_id, str(e))

            # Resume Gmail syncs if needed
            if gmail_sync_needed:
                logger.info("Resuming Gmail sync for %d users in org %s", len(gmail_sync_needed), org_id)
                
                for user in gmail_sync_needed:
                    try:
                        if user_type == 'enterprise' or user_type == 'business':
                            await gmail_sync_service.sync_specific_user(user['email'])
                            logger.info("✅ Resumed Gmail sync for user %s in org %s", user['email'], org_id)
                        else:  # individual
                            # Start sync
                            await gmail_sync_service.perform_initial_sync(org_id)
                            logger.info("✅ Resumed Gmail sync for user %s in org %s", user['email'], org_id)
                    except Exception as e:
                        logger.error("❌ Error resuming Gmail sync for user %s in org %s: %s", 
                                   user['email'], org_id, str(e))

    except Exception as e:
        logger.error("❌ Error during sync service resumption: %s", str(e))

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for FastAPI"""
    logger.debug("🚀 Starting application")
    
    # Initialize container
    app_container = await get_initialized_container()
    app.container = app_container
    
    # Define the routes that Kafka consumer should handle
    kafka_routes = [
        "/drive/{org_id}",
        "/gmail/{org_id}",
        "/drive/{org_id}/sync/start",
        "/drive/{org_id}/sync/pause",
        "/drive/{org_id}/sync/resume",
        "/gmail/{org_id}/sync/start",
        "/gmail/{org_id}/sync/pause",
        "/gmail/{org_id}/sync/resume",
        "/drive/sync/user/{user_email}",
        "/gmail/sync/user/{user_email}"
    ]

    # Kafka Consumer - pass the app_container
    kafka_consumer = KafkaRouteConsumer(
        config_service= app.container.config_service(),
        arango_service=await app.container.arango_service(),
        routes=kafka_routes,  # Pass the list of route patterns
        app_container=app.container
    )
  
    # Initialize Kafka consumer
    consumer = kafka_consumer
    await consumer.start()
    logger.info("✅ Kafka consumer initialized")

    consume_task = asyncio.create_task(consumer.consume_messages())
    
    # Resume sync services
    print(f"App Container: {app.container}")
    await resume_sync_services(app.container)
        
    yield
    
    # Shutdown
    logger.info("🔄 Shutting down application")
    consumer.stop()
    # Cancel the consume task
    consume_task.cancel()
    try:
        await consume_task
    except asyncio.CancelledError:
        logger.info("Kafka consumer task cancelled")
    
    logger.debug("🔄 Shutting down application")
    

# Create FastAPI app with lifespan
app = FastAPI(
    title="Google Drive Sync Service",
    description="Service for syncing Google Drive content to ArangoDB",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(get_initialized_container)]
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# List of paths to apply authentication to
INCLUDE_PATHS = ["/api/v1/stream/record/"]

@app.middleware("http")
async def authenticate_requests(request: Request, call_next):
    logger.info(f"Middleware request: {request.url.path}")
    # Apply middleware only to specific paths
    if not any(request.url.path.startswith(path) for path in INCLUDE_PATHS):
        # Skip authentication for other paths
        return await call_next(request)
    
    try:
        # Apply authentication
        authenticated_request = await authMiddleware(request)
        # Continue with the request
        response = await call_next(authenticated_request)
        return response
        
    except HTTPException as exc:
        # Handle authentication errors
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail}
        )
    except Exception as exc:
        # Handle unexpected errors
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"}
        )

# Include routes
app.include_router(router)

# Global error handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Global error: %s", str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": str(exc),
            "path": request.url.path
        }
    )

def run(host: str = "0.0.0.0", port: int = 8080, workers: int = 1, reload: bool = True):
    """Run the application"""
    uvicorn.run(
        "app.connectors_main:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload,
        workers=workers
    )

if __name__ == "__main__":
    run(reload=False)
