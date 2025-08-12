from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import json
from dataclasses import asdict

from slack_sdk import WebClient  # type: ignore


class SlackConfigBase:
    def create_client(self) -> WebClient: # type: ignore
        raise NotImplementedError

@dataclass
class SlackTokenConfig(SlackConfigBase):
    token: str
    def create_client(self) -> WebClient:
        return WebClient(token=self.token)

@dataclass
class SlackResponse:
    """Standardized Slack API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())

@dataclass
class SlackUser:
    """Slack user information"""
    id: str
    name: str
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    is_bot: bool = False
    is_admin: bool = False
    deleted: bool = False
    
    @classmethod
    def from_slack_response(cls, user_data: Dict[str, Any]) -> 'SlackUser':
        """Create SlackUser from Slack API response"""
        return cls(
            id=user_data.get('id', ''),
            name=user_data.get('name', ''),
            real_name=user_data.get('real_name'),
            display_name=user_data.get('display_name'),
            email=user_data.get('profile', {}).get('email'),
            is_bot=user_data.get('is_bot', False),
            is_admin=user_data.get('is_admin', False),
            deleted=user_data.get('deleted', False)
        )

@dataclass
class SlackChannel:
    """Slack channel information"""
    id: str
    name: str
    is_private: bool = False
    is_archived: bool = False
    is_general: bool = False
    num_members: Optional[int] = None
    topic: Optional[str] = None
    purpose: Optional[str] = None
    
    @classmethod
    def from_slack_response(cls, channel_data: Dict[str, Any]) -> 'SlackChannel':
        """Create SlackChannel from Slack API response"""
        return cls(
            id=channel_data.get('id', ''),
            name=channel_data.get('name', ''),
            is_private=channel_data.get('is_private', False),
            is_archived=channel_data.get('is_archived', False),
            is_general=channel_data.get('is_general', False),
            num_members=channel_data.get('num_members'),
            topic=channel_data.get('topic', {}).get('value') if channel_data.get('topic') else None,
            purpose=channel_data.get('purpose', {}).get('value') if channel_data.get('purpose') else None
        )

@dataclass
class SlackMessage:
    """Slack message information"""
    ts: str
    text: str
    user: str
    channel: str
    type: str = 'message'
    attachments: Optional[List[Dict[str, Any]]] = None
    blocks: Optional[List[Dict[str, Any]]] = None
    
    @classmethod
    def from_slack_response(cls, message_data: Dict[str, Any]) -> 'SlackMessage':
        """Create SlackMessage from Slack API response"""
        return cls(
            ts=message_data.get('ts', ''),
            text=message_data.get('text', ''),
            user=message_data.get('user', ''),
            channel=message_data.get('channel', ''),
            type=message_data.get('type', 'message'),
            attachments=message_data.get('attachments'),
            blocks=message_data.get('blocks')
        )