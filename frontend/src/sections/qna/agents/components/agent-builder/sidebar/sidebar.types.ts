/**
 * Sidebar Type Definitions
 * 
 * Comprehensive type definitions for the flow builder sidebar components.
 * This file provides type safety and documentation for all sidebar-related types.
 * 
 * @module sidebar.types
 */

import { ReactNode } from 'react';
import { IconifyIcon } from '@iconify/react';
import { Connector } from 'src/sections/accountdetails/connectors/types/types';

/**
 * Node category types for classification
 */
export type NodeCategory = 'inputs' | 'llm' | 'tools' | 'knowledge' | 'connectors' | 'outputs' | 'agent' | 'vector';

/**
 * Section type for rendering context
 */
export type SectionType = 'tools' | 'apps' | 'kbs' | 'connectors';

/**
 * Node template configuration
 * Defines the structure and metadata for a draggable node template
 */
export interface NodeTemplate {
  /** Unique identifier for the node type */
  type: string;
  /** Display label for the node */
  label: string;
  /** Description of the node's functionality */
  description: string;
  /** Icon to display (can be Iconify icon or path) */
  icon: IconifyIcon | string;
  /** Default configuration values */
  defaultConfig: NodeDefaultConfig;
  /** Input handle identifiers */
  inputs: string[];
  /** Output handle identifiers */
  outputs: string[];
  /** Category classification */
  category: NodeCategory;
}

/**
 * Node default configuration
 * Contains default values and metadata for node instances
 */
export interface NodeDefaultConfig {
  /** Unique identifier */
  id?: string;
  /** Display name */
  name?: string;
  /** Connector/app name */
  appName?: string;
  /** App display name */
  appDisplayName?: string;
  /** Knowledge base name */
  kbName?: string;
  /** Connector type */
  type?: string;
  /** Icon path for dynamic icons */
  iconPath?: string;
  /** Tool identifier */
  toolId?: string;
  /** Full tool name */
  fullName?: string;
  /** Additional tools in a group */
  tools?: any[];
  /** Additional apps in a group */
  apps?: any[];
  /** Knowledge bases in a group */
  knowledgeBases?: any[];
  /** AI model provider */
  provider?: string;
  /** AI model name */
  modelName?: string;
  /** Whether connector is configured */
  isConfigured?: boolean;
  /** Whether connector has agent capabilities active */
  isAgentActive?: boolean;
  [key: string]: any;
}

/**
 * Connector status information
 */
export interface ConnectorStatus {
  /** Whether the connector is properly configured */
  isConfigured: boolean;
  /** Whether the connector has agent capabilities enabled */
  isAgentActive: boolean;
}

/**
 * Grouped connector instances
 */
export interface GroupedConnectorInstances {
  [displayName: string]: {
    /** Array of connector instances of this type */
    instances: Connector[];
    /** Icon path for this connector type */
    icon: string;
  };
}

/**
 * Tool group data structure
 */
export interface ToolGroupData {
  /** Connector type identifier */
  connectorType: string;
  /** Icon path for the connector */
  connectorIcon: string;
  /** Array of tool templates */
  tools: NodeTemplate[];
  /** Active agent connector instances */
  activeAgentInstances: Connector[];
  /** Whether any instance is configured */
  isConfigured: boolean;
  /** Whether any instance has agent capabilities */
  isAgentActive: boolean;
}

/**
 * Grouped tools by connector type
 */
export interface GroupedToolsByConnectorType {
  [displayName: string]: ToolGroupData;
}

/**
 * Drag data for drag-and-drop operations
 */
export interface DragData {
  /** Primary drag type identifier */
  'application/reactflow': string;
  /** Additional metadata */
  [key: string]: string;
}

/**
 * Expanded state tracking
 */
export interface ExpandedState {
  [key: string]: boolean;
}

/**
 * Category configuration for sidebar sections
 */
export interface CategoryConfig {
  /** Display name for the category */
  name: string;
  /** Icon to display */
  icon: IconifyIcon | string;
  /** Node categories to include */
  categories: NodeCategory[];
}

/**
 * Props for SidebarHeader component
 */
export interface SidebarHeaderProps {
  /** Current search query */
  searchQuery: string;
  /** Callback when search query changes */
  onSearchChange: (query: string) => void;
}

/**
 * Props for SidebarNodeItem component
 */
export interface SidebarNodeItemProps {
  /** Node template to render */
  template: NodeTemplate;
  /** Whether this is a nested sub-item */
  isSubItem?: boolean;
  /** Section type for context-specific rendering */
  sectionType?: SectionType;
  /** Connector status for validation */
  connectorStatus?: ConnectorStatus;
  /** Connector instance data */
  connectorInstance?: Connector;
  /** Icon path for the connector */
  connectorIconPath?: string;
  /** Icon to display */
  itemIcon?: IconifyIcon | string;
  /** Whether the icon is a dynamic image path */
  isDynamicIcon?: boolean;
  /** Whether the item is draggable (default: true) */
  isDraggable?: boolean;
}

/**
 * Props for SidebarCategory component
 */
export interface SidebarCategoryProps {
  /** Display label for the group */
  groupLabel: string;
  /** Icon to display */
  groupIcon: IconifyIcon | string;
  /** Number of items in this category */
  itemCount: number;
  /** Whether the category is expanded */
  isExpanded: boolean;
  /** Callback when category is toggled */
  onToggle: () => void;
  /** Optional drag type if category itself is draggable */
  dragType?: string;
  /** Border color for visual distinction */
  borderColor?: string;
  /** Whether to show configuration icon */
  showConfigureIcon?: boolean;
  /** Whether to show authenticated indicator (green checkmark) */
  showAuthenticatedIndicator?: boolean;
  /** Callback when configure is clicked */
  onConfigureClick?: () => void;
  /** Additional drag data */
  dragData?: Record<string, any>;
  /** Child content */
  children?: ReactNode;
}

/**
 * Props for SidebarToolsSection component
 */
export interface SidebarToolsSectionProps {
  /** Grouped tools by connector type */
  toolsGroupedByConnectorType: GroupedToolsByConnectorType;
  /** Expanded state for apps/instances */
  expandedApps: ExpandedState;
  /** Callback when app/instance is toggled */
  onAppToggle: (key: string) => void;
  /** Whether the user is a business user */
  isBusiness: boolean;
}

/**
 * Props for SidebarKnowledgeSection component
 */
export interface SidebarKnowledgeSectionProps {
  /** Grouped connector instances by type */
  groupedConnectorInstances: GroupedConnectorInstances;
  /** Knowledge base group node template */
  kbGroupNode?: NodeTemplate;
  /** Individual knowledge base templates */
  individualKBs: NodeTemplate[];
  /** Expanded state for sections */
  expandedApps: ExpandedState;
  /** Callback when section is toggled */
  onAppToggle: (key: string) => void;
}

/**
 * Props for main FlowBuilderSidebar component
 */
export interface FlowBuilderSidebarProps {
  /** Whether sidebar is open */
  sidebarOpen: boolean;
  /** Array of all available node templates */
  nodeTemplates: NodeTemplate[];
  /** Whether templates are loading */
  loading: boolean;
  /** Width of the sidebar in pixels */
  sidebarWidth: number;
  /** Active agent-enabled connectors */
  activeAgentConnectors: Connector[];
  /** All active connectors */
  configuredConnectors: Connector[];
  /** Connector registry metadata */
  connectorRegistry: any[];
}

/**
 * Tool group drag metadata
 */
export interface ToolGroupDragData {
  /** Connector instance ID */
  connectorId: string;
  /** Connector type */
  connectorType: string;
  /** Connector instance name */
  connectorName: string;
  /** Connector icon path */
  connectorIconPath: string;
  /** Connector scope (personal, organization, etc.) */
  scope: string;
  /** Number of tools in group */
  toolCount: string;
  /** Configuration status */
  isConfigured: string;
  /** Agent active status */
  isAgentActive: string;
  /** JSON string of all tools */
  allTools: string;
}

/**
 * Tool metadata in drag data
 */
export interface ToolMetadata {
  /** Tool identifier */
  toolId?: string;
  /** Full tool name */
  fullName?: string;
  /** Display name */
  toolName: string;
  /** Parent app name */
  appName?: string;
}

