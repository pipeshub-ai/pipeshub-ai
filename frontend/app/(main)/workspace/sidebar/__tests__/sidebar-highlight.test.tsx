import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

let pathname = '/workspace/general/';
vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
}));

// jsdom has no matchMedia; these tests cover the desktop sidebar.
vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsMobile: () => false,
}));

// Admin with every flag on, so every row is rendered.
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (selector: (s: { isAdmin: boolean }) => unknown) => selector({ isAdmin: true }),
  selectIsAdmin: (s: { isAdmin: boolean }) => s.isAdmin,
}));

vi.mock('@/lib/store/feature-flags-store', () => ({
  useFeatureFlagsStore: (selector: (s: object) => unknown) => selector({}),
  selectMcpEnabled: () => true,
  selectActionsEnabled: () => true,
  selectSkillsEnabled: () => true,
}));

// Spread the real module so exports the import graph needs (initReactI18next) stay defined.
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

import WorkspaceSidebar from '../index';

afterEach(() => {
  cleanup();
  pathname = '/workspace/general/';
});

function renderSidebar() {
  return render(
    <Theme>
      <WorkspaceSidebar />
    </Theme>,
  );
}

const current = () => document.querySelectorAll('[aria-current="page"]');
const peopleHeader = () => screen.getByText('workspace.sidebar.sections.people').closest('button')!;

describe('WorkspaceSidebar — one selected row', () => {
  // A sweep: two renders of the whole rail per page, too slow for the default 5s on a busy machine.
  it('marks exactly the row of the current page, for every page in the rail', () => {
    pathname = '/workspace/users/';
    renderSidebar();
    const hrefs = [...document.querySelectorAll('a')]
      .map((a) => a.getAttribute('href')!)
      .filter((href) => href.startsWith('/workspace/'));
    expect(hrefs.length).toBeGreaterThan(15);
    cleanup();

    for (const href of hrefs) {
      // A page below the row's route (an item's detail page) still selects that row only.
      for (const path of [`${href}/`, `${href}/some-id/`]) {
        pathname = path;
        renderSidebar();
        const selected = current();
        expect(selected, path).toHaveLength(1);
        expect(selected[0].getAttribute('href'), path).toBe(href);
        cleanup();
      }
    }
  }, 30_000);

  it('paints a hovered row with the lighter subtle fill, never the selected fill or border', () => {
    renderSidebar();
    const general = document.querySelector<HTMLElement>('a[href="/workspace/general"]')!;
    const profile = document.querySelector<HTMLElement>('a[href="/workspace/profile"]')!;
    fireEvent.mouseEnter(profile);
    expect(profile.style.backgroundColor).toBe('var(--surface-subtle)');
    expect(profile.style.border).toBe('1px solid transparent');
    expect(general.style.border).toBe('1px solid var(--border-default)');
    expect(general.style.backgroundColor).toBe('var(--fill-muted)');
    expect(current()).toHaveLength(1);
    fireEvent.mouseLeave(profile);
    expect(profile.style.backgroundColor).toBe('transparent');
  });

  it('never paints the People header as selected, even collapsed over the current page', () => {
    pathname = '/workspace/users/';
    renderSidebar();
    const people = peopleHeader();
    fireEvent.click(people);
    expect(people.getAttribute('aria-expanded')).toBe('false');
    expect(people.style.backgroundColor).toBe('transparent');
    expect(people.style.border).toBe('1px solid transparent');
  });

  it('opens People when you arrive on one of its pages, and keeps a manual collapse otherwise', () => {
    const { rerender } = renderSidebar();
    expect(peopleHeader().getAttribute('aria-expanded')).toBe('false');
    pathname = '/workspace/teams/';
    rerender(
      <Theme>
        <WorkspaceSidebar />
      </Theme>,
    );
    expect(peopleHeader().getAttribute('aria-expanded')).toBe('true');
    expect(current()[0].getAttribute('href')).toBe('/workspace/teams');
    fireEvent.click(peopleHeader());
    rerender(
      <Theme>
        <WorkspaceSidebar />
      </Theme>,
    );
    expect(peopleHeader().getAttribute('aria-expanded')).toBe('false');
  });
});
