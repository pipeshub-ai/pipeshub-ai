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
  const status = resp?.statusCode;

  if (!status || status < 200 || status >= 300) {
    throw new Error(
      `Connector service returned non-2xx status ${status ?? '(no response)'}`,
    );
  }

  const data = resp.data as {
    items?: ScheduledConnectorRecord[];
    hasMore?: boolean;
  } | null;

  return {
    items: (data?.items ?? []) as ScheduledConnectorRecord[],
    hasMore: data?.hasMore ?? false,
  };
};

/**
 * Fetch every page. Throws on the first failed page or when the page cap is
 * hit, so callers never act on a partial list.
 */
export const fetchAllScheduledConnectors = async (
  appConfig: AppConfig,
): Promise<ScheduledConnectorRecord[]> => {
  const all: ScheduledConnectorRecord[] = [];
  for (let page = 1; page <= SCHEDULED_CONNECTORS_MAX_PAGES; page++) {
    const { items, hasMore } = await fetchScheduledConnectorsPage(appConfig, page);
    all.push(...items);
    if (!hasMore) {
      return all;
    }
  }
  throw new Error(
    `Scheduled-connector listing exceeded ${SCHEDULED_CONNECTORS_MAX_PAGES} pages`,
  );
};
