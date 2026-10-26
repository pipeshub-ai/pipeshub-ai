import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { McpMyServerEntry } from '../../../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/ConnectorIcon', () => ({
  ConnectorIcon: () => null,
  resolveConnectorType: (s: string) => s,
}));

import { McpPersonalServerCard } from '../mcp-personal-server-card';

afterEach(cleanup);

describe('McpPersonalServerCard', () => {
  it('renders an authenticated server whose tools are missing', () => {
    installBrowserShims();
    const instance = {
      _id: 'i1', name: 'Team Slack', typeId: 'slack', transport: 'streamable_http', authMode: 'oauth',
      isAuthenticated: true, scopes: [], args: [], requiredEnv: [], optionalEnv: [],
    } as unknown as McpMyServerEntry;
    renderInTheme(
      <McpPersonalServerCard
        instance={instance}
        isBusy={false}
        onAuthenticate={vi.fn()}
        onReauthenticate={vi.fn()}
        onRemoveCredentials={vi.fn()}
      />,
    );
    expect(screen.getByText('Team Slack')).toBeTruthy();
  });
});
