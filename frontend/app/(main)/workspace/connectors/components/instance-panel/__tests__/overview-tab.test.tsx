import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const reindexConnector = vi.fn();
vi.mock('../../../api', () => ({
  ConnectorsApi: { reindexConnector: (...args: unknown[]) => reindexConnector(...args) },
}));
const fetchInstanceStats = vi.fn();
vi.mock('../../../utils/fetch-instance-stats', () => ({
  fetchInstanceStats: (...args: unknown[]) => fetchInstanceStats(...args),
}));
const runConnectorResync = vi.fn();
vi.mock('../../../utils/connector-sync-actions', () => ({
  runConnectorResync: (...args: unknown[]) => runConnectorResync(...args),
}));
const routerPush = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: routerPush }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { OverviewTab } from '../overview-tab';
import { useConnectorsStore } from '../../../store';
import type { ConnectorStatsResponse } from '../../../types';
import {
  installDomShims,
  renderInTheme,
  makeInstance,
  toasts,
  clearToasts,
} from '../../../__tests__/fixtures';

function stats(indexingStatus: Record<string, number>, total?: number): ConnectorStatsResponse['data'] {
  return {
    orgId: 'org-1',
    connectorId: 'conn-1',
    origin: 'CONNECTOR',
    stats: {
      total: total ?? Object.values(indexingStatus).reduce((a, b) => a + b, 0),
      indexingStatus: indexingStatus as unknown as ConnectorStatsResponse['data']['stats']['indexingStatus'],
    },
    byRecordType: [],
  };
}

const toastTitles = () => toasts().map((t) => t.title);

beforeEach(() => {
  installDomShims();
  useConnectorsStore.getState().reset();
  clearToasts();
  reindexConnector.mockReset().mockResolvedValue({});
  fetchInstanceStats.mockReset().mockResolvedValue(undefined);
  runConnectorResync.mockReset().mockResolvedValue({ kind: 'backend' });
  routerPush.mockReset();
  vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('OverviewTab: record status', () => {
  it('shows the record counts by indexing status', () => {
    renderInTheme(
      <OverviewTab
        instance={makeInstance({ isActive: true })}
        stats={stats({ COMPLETED: 40, FAILED: 3, QUEUED: 2 }, 45)}
      />
    );
    expect(screen.getByText('Records Status')).toBeTruthy();
    expect(screen.getByText('45')).toBeTruthy();
    expect(screen.getByText('40')).toBeTruthy();
    expect(screen.getByText('Errors (API / permission issues)')).toBeTruthy();
  });

  it('shows placeholders instead of numbers while stats load', () => {
    renderInTheme(
      <OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ COMPLETED: 40 })} statsLoading />
    );
    expect(screen.queryByText('40')).toBeNull();
    expect(screen.queryByText('Indexed and searchable')).toBeNull();
  });

  it('shows sync progress while a sync runs', () => {
    renderInTheme(
      <OverviewTab
        instance={makeInstance({ isActive: true, status: 'SYNCING', syncProgress: { percentage: 35 } })}
        stats={null}
      />
    );
    expect(screen.getByText('35% Complete')).toBeTruthy();
  });

  it('opens the failed records in the knowledge base', () => {
    const instance = makeInstance({ isActive: true });
    useConnectorsStore.setState({ isInstancePanelOpen: true, selectedInstance: instance });
    renderInTheme(<OverviewTab instance={instance} stats={stats({ COMPLETED: 5, FAILED: 3 })} />);

    fireEvent.click(screen.getByText('Errors (API / permission issues)'));

    expect(routerPush).toHaveBeenCalledWith(
      '/knowledge-base?view=all-records&connectorIds=conn-1&indexingStatus=FAILED'
    );
    expect(useConnectorsStore.getState().isInstancePanelOpen).toBe(false);
  });

  it('hides the sync actions while sync is off', () => {
    renderInTheme(
      <OverviewTab instance={makeInstance({ isActive: false })} stats={stats({ FAILED: 3 })} />
    );
    expect(screen.queryByRole('button', { name: /Sync now/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Reindex failed/ })).toBeNull();
  });
});

describe('OverviewTab: actions', () => {
  it('reindexes failed records and refreshes the numbers', async () => {
    renderInTheme(
      <OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ COMPLETED: 5, FAILED: 3 })} />
    );

    fireEvent.click(screen.getByRole('button', { name: /Reindex failed \(3\)/ }));

    await waitFor(() => expect(reindexConnector).toHaveBeenCalledWith('conn-1', ['FAILED']));
    await waitFor(() => expect(toastTitles()).toContain('Reindexing failed records…'));
    expect(fetchInstanceStats).toHaveBeenCalledWith('conn-1', { force: true });
  });

  it('reports a failed reindex', async () => {
    reindexConnector.mockRejectedValue(new Error('nope'));
    renderInTheme(
      <OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ FAILED: 3 })} />
    );

    fireEvent.click(screen.getByRole('button', { name: /Reindex failed \(3\)/ }));

    await waitFor(() => expect(toastTitles()).toContain('Failed to reindex failed records'));
  });

  it('indexes records that were left for manual indexing', async () => {
    renderInTheme(
      <OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ AUTO_INDEX_OFF: 7 })} />
    );

    fireEvent.click(screen.getByRole('button', { name: /Manual index \(7\)/ }));

    await waitFor(() => expect(reindexConnector).toHaveBeenCalledWith('conn-1', ['AUTO_INDEX_OFF']));
  });

  it('starts a sync and confirms it', async () => {
    renderInTheme(<OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ COMPLETED: 1 })} />);

    fireEvent.click(screen.getByRole('button', { name: /Sync now/ }));

    await waitFor(() =>
      expect(runConnectorResync).toHaveBeenCalledWith({ connectorId: 'conn-1', connectorType: 'Jira' })
    );
    await waitFor(() => expect(toastTitles()).toContain('Sync started'));
  });

  it('explains a failed sync start with the reason it was given', async () => {
    runConnectorResync.mockRejectedValue(new Error('A sync is already running for this connector.'));
    renderInTheme(<OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ COMPLETED: 1 })} />);

    fireEvent.click(screen.getByRole('button', { name: /Sync now/ }));

    await waitFor(() => expect(toastTitles()).toContain('Failed to start sync'));
    expect(toasts().find((t) => t.title === 'Failed to start sync')?.description).toBe(
      'A sync is already running for this connector.'
    );
  });

  it('refreshes the record status and confirms it', async () => {
    renderInTheme(<OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ COMPLETED: 1 })} />);

    fireEvent.click(screen.getByRole('button', { name: /Refresh/ }));

    await waitFor(() => expect(fetchInstanceStats).toHaveBeenCalledWith('conn-1', { force: true }));
    await waitFor(() => expect(toastTitles()).toContain('Record status updated'));
  });

  it('reports a failed refresh', async () => {
    fetchInstanceStats.mockRejectedValue(new Error('down'));
    renderInTheme(<OverviewTab instance={makeInstance({ isActive: true })} stats={stats({ COMPLETED: 1 })} />);

    fireEvent.click(screen.getByRole('button', { name: /Refresh/ }));

    await waitFor(() => expect(toastTitles()).toContain('Failed to refresh record status'));
  });
});
