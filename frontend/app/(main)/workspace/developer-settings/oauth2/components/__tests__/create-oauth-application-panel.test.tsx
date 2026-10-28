import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));

const scope = vi.hoisted(() => (name: string, category: string) => ({
  name,
  description: `${name} description`,
  category,
  requiresUserConsent: false,
}));

// agent:execute is left out, as on an instance whose MCP_SCOPES omits it.
vi.mock('../../api', () => ({
  Oauth2Api: {
    getScopes: vi.fn().mockResolvedValue({
      scopes: {
        Access: [scope('offline_access', 'Access')],
        'Knowledge Base': [scope('kb:read', 'Knowledge Base'), scope('kb:write', 'Knowledge Base')],
        Semantic: [scope('semantic:read', 'Semantic'), scope('semantic:write', 'Semantic')],
        Conversations: [scope('conversation:chat', 'Conversations')],
        Agents: [scope('agent:read', 'Agents'), scope('agent:write', 'Agents')],
        Connectors: [scope('connector:read', 'Connectors')],
        Organization: [scope('org:admin', 'Organization')],
      },
    }),
    createApp: vi.fn(),
  },
}));
vi.mock('../../../../service-accounts/api', () => ({
  ServiceAccountsApi: { list: vi.fn().mockResolvedValue({ serviceAccounts: [] }) },
}));

import { CreateOAuthApplicationPanel } from '../create-oauth-application-panel';

afterEach(cleanup);

function scopeChecked(name: string): string | null {
  const row = screen.getByText(name).closest('div')?.parentElement;
  return row?.querySelector('[role="checkbox"]')?.getAttribute('data-state') ?? null;
}

describe('CreateOAuthApplicationPanel default scopes', () => {
  it('ticks the scopes MCP chat needs, and only the ones offered', async () => {
    installBrowserShims();
    renderInTheme(<CreateOAuthApplicationPanel open onOpenChange={() => {}} />);

    await screen.findAllByRole('tab');
    const scopesTab = document.querySelector('[role="tab"][id$="-trigger-scopes"]') as HTMLElement;
    fireEvent.mouseDown(scopesTab);
    fireEvent.click(scopesTab);
    await screen.findByText('kb:read');

    await waitFor(() => expect(scopeChecked('kb:read')).toBe('checked'));
    for (const name of [
      'offline_access',
      'connector:read',
      'semantic:write',
      'conversation:chat',
      'agent:read',
    ]) {
      expect(scopeChecked(name), name).toBe('checked');
    }
    for (const name of ['kb:write', 'semantic:read', 'agent:write', 'org:admin']) {
      expect(scopeChecked(name), name).toBe('unchecked');
    }
    expect(screen.queryByText('agent:execute')).toBeNull();
  });
});
