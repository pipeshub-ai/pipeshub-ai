import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, List
from hubspot import HubSpot  # type: ignore
from hubspot.crm.contacts import SimplePublicObjectInput  # type: ignore
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)


@dataclass
class HubSpotResponse:
    """Standardized HubSpot API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), default=str)


class HubSpotRESTClientViaToken:
    """HubSpot REST client via access token using official SDK
    
    Args:
        access_token: The access token to use for authentication
    """

    def __init__(self, access_token: str) -> None:
        self.client = HubSpot(access_token=access_token)
        self.access_token = access_token
        logger.info("HubSpot client initialized successfully")

    def get_hubspot_client(self) -> HubSpot:
        """Return the HubSpot SDK client"""
        return self.client


@dataclass
class HubSpotTokenConfig:
    """Configuration for HubSpot REST client via access token
    
    Args:
        access_token: The access token to use for authentication
    """
    access_token: str

    def create_client(self) -> HubSpotRESTClientViaToken:
        return HubSpotRESTClientViaToken(self.access_token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class HubSpotClient(IClient):
    """Builder class for HubSpot clients with different construction methods"""

    def __init__(self, client: HubSpotRESTClientViaToken) -> None:
        """Initialize with a HubSpot client object"""
        self.client = client

    def get_client(self) -> HubSpotRESTClientViaToken:
        """Return the HubSpot client object"""
        return self.client

    def get_hubspot_client(self) -> HubSpot:
        """Return the HubSpot SDK client"""
        return self.client.get_hubspot_client()

    @classmethod
    def build_with_config(cls, config: HubSpotTokenConfig) -> 'HubSpotClient':
        """Build HubSpotClient with configuration
        
        Args:
            config: HubSpotTokenConfig instance
            
        Returns:
            HubSpotClient instance
        """
        return cls(config.create_client())

    @classmethod  
    def build_with_token(cls, access_token: str) -> 'HubSpotClient':
        """Build HubSpotClient directly with access token
        
        Args:
            access_token: HubSpot private app access token
            
        Returns:
            HubSpotClient instance
        """
        config = HubSpotTokenConfig(access_token=access_token)
        return cls.build_with_config(config)
