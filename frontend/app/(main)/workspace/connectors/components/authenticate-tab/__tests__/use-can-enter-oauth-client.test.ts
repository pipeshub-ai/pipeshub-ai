import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useCanEditLinkedOAuthApp, usePanelConnectorScope } from '../use-can-enter-oauth-client';
import { useConnectorsStore } from '../../../store';
import { makeConnector, signInAs } from '../../../__tests__/fixtures';

beforeEach(() => {
  useConnectorsStore.getState().reset();
  signInAs('admin');
});

describe('the scope of the connector open in the panel', () => {
  it('stays personal right after a personal connector is created from a catalog row with no scope', () => {
    useConnectorsStore.getState().openPanel(makeConnector({ scope: undefined }), undefined, 'personal');
    useConnectorsStore.setState({ panelConnectorId: 'new-connector' });

    expect(renderHook(() => usePanelConnectorScope()).result.current).toBe('personal');
    expect(renderHook(() => useCanEditLinkedOAuthApp()).result.current).toBe(false);
  });

  it("is an existing connector's own scope, not the page's", () => {
    useConnectorsStore.getState().openPanel(makeConnector({ scope: 'team' }), 'team-connector', 'personal');

    expect(renderHook(() => usePanelConnectorScope()).result.current).toBe('team');
  });
});
