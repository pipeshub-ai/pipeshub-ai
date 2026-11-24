import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: ({ name }: { name: string }) => <span>{name}</span>,
}));
// jsdom has no matchMedia.
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));
// The page's own labels come back as their keys, so the tests can tell them apart.
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

// The store persists to localStorage; stand in for the selectors the page reads.
let user = { isAdmin: true as boolean | null, isProfileInitialized: true };
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: typeof user) => unknown) => selector(user),
  selectIsAdmin: (s: typeof user) => s.isAdmin,
  selectRefreshedIsAdmin: (s: typeof user) => (s.isProfileInitialized ? s.isAdmin : null),
  selectIsProfileInitialized: (s: typeof user) => s.isProfileInitialized,
}));

import { EnterprisePlaceholderPage } from '../enterprise-placeholder-page';
import { enterpriseContactUrl } from '@/lib/constants/enterprise';
import { ENTERPRISE_GLIMPSES } from '../enterprise-glimpses';

afterEach(() => {
  cleanup();
  replace.mockClear();
  user = { isAdmin: true, isProfileInitialized: true };
});

function renderPage(glimpse: React.ComponentProps<typeof EnterprisePlaceholderPage>['glimpse'] = ENTERPRISE_GLIMPSES.usage) {
  return render(
    <Theme>
      <EnterprisePlaceholderPage
        feature="usage"
        title="Usage"
        heading="AI usage and cost are available in the Enterprise Edition"
        message="See what AI costs your organization."
        benefits={['Cost by person', 'Invoices in one place', 'Spending alerts']}
        related={[
          { label: 'Groups', href: '/workspace/groups/' },
          { label: 'Tenants', href: '/workspace/tenants/' },
        ]}
        glimpse={glimpse}
      />
    </Theme>,
  );
}

describe('EnterprisePlaceholderPage', () => {
  it('says what is available and offers a demo booking for it', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Usage' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /available in the Enterprise Edition/ })).toBeTruthy();
    expect(screen.getByText('See what AI costs your organization.')).toBeTruthy();

    // The icon's ligature text stays out of the link's name.
    const contact = screen.getByRole('link', { name: 'workspace.enterprise.contact' });
    expect(contact.getAttribute('href')).toBe(enterpriseContactUrl('usage'));
    expect(contact.getAttribute('target')).toBe('_blank');
    expect(contact.getAttribute('rel')).toBe('noopener noreferrer');
    expect(contact.className).toContain('rt-Button');
    expect(contact.querySelector('[aria-hidden="true"]')?.textContent).toBe('event');
    expect(replace).not.toHaveBeenCalled();
  });

  it('lists what the feature gives, under its own label', () => {
    renderPage();

    const list = screen.getByRole('list', { name: 'workspace.enterprise.benefitsTitle' });
    const items = Array.from(list.querySelectorAll('li')).map((li) => li.textContent);
    // Each line starts with a hidden check mark's ligature.
    expect(items).toEqual(['checkCost by person', 'checkInvoices in one place', 'checkSpending alerts']);
    for (const li of Array.from(list.querySelectorAll('li'))) {
      expect(li.querySelector('[aria-hidden="true"]')?.textContent).toBe('check');
    }
  });

  it('links to the related Enterprise pages inside the app', () => {
    renderPage();

    const related = screen.getByRole('navigation', { name: 'workspace.enterprise.relatedTitle' });
    const links = Array.from(related.querySelectorAll('a'));
    // next/link adds the app's trailing slash only under its own config, which tests don't load.
    const route = (a: HTMLAnchorElement) => a.getAttribute('href')?.replace(/\/$/, '');
    expect(links.map((a) => [a.textContent, route(a), a.getAttribute('target')])).toEqual([
      ['Groups', '/workspace/groups', null],
      ['Tenants', '/workspace/tenants', null],
    ]);
  });

  it('leaves out the list and the related links when there are none', () => {
    render(
      <Theme>
        <EnterprisePlaceholderPage
          feature="scim"
          title="SCIM"
          heading="SCIM is available in the Enterprise Edition"
          message="Sync people."
          benefits={[]}
          related={[]}
          glimpse={ENTERPRISE_GLIMPSES.scim}
        />
      </Theme>,
    );

    expect(screen.queryByRole('list')).toBeNull();
    expect(screen.queryByRole('navigation')).toBeNull();
    // Only the demo booking and the way to everything in Enterprise.
    expect(screen.getAllByRole('link')).toHaveLength(2);
  });

  it('offers the page with everything in Enterprise beside the demo booking', () => {
    renderPage();

    const overview = screen.getByRole('link', { name: 'workspace.enterprise.seeWhatsInEnterprise' });
    // next/link adds the app's trailing slash only under its own config, which tests don't load.
    expect(overview.getAttribute('href')?.replace(/\/$/, '')).toBe('/workspace/enterprise');
    expect(overview.getAttribute('target')).toBeNull();
  });

  it('keeps the decorative premium icon away from screen readers', () => {
    renderPage();

    const icon = screen.getByText('workspace_premium');
    expect(icon.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it('sends a member who opens the URL to General settings, showing nothing', () => {
    user = { isAdmin: false, isProfileInitialized: true };
    const { container } = renderPage();

    expect(replace).toHaveBeenCalledWith('/workspace/general');
    expect(container.textContent).toBe('');
  });

  it('sends the user away when their role could not be loaded', () => {
    user = { isAdmin: null, isProfileInitialized: true };
    renderPage();

    expect(replace).toHaveBeenCalledWith('/workspace/general');
  });

  it('shows nothing, and sends no one away, until it is known who the user is', () => {
    user = { isAdmin: null, isProfileInitialized: false };
    const { container } = renderPage();

    expect(container.textContent).toBe('');
    expect(replace).not.toHaveBeenCalled();
  });

  it('tags the demo link with the page it came from, and nothing about the user', () => {
    const url = new URL(enterpriseContactUrl('tenants'));

    expect(`${url.origin}${url.pathname}`).toBe('https://calendly.com/rishabh-pipeshub/30min');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      utm_source: 'pipeshub-oss',
      utm_medium: 'app',
      utm_campaign: 'enterprise-edition',
      utm_content: 'tenants',
    });
  });

  it.each(Object.entries(ENTERPRISE_GLIMPSES))(
    'shows a blurred look at the %s page that nobody can read out, focus or click',
    (_name, glimpse) => {
      const { container } = renderPage(glimpse);

      const preview = screen.getByTestId('enterprise-glimpse');
      expect(preview.getAttribute('aria-hidden')).toBe('true');
      expect(preview.hasAttribute('inert')).toBe(true);
      expect(preview.style.filter).toContain('blur');
      // Its switches, if any, are not reachable, and nothing in it reads as a real table.
      expect(screen.queryAllByRole('switch')).toHaveLength(0);
      expect(container.querySelectorAll('[role="row"], table')).toHaveLength(0);
    },
  );
});
