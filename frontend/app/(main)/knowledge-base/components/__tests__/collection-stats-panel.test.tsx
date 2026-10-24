import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const reindexKnowledgeBase = vi.fn();
const getCollectionStats = vi.fn();
vi.mock('../../api', () => ({
  KnowledgeBaseApi: {
    reindexKnowledgeBase: (...args: unknown[]) => reindexKnowledgeBase(...args),
    getCollectionStats: (...args: unknown[]) => getCollectionStats(...args),
  },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/(main)/workspace/components/workspace-right-panel', () => ({
  WorkspaceRightPanel: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import { CollectionStatsPanel } from '../collection-stats-panel';
import { useKnowledgeBaseStore } from '../../store';
import type { ConnectorStatsResponse, ExtractionStatusCounts } from '../../../workspace/connectors/types';
import {
  installDomShims,
  renderInTheme,
  toastTitles,
  clearToasts,
} from '../../../workspace/connectors/__tests__/fixtures';

function stats(
  extractionStatus?: ExtractionStatusCounts,
  entitiesStatus?: ExtractionStatusCounts
): ConnectorStatsResponse['data'] {
  return {
    orgId: 'org-1',
    connectorId: 'kb-1',
    origin: 'UPLOAD',
    stats: {
      total: 5,
      indexingStatus: { COMPLETED: 5 } as unknown as ConnectorStatsResponse['data']['stats']['indexingStatus'],
      ...(extractionStatus ? { extractionStatus } : {}),
      ...(entitiesStatus ? { entitiesStatus } : {}),
    },
    byRecordType: [],
  };
}

function openWith(data: ConnectorStatsResponse['data']) {
  getCollectionStats.mockResolvedValue({ success: true, data });
  useKnowledgeBaseStore.getState().openCollectionStatsPanel('kb-1', 'Docs');
  renderInTheme(<CollectionStatsPanel />);
}

const disabled = (name: RegExp) => (screen.getByRole('button', { name }) as HTMLButtonElement).disabled;

beforeEach(() => {
  installDomShims();
  useKnowledgeBaseStore.getState().reset();
  clearToasts();
  reindexKnowledgeBase.mockReset().mockResolvedValue({});
  getCollectionStats.mockReset();
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('CollectionStatsPanel: entity extraction retry', () => {
  it('hides the retry when no record failed entity extraction', async () => {
    openWith(stats(undefined, { COMPLETED: 5, FAILED: 0 }));

    await waitFor(() => expect(screen.getByText('Records status')).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Retry failed entity extraction/ })).toBeNull();
  });

  it('re-runs only the entities stage for failed records and refreshes the numbers', async () => {
    openWith(stats(undefined, { FAILED: 3 }));

    fireEvent.click(await screen.findByRole('button', { name: /Retry failed entity extraction \(3\)/ }));

    await waitFor(() => expect(reindexKnowledgeBase).toHaveBeenCalledWith('kb-1', ['FAILED'], ['entities']));
    await waitFor(() => expect(toastTitles()).toContain('Entity extraction retry queued'));
    await waitFor(() => expect(getCollectionStats).toHaveBeenCalledTimes(2));
  });

  it('reports a failed retry', async () => {
    reindexKnowledgeBase.mockRejectedValue(new Error('nope'));
    openWith(stats(undefined, { FAILED: 3 }));

    fireEvent.click(await screen.findByRole('button', { name: /Retry failed entity extraction \(3\)/ }));

    await waitFor(() => expect(toastTitles()).toContain('Failed to retry entity extraction'));
  });

  it('keeps the classification retry disabled while the entity retry runs', async () => {
    let finish: (value: unknown) => void = () => {};
    reindexKnowledgeBase.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    openWith(stats({ FAILED: 2 }, { FAILED: 3 }));

    fireEvent.click(await screen.findByRole('button', { name: /Retry failed entity extraction \(3\)/ }));

    await waitFor(() => expect(disabled(/Retry failed classification \(2\)/)).toBe(true));
    expect(disabled(/Retry failed entity extraction \(3\)/)).toBe(true);

    finish({});
    await waitFor(() => expect(disabled(/Retry failed classification \(2\)/)).toBe(false));
  });
});
