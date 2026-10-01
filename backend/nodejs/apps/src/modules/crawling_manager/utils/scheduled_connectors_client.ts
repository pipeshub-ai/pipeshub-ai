import { fetchConfigJwtGenerator } from '../../../libs/utils/createJwt';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { executeConnectorCommand } from '../../tokens_manager/utils/connector.utils';
import { AppConfig } from '../../tokens_manager/config/config';
import { ConnectorSyncBlock } from './schedule_config_mapper';

export interface ScheduledConnectorRecord {
  connectorId: string;
  type: string;
  orgId: string;
  ownerUserId?: string | null;
  isActive: boolean;
  sync: ConnectorSyncBlock;
}

export interface ScheduledConnectorsPage {
  items: ScheduledConnectorRecord[];
  hasMore: boolean;
  /** A config read failed, so `items` is not the full page. */
  partial: boolean;
}

export interface ScheduledConnectorListing {
  items: ScheduledConnectorRecord[];
  /** True when any page omitted connectors because a config read failed. */
  partial: boolean;
}

/** Page size sent to the Python all-scheduled endpoint. */
export const SCHEDULED_CONNECTORS_PAGE_SIZE = 50;

/** Safety cap — prevents an infinite loop if the server always returns hasMore:true. */
export const SCHEDULED_CONNECTORS_MAX_PAGES = 1_000;

/**
 * Fetch one page (1-based) of active connectors configured for SCHEDULED sync,
 * across every org. Throws on any HTTP or service error.
 */
export const fetchScheduledConnectorsPage = async (
  appConfig: AppConfig,
  page: number,
): Promise<ScheduledConnectorsPage> => {
  const { connectorBackend, scopedJwtSecret } = appConfig;
  if (!connectorBackend) {
    throw new Error('connectorBackend URL is not configured');
  }
  if (!scopedJwtSecret) {
    throw new Error('scopedJwtSecret is not configured');
  }

  let token: string;
  try {
    token = fetchConfigJwtGenerator('system', 'system', scopedJwtSecret);
  } catch (error) {
    throw new Error(
      `Failed to mint scoped JWT: ${error instanceof Error ? error.message : 'Unknown'}`,
    );
  }

  const url =
    `${connectorBackend}/api/v1/connectors/internal/all-scheduled` +
    `?page=${page}&limit=${SCHEDULED_CONNECTORS_PAGE_SIZE}`;

  const resp = await executeConnectorCommand(url, HttpMethod.GET, {
    Authorization: `Bearer ${token}`,
  });
  const status = resp.statusCode;

  if (status < 200 || status >= 300) {
    throw new Error(`Connector service returned non-2xx status ${String(status)}`);
  }

  const data = resp.data as {
    items?: ScheduledConnectorRecord[];
    hasMore?: boolean;
    partial?: boolean;
  } | null;

  return {
    items: data?.items ?? [],
    hasMore: data?.hasMore ?? false,
    partial: data?.partial === true,
  };
};

/**
 * Fetch every page. Throws on the first failed page or when the page cap is
 * hit. A page that loaded but omitted connectors whose config could not be
 * read is returned with `partial` set, so callers can skip destructive work.
 */
export const fetchAllScheduledConnectors = async (
  appConfig: AppConfig,
): Promise<ScheduledConnectorListing> => {
  const items: ScheduledConnectorRecord[] = [];
  let partial = false;
  for (let page = 1; page <= SCHEDULED_CONNECTORS_MAX_PAGES; page++) {
    const pageResult = await fetchScheduledConnectorsPage(appConfig, page);
    items.push(...pageResult.items);
    partial = partial || pageResult.partial;
    if (!pageResult.hasMore) {
      return { items, partial };
    }
  }
  throw new Error(
    `Scheduled-connector listing exceeded ${SCHEDULED_CONNECTORS_MAX_PAGES} pages`,
  );
};
