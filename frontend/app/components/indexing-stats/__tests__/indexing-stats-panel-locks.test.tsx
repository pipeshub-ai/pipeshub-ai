/**
 * Sync now, Stop, Reindex failed, Manual index and the AI retries all go through
 * routes that check the connector sync permission, so they lock without it.
 * Refresh only reads, and stays usable.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ConnectorStatsResponse } from '@/app/(main)/workspace/connectors/types';

const permissions = vi.hoisted(() => ({ denied: new Set<string>() }));
vi.mock('@/config', () => ({
  PermissionLockIcon: () => <span data-testid="permission-lock" />,
  useUserPermission: (key: string) => !permissions.denied.has(key),
}));

import { IndexingStatsPanel } from '../indexing-stats-panel';

const stats: ConnectorStatsResponse['data'] = {
  orgId: 'org-1',
  connectorId: 'c-1',
  origin: 'CONNECTOR',
  stats: {
    total: 6,
    indexingStatus: { COMPLETED: 3, FAILED: 2, AUTO_INDEX_OFF: 1 } as unknown as ConnectorStatsResponse['data']['stats']['indexingStatus'],
  },
  byRecordType: [],
} as ConnectorStatsResponse['data'];

const handlers = () => ({
  onRefresh: vi.fn().mockResolvedValue(undefined),
  onSync: vi.fn().mockResolvedValue(undefined),
  onReindexFailed: vi.fn().mockResolvedValue(undefined),
  onManualIndex: vi.fn().mockResolvedValue(undefined),
});

const button = (name: RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement;

beforeEach(() => {
  permissions.denied = new Set();
});
afterEach(cleanup);

describe('IndexingStatsPanel sync permission locks', () => {
  it('keeps every action usable with the connector sync permission', () => {
    render(<Theme><IndexingStatsPanel stats={stats} showSyncActions {...handlers()} /></Theme>);

    for (const name of [/Sync now/, /Reindex failed/, /Manual index/, /Refresh/]) {
      expect(button(name).disabled, String(name)).toBe(false);
    }
    expect(screen.queryByTestId('permission-lock')).toBeNull();
  });

  it('locks the sync and reindex actions without it, and leaves Refresh alone', () => {
    permissions.denied = new Set(['manageConnectorSync']);
    const h = handlers();
    render(<Theme><IndexingStatsPanel stats={stats} showSyncActions {...h} /></Theme>);

    for (const name of [/Sync now/, /Reindex failed/, /Manual index/]) {
      expect(button(name).disabled, String(name)).toBe(true);
    }
    expect(screen.getAllByTestId('permission-lock')).toHaveLength(3);
    fireEvent.click(button(/Reindex failed/));
    expect(h.onReindexFailed).not.toHaveBeenCalled();
    expect(button(/Refresh/).disabled).toBe(false);
  });
});
