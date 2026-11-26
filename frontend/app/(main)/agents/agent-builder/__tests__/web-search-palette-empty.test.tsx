import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from './agent-builder-harness';
import { useUserStore } from '@/lib/store/user-store';
import type { ConfiguredWebSearchProvider } from '../../../workspace/web-search/types';
import type { UseWebSearchConfigResult } from '../hooks/use-web-search-config';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../../../workspace/web-search/components', () => ({ ConfigurePanel: () => null }));

import { AgentBuilderWebSearchSection } from '../components/sidebar-web-search-section';

const NOT_SET_UP = /Web search isn’t set up yet/;

function renderSection(config: Partial<UseWebSearchConfigResult>) {
  renderInTheme(
    <AgentBuilderWebSearchSection
      attached={null}
      onNotify={() => {}}
      config={{ configuredProviders: [], loading: false, error: null, reload: async () => {}, ...config }}
    />
  );
}

beforeEach(() => {
  installBrowserShims();
  useUserStore.setState({ profile: { isAdmin: false } } as never);
});

afterEach(cleanup);

describe('web search palette group for a member', () => {
  it('says web search is not set up when an admin has configured no provider', () => {
    renderSection({});
    expect(screen.getByText('Web search')).toBeTruthy();
    expect(screen.getByText(NOT_SET_UP)).toBeTruthy();
  });

  it('says the providers could not be loaded when the request failed', () => {
    renderSection({ error: 'Network Error' });
    expect(screen.getByText('Couldn’t load web search providers.')).toBeTruthy();
    expect(screen.queryByText(NOT_SET_UP)).toBeNull();
  });

  it('lists the configured provider and no hint', () => {
    const serper: ConfiguredWebSearchProvider = {
      providerKey: 'k1',
      provider: 'serper',
      configuration: {},
      isDefault: true,
    };
    renderSection({ configuredProviders: [serper] });
    expect(screen.getByText('Serper')).toBeTruthy();
    expect(screen.queryByText(NOT_SET_UP)).toBeNull();
  });

  it('shows every searching provider to an admin, with no hint', () => {
    useUserStore.setState({ profile: { isAdmin: true } } as never);
    renderSection({});
    expect(screen.getByText('DuckDuckGo')).toBeTruthy();
    expect(screen.queryByText(NOT_SET_UP)).toBeNull();
  });
});
