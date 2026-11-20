/**
 * One recipe for every rail row: selected is the muted fill, a 1px default border, primary text at medium weight and
 * aria-current; hover, press and an open menu are the subtle fill, one step lighter, with no border.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
const nav = vi.hoisted(() => ({ pathname: '/artifacts/' }));
vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: vi.fn() }),
}));

import { SidebarItem } from '../sidebar-item';
import { StaticNavSection } from '../static-nav-section';
import { SidebarFocusStyle, SIDEBAR_ROW_TINT_CLASS } from '@/app/components/sidebar/sidebar-focus-style';

const labelOf = (name: string) => screen.getByText(name);

beforeEach(() => {
  installBrowserShims();
  nav.pathname = '/artifacts/';
});
afterEach(() => cleanup());

describe('SidebarItem states', () => {
  it('paints the open chat as selected: fill, border, primary text at 500, aria-current', () => {
    renderInTheme(<SidebarItem label="Q3 plan" href="/chat/?conversationId=c1" isActive />);
    const link = screen.getByRole('link', { name: 'Q3 plan' });
    expect(link.getAttribute('aria-current')).toBe('page');
    expect(link.style.backgroundColor).toBe('var(--fill-muted)');
    expect(link.style.border).toBe('1px solid var(--border-default)');
    expect(labelOf('Q3 plan').style.color).toBe('var(--text-primary)');
    expect(labelOf('Q3 plan').style.fontWeight).toBe('500');
    // Hovering the selected row keeps it selected.
    fireEvent.mouseEnter(link);
    expect(link.style.backgroundColor).toBe('var(--fill-muted)');
  });

  it('paints a hovered row one step lighter, with no border and its own text', () => {
    renderInTheme(<SidebarItem label="Roadmap" href="/chat/?conversationId=c2" />);
    const link = screen.getByRole('link', { name: 'Roadmap' });
    fireEvent.mouseEnter(link);
    expect(link.style.backgroundColor).toBe('var(--surface-subtle)');
    expect(link.style.border).toBe('1px solid transparent');
    expect(link.getAttribute('aria-current')).toBeNull();
    expect(labelOf('Roadmap').style.color).toBe('var(--text-secondary)');
    expect(labelOf('Roadmap').style.fontWeight).toBe('400');
    fireEvent.mouseLeave(link);
    expect(link.style.backgroundColor).toBe('transparent');
  });

  it('gives a row whose menu is open the hover tint, not the selected look', () => {
    renderInTheme(<SidebarItem label="Roadmap" href="/chat/?conversationId=c2" forceHighlight />);
    const link = screen.getByRole('link', { name: 'Roadmap' });
    expect(link.style.backgroundColor).toBe('var(--surface-subtle)');
    expect(link.style.border).toBe('1px solid transparent');
  });
});

describe('Nav rows', () => {
  it('give the current page the selected border and every row the shared lighter hover', () => {
    renderInTheme(
      <>
        <SidebarFocusStyle />
        <StaticNavSection />
      </>,
    );
    const links = [...document.querySelectorAll<HTMLAnchorElement>('a')];
    const current = links.filter((a) => a.getAttribute('aria-current') === 'page');
    expect(current).toHaveLength(1);
    expect(current[0].getAttribute('href')).toContain('/artifacts');
    expect(current[0].style.border).toBe('1px solid var(--border-default)');
    expect(current[0].style.backgroundColor).toBe('var(--fill-muted)');
    for (const a of links) expect(a.classList.contains(SIDEBAR_ROW_TINT_CLASS)).toBe(true);
    const other = links.find((a) => a !== current[0])!;
    expect(other.style.border).toBe('1px solid transparent');
    // The hover and press paint (jsdom cannot hover, so read the rule the page carries).
    const css = [...document.querySelectorAll('style')].map((s) => s.textContent).join('\n');
    expect(css).toMatch(new RegExp(`\\.${SIDEBAR_ROW_TINT_CLASS}:hover \\{\\s*background-color: var\\(--surface-subtle\\)`));
    expect(css).toMatch(new RegExp(`\\.${SIDEBAR_ROW_TINT_CLASS}:active \\{\\s*background-color: var\\(--surface-subtle\\)`));
  });

  it('select Agents on the Agents page only, and then All artifacts is not selected', () => {
    const currentHrefs = () =>
      [...document.querySelectorAll<HTMLAnchorElement>('a[aria-current="page"]')].map((a) => a.getAttribute('href')?.split('?')[0]);
    const agentsRow = () => screen.getByRole('link', { name: 'Agents' });

    nav.pathname = '/agents/';
    const { unmount } = renderInTheme(<StaticNavSection />);
    expect(currentHrefs()).toEqual([expect.stringMatching(/^\/agents\/?$/)]);
    expect(agentsRow().style.border).toBe('1px solid var(--border-default)');
    expect(agentsRow().style.backgroundColor).toBe('var(--fill-muted)');
    expect(agentsRow().classList.contains(SIDEBAR_ROW_TINT_CLASS)).toBe(true);
    unmount();

    // The builder is another page.
    for (const path of ['/agents/new/', '/agents/edit/']) {
      nav.pathname = path;
      const view = renderInTheme(<StaticNavSection />);
      expect(currentHrefs()).toEqual([]);
      expect(agentsRow().style.border).toBe('1px solid transparent');
      view.unmount();
    }
  });
});
