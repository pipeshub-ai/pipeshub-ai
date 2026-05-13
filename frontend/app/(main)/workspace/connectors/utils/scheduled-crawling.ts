import { isLocalFsConnectorType } from './local-fs-helpers';

interface ScheduledCrawlingInstanceLike {
  _key?: string;
  type?: string;
  isActive?: boolean;
}

interface ScheduledCrawlingConfigLike {
  config?: {
    sync?: {
      selectedStrategy?: string | null;
    } | null;
  } | null;
}

export function isScheduledSyncConfig(
  config: ScheduledCrawlingConfigLike | null | undefined,
): boolean {
  return config?.config?.sync?.selectedStrategy === 'SCHEDULED';
}

export function buildScheduledCrawlingRemovePath(
  connectorType: string,
  connectorId: string,
): string {
  const connectorSegment = encodeURIComponent(String(connectorType).trim().toLowerCase());
  const connectorIdSegment = encodeURIComponent(connectorId);
  return `/api/v1/crawlingManager/${connectorSegment}/${connectorIdSegment}/remove`;
}

export function shouldRemoveScheduledCrawlingJobOnSyncDisable(
  instance: ScheduledCrawlingInstanceLike | null | undefined,
  config: ScheduledCrawlingConfigLike | null | undefined,
): boolean {
  return Boolean(
    instance?._key &&
      instance.isActive === true &&
      typeof instance.type === 'string' &&
      isLocalFsConnectorType(instance.type) &&
      isScheduledSyncConfig(config),
  );
}
