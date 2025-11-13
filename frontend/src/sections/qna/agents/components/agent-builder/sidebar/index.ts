/**
 * Sidebar Components Export Index
 * 
 * Centralized export for all sidebar-related components, types, utilities, and constants.
 * 
 * @module sidebar/index
 */

// Components
export * from './SidebarHeader';
export * from './SidebarNodeItem';
export * from './SidebarCategory';
export * from './SidebarToolsSection';
export * from './SidebarKnowledgeSection';

// Types
export * from './sidebar.types';

// Utilities - explicit exports to avoid duplicate type exports
export {
  filterTemplatesBySearch,
  groupToolsByAppName,
  groupConnectorInstances,
  groupToolsByConnectorType,
  prepareDragData,
  connectorNeedsConfiguration,
  createToolGroupDragData,
} from './sidebar.utils';

export * from './sidebar.hooks';
export * from './sidebar.styles';

// Icons and Constants
export * from './sidebar.icons';
export * from './sidebar.constants';

