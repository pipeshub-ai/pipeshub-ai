import { describe, it, expect, beforeEach, vi } from 'vitest';
import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import type { User } from '@/app/(main)/workspace/users/types';
import { ConnectorsApi } from '../../api';
import type { Connector } from '../../types';
import { RESTRICTED_RECORD_READER, RESTRICTED_RECORD_TITLE } from '../demo-data';
import { checkRestrictedQuestionAccess } from '../restricted-question';

vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: { searchAllRecords: vi.fn() },
}));
vi.mock('@/app/(main)/workspace/users/api', () => ({
  UsersApi: { listUsers: vi.fn() },
}));
vi.mock('../../api', () => ({
  ConnectorsApi: { getActiveConnectors: vi.fn() },
}));

const searchAllRecords = vi.mocked(KnowledgeHubApi.searchAllRecords);
const listUsers = vi.mocked(UsersApi.listUsers);
const getActiveConnectors = vi.mocked(ConnectorsApi.getActiveConnectors);

function records(...names: string[]) {
  return { items: names.map((name, i) => ({ id: `r${i}`, name })) } as never;
}

function users(...emails: string[]) {
  return { users: emails.map((email, i) => ({ userId: `u${i}`, email }) as User), totalCount: emails.length };
}

/** What the viewer's listing returns: for the title search, and for "any demo record". */
function listing(titleMatches: string[], anyRecord: string[]) {
  searchAllRecords.mockImplementation(async (params) =>
    params.q ? records(...titleMatches) : records(...anyRecord),
  );
}

function demoStatus(status: string) {
  getActiveConnectors.mockResolvedValue({
    success: true,
    connectors: [{ _key: 'demo-1', type: 'Demo', status } as Connector],
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  demoStatus('IDLE');
});

describe('checkRestrictedQuestionAccess', () => {
  it('asks for the restricted record by title, within the demo connectors, as the viewer', async () => {
    listing([RESTRICTED_RECORD_TITLE], ['Export runbook']);
    getActiveConnectors.mockResolvedValue({
      success: true,
      connectors: ['demo-1', 'demo-2'].map((_key) => ({ _key, type: 'Demo', status: 'IDLE' }) as Connector),
    } as never);

    const access = await checkRestrictedQuestionAccess(['demo-1', 'demo-2']);

    expect(access).toEqual({ canSee: true, readerEmail: null });
    expect(searchAllRecords).toHaveBeenCalledWith(
      expect.objectContaining({ q: RESTRICTED_RECORD_TITLE, nodeTypes: 'record', connectorIds: 'demo-1,demo-2' }),
      { suppressErrorToast: true },
    );
    expect(listUsers).not.toHaveBeenCalled();
  });

  it('searches for the title only after the demo is settled, so a record landing mid-check still counts', async () => {
    // Sync writes the record, then marks the connector idle. The status answer
    // is released by hand, and the record only "exists" once it has arrived.
    let statusArrived = false;
    let releaseStatus: () => void = () => {};
    getActiveConnectors.mockImplementation(
      () =>
        new Promise((resolve) => {
          releaseStatus = () => {
            statusArrived = true;
            resolve({ success: true, connectors: [{ _key: 'demo-1', type: 'Demo', status: 'IDLE' } as Connector] } as never);
          };
        }),
    );
    const titleSearchedBeforeStatus: boolean[] = [];
    searchAllRecords.mockImplementation(async (params) => {
      if (!params.q) return records('Export runbook');
      titleSearchedBeforeStatus.push(!statusArrived);
      return records(...(statusArrived ? [RESTRICTED_RECORD_TITLE] : []));
    });

    const pending = checkRestrictedQuestionAccess(['demo-1']);
    await Promise.resolve();
    releaseStatus();

    expect(await pending).toEqual({ canSee: true, readerEmail: null });
    expect(titleSearchedBeforeStatus).toEqual([false]);
  });

  it('reads every page of connectors to find each demo connector\'s status', async () => {
    const filler = (n: number, offset: number) =>
      Array.from({ length: n }, (_, i) => ({ _key: `other-${offset + i}`, type: 'Jira', status: 'IDLE' }) as Connector);
    getActiveConnectors
      .mockResolvedValueOnce({ success: true, connectors: filler(100, 0) } as never)
      .mockResolvedValueOnce({
        success: true,
        connectors: [...filler(3, 100), { _key: 'demo-1', type: 'Demo', status: 'SYNCING' } as Connector],
      } as never);
    listing([], ['Export runbook']);

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toBeNull();
    expect(getActiveConnectors).toHaveBeenCalledTimes(2);
    expect(getActiveConnectors.mock.calls[1][1]).toBe(2);
  });

  it('claims nothing when a demo connector is not in the listing at all', async () => {
    getActiveConnectors.mockResolvedValue({ success: true, connectors: [] } as never);
    listing([], ['Export runbook']);

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toBeNull();
  });

  it('keeps every lookup out of the error toasts, since a failure only leaves the question plain', async () => {
    listing([], ['Export runbook']);
    listUsers.mockResolvedValue(users(RESTRICTED_RECORD_READER));

    await checkRestrictedQuestionAccess(['demo-1']);

    for (const call of searchAllRecords.mock.calls) expect(call[1]).toEqual({ suppressErrorToast: true });
    expect(getActiveConnectors.mock.calls[0][3]).toEqual({ suppressErrorToast: true });
    expect(listUsers.mock.calls[0][1]).toEqual({ suppressErrorToast: true });
  });

  it('names the committee member to sign in as when that account exists', async () => {
    listing([], ['Export runbook']);
    listUsers.mockResolvedValue(users('someone@company.com', RESTRICTED_RECORD_READER));

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toEqual({
      canSee: false,
      readerEmail: RESTRICTED_RECORD_READER,
    });
  });

  it('names nobody when the sample accounts were not created', async () => {
    listing([], ['Export runbook']);
    // The search is a substring match; only the exact address counts.
    listUsers.mockResolvedValue(users(`x${RESTRICTED_RECORD_READER}`));

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toEqual({ canSee: false, readerEmail: null });
  });

  it('claims nothing before the demo has synced, when nobody can see any of it yet', async () => {
    listing([], []);

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toBeNull();
    expect(listUsers).not.toHaveBeenCalled();
  });

  it('claims nothing while the demo is syncing, when the record may simply not be in yet', async () => {
    listing([], ['Export runbook']);
    demoStatus('FULL_SYNCING');

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toBeNull();
    expect(searchAllRecords).not.toHaveBeenCalledWith(expect.objectContaining({ q: RESTRICTED_RECORD_TITLE }), expect.anything());
  });

  it('does not count a record whose title only resembles the restricted one', async () => {
    listing([`${RESTRICTED_RECORD_TITLE} (draft notes)`], ['Export runbook']);
    listUsers.mockResolvedValue(users());

    expect((await checkRestrictedQuestionAccess(['demo-1']))?.canSee).toBe(false);
  });

  it('still reports the restriction when the account lookup fails', async () => {
    listing([], ['Export runbook']);
    listUsers.mockRejectedValue(new Error('403'));

    expect(await checkRestrictedQuestionAccess(['demo-1'])).toEqual({ canSee: false, readerEmail: null });
  });

  it('claims nothing when a lookup fails or there is no demo', async () => {
    searchAllRecords.mockRejectedValue(new Error('offline'));
    expect(await checkRestrictedQuestionAccess(['demo-1'])).toBeNull();

    listing([], ['Export runbook']);
    getActiveConnectors.mockRejectedValue(new Error('offline'));
    expect(await checkRestrictedQuestionAccess(['demo-1'])).toBeNull();

    searchAllRecords.mockClear();
    expect(await checkRestrictedQuestionAccess([])).toBeNull();
    expect(searchAllRecords).not.toHaveBeenCalled();
  });
});
