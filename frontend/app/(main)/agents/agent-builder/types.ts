import type { Edge, Node } from '@xyflow/react';
import type { AgentDetail } from '../types';
import type { WebSearchProviderType } from '../../workspace/web-search/types';

export interface AgentWebSearchAttachment {
  /** Provider identifier, e.g. 'duckduckgo' | 'serper' | 'tavily' | 'exa'. */
  provider: WebSearchProviderType;
  /** Stored provider document key; empty for DuckDuckGo when not explicitly configured. */
  providerKey: string;
  /** Human-readable label — cached for display (optional). */
  providerLabel?: string;
  /** Provider logo path used by the flow node header. */
  iconPath?: string;
}

/** Flow node payload (React Flow `data` + metadata). */
export interface FlowNodeData extends Record<string, unknown> {
  id: string;
  type: string;
  label: string;
  description?: string;
  /** Material icon name, or URL/path for connector/toolset icons */
  icon?: string;
  config: Record<string, unknown>;
  inputs?: string[];
  outputs?: string[];
  isConfigured?: boolean;
  category?: string;
}

export type FlowNode = Node<FlowNodeData>;

export interface NodeTemplate {
  type: string;
  label: string;
  description: string;
  icon: string;
  defaultConfig: Record<string, unknown>;
  inputs?: string[];
  outputs?: string[];
  category: 'agent' | 'inputs' | 'outputs' | 'llm' | 'knowledge' | 'tools' | 'connectors';
}

export interface ToolRef {
  name: string;
  fullName: string;
  description?: string;
}

export interface ToolsetReference {
  id: string;
  instanceId?: string;
  /** Human label for this configured instance (shown in builder + API when supported). */
  instanceName?: string;
  name: string;
  displayName: string;
  type: string;
  tools?: ToolRef[];
}

export interface KnowledgeReference {
  id: string;
  connectorId: string;
  filters?: Record<string, unknown>;
}

export interface AgentFormPayload {
  name: string;
  description: string;
  startMessage: string;
  systemPrompt: string;
  instructions?: string;
  models: { provider: string; modelName: string; isReasoning: boolean; modelKey: string }[];
  tags: string[];
  shareWithOrg?: boolean;
  isServiceAccount?: boolean;
  knowledge?: KnowledgeReference[];
  toolsets?: ToolsetReference[];
  webSearch?: AgentWebSearchAttachment | null;
  flow?: { nodes: FlowNode[]; edges: Edge[] };
}

/** Agent shape used when rebuilding the graph (extends API detail with optional legacy fields). */
export type AgentReconstructionSource = AgentDetail & {
  tools?: string[];
  knowledge?: unknown[];
};
