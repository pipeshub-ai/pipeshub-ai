import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));
// jsdom has no matchMedia.
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

// The store persists to localStorage; stand in for the selectors the page reads.
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: object) => unknown) => selector({}),
  selectRefreshedIsAdmin: () => true,
  selectIsProfileInitialized: () => true,
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
  useTranslation: () => ({ t: (key: string) => lookup(key) ?? key }),
}));

import {
  ENTERPRISE_FEATURES,
  relatedEnterpriseFeatures,
  TenantsPlaceholderPage,
  type EnterpriseFeature,
} from '../enterprise-feature-pages';
import { ENTERPRISE_GLIMPSES } from '@/app/(main)/workspace/components/enterprise-glimpses';
import { enterpriseContactUrl } from '@/app/(main)/workspace/components/enterprise-placeholder-page';

const FEATURES = Object.keys(ENTERPRISE_FEATURES) as EnterpriseFeature[];

afterEach(cleanup);

describe('ENTERPRISE_FEATURES', () => {
  it.each(FEATURES)('%s has English copy for every line it shows, and a glimpse', (feature) => {
    const entry = ENTERPRISE_FEATURES[feature];
    const keys = [
      entry.labelKey,
      entry.headingKey,
      entry.messageKey,
      ...entry.benefitKeys,
      ...('titleKey' in entry ? [entry.titleKey, entry.subtitleKey] : []),
    ];

    for (const key of keys) expect(lookup(key), key).toBeTruthy();
    expect(entry.benefitKeys.length).toBeGreaterThan(0);
    expect(ENTERPRISE_GLIMPSES[feature]).toBeTruthy();
  });

  it('keeps the routes the Enterprise Edition uses for Groups and Tenants', () => {
    expect(ENTERPRISE_FEATURES.groups.route).toBe('/workspace/groups');
    expect(ENTERPRISE_FEATURES.tenants.route).toBe('/workspace/tenants');
  });
});

describe('relatedEnterpriseFeatures', () => {
  it.each(FEATURES)('points %s to three other pages, never itself', (feature) => {
    const related = relatedEnterpriseFeatures(feature);

    expect(related).toHaveLength(3);
    expect(related).not.toContain(feature);
    expect(new Set(related).size).toBe(3);
  });
});

describe('TenantsPlaceholderPage', () => {
  it('explains multi-tenancy, what it gives, and where to go next', () => {
    render(
      <Theme>
        <TenantsPlaceholderPage />
      </Theme>,
    );

    expect(screen.getByRole('heading', { name: en.workspace.tenants.title })).toBeTruthy();
    expect(screen.getByText(en.workspace.tenants.subtitle)).toBeTruthy();
    expect(screen.getByRole('heading', { name: en.workspace.enterprise.tenants.heading })).toBeTruthy();
    expect(screen.getByText(en.workspace.enterprise.tenants.message)).toBeTruthy();

    const benefits = screen.getByRole('list', { name: en.workspace.enterprise.benefitsTitle });
    expect(benefits.querySelectorAll('li')).toHaveLength(3);
    expect(benefits.textContent).toContain(en.workspace.enterprise.tenants.benefits.inherit);

    expect(screen.getByRole('link', { name: en.workspace.enterprise.contact }).getAttribute('href')).toBe(
      enterpriseContactUrl('tenants'),
    );

    const related = screen.getByRole('navigation', { name: en.workspace.enterprise.relatedTitle });
    // next/link adds the app's trailing slash only under its own config, which tests don't load.
    expect(Array.from(related.querySelectorAll('a')).map((a) => a.getAttribute('href')?.replace(/\/$/, ''))).toEqual([
      '/workspace/groups',
      '/workspace/enterprise/scim',
      '/workspace/enterprise/usage',
    ]);
  });
});
