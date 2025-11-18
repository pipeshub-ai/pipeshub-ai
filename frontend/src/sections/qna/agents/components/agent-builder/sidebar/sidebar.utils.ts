/**
 * Sidebar Utility Functions
 * 
 * Pure functions for sidebar logic and data manipulation.
 * Provides filtering, grouping, and data transformation utilities.
 * 
 * @module sidebar.utils
 */

import { Connector } from 'src/sections/accountdetails/connectors/types/types';
import { normalizeDisplayName } from '../../../utils/agent';
import {
  NodeTemplate,
  GroupedConnectorInstances,
  GroupedToolsByConnectorType,
  ToolGroupDragData,
  ToolMetadata,
  SectionType,
} from './sidebar.types';
import { DEFAULT_ICONS } from './sidebar.constants';

/**
 * Filter templates based on search query
 * 
 * Performs case-insensitive search across template label, description, category, and app name.
 * 
 * @param templates - Array of node templates to filter
 * @param searchQuery - Search query string
 * @returns Filtered array of templates matching the query
 * 
 * @example
 * ```ts
 * const results = filterTemplatesBySearch(templates, 'gmail');
 * // Returns all templates with 'gmail' in label, description, category, or appName
 * ```
 */
export const filterTemplatesBySearch = (
  templates: NodeTemplate[],
  searchQuery: string
): NodeTemplate[] => {
  if (!searchQuery.trim()) return templates;

  const query = searchQuery.toLowerCase();
  return templates.filter(
    (template) =>
      template.label.toLowerCase().includes(query) ||
      template.description.toLowerCase().includes(query) ||
      template.category.toLowerCase().includes(query) ||
      (template.defaultConfig?.appName &&
        template.defaultConfig.appName.toLowerCase().includes(query))
  );
};

/**
 * Group tool templates by app name
 * 
 * Organizes individual tool templates into groups based on their app/connector type.
 * Filters out tool groups to only include individual tools.
 * 
 * @param templates - Array of all node templates
 * @returns Object with app names as keys and arrays of tool templates as values
 * 
 * @example
 * ```ts
 * const grouped = groupToolsByAppName(templates);
 * // { "Gmail": [tool1, tool2, ...], "Slack": [tool3, tool4, ...] }
 * ```
 */
export const groupToolsByAppName = (
  templates: NodeTemplate[]
): Record<string, NodeTemplate[]> => {
  const individualTools = templates.filter(
    (t) =>
      t.category === 'tools' && t.type.startsWith('tool-') && !t.type.startsWith('tool-group-')
  );
  const grouped: Record<string, NodeTemplate[]> = {};

  individualTools.forEach((template) => {
    const appName = template.defaultConfig?.appName || 'Other';
    if (!grouped[appName]) {
      grouped[appName] = [];
    }
    grouped[appName].push(template);
  });

  return grouped;
};

/**
 * Group connector instances by connector type
 * 
 * Organizes active connectors by their type, including metadata from the connector registry.
 * Provides icon paths for each connector type group.
 * 
 * @param connectors - Array of active connector instances
 * @param connectorRegistry - Connector registry with metadata
 * @returns Grouped connectors with instances and icons
 * 
 * @example
 * ```ts
 * const grouped = groupConnectorInstances(connectors, registry);
 * // { "Gmail": { instances: [...], icon: "/path/to/icon.svg" } }
 * ```
 */
export const groupConnectorInstances = (
  connectors: Connector[],
): GroupedConnectorInstances => {
  const grouped: GroupedConnectorInstances = {};

  connectors.forEach((connector) => {
    const connectorType = connector.type || connector.appGroup || 'Other';
    const displayName = normalizeDisplayName(connectorType);

    if (!grouped[displayName]) {
      grouped[displayName] = {
        instances: [],
        icon: connector.iconPath || DEFAULT_ICONS.CONNECTOR,
      };
    }
    grouped[displayName].instances.push(connector);
  });

  return grouped;
};

/**
 * Group tools by connector type with active agent instance information
 * 
 * Creates a comprehensive mapping of tools grouped by connector type, including:
 * - All available tools for each connector type
 * - Active agent-enabled connector instances
 * - Configuration and activation status
 * 
 * @param toolsByAppName - Tools already grouped by app name
 * @param activeAgentConnectors - Active connector instances with agent capabilities
 * @param connectorRegistry - Connector registry metadata
 * @returns Comprehensive tool group data by connector type
 * 
 * @example
 * ```ts
 * const grouped = groupToolsByConnectorType(toolsByApp, agentConnectors, registry);
 * // {
 * //   "Gmail": {
 * //     tools: [...],
 * //     activeAgentInstances: [...],
 * //     isConfigured: true,
 * //     isAgentActive: true
 * //   }
 * // }
 * ```
 */
export const groupToolsByConnectorType = (
  toolsByAppName: Record<string, NodeTemplate[]>,
  activeAgentConnectors: Connector[],
  connectorRegistry: any[]
): GroupedToolsByConnectorType => {
  const connectorTypeMap = new Map<string, any>();
  connectorRegistry.forEach((reg) => {
    connectorTypeMap.set(reg.type.toLowerCase(), reg);
  });

  const grouped: GroupedToolsByConnectorType = {};

  // First, collect all tools
  Object.entries(toolsByAppName).forEach(([appName, tools]) => {
    const normalizedAppName = appName.toLowerCase();
    const registryEntry = connectorTypeMap.get(normalizedAppName);
    const displayName = normalizeDisplayName(appName);

    if (!grouped[displayName]) {
      grouped[displayName] = {
        connectorType: appName,
        connectorIcon: registryEntry?.iconPath || `/assets/icons/connectors/${appName.replace(" ", "").toLowerCase()}.svg`,
        tools: [],
        activeAgentInstances: [],
        isConfigured: false,
        isAgentActive: false,
      };
    }

    grouped[displayName].tools = tools;
  });

  // Now add active AGENT instance information
  activeAgentConnectors.forEach((connector) => {
    const connectorType = connector.type || connector.appGroup || '';
    const displayName = normalizeDisplayName(connectorType);

    if (grouped[displayName]) {
      grouped[displayName].activeAgentInstances.push(connector);
      if (connector.isConfigured === true) {
        grouped[displayName].isConfigured = true;
      }
      if (connector.isAgentActive === true) {
        grouped[displayName].isAgentActive = true;
      }
    }
  });

  return grouped;
};

/**
 * Prepare drag data for transfer
 * 
 * Prepares drag-and-drop data transfer object with all necessary metadata.
 * Handles section-specific data requirements.
 * 
 * @param template - Node template being dragged
 * @param sectionType - Section context for additional data
 * @param additionalData - Optional additional metadata
 * @returns Object with all drag data as strings
 * 
 * @example
 * ```ts
 * const dragData = prepareDragData(template, 'tools', { connectorId: '123' });
 * // { 'application/reactflow': 'tool-gmail-send', toolAppName: 'Gmail', ... }
 * ```
 */
export const prepareDragData = (
  template: NodeTemplate,
  sectionType?: SectionType,
  additionalData?: Record<string, any>
): Record<string, string> => {
  const data: Record<string, string> = {
    'application/reactflow': template.type,
  };

  if (sectionType === 'tools' && template.defaultConfig?.appName) {
    data.toolAppName = template.defaultConfig.appName;
  }

  if (sectionType === 'connectors' && template.defaultConfig?.id) {
    data.connectorId = template.defaultConfig.id;
    data.connectorType = template.defaultConfig.type || '';
  }

  // Merge additional data
  if (additionalData) {
    Object.entries(additionalData).forEach(([key, value]) => {
      data[key] = String(value);
    });
  }

  return data;
};

/**
 * Check if connector needs configuration
 * 
 * Determines if a connector requires setup before it can be used.
 * Returns true if:
 * - No active instances exist
 * - Not properly configured
 * - Agent capabilities not enabled
 * 
 * @param activeInstances - Array of active connector instances
 * @param isConfigured - Whether any instance is configured
 * @param isAgentActive - Whether any instance has agent capabilities
 * @returns True if configuration is needed
 * 
 * @example
 * ```ts
 * const needsSetup = connectorNeedsConfiguration([], false, false);
 * // true - no instances and not configured
 * ```
 */
export const connectorNeedsConfiguration = (
  activeInstances: Connector[],
  isConfigured: boolean,
  isAgentActive: boolean
): boolean =>
  activeInstances.length === 0 || !isConfigured || !isAgentActive;

/**
 * Create tool group drag data
 * 
 * Generates comprehensive drag data for tool group nodes.
 * Includes all tools, connector metadata, and configuration status.
 * 
 * @param tools - Array of tool templates in the group
 * @param instance - Connector instance
 * @param connectorType - Type of connector
 * @param connectorIcon - Icon path for the connector
 * @param isConfigured - Configuration status
 * @param isAgentActive - Agent capability status
 * @returns Drag data object with all metadata
 * 
 * @example
 * ```ts
 * const dragData = createToolGroupDragData(
 *   gmailTools, connectorInstance, 'gmail', '/icon.svg', true, true
 * );
 * // Returns comprehensive drag metadata for the tool group
 * ```
 */
export const createToolGroupDragData = (
  tools: NodeTemplate[],
  instance: Connector,
  connectorType: string,
  connectorIcon: string,
  isConfigured: boolean,
  isAgentActive: boolean
): ToolGroupDragData => ({
  connectorId: instance._key || (instance as any).id || '',
  connectorType: instance.type || connectorType,
  connectorName: instance.name,
  connectorIconPath: connectorIcon,
  scope: instance.scope || 'personal',
  toolCount: String(tools.length),
  isConfigured: String(isConfigured),
  isAgentActive: String(isAgentActive),
  allTools: JSON.stringify(
    tools.map((t) => ({
      toolId: t.defaultConfig?.toolId,
      fullName: t.defaultConfig?.fullName,
      toolName: t.label,
      appName: t.defaultConfig?.appName,
    }))
  ),
});

