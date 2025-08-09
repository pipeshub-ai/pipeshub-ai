from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from enum import Enum
from app.models.graph import Node, Edge


class MemoryType(str, Enum):
    CONVERSATIONS = "CONVERSATIONS"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    APPS = "APPS"
    ACTIVITIES = "ACTIVITIES"
    VECTOR_DB = "VECTOR_DB"


class ModelProvider(str, Enum):
    OPENAI = "OPENAI"
    AZURE_OPENAI = "AZURE_OPENAI"
    ANTHROPIC = "ANTHROPIC"
    GOOGLE = "GOOGLE"
    COHERE = "COHERE"
    MISTRAL = "MISTRAL"
    OLLAMA = "OLLAMA"
    BEDROCK = "BEDROCK"
    GEMINI = "GEMINI"
    GROQ = "GROQ"
    TOGETHER = "TOGETHER"
    FIREWORKS = "FIREWORKS"
    XAI = "XAI"
    VERTEX_AI = "VERTEX_AI"
    CUSTOM = "CUSTOM"


class ModelType(str, Enum):
    LLM = "LLM"
    EMBEDDING = "EMBEDDING"
    OCR = "OCR"
    SLM = "SLM"
    REASONING = "REASONING"
    MULTIMODAL = "MULTIMODAL"


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class UserRole(str, Enum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


@dataclass
class ToolConfig:
    """Configuration for a tool"""
    name: str
    description: str
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelConfig:
    """Configuration for an AI model"""
    name: str
    role: str
    provider: str
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalConfig:
    """Configuration for action approval"""
    user_ids: List[str] = field(default_factory=list)
    user_group_ids: List[str] = field(default_factory=list)
    order: int = 0


@dataclass
class ActionConfig:
    """Configuration for an action"""
    name: str
    approvers: List[ApprovalConfig] = field(default_factory=list)
    reviewers: List[ApprovalConfig] = field(default_factory=list)


@dataclass
class MemoryConfig:
    """Configuration for agent memory"""
    types: List[MemoryType] = field(default_factory=list)


@dataclass
class AgentTemplate(Node):
    """Agent template that defines the blueprint for creating agents"""
    name: str
    description: str
    start_message: str
    system_prompt: str
    tools: List[ToolConfig] = field(default_factory=list)
    models: List[ModelConfig] = field(default_factory=list)
    actions: List[ActionConfig] = field(default_factory=list)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tags: List[str] = field(default_factory=list)
    org_id: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None
    updated_by_user_id: Optional[str] = None
    deleted_by_user_id: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "startMessage": self.start_message,
            "systemPrompt": self.system_prompt,
            "tools": [tool.__dict__ for tool in self.tools],
            "models": [model.__dict__ for model in self.models],
            "actions": [action.__dict__ for action in self.actions],
            "memory": {
                "type": [memory_type.value for memory_type in self.memory.types]
            },
            "tags": self.tags,
            "orgId": self.org_id,
            "isActive": self.is_active,
            "createdBy": self.created_by,
            "updatedByUserId": self.updated_by_user_id,
            "deletedByUserId": self.deleted_by_user_id,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.description is not None and self.description != "" and
            self.start_message is not None and self.start_message != "" and
            self.system_prompt is not None and self.system_prompt != ""
        )

    def key(self) -> str:
        return self._key


@dataclass
class Agent(Node):
    """Agent instance created from a template"""
    name: str
    description: str
    template_key: str
    system_prompt: str
    starting_message: str
    selected_tools: List[str] = field(default_factory=list)
    selected_models: List[str] = field(default_factory=list)
    selected_actions: List[str] = field(default_factory=list)
    selected_memory_types: List[MemoryType] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    is_active: bool = True
    created_by: Optional[str] = None
    updated_by_user_id: Optional[str] = None
    deleted_by_user_id: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "templateKey": self.template_key,
            "systemPrompt": self.system_prompt,
            "startingMessage": self.starting_message,
            "selectedTools": self.selected_tools,
            "selectedModels": self.selected_models,
            "selectedActions": self.selected_actions,
            "selectedMemoryTypes": [memory_type.value for memory_type in self.selected_memory_types],
            "tags": self.tags,
            "isActive": self.is_active,
            "createdBy": self.created_by,
            "updatedByUserId": self.updated_by_user_id,
            "deletedByUserId": self.deleted_by_user_id,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.description is not None and self.description != "" and
            self.template_key is not None and self.template_key != "" and
            self.system_prompt is not None and self.system_prompt != "" and
            self.starting_message is not None and self.starting_message != ""
        )

    def key(self) -> str:
        return self._key


@dataclass
class Tool(Node):
    """Tool that can be used by agents"""
    name: str
    vendor_name: str
    description: str
    is_active: bool = True
    created_by_user_id: Optional[str] = None
    updated_by_user_id: Optional[str] = None
    deleted_by_user_id: Optional[str] = None
    org_id: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "vendorName": self.vendor_name,
            "description": self.description,
            "isActive": self.is_active,
            "createdByUserId": self.created_by_user_id,
            "updatedByUserId": self.updated_by_user_id,
            "deletedByUserId": self.deleted_by_user_id,
            "orgId": self.org_id,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.vendor_name is not None and self.vendor_name != "" and
            self.description is not None and self.description != ""
        )

    def key(self) -> str:
        return self._key


@dataclass
class AIModel(Node):
    """AI model configuration"""
    name: str
    description: str
    provider: ModelProvider
    model_type: ModelType
    model_key: str
    org_id: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "provider": self.provider.value,
            "modelType": self.model_type.value,
            "modelKey": self.model_key,
            "orgId": self.org_id,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.description is not None and self.description != "" and
            self.provider is not None and
            self.model_type is not None and
            self.model_key is not None and self.model_key != ""
        )

    def key(self) -> str:
        return self._key


@dataclass
class AppAction(Node):
    """App action that can be performed by agents"""
    name: str
    description: str
    org_id: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "orgId": self.org_id,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.description is not None and self.description != ""
        )

    def key(self) -> str:
        return self._key


@dataclass
class Task(Node):
    """Task that can be assigned to agents"""
    org_id: Optional[str] = None
    name: str = ""
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "orgId": self.org_id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.description is not None and self.description != ""
        )

    def key(self) -> str:
        return self._key


@dataclass
class Workflow(Node):
    """Workflow template that defines the structure and actions for agents"""
    name: str
    description: str
    workflow_type: str  # e.g., "ticketing", "knowledge_search", "customer_support"
    steps: List[Dict[str, Any]] = field(default_factory=list)  # Workflow steps/actions
    triggers: List[str] = field(default_factory=list)  # What triggers this workflow
    conditions: Dict[str, Any] = field(default_factory=dict)  # Conditions for workflow execution
    org_id: Optional[str] = None
    is_public: bool = False  # Can be used by any user/org
    created_by: Optional[str] = None
    updated_by_user_id: Optional[str] = None
    deleted_by_user_id: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    deleted_at_timestamp: Optional[float] = None
    is_deleted: bool = False
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "workflowType": self.workflow_type,
            "steps": self.steps,
            "triggers": self.triggers,
            "conditions": self.conditions,
            "orgId": self.org_id,
            "isPublic": self.is_public,
            "createdBy": self.created_by,
            "updatedByUserId": self.updated_by_user_id,
            "deletedByUserId": self.deleted_by_user_id,
            "createdAtTimestamp": self.created_at_timestamp,
            "updatedAtTimestamp": self.updated_at_timestamp,
            "deletedAtTimestamp": self.deleted_at_timestamp,
            "isDeleted": self.is_deleted,
        }

    def validate(self) -> bool:
        return (
            self.name is not None and self.name != "" and
            self.description is not None and self.description != "" and
            self.workflow_type is not None and self.workflow_type != ""
        )

    def key(self) -> str:
        return self._key


# Edge Models
@dataclass
class AgentUserEdge(Edge):
    """Edge between agent and user with role"""
    _from: str
    _to: str
    role: UserRole
    created_at_timestamp: float
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "role": self.role.value,
            "createdAtTimestamp": self.created_at_timestamp,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.role is not None and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class AgentToolEdge(Edge):
    """Edge between agent and tool"""
    _from: str
    _to: str
    created_at_timestamp: float
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class AgentModelEdge(Edge):
    """Edge between agent and AI model"""
    _from: str
    _to: str
    created_at_timestamp: float
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class AgentWorkflowEdge(Edge):
    """Edge between agent and workflow"""
    _from: str
    _to: str
    created_at_timestamp: float
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class TaskAgentEdge(Edge):
    """Edge between task and agent"""
    _from: str
    _to: str
    created_at_timestamp: float
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class TaskActionEdge(Edge):
    """Edge between task and action with approval configuration"""
    _from: str
    _to: str
    created_at_timestamp: float
    approvers: List[ApprovalConfig] = field(default_factory=list)
    reviewers: List[ApprovalConfig] = field(default_factory=list)
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
            "approvers": [approver.__dict__ for approver in self.approvers],
            "reviewers": [reviewer.__dict__ for reviewer in self.reviewers],
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class WorkflowTaskEdge(Edge):
    """Edge between workflow and task"""
    _from: str
    _to: str
    created_at_timestamp: float
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to


@dataclass
class AgentMemoryEdge(Edge):
    """Edge between agent and memory source"""
    _from: str
    _to: str
    created_at_timestamp: float
    source: str  # CONVERSATION, KNOWLEDGE_BASE, APPS
    _key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "_from": self._from,
            "_to": self._to,
            "createdAtTimestamp": self.created_at_timestamp,
            "source": self.source,
        }

    def validate(self) -> bool:
        return (
            self._from is not None and self._from != "" and
            self._to is not None and self._to != "" and
            self.created_at_timestamp is not None and
            self.source is not None and self.source != ""
        )

    def key(self) -> str:
        return self._key

    @property
    def from_node(self) -> str:
        return self._from

    @property
    def to_node(self) -> str:
        return self._to 