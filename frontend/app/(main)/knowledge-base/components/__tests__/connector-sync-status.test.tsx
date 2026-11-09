import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, act } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const getConnectorProgress = vi.fn();
vi.mock('@/app/(main)/workspace/connectors/api', () => ({
  ConnectorsApi: {
    getConnectorProgress: (...args: unknown[]) => getConnectorProgress(...args),
  },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { ConnectorSyncStatus } from '../connector-sync-status';
import { installDomShims, renderInTheme } from '../../../workspace/connectors/__tests__/fixtures';

describe('ConnectorSyncStatus', () => {
  beforeEach(() => {
    installDomShims();
    vi.useFakeTimers();
    getConnectorProgress.mockReset();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('stays silent and stops asking when the viewer may not open the connector', async () => {
    getConnectorProgress.mockRejectedValue({ statusCode: 404, message: 'gone' });
    const { container } = renderInTheme(<ConnectorSyncStatus connectorId="c1" source="Jira" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5 * 60_000);
    });

    expect(getConnectorProgress).toHaveBeenCalledTimes(1);
    expect(getConnectorProgress).toHaveBeenCalledWith('c1', 'summary', { suppressErrorToast: true });
    expect(container.textContent).toBe('');
  });
});
