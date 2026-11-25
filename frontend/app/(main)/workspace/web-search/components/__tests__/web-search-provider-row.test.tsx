import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { WebSearchProviderRow } from '../web-search-provider-row';
import { WEB_SEARCH_PROVIDER_META, type WebSearchProviderType } from '../../types';

function row(type: WebSearchProviderType, configured = true) {
  const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === type);
  if (!meta) throw new Error(type);
  return render(
    <Theme>
      <WebSearchProviderRow
        meta={meta}
        configured={configured ? { providerKey: 'k', provider: type, configuration: {}, isDefault: false } : null}
        isDefault={false}
        isSettingDefault={false}
        anyActionInProgress={false}
        onSetDefault={vi.fn()}
        onConfigure={vi.fn()}
        onDelete={vi.fn()}
      />
    </Theme>,
  );
}

afterEach(cleanup);

describe('WebSearchProviderRow — the default', () => {
  it('a searcher can be made the default', () => {
    row('searxng');
    expect(screen.getByText('workspace.webSearch.actions.setDefault')).toBeTruthy();
  });

  it('an unconfigured searcher hides the button; its badge says what to do', () => {
    row('searxng', false);
    expect(screen.queryByText('workspace.webSearch.actions.setDefault')).toBeNull();
    expect(screen.getByText('workspace.webSearch.badges.notConfigured')).toBeTruthy();
  });

  it('a provider that only reads pages cannot', () => {
    row('crawl4ai');
    expect(screen.queryByText('workspace.webSearch.actions.setDefault')).toBeNull();
  });
});

describe('WebSearchProviderRow — DuckDuckGo', () => {
  it('warns that it is unofficial scraping', () => {
    row('duckduckgo', false);
    expect(screen.getByText('workspace.webSearch.duckduckgoWarning')).toBeTruthy();
  });

  it('can be chosen before it is stored, since it needs no settings', () => {
    row('duckduckgo', false);
    expect(screen.getByText('workspace.webSearch.actions.setDefault')).toBeTruthy();
    expect(screen.queryByLabelText(/deleteConfiguration/)).toBeNull();
  });

  it('can be removed once an admin has chosen it', () => {
    row('duckduckgo', true);
    expect(screen.getByLabelText(/deleteConfiguration/)).toBeTruthy();
  });

  it('is the only provider with the warning', () => {
    row('serper');
    expect(screen.queryByText('workspace.webSearch.duckduckgoWarning')).toBeNull();
  });
});
