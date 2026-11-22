import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const slot = vi.hoisted(() => ({ props: [] as Array<Record<string, unknown>> }));
vi.mock('@/config', () => ({
  ConnectorConfigureExtras: (props: Record<string, unknown>) => {
    slot.props.push(props);
    return null;
  },
}));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { useConnectorsStore } from '../../../store';
import { ConfigureTab } from '..';

const CATALOG = { name: 'Web', type: 'Web', scope: 'team' };

function renderWith(state: Record<string, unknown>) {
  useConnectorsStore.setState({
    connectorSchema: { sync: { supportedStrategies: [], customFields: [] }, filters: {} },
    panelConnector: CATALOG,
    panelConnectorId: 'c1',
    ...state,
  } as never);
  render(
    <Theme>
      <ConfigureTab />
    </Theme>,
  );
  return slot.props.at(-1);
}

afterEach(() => {
  cleanup();
  slot.props = [];
});

describe('the Configure tab extension slot', () => {
  it('gets the instance name, not the catalog entry, for a new instance', () => {
    expect(renderWith({ connectorConfig: null, instanceName: ' Finance site ' })).toMatchObject({
      connectorId: 'c1',
      connectorType: 'Web',
      connectorName: 'Finance site',
      scope: 'team',
    });
  });

  it('prefers the saved instance name', () => {
    expect(renderWith({ connectorConfig: { name: 'Saved name' }, instanceName: 'Typed name' })).toMatchObject({
      connectorName: 'Saved name',
    });
  });

  it('falls back to the catalog name', () => {
    expect(renderWith({ connectorConfig: null, instanceName: '' })).toMatchObject({ connectorName: 'Web' });
  });
});
