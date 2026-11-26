import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));
// jsdom has no matchMedia.
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

// The store persists to localStorage; stand in for the selectors the page reads.
let user = { isAdmin: true as boolean | null, isProfileInitialized: true };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof user) => unknown) => selector(user),
  selectRefreshedIsAdmin: (s: typeof user) => (s.isProfileInitialized ? s.isAdmin : null),
  selectIsProfileInitialized: (s: typeof user) => s.isProfileInitialized,
}));

function lookup(key: string): string | undefined {
  let cur: unknown = en;
  for (const part of key.split('.')) {
    if (typeof cur !== 'object' || cur === null || !(part in cur)) return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return typeof cur === 'string' ? cur : undefined;
}

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) =>
      (lookup(key) ?? key).replace(/\{\{(\w+)\}\}/g, (_m, name: string) => vars?.[name] ?? ''),
  }),
}));

import { EnterpriseOverviewPage } from '../enterprise-overview-page';
import { ENTERPRISE_OVERVIEW_GROUPS } from '../enterprise-overview';
import { ENTERPRISE_FEATURES } from '../enterprise-feature-pages';
import { enterpriseContactUrl } from '@/lib/constants/enterprise';

const OVERVIEW = en.workspace.enterprise.overview;
const ITEMS = ENTERPRISE_OVERVIEW_GROUPS.flatMap((group) => group.items);
// next/link adds the app's trailing slash only under its own config, which tests don't load.
const route = (a: Element) => a.getAttribute('href')?.replace(/\/$/, '');

afterEach(() => {
  cleanup();
  replace.mockClear();
  user = { isAdmin: true, isProfileInitialized: true };
});

function renderPage() {
  return render(
    <Theme>
      <EnterpriseOverviewPage />
    </Theme>,
  );
}

describe('ENTERPRISE_OVERVIEW_GROUPS', () => {
  it('has English copy for every group and feature', () => {
    for (const group of ENTERPRISE_OVERVIEW_GROUPS) {
      expect(lookup(`workspace.enterprise.overview.groups.${group.key}`), group.key).toBeTruthy();
    }
    for (const item of ITEMS) {
      expect(lookup(`workspace.enterprise.overview.items.${item.key}.name`), item.key).toBeTruthy();
      expect(lookup(`workspace.enterprise.overview.items.${item.key}.description`), item.key).toBeTruthy();
    }
  });

  it('lists every Enterprise page once', () => {
    const linked = ITEMS.flatMap((item) => (item.feature ? [item.feature] : []));
    expect([...linked].sort()).toEqual(Object.keys(ENTERPRISE_FEATURES).sort());
  });
});

describe('EnterpriseOverviewPage', () => {
  it('says which edition this is and lists every feature under its group', () => {
    renderPage();

    expect(screen.getByText(OVERVIEW.currentEdition)).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: OVERVIEW.title }).tagName).toBe('H1');
    for (const group of ENTERPRISE_OVERVIEW_GROUPS) {
      const section = screen.getByRole('region', { name: lookup(`workspace.enterprise.overview.groups.${group.key}`) });
      expect(within(section).getAllByTestId('enterprise-overview-item')).toHaveLength(group.items.length);
    }
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(ITEMS.length);
    expect(screen.getByText(OVERVIEW.communityNote)).toBeTruthy();
  });

  it('links each feature that has a page to it, named after the feature', () => {
    renderPage();

    for (const item of ITEMS) {
      const name = lookup(`workspace.enterprise.overview.items.${item.key}.name`)!;
      const label = OVERVIEW.seePreviewLabel.replace('{{name}}', name);
      const link = screen.queryByRole('link', { name: label });
      if (item.feature) {
        expect(link, item.key).not.toBeNull();
        expect(route(link!)).toBe(ENTERPRISE_FEATURES[item.feature].route);
        expect(link!.textContent).toBe(OVERVIEW.seePreview);
      } else {
        expect(link, item.key).toBeNull();
      }
    }
  });

  it('offers a way to talk with us, tagged as coming from this page', () => {
    renderPage();

    const contact = screen.getByRole('link', { name: en.workspace.enterprise.contact });
    expect(contact.getAttribute('href')).toBe(enterpriseContactUrl('overview'));
    expect(contact.getAttribute('target')).toBe('_blank');
    expect(contact.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('sends a member who opens the URL to General settings, showing nothing', () => {
    user = { isAdmin: false, isProfileInitialized: true };
    const { container } = renderPage();

    expect(replace).toHaveBeenCalledWith('/workspace/general');
    expect(container.textContent).toBe('');
  });

  it('shows nothing, and sends no one away, until it is known who the user is', () => {
    user = { isAdmin: null, isProfileInitialized: false };
    const { container } = renderPage();

    expect(container.textContent).toBe('');
    expect(replace).not.toHaveBeenCalled();
  });
});
