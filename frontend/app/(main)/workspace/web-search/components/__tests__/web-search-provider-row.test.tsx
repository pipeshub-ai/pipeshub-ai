import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { WebSearchProviderRow } from '../web-search-provider-row';
import { WEB_SEARCH_PROVIDER_META, type WebSearchProviderType } from '../../types';

function row(type: WebSearchProviderType) {
  const meta = WEB_SEARCH_PROVIDER_META.find((m) => m.type === type);
  if (!meta) throw new Error(type);
  return render(
    <Theme>
      <WebSearchProviderRow
        meta={meta}
        configured={{ providerKey: 'k', provider: type, configuration: {}, isDefault: false }}
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
    expect(screen.getByText('Set as default')).toBeTruthy();
  });

  it('a provider that only reads pages cannot', () => {
    row('crawl4ai');
    expect(screen.queryByText('Set as default')).toBeNull();
  });
});
