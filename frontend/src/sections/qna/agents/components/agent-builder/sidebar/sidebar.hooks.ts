/**
 * Sidebar Custom Hooks
 * 
 * Reusable React hooks for sidebar functionality.
 * Encapsulates common logic for drag-and-drop, state management, and side effects.
 * 
 * @module sidebar.hooks
 */

import React, { useCallback, useState, DragEvent } from 'react';
import { Connector } from 'src/sections/accountdetails/connectors/types/types';
import { NodeTemplate, SectionType, ConnectorStatus } from './sidebar.types';
import { DRAG_DROP } from './sidebar.constants';

/**
 * Hook for handling node template drag operations
 * 
 * @param template - Node template to drag
 * @param sectionType - Section context for additional data
 * @param connectorStatus - Optional connector validation status
 * @param connectorInstance - Optional connector instance data
 * @param connectorIconPath - Optional connector icon path
 * @returns Drag event handler
 * 
 * @example
 * ```tsx
 * const handleDragStart = useDragHandler(template, 'tools', status, instance, iconPath);
 * <div draggable onDragStart={handleDragStart}>...</div>
 * ```
 */
export const useDragHandler = (
  template: NodeTemplate,
  sectionType?: SectionType,
  connectorStatus?: ConnectorStatus,
  connectorInstance?: Connector,
  connectorIconPath?: string
) =>
  useCallback(
    (event: DragEvent) => {
      // Set primary node type
      event.dataTransfer.setData(DRAG_DROP.DATA_TYPE, template.type);

      // Add connector-specific data
      if (sectionType === 'connectors' && template.defaultConfig?.id) {
        event.dataTransfer.setData('connectorId', template.defaultConfig.id);
        event.dataTransfer.setData('connectorType', template.defaultConfig.type || '');
      }

      // Add tool-specific data
      if (sectionType === 'tools') {
        event.dataTransfer.setData('toolAppName', template.defaultConfig?.appName || '');
        
        if (connectorStatus) {
          event.dataTransfer.setData('isConfigured', String(connectorStatus.isConfigured));
          event.dataTransfer.setData('isAgentActive', String(connectorStatus.isAgentActive));
        }
        
        if (connectorInstance) {
          event.dataTransfer.setData(
            'connectorId',
            connectorInstance._key || (connectorInstance as any).id || ''
          );
          event.dataTransfer.setData('connectorType', connectorInstance.type || '');
          event.dataTransfer.setData('connectorName', connectorInstance.name || '');
        }
        
        if (connectorIconPath) {
          event.dataTransfer.setData('connectorIconPath', connectorIconPath);
        }
      }
    },
    [template, sectionType, connectorStatus, connectorInstance, connectorIconPath]
  );

/**
 * Hook for handling category/group drag operations
 * 
 * @param dragType - Type identifier for the draggable group
 * @param dragData - Additional metadata to transfer
 * @returns Drag event handler or undefined if not draggable
 * 
 * @example
 * ```tsx
 * const handleDragStart = useCategoryDragHandler('tool-group-gmail', { toolCount: '5' });
 * <div draggable={!!handleDragStart} onDragStart={handleDragStart}>...</div>
 * ```
 */
export const useCategoryDragHandler = (
  dragType?: string,
  dragData?: Record<string, any>
) =>
  useCallback(
    (event: DragEvent) => {
      if (!dragType) return;

      event.dataTransfer.setData(DRAG_DROP.DATA_TYPE, dragType);

      if (dragData) {
        Object.entries(dragData).forEach(([key, value]) => {
          event.dataTransfer.setData(key, String(value));
        });
      }
    },
    [dragType, dragData]
  );

/**
 * Hook for handling app knowledge drag operations
 * 
 * @param appName - Name of the app
 * @param connectorId - Connector instance ID
 * @param connectorType - Connector type
 * @returns Drag event handler
 * 
 * @example
 * ```tsx
 * const handleDragStart = useAppKnowledgeDragHandler('gmail', 'conn_123', 'gmail');
 * <div draggable onDragStart={handleDragStart}>...</div>
 * ```
 */
export const useAppKnowledgeDragHandler = (
  appName: string,
  connectorId: string,
  connectorType: string
) =>
  useCallback(
    (event: DragEvent) => {
      const appDragType = `app-${appName.toLowerCase().replace(/\s+/g, '-')}`;
      event.dataTransfer.setData(DRAG_DROP.DATA_TYPE, appDragType);
      event.dataTransfer.setData('connectorId', connectorId);
      event.dataTransfer.setData('connectorType', connectorType);
    },
    [appName, connectorId, connectorType]
  );

/**
 * Hook for managing toggle state
 * 
 * @param initialState - Initial expanded state
 * @returns [state, toggle function, set function]
 * 
 * @example
 * ```tsx
 * const [expanded, toggleExpanded, setExpanded] = useToggleState({ tools: true });
 * <button onClick={() => toggleExpanded('tools')}>Toggle</button>
 * ```
 */
export const useToggleState = <T extends Record<string, boolean>>(initialState: T) => {
  const [state, setState] = useState<T>(initialState);

  const toggle = useCallback((key: string) => {
    setState((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, []);

  return [state, toggle, setState] as const;
};

