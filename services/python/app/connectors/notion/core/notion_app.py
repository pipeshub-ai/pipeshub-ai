import asyncio
import aiohttp
from app.connectors.notion.core.notion_service import NotionService

class NotionApp:
    """Singleton NotionApp that manages all Notion integrations across organizations."""
    
    def __init__(self, logger, arango_service, kafka_service, config_service):
        self.logger = logger
        self.arango_service = arango_service
        self.kafka_service = kafka_service
        self.config_service = config_service
        self.org_integrations = {}  # org_id -> {workspace_id -> NotionService}
        self.workspace_cache = {}   # integration_secret -> workspace_info
        self
        self._lock = asyncio.Lock()
    
    async def initialize_org_integrations(self, org_id: str, integration_secrets: list) -> int:
        """Initialize all Notion integrations for an organization."""
        async with self._lock:
            self.logger.info(f"🔄 Initializing Notion integrations for org: {org_id}")
            
            if org_id not in self.org_integrations:
                self.org_integrations[org_id] = {}
            
            successful_count = 0
            
            for i, integration_secret in enumerate(integration_secrets):
                try:
                    # Get workspace info
                    workspace_info = await self._get_workspace_info(integration_secret)
                    workspace_id = workspace_info.get("id", "").replace("-", "")
                    
                    # Create NotionService for this integration
                    notion_service = NotionService(
                        integration_secret=integration_secret,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        logger=self.logger,
                        arango_service=self.arango_service,
                        kafka_service=self.kafka_service,
                        config_service=self.config_service
                    )
                    
                    # Store in org integrations
                    self.org_integrations[org_id][workspace_id] = notion_service
                    self.workspace_cache[integration_secret] = workspace_info
                    
                    self.logger.info(f"✅ Initialized integration {i+1} for workspace: {workspace_id}")
                    successful_count += 1
                    
                except Exception as e:
                    self.logger.error(f"❌ Failed to initialize integration {i+1}: {str(e)}")
                    continue
            
            self.logger.info(f"📊 Initialized {successful_count}/{len(integration_secrets)} integrations for org {org_id}")
            return successful_count
    
    async def sync_org_data(self, org_id: str) -> dict:
        """Sync data for all integrations in an organization."""
        if org_id not in self.org_integrations:
            raise ValueError(f"No integrations found for org: {org_id}")
        
        results = {
            "total_integrations": len(self.org_integrations[org_id]),
            "successful_syncs": 0,
            "failed_syncs": 0,
            "sync_details": []
        }
        
        for workspace_id, notion_service in self.org_integrations[org_id].items():
            try:
                self.logger.info(f"🔄 Starting sync for workspace: {workspace_id}")
                
                sync_result = {"workspace_id": workspace_id, "status": "started"}
                
                # Process the async generator
                async for result in notion_service.fetch_and_create_notion_records():
                    step = result.get("step")
                    status = result.get("status")
                    message = result.get("message")
                    data = result.get("data", {})
                    
                    if status == "completed" and step == "sync":
                        sync_result.update({
                            "status": "completed",
                            "user_count": data.get('user_count', 0),
                            "page_count": data.get('page_count', 0),
                            "database_count": data.get('database_count', 0)
                        })
                        results["successful_syncs"] += 1
                        self.logger.info(f"✅ Completed sync for workspace: {workspace_id}")
                        break
                    elif status == "failed":
                        sync_result.update({"status": "failed", "error": result.get("error")})
                        results["failed_syncs"] += 1
                        self.logger.error(f"❌ Failed sync for workspace: {workspace_id}")
                        break
                
                results["sync_details"].append(sync_result)
                
            except Exception as e:
                self.logger.error(f"❌ Error syncing workspace {workspace_id}: {str(e)}")
                results["failed_syncs"] += 1
                results["sync_details"].append({
                    "workspace_id": workspace_id,
                    "status": "failed",
                    "error": str(e)
                })
        
        return results
    
    async def _get_workspace_info(self, integration_secret: str) -> dict:
        """Get workspace information from Notion API."""
        if integration_secret in self.workspace_cache:
            return self.workspace_cache[integration_secret]
        
        url = "https://api.notion.com/v1/users/me"
        headers = {
            "Authorization": f"Bearer {integration_secret}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to fetch workspace info: {error_text}")
                
                return await response.json()
    
    def get_org_integrations(self, org_id: str) -> dict:
        """Get all integrations for an organization."""
        return self.org_integrations.get(org_id, {})
    
    def get_stats(self) -> dict:
        """Get global statistics."""
        total_orgs = len(self.org_integrations)
        total_integrations = sum(len(integrations) for integrations in self.org_integrations.values())
        
        return {
            "total_organizations": total_orgs,
            "total_integrations": total_integrations,
            "organizations": list(self.org_integrations.keys())
        }
    
    async def remove_org_integrations(self, org_id: str):
        """Remove all integrations for an organization."""
        async with self._lock:
            if org_id in self.org_integrations:
                del self.org_integrations[org_id]
                self.logger.info(f"🗑️ Removed all integrations for org: {org_id}")

