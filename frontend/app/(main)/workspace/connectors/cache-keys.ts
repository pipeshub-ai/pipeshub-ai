import { SWR_NS } from '@/lib/api/swr-cache';
import { ConnectorsApi } from './api';

/**
 * SWR key of the active team connector list (page 1, up to 100). Every reader
 * uses it with `fetchActiveTeamConnectors`, so they share one request.
 */
export const ACTIVE_TEAM_CONNECTORS_KEY = [SWR_NS.teamConnectorLists, 'active'] as const;

// Read in the background (chat, Collections) as well as on the connectors page;
// readers show their own state, so a failure raises no toast.
export const fetchActiveTeamConnectors = () =>
  ConnectorsApi.getActiveConnectors('team', 1, 100, { suppressErrorToast: true });
