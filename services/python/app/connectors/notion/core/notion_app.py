from typing import Dict, List, Optional, Tuple
import logging
import threading
from functools import wraps

def singleton(cls):
    """Decorator to make any class a singleton"""
    instances = {}
    lock = threading.Lock()
    
    @wraps(cls)
    def get_instance(*args, **kwargs):
        if cls not in instances:
            with lock:
                if cls not in instances:
                    instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    return get_instance


@singleton
class NotionApp:
    """Singleton NotionApp to manage Notion integrations globally across the application"""
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._integration_map: Dict[Tuple[str, str], str] = {}
        self._org_workspaces: Dict[str, List[str]] = {}
        self._lock = threading.Lock()
        self.logger.info("NotionApp singleton initialized")
    
    def add_integration(self, org_id: str, workspace_id: str, integration_secret: str) -> None:
        """Thread-safe add integration"""
        with self._lock:
            key = (org_id, workspace_id)
            self._integration_map[key] = integration_secret
            
            if org_id not in self._org_workspaces:
                self._org_workspaces[org_id] = []
            
            if workspace_id not in self._org_workspaces[org_id]:
                self._org_workspaces[org_id].append(workspace_id)
                
        self.logger.info(f"Added Notion integration for org {org_id}, workspace {workspace_id}")
    
    def get_integration_secret(self, org_id: str, workspace_id: str) -> Optional[str]:
        """Thread-safe get integration secret"""
        with self._lock:
            key = (org_id, workspace_id)
            return self._integration_map.get(key)
    
    def get_org_integrations(self, org_id: str) -> Dict[str, str]:
        """Thread-safe get all integrations for an organization"""
        with self._lock:
            org_integrations = {}
            if org_id in self._org_workspaces:
                for workspace_id in self._org_workspaces[org_id]:
                    key = (org_id, workspace_id)
                    if key in self._integration_map:
                        org_integrations[workspace_id] = self._integration_map[key]
            return org_integrations
    
    def get_all_integrations(self) -> Dict[Tuple[str, str], str]:
        """Thread-safe get all integrations"""
        with self._lock:
            return self._integration_map.copy()
    
    def has_integration(self, org_id: str, workspace_id: str) -> bool:
        """Thread-safe check if integration exists"""
        with self._lock:
            key = (org_id, workspace_id)
            return key in self._integration_map
    
    def remove_integration(self, org_id: str, workspace_id: str) -> bool:
        """Thread-safe remove integration"""
        with self._lock:
            key = (org_id, workspace_id)
            if key in self._integration_map:
                del self._integration_map[key]
                
                if org_id in self._org_workspaces:
                    self._org_workspaces[org_id] = [
                        ws for ws in self._org_workspaces[org_id] if ws != workspace_id
                    ]
                    if not self._org_workspaces[org_id]:
                        del self._org_workspaces[org_id]
                
                self.logger.info(f"Removed integration for org {org_id}, workspace {workspace_id}")
                return True
            return False
    
    def get_stats(self) -> Dict[str, int]:
        """Get statistics about stored integrations"""
        with self._lock:
            return {
                "total_integrations": len(self._integration_map),
                "total_orgs": len(self._org_workspaces),
                "avg_workspaces_per_org": len(self._integration_map) / max(len(self._org_workspaces), 1)
            }
    
    def load_from_secrets_response(self, org_id: str, notion_secrets_response: dict) -> int:
        """Load integrations from Notion secrets response"""
        integration_secrets = notion_secrets_response.get('integrationSecrets', [])
        loaded_count = 0
        
        for i, secret_data in enumerate(integration_secrets):
            # Handle both string secrets and object secrets
            if isinstance(secret_data, str):
                # If it's just a string, use it as the secret with default workspace
                workspace_id = f'workspace_{i}'
                integration_secret = secret_data
            else:
                # If it's an object, extract workspace_id and secret
                workspace_id = secret_data.get('workspace_id') or secret_data.get('workspaceId') or f'workspace_{i}'
                integration_secret = secret_data.get('secret') or secret_data.get('integration_secret') or secret_data
            
            if integration_secret:
                self.add_integration(org_id, workspace_id, integration_secret)
                loaded_count += 1
        
        self.logger.info(f"Loaded {loaded_count} Notion integrations for org {org_id}")
        return loaded_count
    
    def __str__(self) -> str:
        stats = self.get_stats()
        return f"NotionApp({stats['total_integrations']} integrations, {stats['total_orgs']} orgs)"

