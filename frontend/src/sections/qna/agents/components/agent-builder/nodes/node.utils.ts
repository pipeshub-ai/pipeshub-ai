// Node utility functions
// Pure utility functions for node logic and validation

import { NodeData } from '../../../types/agent';
import {
  NODE_TYPES_WITHOUT_INPUT_HANDLES,
  NODE_TYPES_WITHOUT_OUTPUT_HANDLES,
  SPECIAL_NODE_TYPES,
} from './node.constants';

/**
 * Check if a node type should display input handles
 */
export const shouldShowInputHandles = (nodeType: string): boolean => {
  // Check all node types that shouldn't have input handles
  const checkers = Object.values(NODE_TYPES_WITHOUT_INPUT_HANDLES);
  return !checkers.some((checker) => checker(nodeType));
};

/**
 * Check if a node type should display output handles
 */
export const shouldShowOutputHandles = (nodeType: string): boolean => {
  // Check all node types that shouldn't have output handles
  const checkers = Object.values(NODE_TYPES_WITHOUT_OUTPUT_HANDLES);
  return !checkers.some((checker) => checker(nodeType));
};

/**
 * Check if a node is a tool node (individual or group)
 */
export const isToolNode = (nodeType: string): boolean =>
  nodeType.startsWith('tool-');

/**
 * Check if a node is an individual tool (not a group)
 */
export const isIndividualToolNode = (nodeType: string): boolean =>
  nodeType.startsWith('tool-') && !nodeType.startsWith('tool-group-');

/**
 * Check if a node is a tool group
 */
export const isToolGroupNode = (nodeType: string): boolean =>
  nodeType.startsWith('tool-group-');

/**
 * Check if a node is a knowledge app
 */
export const isKnowledgeAppNode = (nodeType: string): boolean =>
  nodeType.startsWith('app-');

/**
 * Check if a node is a knowledge base
 */
export const isKnowledgeBaseNode = (nodeType: string): boolean =>
  nodeType.startsWith('kb-');

/**
 * Check if a node is an LLM/Model node
 */
export const isLLMNode = (nodeType: string): boolean =>
  nodeType.startsWith('llm-');

/**
 * Check if a node is the agent core
 */
export const isAgentCoreNode = (nodeType: string): boolean =>
  nodeType === SPECIAL_NODE_TYPES.AGENT_CORE;

/**
 * Check if a node is a chat response node
 */
export const isChatResponseNode = (nodeType: string): boolean =>
  nodeType === SPECIAL_NODE_TYPES.CHAT_RESPONSE;

/**
 * Check if a node is a user input node
 */
export const isUserInputNode = (nodeType: string): boolean =>
  nodeType === SPECIAL_NODE_TYPES.USER_INPUT;

/**
 * Get node category from node type
 */
export const getNodeCategory = (nodeType: string): string => {
  if (isToolNode(nodeType)) return 'tools';
  if (isKnowledgeAppNode(nodeType)) return 'knowledge-apps';
  if (isKnowledgeBaseNode(nodeType)) return 'knowledge-bases';
  if (isLLMNode(nodeType)) return 'models';
  if (isAgentCoreNode(nodeType)) return 'agent';
  return 'other';
};

/**
 * Extract connector icon path from node data
 */
export const getConnectorIconPath = (data: NodeData): string | null =>
  data.config?.iconPath || null;

/**
 * Get default fallback icon based on node type
 */
export const getDefaultIconForNodeType = (nodeType: string): string => {
  if (isToolNode(nodeType)) return '/assets/icons/connectors/default.svg';
  if (isKnowledgeAppNode(nodeType)) return '/assets/icons/connectors/collections-gray.svg';
  if (isLLMNode(nodeType)) return '/assets/icons/models/default.svg';
  return '/assets/icons/default.svg';
};

/**
 * Check if a node has connector icon (for dual icon display)
 */
export const hasConnectorIcon = (data: NodeData): boolean =>
  isIndividualToolNode(data.type) && !!data.config?.iconPath;

/**
 * Validate node configuration
 */
export const isNodeConfigured = (data: NodeData): boolean => {
  // Auto-configured node types
  if (
    data.type.startsWith('app-') ||
    data.type.startsWith('tool-group-') ||
    data.type.startsWith('kb-')
  ) {
    return true;
  }

  // Check if node has required configuration
  return !!data.config && Object.keys(data.config).length > 0;
};

/**
 * Get display label for a node
 */
export const getNodeDisplayLabel = (data: NodeData): string => {
  if (data.config?.name) return data.config.name;
  if (data.config?.label) return data.config.label;
  return data.label || 'Unnamed Node';
};

/**
 * Format tool count display
 */
export const formatToolCount = (count: number): string => {
  if (count === 0) return 'No tools';
  if (count === 1) return '1 tool';
  return `${count} tools`;
};

/**
 * Calculate handle position based on index and total count
 */
export const calculateHandlePosition = (
  index: number,
  total: number,
  baseOffset: number,
  increment: number
): string => {
  if (total === 1) return '50%';
  
  const spacing = increment;
  const startPosition = baseOffset - ((total - 1) * spacing) / 2;
  return `${startPosition + index * spacing}%`;
};

/**
 * Generate unique handle ID
 */
export const generateHandleId = (
  nodeId: string,
  type: 'input' | 'output',
  handleName: string,
  index?: number
): string => {
  const suffix = index !== undefined ? `-${index}` : '';
  return `${nodeId}-${type}-${handleName}${suffix}`;
};

/**
 * Check if node can be deleted
 * Agent core cannot be deleted
 */
export const canDeleteNode = (nodeType: string): boolean =>
  nodeType !== SPECIAL_NODE_TYPES.AGENT_CORE;

/**
 * Get node width based on type
 */
export const getNodeWidth = (nodeType: string): number => {
  if (isAgentCoreNode(nodeType)) return 420;
  if (isChatResponseNode(nodeType)) return 280;
  if (isLLMNode(nodeType)) return 280;
  if (isToolNode(nodeType)) return 280;
  if (isKnowledgeAppNode(nodeType) || isKnowledgeBaseNode(nodeType)) return 280;
  return 280;
};

