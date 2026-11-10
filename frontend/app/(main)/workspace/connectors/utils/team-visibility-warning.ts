import type { Connector } from '../types';

/**
 * Team connectors whose source access rules this edition does not apply, so
 * everything they sync is searchable by the whole organization. `hasPersonal`
 * is false where the connector only registers the team scope.
 */
export const TEAM_CONNECTOR_VISIBILITY_WARNINGS: Readonly<Record<string, { hasPersonal: boolean }>> = {
  S3: { hasPersonal: true },
  'Azure Blob': { hasPersonal: true },
  'Azure Files': { hasPersonal: true },
  GCS: { hasPersonal: true },
  MinIO: { hasPersonal: true },
  PostgreSQL: { hasPersonal: true },
  MariaDB: { hasPersonal: true },
};

export function getTeamVisibilityWarning(
  connector: Connector | null | undefined,
  warnings: Readonly<Record<string, { hasPersonal: boolean }>>,
): { hasPersonal: boolean } | null {
  if (!connector?.type) return null;
  return warnings[connector.type] ?? null;
}
