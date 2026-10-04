'use client';

import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { ConnectorsApi } from '../api';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { RESTRICTED_RECORD_READER, RESTRICTED_RECORD_TITLE } from './demo-data';

export interface RestrictedQuestionAccess {
  /** Whether this person can open the record the restricted question is about. */
  canSee: boolean;
  /** A sample account that can, if it exists in this org, to sign in as instead. */
  readerEmail: string | null;
}

const SYNCING_STATUSES: ReadonlySet<string> = new Set([
  CONNECTOR_INSTANCE_STATUS.SYNCING,
  CONNECTOR_INSTANCE_STATUS.FULL_SYNCING,
]);

// Background lookups: a failure leaves the question plain, so no error toast.
const QUIET = { suppressErrorToast: true } as const;

const STATUS_PAGE_SIZE = 100;
const STATUS_MAX_PAGES = 10;

/**
 * Whether any of the demo connectors is syncing, or `null` if one of them was
 * not in the listing: the team list is paged, and an unseen status proves nothing.
 */
async function anyDemoSyncing(demoConnectorIds: string[]): Promise<boolean | null> {
  const unseen = new Set(demoConnectorIds);
  let syncing = false;
  for (let page = 1; page <= STATUS_MAX_PAGES && unseen.size > 0; page += 1) {
    const { connectors = [] } = await ConnectorsApi.getActiveConnectors('team', page, STATUS_PAGE_SIZE, QUIET);
    for (const c of connectors) {
      if (!c._key || !unseen.delete(c._key)) continue;
      if (SYNCING_STATUSES.has(c.status ?? '')) syncing = true;
    }
    if (connectors.length < STATUS_PAGE_SIZE) break;
  }
  return unseen.size > 0 ? null : syncing;
}

/**
 * Whether the demo's data is there to judge by: none of its connectors mid-sync,
 * and at least one of its records visible. Before that, a missing record says
 * nothing about access.
 */
async function demoDataSettled(demoConnectorIds: string[]): Promise<boolean> {
  const [syncing, anyRecord] = await Promise.all([
    anyDemoSyncing(demoConnectorIds),
    KnowledgeHubApi.searchAllRecords(
      { nodeTypes: 'record', connectorIds: demoConnectorIds.join(','), flattened: true, limit: 1, include: undefined },
      QUIET,
    ),
  ]);
  return syncing === false && (anyRecord.items ?? []).length > 0;
}

/**
 * The demo's pricing question only answers for Acme's pricing committee.
 * Records are listed with the viewer's own permissions, so once the demo data
 * is in, a missing record means this person cannot see it. `null` when that
 * cannot be told yet, in which case the question is offered like any other.
 */
export async function checkRestrictedQuestionAccess(
  demoConnectorIds: string[],
): Promise<RestrictedQuestionAccess | null> {
  if (demoConnectorIds.length === 0) return null;
  try {
    // Settled first: sync marks the connector idle only after writing its
    // records, so a title search made after that cannot miss a record in flight.
    if (!(await demoDataSettled(demoConnectorIds))) return null;
    const res = await KnowledgeHubApi.searchAllRecords(
      {
        q: RESTRICTED_RECORD_TITLE,
        nodeTypes: 'record',
        connectorIds: demoConnectorIds.join(','),
        flattened: true,
        limit: 5,
        include: undefined,
      },
      QUIET,
    );
    if ((res.items ?? []).some((item) => item.name === RESTRICTED_RECORD_TITLE)) {
      return { canSee: true, readerEmail: null };
    }
  } catch {
    return null;
  }

  // Sample accounts are optional at install, so only name one that exists.
  try {
    const { users } = await UsersApi.listUsers({ search: RESTRICTED_RECORD_READER, limit: 5 }, QUIET);
    const exists = users.some((u) => (u.email ?? '').trim().toLowerCase() === RESTRICTED_RECORD_READER);
    return { canSee: false, readerEmail: exists ? RESTRICTED_RECORD_READER : null };
  } catch {
    return { canSee: false, readerEmail: null };
  }
}
