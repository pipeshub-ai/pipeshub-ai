/**
 * Type guard utilities for Knowledge Base
 */

import type { ConnectorType } from '@/app/components/ui/ConnectorIcon';
import { CONNECTOR_ICONS } from '@/app/components/ui/ConnectorIcon';

/**
 * Type guard to check if a string is a valid ConnectorType
 * @param value - String to check
 * @returns true if value is a valid ConnectorType
 *
 * @example
 * if (isConnectorType(item.sourceType)) {
 *   // TypeScript knows sourceType is ConnectorType here
 *   return <ConnectorIcon type={item.sourceType} />;
 * }
 */
export function isConnectorType(value: string): value is ConnectorType {
  return Object.prototype.hasOwnProperty.call(CONNECTOR_ICONS, value);
}
