import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));
// jsdom has no matchMedia.
let mobile = false;
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile }));

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

const fill = (text: string, vars?: Record<string, unknown>) =>
  text.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(vars?.[name] ?? ''));

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string, vars?: Record<string, unknown>) => fill(lookup(key) ?? key, vars) }),
}));

import { EnterpriseOverviewPage } from '../enterprise-overview-page';
import { COMMUNITY_PLAN_ITEMS, ENTERPRISE_OVERVIEW_GROUPS, ENTERPRISE_OVERVIEW_ITEMS } from '../enterprise-overview';
import { ENTERPRISE_FEATURES } from '../enterprise-feature-pages';
import { enterpriseContactUrl } from '@/lib/constants/enterprise';

const OVERVIEW = en.workspace.enterprise.overview;
const ITEMS = ENTERPRISE_OVERVIEW_ITEMS;
const itemName = (key: string) => lookup(`workspace.enterprise.overview.items.${key}.name`)!;
// next/link adds the app's trailing slash only under its own config, which tests don't load.
const route = (a: Element) => a.getAttribute('href')?.replace(/\/$/, '');

afterEach(() => {
  cleanup();
  replace.mockClear();
  user = { isAdmin: true, isProfileInitialized: true };
  mobile = false;
});

function renderPage() {
  return render(
    <Theme>
      <EnterpriseOverviewPage />
    </Theme>,
  );
}

describe('ENTERPRISE_OVERVIEW_GROUPS', () => {
  it('has English copy for every group, feature, plan item and question', () => {
    for (const group of ENTERPRISE_OVERVIEW_GROUPS) {
      expect(lookup(`workspace.enterprise.overview.groups.${group.key}`), group.key).toBeTruthy();
    }
    for (const item of ITEMS) {
      expect(lookup(`workspace.enterprise.overview.items.${item.key}.name`), item.key).toBeTruthy();
      expect(lookup(`workspace.enterprise.overview.items.${item.key}.description`), item.key).toBeTruthy();
      if (item.inCommunity) {
        expect(lookup(`workspace.enterprise.overview.compare.communityHas.${item.key}`), item.key).toBeTruthy();
      }
    }
    for (const key of COMMUNITY_PLAN_ITEMS) {
      expect(lookup(`workspace.enterprise.overview.plans.community.items.${key}`), key).toBeTruthy();
    }
    // Every Community entry in the table has a feature to sit beside, and no stray copy is left.
    expect(Object.keys(OVERVIEW.compare.communityHas).sort()).toEqual(
      ITEMS.filter((item) => item.inCommunity).map((item) => item.key).sort()
    );
  });

  it('lists every Enterprise page once', () => {
    const linked = ITEMS.flatMap((item) => (item.feature ? [item.feature] : []));
    expect([...linked].sort()).toEqual(Object.keys(ENTERPRISE_FEATURES).sort());
  });

  it('puts a short list on the Enterprise card, not every feature', () => {
    const highlighted = ITEMS.filter((item) => item.highlight).length;
    expect(highlighted).toBeGreaterThanOrEqual(3);
    expect(highlighted).toBeLessThan(ITEMS.length);
  });
});

describe('EnterpriseOverviewPage', () => {
  it('says which edition this is', () => {
    renderPage();

    expect(screen.getByText(OVERVIEW.currentEdition)).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: OVERVIEW.title }).tagName).toBe('H1');
  });

  it('shows the Community Edition as the admin’s plan, with what it has today and nothing to press', () => {
    renderPage();

    const card = screen.getByRole('region', { name: OVERVIEW.plans.community.name });
    expect(within(card).getByText(OVERVIEW.plans.community.yourPlan)).toBeTruthy();
    expect(within(card).getAllByRole('listitem')).toHaveLength(COMMUNITY_PLAN_ITEMS.length);
    expect(within(card).getByText(OVERVIEW.plans.community.current)).toBeTruthy();
    expect(within(card).queryByRole('link')).toBeNull();
    expect(within(card).queryByRole('button')).toBeNull();
  });

  it('shows the Enterprise card with what it adds, a way to talk with us, and a way to every feature', () => {
    renderPage();

    const card = screen.getByRole('region', { name: OVERVIEW.plans.enterprise.name });
    expect(within(card).getByText(OVERVIEW.plans.enterprise.lead)).toBeTruthy();
    const listed = within(card).getAllByRole('listitem').map((li) => li.textContent);
    const highlights = ITEMS.filter((item) => item.highlight);
    expect(listed).toHaveLength(highlights.length);
    highlights.forEach((item, i) => expect(listed[i]).toContain(itemName(item.key)));

    const contact = within(card).getByRole('link', { name: en.workspace.enterprise.contact });
    expect(contact.getAttribute('href')).toBe(enterpriseContactUrl('overview'));
    expect(contact.getAttribute('target')).toBe('_blank');
    expect(contact.getAttribute('rel')).toBe('noopener noreferrer');

    const seeAll = within(card).getByRole('link', { name: OVERVIEW.plans.enterprise.seeAll });
    const target = seeAll.getAttribute('href')!.slice(1);
    expect(document.getElementById(target)).toBe(screen.getByRole('region', { name: OVERVIEW.compare.title }));
  });

  it('offers one way to talk with us on the page', () => {
    renderPage();

    expect(screen.getAllByRole('link', { name: en.workspace.enterprise.contact })).toHaveLength(1);
  });

  it('compares every feature, grouped, with what Community has beside what Enterprise includes', () => {
    renderPage();

    const table = within(screen.getByRole('region', { name: OVERVIEW.compare.title })).getByRole('table');
    for (const group of ENTERPRISE_OVERVIEW_GROUPS) {
      expect(within(table).getByRole('columnheader', { name: lookup(`workspace.enterprise.overview.groups.${group.key}`) })).toBeTruthy();
    }
    const rows = within(table).getAllByTestId('enterprise-compare-row');
    expect(rows).toHaveLength(ITEMS.length);
    ITEMS.forEach((item, i) => {
      const row = rows[i];
      expect(within(row).getByRole('rowheader').textContent).toContain(itemName(item.key));
      const community = within(row).getByTestId('enterprise-compare-community').textContent;
      expect(community, item.key).toBe(
        item.inCommunity ? lookup(`workspace.enterprise.overview.compare.communityHas.${item.key}`) : `—${OVERVIEW.compare.notIncluded}`
      );
      expect(row.textContent).toContain(OVERVIEW.compare.included);
    });
  });

  it('on a phone, keeps the table narrow but still says what Enterprise includes', () => {
    mobile = true;
    renderPage();

    const table = within(screen.getByRole('region', { name: OVERVIEW.compare.title })).getByRole('table');
    expect(within(table).queryByText(OVERVIEW.compare.pricing)).toBeNull();
    // The word is hidden from sight only; a screen reader still hears it in every row.
    for (const row of within(table).getAllByTestId('enterprise-compare-row')) {
      expect(within(row).getByText(OVERVIEW.compare.included)).toBeTruthy();
    }
  });

  it('links each feature that has a page to it, named after the feature', () => {
    renderPage();

    for (const item of ITEMS) {
      const label = fill(OVERVIEW.seePreviewLabel, { name: itemName(item.key) });
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

  it('answers the first questions, naming the page’s own link and button', () => {
    renderPage();

    const questions = within(screen.getByRole('region', { name: OVERVIEW.faq.title })).getAllByTestId('enterprise-question');
    expect(questions.map((q) => q.querySelector('summary')!.textContent)).toEqual([
      OVERVIEW.faq.free.question,
      OVERVIEW.faq.preview.question,
      OVERVIEW.faq.get.question,
    ]);
    expect(questions[1].textContent).toContain(`“${OVERVIEW.seePreview}”`);
    expect(questions[2].textContent).toContain(en.workspace.enterprise.contact);
    expect(questions.map((q) => q.textContent).join('')).not.toContain('{{');
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
