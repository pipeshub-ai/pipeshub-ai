// src/sections/qna/agents/utils/flow-builder-types.ts
import type { Agent } from 'src/types/agent';

// Core data interfaces
export interface NodeData extends Record<string, unknown> {
  id: string;
  type: string;
  label: string;
  config: Record<string, any>;
  description?: string;
  icon?: any;
  inputs?: string[];
  outputs?: string[];
  isConfigured?: boolean;
}

export interface NodeTemplate {
  type: string;
  label: string;
  description: string;
  icon: any;
  defaultConfig: Record<string, any>;
  inputs: string[];
  outputs: string[];
  category: 'inputs' | 'llm' | 'tools' | 'knowledge' | 'outputs' | 'agent';
}

// Component props interfaces
export interface AgentBuilderProps {
    editingAgent?: Agent  | null;
  onSuccess: (agent: Agent) => void;
  onClose: () => void;
}

export interface AgentBuilderHeaderProps {
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  agentName: string;
  setAgentName: (name: string) => void;
  saving: boolean;
  onSave: () => void;
  onClose: () => void;
  editingAgent?: Agent  | null;
  originalAgentName?: string; // The original name from API
  templateDialogOpen: boolean;
  setTemplateDialogOpen: (open: boolean) => void;
  templatesLoading: boolean;
  agentId?: string;
}

export interface AgentBuilderCanvasWrapperProps {
  sidebarOpen: boolean;
  sidebarWidth: number;
  nodeTemplates: NodeTemplate[];
  loading: boolean;
  nodes: any[];
  edges: any[];
  onNodesChange: (changes: any) => void;
  onEdgesChange: (changes: any) => void;
  onConnect: (connection: any) => void;
  onNodeClick: (event: React.MouseEvent, node: any) => void;
  onEdgeClick: (event: React.MouseEvent, edge: any) => void;
  onDrop: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  setNodes: React.Dispatch<React.SetStateAction<any[]>>;
  onNodeEdit?: (nodeId: string, data: any) => void;
  onNodeDelete?: (nodeId: string) => void;
}

export interface AgentBuilderNotificationPanelProps {
  error: string | null;
  success: string | null;
  onErrorClose: () => void;
  onSuccessClose: () => void;
}

export interface AgentBuilderDialogManagerProps {
  selectedNode: any;
  configDialogOpen: boolean;
  onConfigDialogClose: () => void;
  onNodeConfig: (nodeId: string, config: Record<string, any>) => void;
  onDeleteNode: (nodeId: string) => void;
  deleteDialogOpen: boolean;
  onDeleteDialogClose: () => void;
  nodeToDelete: string | null;
  onConfirmDelete: () => Promise<void>;
  edgeDeleteDialogOpen: boolean;
  onEdgeDeleteDialogClose: () => void;
  edgeToDelete: any;
  onConfirmEdgeDelete: () => Promise<void>;
  deleting: boolean;
  nodes: any[];
}

// Hook return types
export interface UseAgentBuilderDataReturn {
  availableTools: any[];
  availableModels: any[];
  availableKnowledgeBases: any[];
  loading: boolean;
  loadedAgent: Agent | null;
  error: string | null;
  setError: (error: string | null) => void;
}

export interface UseAgentBuilderStateReturn {
  selectedNode: any;
  setSelectedNode: (node: any) => void;
  configDialogOpen: boolean;
  setConfigDialogOpen: (open: boolean) => void;
  deleteDialogOpen: boolean;
  setDeleteDialogOpen: (open: boolean) => void;
  nodeToDelete: string | null;
  setNodeToDelete: (nodeId: string | null) => void;
  edgeDeleteDialogOpen: boolean;
  setEdgeDeleteDialogOpen: (open: boolean) => void;
  edgeToDelete: any;
  setEdgeToDelete: (edge: any) => void;
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  agentName: string;
  setAgentName: (name: string) => void;
  saving: boolean;
  setSaving: (saving: boolean) => void;
  deleting: boolean;
  setDeleting: (deleting: boolean) => void;
  success: string | null;
  setSuccess: (success: string | null) => void;
}

export interface UseAgentBuilderNodeTemplatesReturn {
  nodeTemplates: NodeTemplate[];
}

export interface UseAgentBuilderReconstructionReturn {
  reconstructFlowFromAgent: (
    agent: Agent,
    models: any[],
    tools: any[],
    knowledgeBases: any[]
  ) => { nodes: any[]; edges: any[] };
}
